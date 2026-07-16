// quality-push.ts — push one quality-bar run (Bren's row.json) into the Notion dashboard.
//
// Quality is PURELY the eval script's numbers — no OpenSRE/QA-agent analysis. Reads the row.json
// emitted by agent-catalog's quality-bar-run.ts (16 clips → per-clip pass/fail → aggregate
// pass_ratio), updates the agent's Quality matrix cells (windowed, like Functional) and appends a
// Quality audit-trail row with the full per-clip breakdown in the body.
//
//   NOTION_TOKEN=… NOTION_READINESS_DB=… NOTION_AUDIT_DB=… \
//   tsx quality-push.ts --row row.json --agent hiring-coach-lab2 [--report-url URL]
//                       [--trigger manual|scheduled] [--green 0.9] [--yellow 0.75]
//
// Delta: matrix "Quality (1d)" = latest score + change vs the previous run; "(3d)"/"(7d)" = rolling
// average pass_ratio over the window (computed from the Quality audit-row history).

import { readFileSync } from 'node:fs';
import { Client } from '@notionhq/client';

const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };

const TOKEN = process.env.NOTION_TOKEN ?? '';
const READINESS_DB = process.env.NOTION_READINESS_DB ?? '';
const AUDIT_DB = process.env.NOTION_AUDIT_DB ?? '';
const GREEN = Number(arg('--green') ?? 0.9);   // pass_ratio ≥ this → 🟢
const YELLOW = Number(arg('--yellow') ?? 0.75); // ≥ this → 🟡, else 🔴
const STRICT = process.argv.includes('--strict');

const bail = (m: string): never => { console.error(`[quality-push] ${m}`); process.exit(STRICT ? 1 : 0); };
const emoji = (r: number) => (r >= GREEN ? '🟢' : r >= YELLOW ? '🟡' : '🔴');
const verdictLabel = (r: number) => (r >= GREEN ? '🟢 Pass' : r >= YELLOW ? '🟡 Needs review' : '🔴 No-go');
const pct = (r: number) => `${Math.round(r * 100)}%`;

const title = (t: string) => ({ title: [{ text: { content: t.slice(0, 2000) } }] });
const rich = (t: string) => ({ rich_text: t ? [{ text: { content: t.slice(0, 2000) } }] : [] });
const select = (n: string) => ({ select: n ? { name: n } : null });

function clipLine(c: any): string {
  const mark = c.pass ? '✓' : '✗';
  const dims = (c.checks ?? [])
    .map((k: any) => `${k.dim[0]}${k.actual}${k.pass ? '' : '✗'}(${k.expected})`)
    .join(' · ');
  return `${mark} ${c.clip} [${c.final_status}] ${dims}`;
}

async function scoreHistory(notion: Client, agentPageId: string, days: number): Promise<number[]> {
  const cutoff = new Date(Date.now() - days * 86_400_000).toISOString();
  const scores: number[] = [];
  let cursor: string | undefined;
  do {
    const page: any = await notion.databases.query({
      database_id: AUDIT_DB,
      filter: {
        and: [
          { property: 'Agent', relation: { contains: agentPageId } },
          { property: 'Dimension', select: { equals: 'Quality' } },
          { property: 'Timestamp', date: { on_or_after: cutoff } },
        ],
      },
      page_size: 100,
      start_cursor: cursor,
    });
    for (const r of page.results) {
      const s = r.properties?.Score?.number;
      if (typeof s === 'number') scores.push(s);
    }
    cursor = page.has_more ? page.next_cursor : undefined;
  } while (cursor);
  return scores;
}

// The last `n` Quality pass_ratios (most recent n runs), newest first — run-based, not time-based.
// Includes the run we just recorded (created before the matrix update). n ≤ 100 → one query.
async function lastNScores(notion: Client, agentPageId: string, n: number): Promise<number[]> {
  const page: any = await notion.databases.query({
    database_id: AUDIT_DB,
    filter: {
      and: [
        { property: 'Agent', relation: { contains: agentPageId } },
        { property: 'Dimension', select: { equals: 'Quality' } },
      ],
    },
    sorts: [{ property: 'Timestamp', direction: 'descending' }],
    page_size: n,
  });
  return page.results.map((r: any) => r.properties?.Score?.number).filter((s: any) => typeof s === 'number');
}

async function main(): Promise<void> {
  if (!TOKEN || !READINESS_DB || !AUDIT_DB) bail('missing NOTION_TOKEN / NOTION_READINESS_DB / NOTION_AUDIT_DB');
  const rowPath = arg('--row');
  if (!rowPath) bail('missing --row <path to quality-bar row.json>');
  let row: any;
  try { row = JSON.parse(readFileSync(rowPath!, 'utf8')); } catch (e) { bail(`cannot read row: ${e instanceof Error ? e.message : e}`); }

  const agent = arg('--agent') || row.agent_alias || 'hiring-coach-lab2';
  const agg = row.aggregate ?? {};
  const ratio: number = typeof agg.pass_ratio === 'number' ? agg.pass_ratio : 0;
  const passed = agg.clips_passed ?? 0;
  const total = agg.clips_total ?? (row.per_clip?.length ?? 0);
  const runId = row.run_id || `quality-${row.timestamp_utc || Date.now()}`;
  const reportUrl = arg('--report-url') || '';
  const trigger = arg('--trigger') || 'manual';
  const ts = row.finished_utc || row.timestamp_utc || new Date().toISOString();

  const notion = new Client({ auth: TOKEN });

  // Agent row (create if missing).
  const found = await notion.databases.query({ database_id: READINESS_DB, filter: { property: 'Agent', title: { equals: agent } }, page_size: 1 });
  const agentPageId = found.results.length
    ? found.results[0].id
    : (await notion.pages.create({ parent: { database_id: READINESS_DB }, properties: { Agent: title(agent), Layer: select('Manykind Agents') } as any })).id;

  // Dedup on run_id + Quality.
  const dup = await notion.databases.query({
    database_id: AUDIT_DB,
    filter: { and: [{ property: 'Conversation ID', rich_text: { equals: runId } }, { property: 'Dimension', select: { equals: 'Quality' } }] },
    page_size: 1,
  });
  if (dup.results.length) { console.log(`[quality-push] run ${runId} already recorded — no-op.`); return; }

  // Delta vs the previous quality run (most recent Quality score before this push).
  const prior = await scoreHistory(notion, agentPageId, 3650); // all history
  const prevRatio = prior.length ? prior[prior.length - 1] : null; // history is oldest→newest-ish; use last as previous
  const deltaClips = prevRatio == null ? null : passed - Math.round(prevRatio * total);
  const deltaStr = deltaClips == null || deltaClips === 0 ? '' : ` ${deltaClips > 0 ? '▲' : '▼'}${Math.abs(deltaClips)}`;

  // Audit-trail row (append-only) with the per-clip breakdown in the body.
  const bodyMd =
    `## ${emoji(ratio)} Quality ${passed}/${total} · ${pct(ratio)}${deltaStr}\n\n` +
    `### Aggregate\n- passed ${passed}/${total} · completed ${agg.clips_completed ?? '?'} · failed ${agg.clips_failed ?? '?'} · timeout ${agg.clips_timeout ?? '?'}\n\n` +
    `### Per clip\n${(row.per_clip ?? []).map((c: any) => `- ${clipLine(c)}`).join('\n')}\n\n` +
    `_Quality bar (eval script; no QA-agent). run ${runId}._`;

  const auditProps: Record<string, any> = {
    Check: title(`${agent} · Quality · ${passed}/${total} · ${new Date(ts).toISOString().slice(0, 16).replace('T', ' ')}`),
    Agent: { relation: [{ id: agentPageId }] },
    Dimension: select('Quality'),
    Verdict: select(verdictLabel(ratio)),
    Score: { number: ratio },
    'Conversation ID': rich(runId),
    'Root cause': rich(`${passed}/${total} clips passed (${pct(ratio)})`),
    Trigger: select(trigger),
    Source: select('CI'),
    Timestamp: { date: { start: ts } },
  };
  if (reportUrl) auditProps['CI run'] = { url: reportUrl };
  const body = bodyMd.split('\n').filter((l) => l.trim()).slice(0, 100).map((l) => {
    if (l.startsWith('### ')) return { object: 'block', type: 'heading_3', heading_3: { rich_text: [{ text: { content: l.slice(4).slice(0, 2000) } }] } };
    if (l.startsWith('## ')) return { object: 'block', type: 'heading_2', heading_2: { rich_text: [{ text: { content: l.slice(3).slice(0, 2000) } }] } };
    if (l.startsWith('- ')) return { object: 'block', type: 'bulleted_list_item', bulleted_list_item: { rich_text: [{ text: { content: l.slice(2).slice(0, 2000) } }] } };
    return { object: 'block', type: 'paragraph', paragraph: { rich_text: [{ text: { content: l.slice(0, 2000) } }] } };
  });
  await notion.pages.create({ parent: { database_id: AUDIT_DB }, properties: auditProps as any, children: body as any });

  const avg = (arr: number[]) => (arr.length ? arr.reduce((a, b) => a + b, 0) / arr.length : ratio);

  // Day-based windows (kept untouched for the existing 1d/3d/7d views). scoreHistory includes the row
  // we just created (don't re-append the current run — that would double-count it).
  const avg3 = avg(await scoreHistory(notion, agentPageId, 3));
  const avg7 = avg(await scoreHistory(notion, agentPageId, 7));

  // Run-based windows (Fred's reframe): last 1 = latest score + pp-delta vs previous; last 3/7/10 =
  // rolling avg pass_ratio over the most recent N runs. Percentage only — no x/y (that's the "score"
  // confusion). Raw per-clip counts live in the audit-row body / drill-down.
  const deltaPp = prevRatio == null ? null : Math.round((ratio - prevRatio) * 100);
  const deltaPpStr = deltaPp == null || deltaPp === 0 ? '' : ` ${deltaPp > 0 ? '▲' : '▼'}${Math.abs(deltaPp)}`;
  const rAvg3 = avg(await lastNScores(notion, agentPageId, 3));
  const rAvg7 = avg(await lastNScores(notion, agentPageId, 7));
  const rAvg10 = avg(await lastNScores(notion, agentPageId, 10));

  await notion.pages.update({
    page_id: agentPageId,
    properties: {
      // Day windows (unchanged shape).
      'Quality (1d)': rich(`${emoji(ratio)} ${passed}/${total} · ${pct(ratio)}${deltaStr}`),
      'Quality (3d)': rich(`${emoji(avg3)} avg ${pct(avg3)}`),
      'Quality (7d)': rich(`${emoji(avg7)} avg ${pct(avg7)}`),
      // Run windows.
      'Quality (last 1)': rich(`${emoji(ratio)} ${pct(ratio)}${deltaPpStr}`),
      'Quality (last 3)': rich(`${emoji(rAvg3)} avg ${pct(rAvg3)}`),
      'Quality (last 7)': rich(`${emoji(rAvg7)} avg ${pct(rAvg7)}`),
      'Quality (last 10)': rich(`${emoji(rAvg10)} avg ${pct(rAvg10)}`),
    } as any,
  }).catch((e) => console.log(`[quality-push] matrix update failed: ${e instanceof Error ? e.message : e}`));

  console.log(`[quality-push] Quality ${pct(ratio)}${deltaPpStr} for ${agent} → last3 ${pct(rAvg3)}, last7 ${pct(rAvg7)}, last10 ${pct(rAvg10)}`);
}

main().catch((e) => bail(`FAILED — ${e instanceof Error ? e.message : String(e)}`));
