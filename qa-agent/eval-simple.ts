// eval-simple.ts — minimal end-to-end smoke test against the connected QA-vault agent.
//
// 1) resolve the connected agent + its onAudio event handle (from the connection manifest),
// 2) publish ONE audio-analysis event (a single short clip),
// 3) poll the vault for the resulting job and read its tasks + task logs — i.e. the agent's own logs.
//
//   tsx eval-simple.ts --wallet <keystore.json> --password 1234 [--clip HIA-A3]

import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { Keyring } from '@polkadot/keyring';
import type { KeyringPair$Json } from '@polkadot/keyring/types';
import { cryptoWaitReady } from '@polkadot/util-crypto';
import { u8aToHex } from '@polkadot/util';
import { VaultSDK } from '@cef-ai/vault-sdk';

const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };
const expand = (p: string): string => (p.startsWith('~') ? join(homedir(), p.slice(1)) : p);
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

const VAULT_URL = (process.env.VAULT_URL ?? 'https://vault-api.compute.test.ddcdragon.com').replace(/\/$/, '');
const MARKETPLACE = (process.env.MARKETPLACE_URL ?? 'https://agent-marketplace.compute.test.ddcdragon.com').replace(/\/$/, '');
const GAR = (process.env.GAR_URL ?? 'https://gar.compute.test.ddcdragon.com/api/v1').replace(/\/$/, '');
const AS = (process.env.CEF_AGENT_SERVICE_PUBKEY ?? '34bba26b1b080fd381b4e84a4e74befd61653697dd0b610bf75ccbc3bd6c8760').replace(/^0x/, '');
const ALIAS = process.env.HIRING_AGENT_ALIAS ?? 'hiring-coach-lab2';
const AGENT_ID = `${AS}:${ALIAS}`;
const SCOPE = process.env.AGENT_E2E_SCOPE ?? 'default';
const AUDIO_BASE = process.env.AUDIO_BASE ?? 'https://ddc-s3-gateway.compute.test.ddcdragon.com/hiringcoach-public/scenarios/audio';
const WALLET = expand(arg('--wallet') ?? join(homedir(), 'RustroverProjects/opensre/6QeLWV6XLRYbxwwMgnv7PSHFJgYWYc38xFk6htS2fWkzcZ1R.json'));
const PASSWORD = arg('--password') ?? '1234';

// A short clip (2 chunks) so the smoke test is quick.
const CLIP = arg('--clip') ?? 'HIA-A3';
const STEM = arg('--stem') ?? 'b3_reading_ai';
const CHUNKS = (arg('--chunks') ?? '000,001').split(',');

function deriveAudioEvent(manifest: unknown): string {
  interface Eng { id?: string; handles?: Record<string, string> }
  const engs = ((manifest as { engagements?: Eng[] } | undefined)?.engagements) ?? [];
  const hasOnAudio = (e: Eng): boolean => !!e.handles && Object.values(e.handles).includes('onAudio');
  const eng = engs.find((e) => e.id === 'asr-whisper-turbo' && hasOnAudio(e)) ?? engs.find(hasOnAudio);
  const evt = eng?.handles && Object.entries(eng.handles).find(([, h]) => h === 'onAudio')?.[0];
  return evt || 'analyze.audio';
}

async function main(): Promise<void> {
  await cryptoWaitReady();
  const j = JSON.parse(readFileSync(WALLET, 'utf8')) as KeyringPair$Json;
  const pair = new Keyring().addFromJson(j); pair.decodePkcs8(PASSWORD);
  const wallet = { pubkey: (): string => u8aToHex(pair.publicKey), sign: async (b: Uint8Array): Promise<Uint8Array> => pair.sign(b) };

  const sdk = new VaultSDK({ endpoint: VAULT_URL, marketplaceEndpoint: MARKETPLACE, garEndpoint: GAR, wallet });
  const vault = await sdk.vault.current();

  const conn = await vault.agents.get(AGENT_ID);
  const audioEvent = deriveAudioEvent(conn.manifest);
  console.log(`[eval] vault=${vault.id}  agent=${AGENT_ID}  v=${conn.version} status=${conn.status}`);
  console.log(`[eval] audio event type: ${audioEvent} (v0.8.79 binds onAudio to the unversioned 'analyze.audio')`);

  // 1) Publish one audio-analysis event.
  const conv = randomUUID();
  const urls = CHUNKS.map((c) => `${AUDIO_BASE}/${STEM}.${c}.mp3`);
  console.log(`[eval] publishing ${CLIP} (${urls.length} chunk(s)) conv=${conv}`);
  const published = await vault.scope(SCOPE).publish({
    type: audioEvent,
    role: 'user',
    context: conv,
    target: AGENT_ID,
    payload: { conversation_id: conv, candidate_id: CLIP, audio_ddc_urls: urls },
  });
  console.log(`[eval] published eventId=${(published as { eventId?: string }).eventId ?? '?'}`);

  // 2) Poll for the job the orchestrator created for this context, then read its tasks + logs.
  // Jobs are listed under the agent connection; the SDK pages are `{ items, hasMore }`.
  console.log('[eval] polling for the job + agent logs (up to ~3 min)…');
  const deadline = Date.now() + 180_000;
  let jobId = '';
  while (Date.now() < deadline) {
    await sleep(6000);
    const page = await conn.jobs.list({ limit: 20 } as never).catch(() => null);
    const jobs = (page as { items?: Array<{ jobId: string; status?: string; context?: string }> } | null)?.items ?? [];
    const job = jobs.find((x) => x.context === conv);
    if (job) {
      jobId = job.jobId;
      console.log(`[eval] job=${jobId} status=${job.status || '(running)'}`);
      if (['completed', 'failed', 'error'].includes(String(job.status))) break;
    } else {
      console.log('[eval]   …no job yet');
    }
  }

  if (!jobId) { console.log('[eval] no job appeared — agent may not have picked up the event.'); return; }

  // 3) Read the agent's own logs: tasks → per-task structured logs.
  const jh = vault.jobs.get(jobId);
  const tasks = (await jh.tasks.list().catch(() => null)) as { items?: Array<{ taskId: string; name?: string; status?: string }> } | null;
  const tlist = tasks?.items ?? [];
  console.log(`\n[eval] ==== AGENT LOGS (job ${jobId}, ${tlist.length} task(s)) ====`);
  for (const t of tlist) {
    console.log(`\n  ▸ task ${t.taskId} ${t.name ? `(${t.name})` : ''} status=${t.status ?? '?'}`);
    const lp = (await jh.tasks.logs(t.taskId, { limit: 50 } as never).catch(() => null)) as { logs?: Array<{ timestamp?: string; message?: string }> } | null;
    for (const l of (lp?.logs ?? [])) console.log(`      ${l.timestamp ?? ''}  ${l.message ?? ''}`);
    if (!lp?.logs?.length) console.log('      (no structured logs for this task)');
  }
}

main().catch((e: unknown) => { console.error(`[eval] FATAL — ${e instanceof Error ? e.message : String(e)}`); if (e instanceof Error && e.stack) console.error(e.stack); process.exit(1); });
