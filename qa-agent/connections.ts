// connections.ts — manage the QA vault's agent connections.
//
// Lists every agent currently connected to the QA vault. With --connect, connects a target agent
// (fetching its published manifest from the marketplace). With --prune, disconnects every OTHER
// connection so only the target remains. Dry-run by default: reads only, mutates nothing — pass
// --apply to actually connect/disconnect.
//
//   VAULT_URL=… MARKETPLACE_URL=… GAR_URL=… \
//   tsx connections.ts --wallet /path/to/wallet.json --password 1234 \
//     [--connect <asPubkey:alias>] [--prune] [--apply]
//
// Example (connect the new agent + remove all old ones):
//   tsx connections.ts --wallet w.json --password 1234 \
//     --connect 34bba26b…:hiring-coach-lab2 --prune --apply

import { readFileSync } from 'node:fs';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { Keyring } from '@polkadot/keyring';
import type { KeyringPair$Json } from '@polkadot/keyring/types';
import { cryptoWaitReady } from '@polkadot/util-crypto';
import { u8aToHex } from '@polkadot/util';
import { VaultSDK } from '@cef-ai/vault-sdk';

const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };
const has = (n: string): boolean => process.argv.includes(n);
const expand = (p: string): string => (p.startsWith('~') ? join(homedir(), p.slice(1)) : p);

const VAULT_URL = (process.env.VAULT_URL ?? 'https://vault-api.compute.test.ddcdragon.com').replace(/\/$/, '');
const MARKETPLACE = (process.env.MARKETPLACE_URL ?? 'https://agent-marketplace.compute.test.ddcdragon.com').replace(/\/$/, '');
const GAR = (process.env.GAR_URL ?? 'https://gar.compute.test.ddcdragon.com/api/v1').replace(/\/$/, '');
const SCOPE = process.env.AGENT_E2E_SCOPE ?? 'default';

const APPLY = has('--apply');
const PRUNE = has('--prune');
const CONNECT = (arg('--connect') ?? '').replace(/^0x/, '');
const WALLET = expand(arg('--wallet') ?? join(homedir(), 'RustroverProjects/opensre/6QeLWV6XLRYbxwwMgnv7PSHFJgYWYc38xFk6htS2fWkzcZ1R.json'));
const PASSWORD = arg('--password') ?? '1234';

const short = (s: string): string => (s.length > 22 ? `${s.slice(0, 12)}…${s.slice(-6)}` : s);

async function main(): Promise<void> {
  await cryptoWaitReady();
  const json = JSON.parse(readFileSync(WALLET, 'utf8')) as KeyringPair$Json;
  const pair = new Keyring().addFromJson(json);
  pair.decodePkcs8(PASSWORD);
  const wallet = { pubkey: (): string => u8aToHex(pair.publicKey), sign: async (b: Uint8Array): Promise<Uint8Array> => pair.sign(b) };

  const sdk = new VaultSDK({ endpoint: VAULT_URL, marketplaceEndpoint: MARKETPLACE, garEndpoint: GAR, wallet });
  const vault = await sdk.vault.current();
  console.log(`[conn] vault=${short(vault.id)}  mode=${APPLY ? 'APPLY' : 'DRY-RUN'}${CONNECT ? `  connect=${short(CONNECT)}` : ''}${PRUNE ? '  prune=on' : ''}\n`);

  // 1) List current connections.
  const before = await vault.agents.list();
  console.log(`[conn] ${before.length} current connection(s):`);
  for (const c of before) {
    console.log(`   • ${c.agentId}  v=${c.version}  status=${c.status}  created=${c.createdAt}`);
  }
  console.log('');

  // 2) Connect the target agent (if not already connected).
  if (CONNECT) {
    const already = before.find((c) => c.agentId === CONNECT);
    if (already) {
      console.log(`[conn] target ${short(CONNECT)} already connected (v=${already.version}) — skip connect.`);
    } else {
      const manifest = await sdk.marketplace.getAgent(CONNECT);
      console.log(`[conn] ${APPLY ? 'CONNECTING' : 'would connect'} ${CONNECT}  (published v=${(manifest as { version?: string }).version ?? '?'})`);
      if (APPLY) {
        // vault-sdk 2.0.0: connect takes { agentId, scope, settings } and derives the AS pubkey
        // from the agentId prefix (fetches the manifest + signs the GAR agreement internally).
        const handle = await vault.agents.connect({ agentId: CONNECT, scope: SCOPE, settings: {} });
        console.log(`[conn]   connected ✓  v=${handle.version} status=${handle.status}`);
      }
    }
  }

  // 3) Prune: disconnect every connection that is NOT the target.
  if (PRUNE) {
    const victims = before.filter((c) => c.agentId !== CONNECT);
    console.log(`\n[conn] prune: ${victims.length} connection(s) to remove${CONNECT ? ` (keeping ${short(CONNECT)})` : ''}:`);
    for (const c of victims) {
      console.log(`   • ${APPLY ? 'DISCONNECTING' : 'would disconnect'} ${c.agentId}  v=${c.version}`);
      if (APPLY) {
        try {
          await c.disconnect();
          console.log('       removed ✓');
        } catch (e) {
          console.log(`       FAILED: ${e instanceof Error ? e.message : String(e)}`);
        }
      }
    }
  }

  // 4) Show the resulting state (only meaningful after --apply).
  if (APPLY) {
    const after = await vault.agents.list();
    console.log(`\n[conn] result: ${after.length} connection(s):`);
    for (const c of after) console.log(`   • ${c.agentId}  v=${c.version}  status=${c.status}`);
  } else {
    console.log('\n[conn] DRY-RUN — nothing changed. Re-run with --apply to perform the above.');
  }
}

main().catch((e: unknown) => { console.error(`[conn] FATAL — ${e instanceof Error ? e.message : String(e)}`); if (e instanceof Error && e.stack) console.error(e.stack); process.exit(1); });
