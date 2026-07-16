// notion-push.ts — mirror one QA result into the Notion readiness dashboard (matrix + audit trail).
//
// This lives OUTSIDE OpenSRE on purpose: OpenSRE investigates and owns its Telegram/Slack reporting;
// the Notion dashboard is QA-specific plumbing, so the qa-agent owns it. Run as a separate CI step
// AFTER `opensre investigate --output result.json`. Dimension-agnostic (--dimension), so the same
// script later handles UX (Midscene) and Quality (eval) — they just pass a different --dimension.
//
//   NOTION_TOKEN=… NOTION_READINESS_DB=… NOTION_AUDIT_DB=… \
//   tsx notion-push.ts --result result.json --alert "$alert" --agent hiring-coach-lab2 \
//     [--dimension Functional] [--report-url <url>] [--trigger manual|scheduled] [--strict]
//
// Two databases (dual-related, so setting the audit row's Agent auto-links the matrix):
//   Readiness — one row per agent; the dimension cell always mirrors the LATEST verdict.
//   Audit Trail — append-only, one row per run (deduped on conversation_id + dimension); the row
//                 body carries the full RCA report.

import { readFileSync, existsSync } from 'node:fs';
import { basename } from 'node:path';
import { Client } from '@notionhq/client';

const NOTION_VERSION = '2022-06-28';

// Upload a local file to Notion (single-part File Upload API — @notionhq/client 2.3 has no helper,
// so raw fetch) and return the file_upload id, ready to attach as a block. Best-effort: returns null
// on any failure so a bad attachment never fails the push.
async function uploadFileToNotion(token: string, filePath: string): Promise<string | null> {
  try {
    if (!existsSync(filePath)) return null;
    const name = basename(filePath);
    const create = await fetch('https://api.notion.com/v1/file_uploads', {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Notion-Version': NOTION_VERSION, 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode: 'single_part', filename: name }),
    });
    const cj: any = await create.json();
    if (!cj?.id || !cj?.upload_url) throw new Error(`create: ${JSON.stringify(cj).slice(0, 200)}`);
    const form = new FormData();
    form.append('file', new Blob([readFileSync(filePath)]), name);
    const send = await fetch(cj.upload_url, {
      method: 'POST',
      headers: { Authorization: `Bearer ${token}`, 'Notion-Version': NOTION_VERSION },
      body: form,
    });
    const sj: any = await send.json();
    if (sj?.object === 'error') throw new Error(`send: ${JSON.stringify(sj).slice(0, 200)}`);
    return cj.id as string;
  } catch (e) {
    console.log(`[notion-push] file upload failed for ${filePath}: ${e instanceof Error ? e.message : String(e)}`);
    return null;
  }
}

const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };
const has = (n: string): boolean => process.argv.includes(n);

const STRICT = has('--strict');
const TOKEN = process.env.NOTION_TOKEN ?? '';
const READINESS_DB = process.env.NOTION_READINESS_DB ?? '';
const AUDIT_DB = process.env.NOTION_AUDIT_DB ?? '';

const VERDICT_LABEL: Record<string, string> = {
  pass: '🟢 Pass',
  no_go: '🔴 No-go',
  needs_review: '🟡 Needs review',
};
const CONFIDENCE_OPTIONS = new Set(['high', 'medium', 'low']);

// Best-effort exit: a Notion hiccup must never fail the QA job unless --strict.
function bail(msg: string): never {
  console.error(`[notion-push] ${msg}`);
  process.exit(STRICT ? 1 : 0);
}

function loadJson(raw: string | null): any {
  if (!raw) return {};
  // A path, or an inline JSON string.
  try {
    if (raw.trim().startsWith('{')) return JSON.parse(raw);
    return JSON.parse(readFileSync(raw, 'utf8'));
  } catch (e) {
    bail(`could not parse JSON (${raw.slice(0, 40)}…): ${e instanceof Error ? e.message : String(e)}`);
  }
}

// --- Notion property + block builders --------------------------------------------------------
const title = (t: string) => ({ title: [{ text: { content: t.slice(0, 2000) } }] });
const rich = (t: string) => ({ rich_text: t ? [{ text: { content: t.slice(0, 2000) } }] : [] });
const select = (name: string) => ({ select: name ? { name } : null });

function reportBlocks(text: string): any[] {
  const blocks: any[] = [];
  for (const raw of (text || '').split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (!line.trim()) continue;
    const rt = (s: string) => [{ type: 'text', text: { content: s.slice(0, 2000) } }];
    if (line.startsWith('### ')) blocks.push({ object: 'block', type: 'heading_3', heading_3: { rich_text: rt(line.slice(4)) } });
    else if (line.startsWith('## ')) blocks.push({ object: 'block', type: 'heading_2', heading_2: { rich_text: rt(line.slice(3)) } });
    else if (/^\s*[-•]\s/.test(line)) blocks.push({ object: 'block', type: 'bulleted_list_item', bulleted_list_item: { rich_text: rt(line.replace(/^\s*[-•]\s/, '')) } });
    else blocks.push({ object: 'block', type: 'paragraph', paragraph: { rich_text: rt(line) } });
  }
  return blocks.slice(0, 100); // Notion caps children per create call
}

// Pass/total over the last `days` days for one agent+dimension (paginated so a busy window isn't
// truncated). The just-recorded run is included since it's created before this runs.
async function rollingWindow(notion: Client, agentPageId: string, dimension: string, days: number): Promise<{ passes: number; total: number }> {
  const cutoff = new Date(Date.now() - days * 86_400_000).toISOString();
  let cursor: string | undefined;
  let passes = 0, total = 0;
  do {
    const page: any = await notion.databases.query({
      database_id: AUDIT_DB,
      filter: {
        and: [
          { property: 'Agent', relation: { contains: agentPageId } },
          { property: 'Dimension', select: { equals: dimension } },
          { property: 'Timestamp', date: { on_or_after: cutoff } },
        ],
      },
      page_size: 100,
      start_cursor: cursor,
    });
    for (const r of page.results) {
      total += 1;
      if ((r.properties?.Verdict?.select?.name ?? '') === '🟢 Pass') passes += 1;
    }
    cursor = page.has_more ? page.next_cursor : undefined;
  } while (cursor);
  return { passes, total };
}

// Pass/total over the last `n` RUNS (most recent n audit rows) for one agent+dimension — run-based,
// not time-based. Ordered by Timestamp desc so we take the newest n regardless of when they ran. The
// just-recorded run is included (created before this runs). n ≤ 100 so a single query suffices.
async function lastNRuns(notion: Client, agentPageId: string, dimension: string, n: number): Promise<{ passes: number; total: number }> {
  const page: any = await notion.databases.query({
    database_id: AUDIT_DB,
    filter: {
      and: [
        { property: 'Agent', relation: { contains: agentPageId } },
        { property: 'Dimension', select: { equals: dimension } },
      ],
    },
    sorts: [{ property: 'Timestamp', direction: 'descending' }],
    page_size: n,
  });
  let passes = 0, total = 0;
  for (const r of page.results) {
    total += 1;
    if ((r.properties?.Verdict?.select?.name ?? '') === '🟢 Pass') passes += 1;
  }
  return { passes, total };
}

// One combined cell: current status (dot+word from the LATEST verdict) + window uptime %.
// The dot reflects "is it up right now" — green when the latest run passed, even if the window had
// an earlier blip; the % carries the reliability. Empty if no runs in the window.
function statusPrefix(verdict: string): string {
  return verdict === 'pass' ? '🟢 Up' : verdict === 'no_go' ? '🔴 Down' : '🟡 Review';
}
function windowCell(passes: number, total: number, verdict: string): string {
  if (!total) return '';
  const pct = Math.round((100 * passes) / total);
  return `${statusPrefix(verdict)} · ${pct}%`;
}

async function main(): Promise<void> {
  if (!TOKEN || !READINESS_DB || !AUDIT_DB) {
    bail('missing NOTION_TOKEN / NOTION_READINESS_DB / NOTION_AUDIT_DB — skipping.');
  }
  const result = loadJson(arg('--result'));
  const alert = loadJson(arg('--alert'));
  const ann = (alert.commonAnnotations ?? alert.annotations ?? {}) as Record<string, string>;

  const agent = arg('--agent') || ann.agent || '';
  if (!agent) bail('no --agent and no `agent` in the alert — cannot key the matrix row.');

  const dimension = arg('--dimension') || 'Functional';
  const conversationId = arg('--conversation-id') || ann.conversation_id || result.conversation_id || '';
  const manifestVersion = arg('--manifest-version') || ann.manifest_version || '';
  const clip = arg('--clip') || ann.clip || '';
  const reportUrl = arg('--report-url') || '';
  const attach = (arg('--attach') || '').split(',').map((s) => s.trim()).filter(Boolean); // files to attach to the audit row
  const trigger = arg('--trigger') || 'manual';
  const source = arg('--source') || 'CI';
  const ciRun = arg('--ci-run')
    || (process.env.GITHUB_REPOSITORY && process.env.GITHUB_RUN_ID
      ? `${process.env.GITHUB_SERVER_URL ?? 'https://github.com'}/${process.env.GITHUB_REPOSITORY}/actions/runs/${process.env.GITHUB_RUN_ID}`
      : '');

  const verdict: string = String(result.verdict || 'needs_review');
  const verdictLabel = VERDICT_LABEL[verdict] ?? '🟡 Needs review';
  const confidence: string = String(result.confidence || '');
  const confidenceSel = CONFIDENCE_OPTIONS.has(confidence) ? confidence : '';
  const validity: number | null = typeof result.validity_score === 'number' ? result.validity_score : null;
  const rootCause = String(result.root_cause || '').trim();
  const reportText = String(result.report || result.slack_message || '').trim();

  const notion = new Client({ auth: TOKEN });

  // 1) Find (or create) the agent's matrix row → its page id.
  const found = await notion.databases.query({
    database_id: READINESS_DB,
    filter: { property: 'Agent', title: { equals: agent } },
    page_size: 1,
  });
  let agentPageId: string;
  if (found.results.length) {
    agentPageId = found.results[0].id;
  } else {
    const created = await notion.pages.create({
      parent: { database_id: READINESS_DB },
      properties: { Agent: title(agent), Layer: select('Manykind Agents') } as any,
    });
    agentPageId = created.id;
  }

  // 2) Dedup: has this exact run (conversation_id + dimension) already been recorded? Immutable.
  if (conversationId) {
    const dup = await notion.databases.query({
      database_id: AUDIT_DB,
      filter: {
        and: [
          { property: 'Conversation ID', rich_text: { equals: conversationId } },
          { property: 'Dimension', select: { equals: dimension } },
        ],
      },
      page_size: 1,
    });
    if (dup.results.length) {
      console.log(`[notion-push] already recorded (conv=${conversationId} dim=${dimension}) — no-op.`);
      return;
    }
  }

  // 3) Append the immutable Audit Trail row (body = full RCA). Setting Agent auto-links the matrix.
  const subtitle = [dimension, manifestVersion, clip].filter(Boolean).join(' · ');
  const auditProps: Record<string, any> = {
    Check: title(subtitle ? `${agent} · ${subtitle}` : agent),
    Agent: { relation: [{ id: agentPageId }] },
    Dimension: select(dimension),
    Verdict: select(verdictLabel),
    'Manifest Version': rich(manifestVersion),
    'Conversation ID': rich(conversationId),
    'Root cause': rich(rootCause),
    Trigger: select(trigger),
    Source: select(source),
    Timestamp: { date: { start: new Date().toISOString() } },
  };
  if (confidenceSel) auditProps.Confidence = select(confidenceSel);
  if (validity !== null) auditProps['Validity score'] = { number: validity };
  if (ciRun) auditProps['CI run'] = { url: ciRun };
  if (reportUrl) auditProps['Report URL'] = { url: reportUrl };

  const audit = await notion.pages.create({
    parent: { database_id: AUDIT_DB },
    properties: auditProps as any,
    children: reportText ? reportBlocks(reportText) : undefined,
  });

  // Add attachments to the audit row body. IMAGES (screenshots) become inline image blocks — they
  // render right in the row, nothing to click/download. Other files (e.g. an HTML report zip) become
  // downloadable file blocks. Best-effort; a failed attach never fails the push.
  const IMG_EXT = /\.(png|jpe?g|gif|webp)$/i;
  if (attach.length && (audit as any).id) {
    const blocks: any[] = [];
    for (const f of attach) {
      const id = await uploadFileToNotion(TOKEN, f);
      if (!id) continue;
      blocks.push(
        IMG_EXT.test(f)
          ? { object: 'block', type: 'image', image: { type: 'file_upload', file_upload: { id } } }
          : { object: 'block', type: 'file', file: { type: 'file_upload', file_upload: { id } } },
      );
    }
    if (blocks.length) {
      await notion.blocks.children.append({ block_id: (audit as any).id, children: blocks }).catch((e) =>
        console.log(`[notion-push] attach append failed: ${e instanceof Error ? e.message : String(e)}`),
      );
      const imgs = blocks.filter((b) => b.type === 'image').length;
      console.log(`[notion-push] added ${imgs} inline image(s) + ${blocks.length - imgs} file(s) to the audit row`);
    }
  }

  // 5) Upsert the matrix. The row is shared per-agent across dimensions, so each dimension writes
  //    ONLY its own cell — never another dimension's. The shared columns (Manifest Version, Latest
  //    RCA, Confidence, Last checked, Functional windowed cells) belong to Functional; a UX/Quality
  //    push must not clobber them.
  const matrixProps: Record<string, any> = { [dimension]: select(verdictLabel) };

  // Run-based windows (Fred's reframe: recent RUNS, not days) for THIS dimension. Each is its own
  // cell (`<Dim> (last N)`), so every dimension writes them safely — no shared-column clobber.
  //   last 1  → just the latest verdict (a 1-run "uptime" of 0/100% is meaningless).
  //   last 3/7/10 → dot/word = latest verdict; % = uptime over the last N runs. No x/y (a fraction
  //   reads like a score and confused people); the raw counts live only in the drill-down toggle.
  matrixProps[`${dimension} (last 1)`] = rich(verdictLabel);
  for (const n of [3, 7, 10]) {
    const { passes, total } = await lastNRuns(notion, agentPageId, dimension, n);
    const cell = windowCell(passes, total, verdict);
    if (cell) matrixProps[`${dimension} (last ${n})`] = rich(cell);
  }

  if (dimension === 'Functional') {
    // Day-based windows — kept untouched for the existing 1d/3d/7d views (useful later).
    for (const days of [1, 3, 7]) {
      const { passes, total } = await rollingWindow(notion, agentPageId, dimension, days);
      const cell = windowCell(passes, total, verdict); // dot/word = latest verdict; % = window uptime
      if (cell) matrixProps[`Functional (${days}d)`] = rich(cell);
    }
    matrixProps['Manifest Version'] = rich(manifestVersion);
    matrixProps['Latest RCA'] = rich(rootCause);
    matrixProps['Last checked'] = { date: { start: new Date().toISOString() } };
    if (confidenceSel) matrixProps['Confidence'] = select(confidenceSel);
  }
  await notion.pages.update({ page_id: agentPageId, properties: matrixProps as any });

  console.log(`[notion-push] ${dimension} ${verdict} for ${agent} → ${(audit as any).url ?? 'ok'}`);
}

main().catch((e: unknown) => bail(`FAILED — ${e instanceof Error ? e.message : String(e)}`));
