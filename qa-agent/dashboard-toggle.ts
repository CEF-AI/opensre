// dashboard-toggle.ts — rebuild the "Last 10 runs" toggle + "Best today" high-water line on each
// agent's readiness page. Runs as a dedicated CI POST-JOB (needs: qa/ux/quality) so it fires once
// after all three parallel dimension pushes have landed — never racing them on the same block.
//
// It owns exactly one block per page (a toggle whose title contains TOGGLE_MARKER): on each run it
// deletes the previous one and appends a fresh one built from the latest audit-trail rows. Read-only
// against everything else. Best-effort: a Notion hiccup logs and exits 0 so it never fails the run.
//
//   NOTION_TOKEN=… NOTION_READINESS_DB=… NOTION_AUDIT_DB=… \
//   tsx dashboard-toggle.ts --agent hiring-coach-lab2 --agent hiring-coach-qa

import { Client } from '@notionhq/client';

const TOKEN = process.env.NOTION_TOKEN ?? '';
const READINESS_DB = process.env.NOTION_READINESS_DB ?? '';
const AUDIT_DB = process.env.NOTION_AUDIT_DB ?? '';
const TOGGLE_MARKER = 'Last 10 runs'; // how we recognise (and replace) the toggle we own

const agents = process.argv.reduce<string[]>(
  (acc, a, i) => (a === '--agent' && process.argv[i + 1] ? [...acc, process.argv[i + 1]] : acc),
  [],
);
if (!agents.length) agents.push('hiring-coach-lab2', 'hiring-coach-qa');

// "Jul 16 06:53" — short, UTC, no seconds. The rows are already newest-first.
const fmt = (iso: string): string => (iso ? iso.slice(0, 16).replace('T', ' ') : '—');

interface AuditRow {
  ts: string;
  dim: string;
  verdict: string;
  score: number | null;
  url: string;
}

async function lastRuns(notion: Client, agentPageId: string, n: number): Promise<AuditRow[]> {
  const page: any = await notion.databases.query({
    database_id: AUDIT_DB,
    filter: { property: 'Agent', relation: { contains: agentPageId } },
    sorts: [{ property: 'Timestamp', direction: 'descending' }],
    page_size: n,
  });
  return page.results.map((r: any) => ({
    ts: r.properties?.Timestamp?.date?.start ?? '',
    dim: r.properties?.Dimension?.select?.name ?? '?',
    verdict: r.properties?.Verdict?.select?.name ?? '',
    score: typeof r.properties?.Score?.number === 'number' ? r.properties.Score.number : null,
    url: r.url ?? '',
  }));
}

// High-water mark for TODAY, per dimension: Quality → best pass_ratio; Functional/UX → best verdict.
// Makes daily improvement visible (Fred's "power of 37").
function bestToday(rows: AuditRow[], today: string): string {
  const todays = rows.filter((r) => r.ts.slice(0, 10) === today);
  if (!todays.length) return 'Best today — no runs yet';
  const rank: Record<string, number> = { '🟢 Pass': 2, '🟡 Needs review': 1, '🔴 No-go': 0 };
  const parts: string[] = [];
  for (const dim of ['Functional', 'Quality', 'UX']) {
    const d = todays.filter((r) => r.dim === dim);
    if (!d.length) continue;
    if (dim === 'Quality') {
      const best = Math.max(...d.map((r) => r.score ?? 0));
      parts.push(`Quality ${Math.round(best * 100)}%`);
    } else {
      const best = d.reduce((a, b) => ((rank[b.verdict] ?? -1) > (rank[a.verdict] ?? -1) ? b : a));
      parts.push(`${dim} ${best.verdict}`);
    }
  }
  return `Best today — ${parts.join(' · ')}`;
}

// One run → a linked bullet: "Jul 16 06:53 · Quality · 🟢 Pass · 81%". Quality shows its %; the
// pass/fail dimensions don't (their verdict already carries the outcome — no score to confuse).
function runLine(r: AuditRow): any {
  const score = r.dim === 'Quality' && r.score != null ? ` · ${Math.round(r.score * 100)}%` : '';
  const label = `${fmt(r.ts)} · ${r.dim} · ${r.verdict}${score}`;
  const text = r.url
    ? { type: 'text', text: { content: label, link: { url: r.url } } }
    : { type: 'text', text: { content: label } };
  return { object: 'block', type: 'bulleted_list_item', bulleted_list_item: { rich_text: [text] } };
}

async function rebuild(notion: Client, agent: string, nowIso: string): Promise<void> {
  const found = await notion.databases.query({
    database_id: READINESS_DB,
    filter: { property: 'Agent', title: { equals: agent } },
    page_size: 1,
  });
  if (!found.results.length) {
    console.log(`[toggle] no readiness row for ${agent} — skip`);
    return;
  }
  const pageId = found.results[0].id;

  const rows = await lastRuns(notion, pageId, 10);
  if (!rows.length) {
    console.log(`[toggle] no audit rows for ${agent} — skip`);
    return;
  }

  // Remove the toggle(s) we own so we replace rather than stack duplicates.
  const children: any = await notion.blocks.children.list({ block_id: pageId, page_size: 100 });
  for (const b of children.results) {
    const rt = b?.toggle?.rich_text?.[0]?.plain_text ?? '';
    if (b.type === 'toggle' && rt.includes(TOGGLE_MARKER)) {
      await notion.blocks.delete({ block_id: b.id }).catch(() => {});
    }
  }

  const toggle = {
    object: 'block',
    type: 'toggle',
    toggle: {
      rich_text: [{ type: 'text', text: { content: `🕘 ${TOGGLE_MARKER} — updated ${fmt(nowIso)} UTC` } }],
      children: [
        {
          object: 'block',
          type: 'callout',
          callout: { icon: { emoji: '🏔️' }, rich_text: [{ type: 'text', text: { content: bestToday(rows, nowIso.slice(0, 10)) } }] },
        },
        ...rows.map(runLine),
      ],
    },
  };
  await notion.blocks.children.append({ block_id: pageId, children: [toggle] as any });
  console.log(`[toggle] ${agent}: rebuilt with ${rows.length} runs`);
}

async function main(): Promise<void> {
  if (!TOKEN || !READINESS_DB || !AUDIT_DB) {
    console.error('[toggle] missing NOTION_TOKEN / NOTION_READINESS_DB / NOTION_AUDIT_DB — skip');
    return;
  }
  const notion = new Client({ auth: TOKEN });
  const nowIso = new Date().toISOString();
  for (const a of agents) {
    try {
      await rebuild(notion, a, nowIso);
    } catch (e) {
      console.log(`[toggle] ${a} failed: ${e instanceof Error ? e.message : e}`);
    }
  }
}

main().catch((e) => {
  console.error('[toggle] FATAL', e instanceof Error ? e.message : e);
  process.exit(0);
});
