// lab-batch-run.ts — publish all HIA clips then poll in parallel (up to 50 concurrent).
// Publishes analyze.audio for each, polls to completion, then dumps structured JSON.
// Usage:
//   CEF_AGENT_SERVICE_PUBKEY=0xcd817... VAULT_URL=... \
//   tsx scripts/lab-batch-run.ts \
//     --wallet ~/Downloads/6US5V6c...json --password cef-agents
//
// Output: one JSON object per line (NDJSON) of shape:
//   { clip, convId, variant, scores, turns[] }

import { readFileSync, writeFileSync, mkdirSync, appendFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { randomUUID } from 'node:crypto';
import { homedir } from 'node:os';
import { join } from 'node:path';
import { Keyring } from '@polkadot/keyring';
import type { KeyringPair$Json } from '@polkadot/keyring/types';
import { cryptoWaitReady } from '@polkadot/util-crypto';
import { u8aToHex } from '@polkadot/util';
import { Vault } from '@cef-ai/client-sdk';

const VAULT_URL = (process.env.VAULT_URL ?? 'https://vault-api.compute.test.ddcdragon.com').replace(/\/$/, '');
const SCOPE = 'default';
const AS = (process.env.CEF_AGENT_SERVICE_PUBKEY ?? '').replace(/^0x/, '');
if (!AS) throw new Error('Set CEF_AGENT_SERVICE_PUBKEY');
const AGENT_ALIAS = process.env.HIRING_AGENT_ALIAS ?? 'hiring-coach-qa';
const AGENT_ID = `${AS}:${AGENT_ALIAS}`;
const S3 = parseArg('--s3-base') ?? 'https://ddc-s3-gateway.compute.test.ddcdragon.com/hiringcoach-public/scenarios/audio';
// The event type that triggers a run (maps to the agent's onAudio handler). Newer manifests version
// their engagement handles (e.g. `analyze.audio.v0843`), so a hardcoded `analyze.audio` matches no
// handle and the orchestrator creates NO job. manifest-watch derives the right name from the target
// manifest's engagement `handles` and passes it here; default keeps the legacy unversioned contract.
const AUDIO_EVENT_TYPE = process.env.CEF_AUDIO_EVENT_TYPE || 'analyze.audio';

// --- QA trigger (opt-in) ------------------------------------------------------------------
// When OPENSRE_URL is set, each finished run is handed to OpenSRE, which investigates it
// across the CEF stack and posts the QA report itself (via its own publish stage). This is a
// trigger only — no analysis or Telegram posting happens here. Unset OPENSRE_URL = no-op, so
// normal eval runs are unaffected. Trigger failures never break the eval.
const OPENSRE_URL = (process.env.OPENSRE_URL ?? '').replace(/\/$/, '');
const OPENSRE_API_KEY = process.env.OPENSRE_API_KEY ?? '';
// CLI mode (CI): instead of POSTing to a gateway, append each alert (JSON line) to this file;
// a later step runs `opensre investigate --input-json <alert>` per line. Set by the workflow.
const QA_EMIT_FILE = process.env.QA_EMIT_FILE ?? '';
const CEF_CLUSTER = process.env.CEF_CLUSTER ?? 'dragon1-testnet';
const QA_MODEL = process.env.CEF_JUDGE_MODEL ?? 'gemma4_31b';
const AGENT_MODELS = process.env.CEF_AGENT_MODELS ?? ''; // this agent's model names (from manifest-watch)
const QA_TRIGGERS: Promise<void>[] = [];

function triggerQA(convId: string, clip: string, variant: string, mode: 'full_qa' | 'execution') {
  if (!OPENSRE_URL && !QA_EMIT_FILE) return;
  const execPart = `PART A execution: cef_agent_logs for conversation ${convId} (did every activity complete?), `
    + `plus a component sweep with cef_component_logs (ddc-s3-gateway, orchestrator, agent-runtime). `
    + `Do NOT conclude from agent logs alone.`;
  const regrPart = `PART B score quality: cef_clip_history for clip ${clip}, compare THIS run's scores `
    + `(reading_likelihood, linguistic_score, clarity_overall) to the clip's OWN historical baseline; `
    + `classify regression vs improvement vs noise. No fixed thresholds — judge against the clip's own history.`;
  const topic = mode === 'execution' ? 'investigation_procedure' : 'full_qa';
  const body = mode === 'execution' ? execPart : `${execPart} ${regrPart} Report both A and B and which part is the issue.`;
  const alert = {
    alert_name: `CEF hiring-coach QA (${mode})`,
    severity: 'high',
    alert_source: 'cef', // OpenSRE routes this to the beautified CEF QA report
    commonAnnotations: {
      summary: `QA of hiring-coach run ${convId} (${mode}).`,
      description: `QA of hiring-coach run conversation_id ${convId} (clip ${clip}, variant ${variant}). `
        + `Call get_cef_guidance topic ${topic}. ${body}`,
      context_sources: 'cef,grafana',
      // structured keys the beautified subtitle/footer read:
      variant, clip, cluster: CEF_CLUSTER, model: QA_MODEL, conversation_id: convId,
      // this agent's model names, so QA can scope inference checks to them (see investigation_procedure)
      agent_models: AGENT_MODELS,
    },
  };
  // CLI mode: append the alert as a JSON line for `opensre investigate --input-json` to consume.
  if (QA_EMIT_FILE) {
    appendFileSync(QA_EMIT_FILE, JSON.stringify(alert) + '\n');
    console.error(`  [qa] ${clip} ${mode} → emitted to ${QA_EMIT_FILE}`);
    return;
  }
  // Gateway mode: POST to a running OpenSRE gateway.
  const req = fetch(`${OPENSRE_URL}/investigate`, {
    method: 'POST',
    headers: { 'content-type': 'application/json', ...(OPENSRE_API_KEY ? { 'x-api-key': OPENSRE_API_KEY } : {}) },
    body: JSON.stringify({ raw_alert: alert, alert_name: alert.alert_name, pipeline_name: 'hiring-coach', severity: 'high' }),
  })
    .then(r => { console.error(`  [qa] ${clip} ${mode} → OpenSRE ${r.status}`); })
    .catch(e => { console.error(`  [qa] ${clip} ${mode} trigger failed: ${e instanceof Error ? e.message : String(e)}`); });
  QA_TRIGGERS.push(req);
}

const ALL_CLIPS: { name: string; stem: string; chunks: string[] }[] = [
  { name: 'HIA-C1', stem: 'heinrich_clarity_good', chunks: ['000','001','002','003','004'] },
  { name: 'HIA-C2', stem: 'henrich_clarity_fp',    chunks: ['000','001','002','003'] },
  { name: 'HIA-C3', stem: 'Saad_clarity_poor',     chunks: ['000','001','002','003','004','005','006'] },
  { name: 'HIA-C4', stem: 'yassine_clarity1',      chunks: ['000','001','002','003'] },
  { name: 'HIA-C5', stem: 'yassine_clarity2',      chunks: ['000','001','002','003','004','005','006'] },
  { name: 'HIA-C8', stem: 'Henrich_casual',        chunks: ['000','001','002','003'] },
  { name: 'HIA-C9', stem: 'b3_ceo_interview',     chunks: ['000','001'] },
  { name: 'HIA-E1', stem: 'henrich_engaged_pos',   chunks: ['000','001','002','003','004'] },
  { name: 'HIA-E2', stem: 'heinrich_engagement',   chunks: ['000','001','002','003'] },
  { name: 'HIA-C6', stem: 'Saad_clarity_bad',      chunks: ['000','001','002','003','004'] },
  { name: 'HIA-C7', stem: 'Saad_clarity_zero',     chunks: ['000','001','002','003','004'] },
  { name: 'HIA-A1', stem: 'dmitry_sfp',            chunks: ['000','001','002','003','004'] },
  { name: 'HIA-A2', stem: 'heinrich_clarity_good', chunks: ['000','001','002','003','004'] },
  // STAR clarity calibration clips
  { name: 'HIA-C16', stem: 'star_csm_agency',     chunks: ['000','001','002','003','004','005'] },
  { name: 'HIA-C17', stem: 'star_consulting_sia',  chunks: ['000','001','002','003','004'] },
  { name: 'HIA-C18', stem: 'star_rei_chris',        chunks: ['000'] },
  // Batch 4 — low clarity calibration clips
  { name: 'HIA-C10', stem: 'b4_caitlin_upton',     chunks: ['000','001'] },
  { name: 'HIA-C11', stem: 'b4_sarah_palin_couric', chunks: ['000','001'] },
  { name: 'HIA-C12', stem: 'b4_dan_quayle',         chunks: ['000','001'] },
  { name: 'HIA-C14', stem: 'b4_boris_johnson',      chunks: ['000','001','002','003'] },
  { name: 'HIA-C15', stem: 'b4_zuckerberg_senate',  chunks: ['000','001'] },
  // Batch 3 — authenticity edge cases
  { name: 'HIA-A3', stem: 'b3_reading_ai',        chunks: ['000','001'] },
  { name: 'HIA-A4', stem: 'b3_ceo_not_reading',   chunks: ['000','001'] },
  { name: 'HIA-A5', stem: 'b3_ceo_spontaneous',   chunks: ['000','001'] },
  { name: 'HIA-A6', stem: 'b3_boring_monotone',   chunks: ['000','001','002'] },
  { name: 'HIA-A7', stem: 'b3_amateur_rehearsed', chunks: ['000','001','002'] },
  { name: 'HIA-A8', stem: 'b3_ceo_interview',     chunks: ['000','001'] },
  { name: 'HIA-A9', stem: 'b3_politician',        chunks: ['000','001','002'] },
  { name: 'HIA-A10',stem: 'b3_bad_presentation',  chunks: ['000','001','002'] },
  // Engagement calibration — Naval + B1 candidate question sections
  { name: 'HIA-E3', stem: 'b5_naval_meaning_life',  chunks: ['000','001','002','003','004','005','006','007','008','009'] },
  { name: 'HIA-E4', stem: 'b1_heinrich_questions',  chunks: ['000','001','002','003','004','005','006','007'] },
  { name: 'HIA-E5', stem: 'b1_fazle_questions',     chunks: ['000','001','002','003','004','005','006','007'] },
  { name: 'HIA-E6', stem: 'b1_saad_questions',      chunks: ['000','001','002','003','004','005'] },
  { name: 'HIA-E7', stem: 'b1_yassine_questions',   chunks: ['000','001','002','003','004','005'] },
  { name: 'HIA-E8', stem: 'b1_emre_questions',      chunks: ['000','001','002','003','004','005','006','007','008'] },
  { name: 'HIA-E9', stem: 'b1_dmitry_questions',    chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013'] },
  { name: 'HIA-E10', stem: 'b5_bad_engagement_full', chunks: ['000','001','002','003','004','005'] },
  { name: 'HIA-E11', stem: 'b5_fisher_string',       chunks: ['000','001','002','003','004','005','006','007','008','009','010','011'] },
  // Batch 1 — full interviews (25s chunks, DDC staging)
  { name: 'HIA-F1', stem: 'fazle_rahman_b1',     chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035'] },
  { name: 'HIA-F2', stem: 'saad_khatimiti_b1',   chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038'] },
  { name: 'HIA-F3', stem: 'yassine_ferkouch_b1', chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040'] },
  { name: 'HIA-F4', stem: 'emre_ulgac_b1',       chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041'] },
  { name: 'HIA-F5',  stem: 'heinrich_heimbuch_b1',  chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051','052','053'] },
  { name: 'HIA-F6',  stem: 'dmitry_fadeev_b1',      chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051','052','053','054','055','056','057'] },
  // Batch 2 — full interviews
  { name: 'HIA-F7',  stem: 'andrey_zakharov_b2',    chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029'] },
  { name: 'HIA-F8',  stem: 'bengu_sevik_b2',         chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051','052','053','054','055','056','057','058','059','060','061'] },
  { name: 'HIA-F9',  stem: 'devesh_dubey_b2',        chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045'] },
  { name: 'HIA-F10', stem: 'mahesh_haldar_b2',       chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051','052','053','054','055','056','057','058','059','060','061','062','063','064','065','066','067','068'] },
  { name: 'HIA-F11', stem: 'shrilaxmi_laxmish_b2',   chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051','052','053','054','055','056','057','058','059','060','061','062','063'] },
  { name: 'HIA-F12', stem: 'sudarshan_b2',           chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024','025','026','027','028','029','030','031','032','033','034','035','036','037','038','039','040','041','042','043','044','045','046','047','048','049','050','051'] },
  { name: 'HIA-F13', stem: 'angel_b2',               chunks: ['000','001','002','003','004','005','006','007','008','009','010','011','012','013','014','015','016','017','018','019','020','021','022','023','024'] },
  { name: 'HIA-F14', stem: 'yulia_b2',               chunks: ['000'] },
];
const PRESETS: Record<string, string[]> = {
  // curated 15-clip power batch covering auth/clarity/engagement edge cases
  hitlist: ['HIA-A2','HIA-A3','HIA-A8','HIA-A10','HIA-C1','HIA-C7','HIA-C9','HIA-C10','HIA-C14','HIA-C16','HIA-C17','HIA-E1','HIA-E3','HIA-E4','HIA-E6','HIA-E11'],
};
// --preset hitlist | --clip HIA-A1 (single) | --clips HIA-A1,HIA-A2 (comma-separated) | (none) = all
const presetArg = parseArg('--preset');
const clipArg = parseArg('--clip');
const clipsArg = parseArg('--clips');
const presetNames = presetArg ? (PRESETS[presetArg] ?? (() => { throw new Error(`Unknown preset: ${presetArg}`); })()) : null;
const clipFilter = presetNames ? new Set(presetNames) : clipsArg ? new Set(clipsArg.split(',').map(s => s.trim())) : clipArg ? new Set([clipArg]) : null;
const CLIPS = clipFilter ? ALL_CLIPS.filter(c => clipFilter.has(c.name)) : ALL_CLIPS;
// --concurrency N: poll N clips in parallel after publishing all (default 1 = sequential)
const CONCURRENCY = Math.max(1, parseInt(parseArg('--concurrency') ?? '1', 10));
// --exp E003: tag this batch with an experiment ID (stored in payload; also used as profile.label when --label is absent)
const expIdArg = parseArg('--exp');
// --label "my-label": explicit profile.label override (falls back to --exp, then agent-side profileSignature)
const labelArg = parseArg('--label') ?? expIdArg;
// --judge-prompt "...": override the authenticity judge system prompt via profile.authPrompt
const judgePromptArg = parseArg('--judge-prompt');
// --clarity-prompt "...": override the clarity judge system prompt via profile.clarityPrompt
const clarityPromptArg = parseArg('--clarity-prompt');
// --engagement-prompt "...": override the engagement judge system prompt via profile.engagementPrompt
const engagementPromptArg = parseArg('--engagement-prompt');
// --auth-mode full|judge-only: override fusion mode via profile.authMode
const authModeArg = parseArg('--auth-mode');
// --speaker-strategy diarization|alternation|single: override via profile.speakerStrategy
const speakerStrategyArg = parseArg('--speaker-strategy');
// --diarization-windowing vad|whisper-segment: override VAD window source via profile.diarizationWindowing
const diarizationWindowingArg = parseArg('--diarization-windowing');
// --emotion true|false: toggle acoustic SER on/off via profile.emotion
const emotionArg = parseArg('--emotion');
// --campplus-alias campplusSpeaker|eres2netv2Speaker: override speaker-embedder model alias
const campplusAliasArg = parseArg('--campplus-alias');
// --asr-alias whisper_turbo|whisperLarge: override ASR model alias via profile.asrAlias
const asrAliasArg = parseArg('--asr-alias');
// --per-turn-vocal: enable per-turn SER (profile.perTurnVocal=true)
const perTurnVocalArg = process.argv.includes('--per-turn-vocal') ? true : process.argv.includes('--no-per-turn-vocal') ? false : null;
// --per-turn-auth / --per-turn-clarity / --per-turn-engagement: per-turn judging knobs
const perTurnAuthArg = process.argv.includes('--per-turn-auth') ? true : process.argv.includes('--no-per-turn-auth') ? false : null;
const perTurnClarityArg = process.argv.includes('--per-turn-clarity') ? true : process.argv.includes('--no-per-turn-clarity') ? false : null;
const perTurnEngagementArg = process.argv.includes('--per-turn-engagement') ? true : process.argv.includes('--no-per-turn-engagement') ? false : null;

const expand = (p: string) => p.startsWith('~') ? join(homedir(), p.slice(1)) : p;
const sleep = (ms: number) => new Promise<void>(r => setTimeout(r, ms));

function extractRows(res: unknown): Record<string, unknown>[] {
  if (res && typeof res === 'object') {
    const r = res as Record<string, unknown>;
    if (Array.isArray(r['columns']) && Array.isArray(r['rows'])) {
      const names = (r['columns'] as unknown[]).map(String);
      return (r['rows'] as unknown[][]).map(row =>
        Object.fromEntries(names.map((c, i) => [c, row[i]]))
      );
    }
    if (Array.isArray(r['rows'])) return r['rows'] as Record<string, unknown>[];
  }
  if (Array.isArray(res)) return res as Record<string, unknown>[];
  return [];
}

interface AgentVault {
  current(): Promise<{ vaultId: string }>;
  events: { publish(v: string, s: string, events: unknown[]): Promise<unknown> };
  cubbies: { query(v: string, s: string, a: string, alias: string, q: { sql: string; params: unknown[] }): Promise<unknown> };
}

function parseArg(name: string): string | null {
  const i = process.argv.indexOf(name);
  return i >= 0 ? (process.argv[i + 1] ?? null) : null;
}

function todayDDMMYY(): string {
  const d = new Date();
  return String(d.getUTCDate()).padStart(2,'0') + String(d.getUTCMonth()+1).padStart(2,'0') + String(d.getUTCFullYear()).slice(-2);
}
function dimLetter(clipName: string): string {
  const m = clipName.match(/^HIA-([ACE])/);
  return m ? m[1]! : 'X';
}
function inputCode(clipName: string): string {
  return clipName.replace(/^HIA-/, '');
}
async function nextSeqNum(vault: AgentVault, vaultId: string, prefix: string): Promise<number> {
  try {
    const res = await vault.cubbies.query(vaultId, SCOPE, AGENT_ID, 'hiring', {
      sql: `SELECT label FROM analysis_runs WHERE label LIKE ? ORDER BY label DESC LIMIT 20`,
      params: [`${prefix}%`],
    });
    const rows = extractRows(res);
    let max = 0;
    for (const row of rows) {
      const label = String(row['label'] ?? '');
      const n = parseInt(label.slice(prefix.length), 10);
      if (!isNaN(n) && n > max) max = n;
    }
    return max + 1;
  } catch { return 1; }
}

interface BatchResult {
  clip: string; convId: string; variant: string;
  scores: Record<string, unknown>;
  timeline_source: string | null;
  turns: { idx: number; text: string; wpm: number | null; filler: number | null; speaking: number | null }[];
  questions: unknown[];
}

async function main() {
  const walletPath = parseArg('--wallet');
  const password = parseArg('--password');
  if (!walletPath || !password) throw new Error('Need --wallet and --password');

  const batchResults: BatchResult[] = [];

  const json = JSON.parse(readFileSync(expand(walletPath), 'utf8')) as KeyringPair$Json;
  await cryptoWaitReady();
  const pair = new Keyring().addFromJson(json);
  pair.decodePkcs8(password);
  const pub = u8aToHex(pair.publicKey);

  const wallet = {
    type: 'ed25519' as const,
    address: pub, publicKey: pub,
    isReady: async () => true,
    sign: async (d: string) => u8aToHex(pair.sign(new TextEncoder().encode(d))).replace(/^0x/, ''),
    signRawBytes: async (b: Uint8Array) => u8aToHex(pair.sign(b)).replace(/^0x/, ''),
  };

  const vault = new Vault({ url: VAULT_URL, wallet: wallet as never }) as unknown as AgentVault;
  const vaultId = (await vault.current()).vaultId;
  console.error(`vault=${vaultId.slice(0, 14)} agent=${AGENT_ID.slice(0, 18)}`);

  const t0 = Date.now();
  const elapsed = () => `+${((Date.now() - t0) / 1000).toFixed(1)}s`;

  // Phase 1 — publish all clips sequentially (wallet signing is nonce-sequential)
  console.error(`[batch] START ${new Date().toISOString()} — ${CLIPS.length} clips`);
  const jobs: Array<{ clip: (typeof CLIPS)[0]; convId: string }> = [];
  for (const clip of CLIPS) {
    const convId = randomUUID();
    const urls = clip.chunks.map(c => `${S3}/${clip.stem}.${c}.mp3`);
    let clipLabel: string;
    if (labelArg) {
      clipLabel = labelArg;
    } else {
      const ddmmyy = todayDDMMYY();
      const dim = dimLetter(clip.name);
      const code = inputCode(clip.name);
      const prefix = `HIA-T${dim}-${ddmmyy}-${code}-`;
      const seq = await nextSeqNum(vault, vaultId, prefix);
      clipLabel = `${prefix}${seq}`;
    }
    console.error(`[${clip.name}] publishing ${urls.length} chunks → conv=${convId} event=${AUDIO_EVENT_TYPE}${expIdArg ? ` exp=${expIdArg}` : ''} label=${clipLabel}`);

    await vault.events.publish(vaultId, SCOPE, [{
      type: AUDIO_EVENT_TYPE, role: 'user', scope: SCOPE,
      context: convId, target: AGENT_ID,
      timestamp: new Date().toISOString(),
      payload: {
        conversation_id: convId,
        candidate_id: clip.name,
        audio_ddc_urls: urls,
        ...(expIdArg ? { experiment_id: expIdArg } : {}),
        profile: {
          ...(judgePromptArg ? { authPrompt: judgePromptArg } : {}),
          ...(clarityPromptArg ? { clarityPrompt: clarityPromptArg } : {}),
          ...(engagementPromptArg ? { engagementPrompt: engagementPromptArg } : {}),
          ...(authModeArg ? { authMode: authModeArg } : {}),
          ...(campplusAliasArg ? { campplusAlias: campplusAliasArg } : {}),
          ...(asrAliasArg ? { asrAlias: asrAliasArg } : {}),
          ...(speakerStrategyArg ? { speakerStrategy: speakerStrategyArg } : {}),
          ...(diarizationWindowingArg ? { diarizationWindowing: diarizationWindowingArg } : {}),
          ...(emotionArg !== null ? { emotion: emotionArg === 'true' } : {}),
          ...(perTurnVocalArg !== null ? { perTurnVocal: perTurnVocalArg } : {}),
          ...(perTurnAuthArg !== null ? { perTurnAuthenticity: perTurnAuthArg } : {}),
          ...(perTurnClarityArg !== null ? { perTurnClarity: perTurnClarityArg } : {}),
          ...(perTurnEngagementArg !== null ? { perTurnEngagement: perTurnEngagementArg } : {}),
          label: clipLabel,
        },
      },
    }]);
    jobs.push({ clip, convId });
  }

  // Phase 2 — poll clips with configurable concurrency (default 1 = sequential)
  console.error(`[batch] all ${jobs.length} clips published ${elapsed()} — polling (concurrency=${CONCURRENCY})…`);
  const clipResultArrays: BatchResult[][] = [];
  for (let i = 0; i < jobs.length; i += CONCURRENCY) {
    const batch = jobs.slice(i, i + CONCURRENCY);
    const batchResults2 = await Promise.all(batch.map(async ({ clip, convId }) => {
    const localResults: BatchResult[] = [];
    const deadline = Date.now() + 600_000;
    let done = false;
    while (!done && Date.now() < deadline) {
      await sleep(8_000);
      try {
        const res = await vault.cubbies.query(vaultId, SCOPE, AGENT_ID, 'hiring', {
          sql: `SELECT variant, status, reading_likelihood, prosodic_score, linguistic_score,
                       clarity_structure, clarity_expression, clarity_overall,
                       vocal_arousal, vocal_valence, vocal_json,
                       engagement_overall, engagement_question_quality,
                       engagement_curiosity, engagement_questions_asked,
                       timeline_json, engagement_json
                FROM analysis_runs
                WHERE conversation_id = ?
                ORDER BY CASE variant WHEN 'control' THEN 0 ELSE 1 END`,
          params: [convId],
        });
        const dbRows = extractRows(res);
        const completed = dbRows.filter(r => r['status'] === 'completed');
        console.error(`  [${clip.name}] rows=${dbRows.length} completed=${completed.length}`);
        if (completed.length >= 2 || (completed.length === 1 && dbRows.length === 1 && Date.now() > deadline - 540_000)) {
          done = true;
          let qaVariant = '';
          for (const row of completed) {
            const rawVariant = String(row['variant'] ?? '');
            const variantLabel = /^E\d{3,}$/.test(rawVariant) ? rawVariant : rawVariant === 'control' ? 'E001' : 'E002';
            if (qaVariant === '' || variantLabel !== 'E001') qaVariant = variantLabel; // prefer the non-control variant
            const tl = row['timeline_json'] ? JSON.parse(String(row['timeline_json'])) : null;
            const eng = row['engagement_json'] ? JSON.parse(String(row['engagement_json'])) : null;
            const turns: { idx: number; text: string; wpm: number | null; filler: number | null; speaking: number | null }[] = [];
            if (tl?.turns) {
              for (const t of tl.turns) {
                if (t.participantId === 'candidate' || t.participantId?.includes('SPEAKER')) {
                  const d = t.delivery;
                  turns.push({
                    idx: t.index,
                    text: String(t.text ?? '').trim(),
                    wpm: d?.wpm ?? null,
                    filler: d?.fillerPct ?? null,
                    speaking: d?.speakingPct ?? null,
                  });
                }
              }
            }
            const out: BatchResult = {
              clip: clip.name,
              convId,
              variant: variantLabel,
              scores: {
                reading_likelihood: row['reading_likelihood'],
                prosodic_score: row['prosodic_score'],
                linguistic_score: row['linguistic_score'],
                clarity_structure: row['clarity_structure'],
                clarity_expression: row['clarity_expression'],
                clarity_overall: row['clarity_overall'],
                vocal_arousal: row['vocal_arousal'],
                engagement_overall: row['engagement_overall'],
                engagement_question_quality: row['engagement_question_quality'],
                engagement_curiosity: row['engagement_curiosity'],
                engagement_questions_asked: row['engagement_questions_asked'],
              },
              timeline_source: tl?.source ?? null,
              turns,
              questions: eng?.questions ?? [],
            };
            process.stdout.write(JSON.stringify(out) + '\n');
            localResults.push(out);
            console.error(`  ✓ ${clip.name} ${variantLabel} ${elapsed()} auth=${row['reading_likelihood']} clarity=${row['clarity_overall']} eng=${row['engagement_overall']} turns=${turns.length}`);
          }
          triggerQA(convId, clip.name, qaVariant, 'execution'); // run finished → E2E execution QA only (scoring deferred)
        }
      } catch (e) {
        console.error(`  poll err [${clip.name}]: ${e instanceof Error ? e.message : String(e)}`);
      }
    }
    if (!done) {
      console.error(`  ⚠ ${clip.name} TIMEOUT ${elapsed()}`);
      triggerQA(convId, clip.name, '', 'execution'); // run never completed → execution RCA
    }
    return localResults;
  }));
    clipResultArrays.push(...batchResults2);
  }

  for (const clipResults of clipResultArrays) {
    batchResults.push(...clipResults);
  }

  // Auto-save CSVs
  if (batchResults.length > 0) {
    const expTag = expIdArg ?? 'batch';
    const ts = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const scriptDir = join(fileURLToPath(import.meta.url), '..');
    const resultsDir = join(scriptDir, '../results');
    mkdirSync(resultsDir, { recursive: true });

    // 1. Flat CSV (quick reference)
    const flatPath = join(resultsDir, `${expTag}-${ts}.csv`);
    const CSV_HEADERS = [
      'exp_id','clip','conv_id',
      'reading_likelihood','prosodic_score','linguistic_score',
      'clarity_structure','clarity_expression','clarity_overall',
      'vocal_arousal',
      'engagement_overall','engagement_question_quality','engagement_curiosity','engagement_questions_asked',
      'timeline_source','turn_count',
    ];
    const flatRows = [CSV_HEADERS.join(',')];
    for (const r of batchResults) {
      const s = r.scores;
      flatRows.push([
        expTag, r.clip, r.convId,
        s['reading_likelihood'] ?? '', s['prosodic_score'] ?? '', s['linguistic_score'] ?? '',
        s['clarity_structure'] ?? '', s['clarity_expression'] ?? '', s['clarity_overall'] ?? '',
        s['vocal_arousal'] ?? '',
        s['engagement_overall'] ?? '', s['engagement_question_quality'] ?? '',
        s['engagement_curiosity'] ?? '', s['engagement_questions_asked'] ?? '',
        r.timeline_source ?? '', r.turns.length,
      ].join(','));
    }
    writeFileSync(flatPath, flatRows.join('\n') + '\n');
    console.error(`\nWrote ${batchResults.length} flat rows → ${flatPath}`);

    // 2. Block CSV — tall format matching gen-results-csv.ts (sheet Import tab)
    const T_COLS = Array.from({ length: 50 }, (_, i) => `T${i + 1}`);
    const blockHeader = ['exp_id', 'input_id', 'metric', 'overall', ...T_COLS].join(',');
    const blockLines: string[] = [blockHeader];

    function scalarBlockRow(expId: string, inputId: string, metric: string, val: unknown, turnCount: number): string | null {
      if (val === null || val === undefined || val === '') return null;
      const v = Number(val);
      if (isNaN(v)) return null;
      const tCells = turnCount > 0 ? Array(turnCount).fill(v) : [];
      return [expId, inputId, metric, v, ...tCells, ...Array(50 - turnCount).fill('')].join(',');
    }
    function turnBlockRow(expId: string, inputId: string, metric: string, vals: (number | null)[]): string | null {
      const valid = vals.filter((v): v is number => v !== null);
      if (!valid.length) return null;
      const overall = Math.round(valid.reduce((a, b) => a + b, 0) / valid.length * 100) / 100;
      return [expId, inputId, metric, overall, ...vals.map(v => v ?? ''), ...Array(50 - vals.length).fill('')].join(',');
    }
    function textBlockRow(expId: string, inputId: string, texts: string[]): string | null {
      if (!texts.length) return null;
      return [expId, inputId, 'turn_text', '', ...texts.map(t => `"${t.replace(/"/g, '""')}"`), ...Array(50 - texts.length).fill('')].join(',');
    }

    for (const r of batchResults) {
      const s = r.scores;
      const n = r.turns.length;
      const add = (row: string | null) => { if (row) blockLines.push(row); };
      blockLines.push([r.variant, r.clip, 'run_date', new Date().toISOString(), ...Array(50).fill('')].join(','));
      add(scalarBlockRow(r.variant, r.clip, 'reading_likelihood', s['reading_likelihood'], n));
      add(scalarBlockRow(r.variant, r.clip, 'prosodic_score', s['prosodic_score'], n));
      add(turnBlockRow(r.variant, r.clip, 'wpm', r.turns.map(t => t.wpm)));
      add(turnBlockRow(r.variant, r.clip, 'filler_pct', r.turns.map(t => t.filler)));
      add(turnBlockRow(r.variant, r.clip, 'speaking_pct', r.turns.map(t => t.speaking)));
      add(scalarBlockRow(r.variant, r.clip, 'linguistic_score', s['linguistic_score'], n));
      add(scalarBlockRow(r.variant, r.clip, 'clarity_overall', s['clarity_overall'], n));
      add(scalarBlockRow(r.variant, r.clip, 'structure_score', s['clarity_structure'], n));
      add(scalarBlockRow(r.variant, r.clip, 'expression_score', s['clarity_expression'], n));
      add(scalarBlockRow(r.variant, r.clip, 'engagement_overall', s['engagement_overall'], n));
      add(scalarBlockRow(r.variant, r.clip, 'questions_asked', s['engagement_questions_asked'], n));
      add(scalarBlockRow(r.variant, r.clip, 'question_quality', s['engagement_question_quality'], n));
      add(scalarBlockRow(r.variant, r.clip, 'curiosity', s['engagement_curiosity'], n));
      add(scalarBlockRow(r.variant, r.clip, 'vocal_arousal', s['vocal_arousal'], n));
      add(textBlockRow(r.variant, r.clip, r.turns.map(t => t.text)));
    }
    const blockPath = join(resultsDir, `${expTag}-${ts}-block.csv`);
    writeFileSync(blockPath, blockLines.join('\n') + '\n');
    console.error(`Wrote ${blockLines.length - 1} block rows  → ${blockPath}`);
    console.error(`[batch] DONE ${elapsed()} — ${batchResults.length} completed / ${CLIPS.length} total`);
  }

  // Wait for any QA investigations to be accepted/finished so none are abandoned on exit.
  if (QA_TRIGGERS.length) {
    console.error(`[batch] awaiting ${QA_TRIGGERS.length} QA investigation(s) at OpenSRE…`);
    await Promise.allSettled(QA_TRIGGERS);
  }
}

main().catch(e => { console.error('FAIL', e instanceof Error ? e.message : e); process.exit(1); });
