// manifest-watch.ts — poll the marketplace for a newly published hiring-coach manifest and,
// when one appears, INSTALL it onto the lab agent's vault connection (teardown + reconnect to
// the new CID), then kick the eval — whose per-run QA hook triggers OpenSRE.
//
// Publishing ≠ installing: a published manifest sits in the marketplace, but the AgentConnection
// pins a version until it is re-connected. This watcher closes that gap for the QA lab agent.
//
// Dry-run by default (reads only, mutates nothing). Pass --apply to perform the destructive
// disconnect+reconnect install and run the eval.
//
//   VAULT_URL=… CEF_AGENT_SERVICE_PUBKEY=0x… OPENSRE_URL=… \
//   pnpm --filter @cef-ai/qa-agent exec -- tsx manifest-watch.ts \
//     --wallet /path/to/wallet.json --password cef-agents [--clips HIA-C1,HIA-A1,HIA-E1] [--apply]

import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { spawnSync } from 'node:child_process';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { Keyring } from '@polkadot/keyring';
import type { KeyringPair$Json } from '@polkadot/keyring/types';
import { cryptoWaitReady } from '@polkadot/util-crypto';
import { u8aToHex } from '@polkadot/util';
import { VaultSDK } from '@cef-ai/vault-sdk';

const HERE = dirname(fileURLToPath(import.meta.url));
const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };
const has = (n: string): boolean => process.argv.includes(n);
const expand = (p: string): string => (p.startsWith('~') ? join(homedir(), p.slice(1)) : p);
const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

const VAULT_URL = (process.env.VAULT_URL ?? 'https://vault-api.compute.test.ddcdragon.com').replace(/\/$/, '');
const MARKETPLACE = (process.env.MARKETPLACE_URL ?? 'https://agent-marketplace.compute.test.ddcdragon.com').replace(/\/$/, '');
const GAR = (process.env.GAR_URL ?? 'https://gar.compute.test.ddcdragon.com/api/v1').replace(/\/$/, '');
const AS = (process.env.CEF_AGENT_SERVICE_PUBKEY ?? '0x5df19be727730d88cb64539a52f6f263d3353eb7dff6a66a31c8e9de05addcca').replace(/^0x/, '');
const ALIAS = process.env.HIRING_AGENT_ALIAS ?? 'hiring-coach-qa';
const AGENT_ID = `${AS}:${ALIAS}`;
const SCOPE = process.env.AGENT_E2E_SCOPE ?? 'default';

const APPLY = has('--apply');
const NO_EVAL = has('--no-eval');
// Stateless mode (CI): ignore the local state file and decide purely from connection-vs-marketplace
// version. The vault connection version IS the persistent state — act iff the connection is behind
// the published version, so a version is QA'd exactly once (right when we upgrade to it).
const NO_STATE = has('--no-state');
// Force mode (manual/on-demand): run the eval + QA even when the connection is already on the
// published version. Still upgrades first if the connection is behind. Without it, behaviour is
// version-gated (only QA a newly published version) — that's the scheduled/auto path.
const FORCE = has('--force');
const WALLET = expand(arg('--wallet') ?? join(homedir(), 'RustroverProjects/hiring-coach-eval/wallet.json'));
const PASSWORD = arg('--password') ?? 'cef-agents';
// One clip = one end-to-end execution run to QA per version. QA verifies execution health
// (did the run complete?), not score quality, so a single clip is enough — pass --clips to widen.
const CLIPS = arg('--clips') ?? 'HIA-C1';
const STATE = expand(arg('--state') ?? join(homedir(), '.cef-qa', `last-cid-${ALIAS}`));

interface Conn { version: string; status: string; disconnect: () => Promise<void>; }

async function main(): Promise<void> {
  await cryptoWaitReady();
  const json = JSON.parse(readFileSync(WALLET, 'utf8')) as KeyringPair$Json;
  const pair = new Keyring().addFromJson(json);
  pair.decodePkcs8(PASSWORD);
  const wallet = { pubkey: (): string => u8aToHex(pair.publicKey), sign: async (b: Uint8Array): Promise<Uint8Array> => pair.sign(b) };

  const sdk = new VaultSDK({ endpoint: VAULT_URL, marketplaceEndpoint: MARKETPLACE, garEndpoint: GAR, wallet });
  const vault = await sdk.vault.current();

  const latest = await sdk.marketplace.getAgent(AGENT_ID);
  let current: Conn | null = null;
  try { current = (await vault.agents.get(AGENT_ID)) as unknown as Conn; } catch { current = null; }

  const lastCid = NO_STATE ? '' : (existsSync(STATE) ? readFileSync(STATE, 'utf8').trim() : '');
  const running = current?.version ?? '(not connected)';
  const needInstall = !current || current.version !== latest.version;

  console.log(`[watch] agent   = ${AGENT_ID}`);
  console.log(`[watch] published= ${latest.version}   as=${latest.agentServicePubkey}`);
  console.log(`[watch] running  = ${running}   status=${current?.status ?? '-'}`);
  console.log(`[watch] state    = ${NO_STATE ? 'stateless (connection vs published)' : `handled=${lastCid || '(none)'}`}`);

  if (FORCE) {
    console.log('[watch] FORCE — re-QA regardless of version (manual/on-demand).');
  } else if (NO_STATE) {
    // Stateless: only act when the connection is behind the published version.
    if (!needInstall) {
      console.log('[watch] connection is on the published version — nothing to do.');
      return;
    }
  } else if (lastCid && lastCid === latest.version) {
    console.log('[watch] already QA’d this published version — nothing to do.');
    return;
  }
  console.log(
    FORCE && !needInstall
      ? `[watch] re-QA current version ${running} (no upgrade needed), then eval [${CLIPS}]`
      : `[watch] NEW version → ${needInstall ? `install ${running} → ${latest.version}` : 'already installed'}, then eval [${CLIPS}]`,
  );

  if (!APPLY) {
    console.log('\n[watch] DRY-RUN (no --apply): nothing mutated. Re-run with --apply to install + eval.');
    return;
  }

  if (needInstall) {
    if (current) { console.log(`[watch] disconnecting ${running}…`); await current.disconnect(); }
    console.log(`[watch] connecting ${latest.version}…`);
    // vault-sdk 2.0.0: connect takes { agentId, scope, settings } (was { manifest, … } in 0.5.x);
    // it derives the AS pubkey from the agentId prefix and resolves the manifest server-side.
    await vault.agents.connect({ agentId: AGENT_ID, scope: SCOPE, settings: {} });
    const deadline = Date.now() + 120_000;
    let ok = false;
    while (Date.now() < deadline) {
      await sleep(4000);
      try {
        const c = (await vault.agents.get(AGENT_ID)) as unknown as Conn;
        console.log(`[watch]   connection: version=${c.version} status=${c.status}`);
        if (c.status === 'active' && c.version === latest.version) { ok = true; break; }
      } catch { /* keep polling */ }
    }
    if (!ok) throw new Error(`connection did not reach active@${latest.version} within 120s`);
    console.log(`[watch] installed ${latest.version} ✓`);
  }

  if (!NO_STATE) {
    mkdirSync(dirname(STATE), { recursive: true });
    writeFileSync(STATE, `${latest.version}\n`); // dedup key: published version (2.0.0 card has no manifestCid)
  }

  if (NO_EVAL) { console.log('[watch] --no-eval: skipping eval kick.'); return; }
  // Model NAMES this agent uses (from its own manifest), so QA can scope inference checks to them
  // — dynamic, never hardcoded. Model name = the URL tail before the version (matches orchestrator
  // `modelName=` in inference logs).
  const modelsMap = ((latest as unknown as { models?: Record<string, string> }).models) ?? {};
  const agentModels = [
    ...new Set(
      Object.values(modelsMap)
        .map((u) => String(u).split('/models/').pop()?.split('/')[0])
        .filter((x): x is string => !!x),
    ),
  ];
  console.log(`[watch] agent models: ${agentModels.join(', ') || '(none)'}`);
  // The event type that TRIGGERS a run (maps to the agent's onAudio handler). Newer manifests version
  // their engagement handles (e.g. `analyze.audio.v0843`); publishing the legacy `analyze.audio` then
  // matches NO handle and the orchestrator creates no job. Read the trigger from the manifest's
  // engagements so the eval always fires an event this version actually listens for.
  interface Engagement { id?: string; handles?: Record<string, string> }
  const engagements = ((latest as unknown as { engagements?: Engagement[] }).engagements) ?? [];
  const hasOnAudio = (e: Engagement): boolean => !!e.handles && Object.values(e.handles).includes('onAudio');
  const audioEngagement =
    engagements.find((e) => e.id === 'asr-whisper-turbo' && hasOnAudio(e)) ?? engagements.find(hasOnAudio);
  const audioEventType =
    (audioEngagement?.handles &&
      Object.entries(audioEngagement.handles).find(([, h]) => h === 'onAudio')?.[0]) || 'analyze.audio';
  console.log(`[watch] audio trigger event: ${audioEventType}${audioEngagement?.id ? ` (engagement ${audioEngagement.id})` : ''}`);
  const args = ['exec', 'tsx', join(HERE, 'lab-batch-run.ts'), '--wallet', WALLET, '--password', PASSWORD, '--clips', CLIPS, '--exp', `mp-${latest.version}`];
  console.log(`[watch] eval: pnpm ${args.join(' ')}`);
  const childEnv = { ...process.env, CEF_AGENT_SERVICE_PUBKEY: `0x${AS}`, VAULT_URL, HIRING_AGENT_ALIAS: ALIAS, CEF_AGENT_MODELS: agentModels.join(','), CEF_AUDIO_EVENT_TYPE: audioEventType, CEF_MANIFEST_VERSION: latest.version };
  const res = spawnSync('pnpm', args, { cwd: HERE, stdio: 'inherit', env: childEnv });
  process.exit(res.status ?? 0);
}

main().catch((e: unknown) => { console.error(`FATAL — ${e instanceof Error ? e.message : String(e)}`); process.exit(1); });
