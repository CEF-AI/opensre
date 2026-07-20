// quality-bar-run.ts
//
// Daily hiring-coach quality bar. Publishes analyze.audio.v0843 for each of
// the 16 canonical quality-bar clips (same event type + suffix as production
// widget flow), polls analysis_runs.status, applies the pass/fail predicate
// against quality-bar-inputs.ts, and emits ONE JSON row.
//
// Krishna's GH Action consumes the row and appends it to a Notion database.
// Rolling averages / perfect-run % / watermark are computed on the Notion
// side from history — this script emits only the current run's numbers.
//
// Usage (from agents/hiring-coach/):
//   CEF_AGENT_SERVICE_PUBKEY=0x5df19be7... \
//   VAULT_URL=https://vault-api.compute.test.ddcdragon.com \
//   ./node_modules/.bin/tsx scripts/lab/quality-bar-run.ts \
//     --wallet ~/agent-catalog-hiring/agents/hiring-coach/wallet.json \
//     --password cef-agents \
//     --out row.json
//
// Emits row.json with schema:
//   { timestamp_utc, run_id, agent, agent_alias, as_pubkey,
//     clips_total, clips_passed, pass_ratio,
//     per_clip: [{ clip, pass, checks: [{dim, expected, actual, pass}] }, ...] }
//
// Exit code: 0 always (as long as script runs to completion). Row content
// tells you pass/fail per clip. GH Action can inspect row.json.aggregate.

import { randomUUID } from 'node:crypto';
import { readFileSync, writeFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { Keyring } from '@polkadot/keyring';
import type { KeyringPair$Json } from '@polkadot/keyring/types';
import { cryptoWaitReady } from '@polkadot/util-crypto';
import { u8aToHex } from '@polkadot/util';
import { Vault } from '@cef-ai/client-sdk';
import { QUALITY_BAR_CLIPS, checkExpected } from './quality-bar-inputs.ts';

const VAULT_URL = (process.env.VAULT_URL ?? 'https://vault-api.compute.test.ddcdragon.com').replace(/\/$/, '');
const SCOPE = 'default';
const AS = (process.env.CEF_AGENT_SERVICE_PUBKEY ?? '').replace(/^0x/, '');
if (!AS) throw new Error('Set CEF_AGENT_SERVICE_PUBKEY');
const AGENT_ALIAS = process.env.HIRING_AGENT_ALIAS ?? 'hiring-coach-lab2';
const ASR_ALIAS = process.env.CEF_ASR_ALIAS ?? 'parakeetTdtV2'; // model with live testnet nodes
const AGENT_ID = `${AS}:${AGENT_ALIAS}`;
const AUDIO_BASE = process.env.AUDIO_BASE ?? 'https://ddc-s3-gateway.compute.test.ddcdragon.com/hiringcoach-public/scenarios/audio';
const PER_CLIP_TIMEOUT_MS = Number(process.env.PER_CLIP_TIMEOUT_MS ?? '900000'); // 15 min per clip
const POLL_INTERVAL_MS = 8000;

function parseArg(flag: string): string | undefined {
  const i = process.argv.indexOf(flag);
  return i >= 0 && i + 1 < process.argv.length ? process.argv[i + 1] : undefined;
}

function expand(p: string): string {
  return p.startsWith('~') ? join(homedir(), p.slice(1)) : p;
}

function rows(res: unknown): Record<string, unknown>[] {
  const r = res as { columns?: unknown; rows?: unknown };
  if (Array.isArray(r.columns) && Array.isArray(r.rows)) {
    const names = r.columns.map(String);
    return (r.rows as unknown[][]).map((row) => Object.fromEntries(names.map((c, i) => [c, row[i]])));
  }
  return [];
}

interface ClipOutcome {
  clip: string;
  conv_id: string;
  final_status: 'completed' | 'failed' | 'timeout';
  auth: number | null;
  clarity: number | null;
  engagement: number | null;
  checks: Array<{ dim: 'authenticity' | 'clarity' | 'engagement'; expected: string; actual: number | null; pass: boolean; reason: string }>;
  pass: boolean;
  elapsed_sec: number;
}

async function processClip(
  vault: any,
  vaultId: string,
  clip: typeof QUALITY_BAR_CLIPS[number],
): Promise<ClipOutcome> {
  const conv = randomUUID();
  const t0 = Date.now();
  const urls = clip.chunks.map((c) => `${AUDIO_BASE}/${clip.stem}.${c}.mp3`);

  console.error(`[${clip.clip_code}] conv=${conv.slice(0, 8)}… chunks=${urls.length}`);

  await vault.events.publish(vaultId, SCOPE, [{
    // v0.8.79 binds onAudio to the unversioned `analyze.audio` (no suffix — confirmed by Bren);
    // the old `.v0843` suffix matched no handler → no job.
    type: 'analyze.audio', role: 'user', scope: SCOPE, context: conv, target: AGENT_ID,
    timestamp: new Date().toISOString(),
    payload: {
      conversation_id: conv,
      candidate_id: clip.clip_code,
      audio_ddc_urls: urls,
      // Pin ASR to a model with live inference nodes (parakeetTdtV2); canary had none → runs failed.
      profile: { asrAlias: ASR_ALIAS },
    },
  }]);

  const deadline = Date.now() + PER_CLIP_TIMEOUT_MS;
  let finalStatus: 'completed' | 'failed' | 'timeout' = 'timeout';
  let auth: number | null = null, clarity: number | null = null, engagement: number | null = null;

  while (Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    try {
      const res = await vault.cubbies.query(vaultId, SCOPE, AGENT_ID, 'hiring', {
        sql: 'SELECT status, reading_likelihood, clarity_overall, engagement_overall FROM analysis_runs WHERE conversation_id = ?',
        params: [conv],
      });
      const row = rows(res)[0];
      if (row) {
        const s = String(row['status']);
        if (s === 'completed' || s === 'failed') {
          finalStatus = s as 'completed' | 'failed';
          const parseN = (v: unknown): number | null => {
            if (v == null) return null;
            const n = Number(v);
            return Number.isFinite(n) ? n : null;
          };
          auth = parseN(row['reading_likelihood']);
          clarity = parseN(row['clarity_overall']);
          engagement = parseN(row['engagement_overall']);
          break;
        }
      }
    } catch {
      // keep polling
    }
  }

  const cA = checkExpected(auth, clip.expected.authenticity);
  const cC = checkExpected(clarity, clip.expected.clarity);
  const cE = checkExpected(engagement, clip.expected.engagement);
  const clipPass = finalStatus === 'completed' && cA.pass && cC.pass && cE.pass;
  const elapsed_sec = Math.round((Date.now() - t0) / 1000);

  console.error(`[${clip.clip_code}] status=${finalStatus} pass=${clipPass} auth=${auth} clarity=${clarity} eng=${engagement} elapsed=${elapsed_sec}s`);

  return {
    clip: clip.clip_code,
    conv_id: conv,
    final_status: finalStatus,
    auth,
    clarity,
    engagement,
    checks: [
      { dim: 'authenticity', expected: clip.expected.authenticity, actual: auth,       pass: cA.pass, reason: cA.reason },
      { dim: 'clarity',      expected: clip.expected.clarity,      actual: clarity,    pass: cC.pass, reason: cC.reason },
      { dim: 'engagement',   expected: clip.expected.engagement,   actual: engagement, pass: cE.pass, reason: cE.reason },
    ],
    pass: clipPass,
    elapsed_sec,
  };
}

async function main(): Promise<void> {
  await cryptoWaitReady();

  const walletPath = parseArg('--wallet');
  const password = parseArg('--password');
  const out = parseArg('--out');
  if (!walletPath || !password) throw new Error('Need --wallet <keystore.json> --password <pw>');

  const wJson = JSON.parse(readFileSync(expand(walletPath), 'utf8')) as KeyringPair$Json;
  const pair = new Keyring().addFromJson(wJson);
  pair.decodePkcs8(password);
  const pub = u8aToHex(pair.publicKey);
  const wallet = {
    type: 'ed25519' as const, address: pub, publicKey: pub, isReady: async () => true,
    sign: async (d: string) => u8aToHex(pair.sign(new TextEncoder().encode(d))).replace(/^0x/, ''),
    signRawBytes: async (b: Uint8Array) => u8aToHex(pair.sign(b)).replace(/^0x/, ''),
  };
  const vault = new Vault({ url: VAULT_URL, wallet: wallet as never }) as unknown as {
    current: () => Promise<{ vaultId: string }>;
    events: { publish: (v: string, s: string, e: unknown[]) => Promise<unknown> };
    cubbies: { query: (v: string, s: string, a: string, alias: string, q: { sql: string; params: unknown[] }) => Promise<unknown> };
  };
  const vaultId = (await vault.current()).vaultId;

  const runId = `quality-bar-${new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19)}`;
  const startedAt = new Date().toISOString();
  console.error(`[quality-bar] vault=${vaultId.slice(0, 14)}… agent=${AGENT_ID.slice(0, 24)}… clips=${QUALITY_BAR_CLIPS.length} run_id=${runId}`);

  // Grouped processing (Bren's guidance): fire in groups of GROUP_SIZE, stagger each publish within a
  // group by STAGGER_MS, and wait for the whole group to finish before the next. Caps concurrent
  // cluster/whisper load at GROUP_SIZE, and the stagger keeps the wallet-signed publishes
  // nonce-sequential (concurrent signing would race). Order is preserved for the per_clip output.
  const GROUP_SIZE = Number(process.env.QUALITY_GROUP_SIZE ?? 4);
  const STAGGER_MS = Number(process.env.QUALITY_STAGGER_MS ?? 2000);
  const outcomes: ClipOutcome[] = [];
  for (let i = 0; i < QUALITY_BAR_CLIPS.length; i += GROUP_SIZE) {
    const group = QUALITY_BAR_CLIPS.slice(i, i + GROUP_SIZE);
    console.error(`[quality-bar] group ${i / GROUP_SIZE + 1}: ${group.map((c) => c.clip_code).join(', ')}`);
    const groupOutcomes = await Promise.all(
      group.map(async (clip, j) => {
        await new Promise((r) => setTimeout(r, j * STAGGER_MS)); // stagger publishes → nonce-safe
        return processClip(vault, vaultId, clip);
      }),
    );
    outcomes.push(...groupOutcomes);
  }

  const clipsPassed = outcomes.filter((o) => o.pass).length;
  const passRatio = QUALITY_BAR_CLIPS.length > 0 ? clipsPassed / QUALITY_BAR_CLIPS.length : 0;
  const finishedAt = new Date().toISOString();

  const row = {
    schema_version: 1,
    timestamp_utc: startedAt,
    finished_utc: finishedAt,
    run_id: runId,
    agent: 'hiring-coach',
    agent_alias: AGENT_ALIAS,
    as_pubkey: `0x${AS}`,
    predicate: {
      description: "clip passes iff analysis_runs.status='completed' AND all 3 dim checks pass; observe=FAIL; '>X' → actual > X; '<X' → actual < X",
      dimensions: ['authenticity', 'clarity', 'engagement'],
    },
    aggregate: {
      clips_total: QUALITY_BAR_CLIPS.length,
      clips_passed: clipsPassed,
      clips_completed: outcomes.filter((o) => o.final_status === 'completed').length,
      clips_failed: outcomes.filter((o) => o.final_status === 'failed').length,
      clips_timeout: outcomes.filter((o) => o.final_status === 'timeout').length,
      pass_ratio: passRatio,
    },
    per_clip: outcomes,
  };

  const rowJson = JSON.stringify(row, null, 2);
  if (out) {
    writeFileSync(out, rowJson + '\n');
    console.error(`[quality-bar] wrote row → ${out}`);
  } else {
    process.stdout.write(rowJson + '\n');
  }
  console.error(`[quality-bar] ${clipsPassed}/${QUALITY_BAR_CLIPS.length} clips passed (pass_ratio=${passRatio.toFixed(3)})`);
}

main().catch((e: unknown) => {
  console.error('[quality-bar] FAIL', e instanceof Error ? e.message : e);
  process.exit(1);
});
