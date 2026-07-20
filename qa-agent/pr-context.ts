// pr-context.ts — resolve a set of PR links into structured context for the deploy-triggered QA.
//
// Input: PR URLs (comma/space/newline separated) via --prs or PRS env (as delivered by the Slack
// trigger's client_payload). For each PR, fetch title/body/author/state/merged/changed-files from
// the GitHub API (cross-repo → needs a token with read access to the source repos: --token or
// GH_PR_TOKEN / GITHUB_TOKEN). Emits:
//   --out <file>   : JSON context [{repo, number, url, title, body, author, merged, files[]}]
//   --slack-summary: prints a Slack-mrkdwn summary block to stdout (for the announce step)
//
// Pure retrieval — no Slack POST here (the workflow step posts). Best-effort per PR: a PR that
// can't be fetched is recorded with an `error` so the run still proceeds.
//
//   tsx pr-context.ts --prs "https://github.com/org/repo/pull/12, https://github.com/org/repo2/pull/3" \
//     --token "$QA_PR_READ_TOKEN" --out pr-context.json --slack-summary

import { writeFileSync } from 'node:fs';

const arg = (n: string): string | null => { const i = process.argv.indexOf(n); return i >= 0 ? (process.argv[i + 1] ?? null) : null; };
const has = (n: string): boolean => process.argv.includes(n);

const TOKEN = arg('--token') || process.env.QA_PR_READ_TOKEN || process.env.GH_PR_TOKEN || process.env.GITHUB_TOKEN || '';
const RAW = arg('--prs') || process.env.PRS || '';
const OUT = arg('--out');
const SLACK = has('--slack-summary');
const FILE_LIMIT = Number(arg('--file-limit') || 60);

interface PrRef { owner: string; repo: string; number: number; url: string }
interface PrInfo extends PrRef {
  title: string; body: string; author: string; state: string;
  merged: boolean; merged_at: string | null; files: string[]; error?: string;
}

// Accept full URLs and `owner/repo#123` shorthand; dedupe.
function parseRefs(raw: string): PrRef[] {
  const refs: PrRef[] = [];
  const seen = new Set<string>();
  const push = (owner: string, repo: string, number: number) => {
    const key = `${owner}/${repo}#${number}`;
    if (seen.has(key)) return;
    seen.add(key);
    refs.push({ owner, repo, number, url: `https://github.com/${owner}/${repo}/pull/${number}` });
  };
  for (const tok of raw.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)) {
    let m = tok.match(/github\.com\/([^/]+)\/([^/]+)\/pull\/(\d+)/i);
    if (m) { push(m[1], m[2], Number(m[3])); continue; }
    m = tok.match(/^([^/\s]+)\/([^/#\s]+)#(\d+)$/);
    if (m) { push(m[1], m[2], Number(m[3])); }
  }
  return refs;
}

async function gh(path: string): Promise<any> {
  const res = await fetch(`https://api.github.com${path}`, {
    headers: {
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...(TOKEN ? { Authorization: `Bearer ${TOKEN}` } : {}),
    },
  });
  if (!res.ok) throw new Error(`GET ${path} → ${res.status} ${(await res.text()).slice(0, 120)}`);
  return res.json();
}

async function fetchPr(ref: PrRef): Promise<PrInfo> {
  try {
    const pr = await gh(`/repos/${ref.owner}/${ref.repo}/pulls/${ref.number}`);
    let files: string[] = [];
    try {
      const f = await gh(`/repos/${ref.owner}/${ref.repo}/pulls/${ref.number}/files?per_page=${FILE_LIMIT}`);
      files = (f as Array<{ filename: string }>).map((x) => x.filename);
    } catch { /* files optional */ }
    return {
      ...ref,
      title: pr.title ?? '', body: (pr.body ?? '').trim(), author: pr.user?.login ?? '?',
      state: pr.state ?? '?', merged: !!pr.merged_at, merged_at: pr.merged_at ?? null, files,
    };
  } catch (e) {
    return { ...ref, title: '', body: '', author: '?', state: 'error', merged: false, merged_at: null, files: [], error: e instanceof Error ? e.message : String(e) };
  }
}

function slackSummary(prs: PrInfo[]): string {
  const lines = prs.map((p) => {
    if (p.error) return `• <${p.url}|${p.owner}/${p.repo}#${p.number}> — ⚠️ could not fetch (${p.error})`;
    const merged = p.merged ? '✅ merged' : `(${p.state})`;
    const first = (p.body.split('\n').find((l) => l.trim()) ?? '').slice(0, 140);
    return `• <${p.url}|${p.owner}/${p.repo}#${p.number}> *${p.title}* — @${p.author} ${merged}${first ? `\n   ${first}` : ''}`;
  });
  return lines.join('\n');
}

async function main(): Promise<void> {
  const refs = parseRefs(RAW);
  if (!refs.length) { console.error('[pr-context] no PR links parsed from input'); if (OUT) writeFileSync(OUT, '[]\n'); if (SLACK) process.stdout.write('_(no PRs provided)_'); return; }
  const prs: PrInfo[] = [];
  for (const r of refs) prs.push(await fetchPr(r)); // sequential — small N, avoids rate spikes
  if (OUT) { writeFileSync(OUT, JSON.stringify(prs, null, 2) + '\n'); console.error(`[pr-context] wrote ${prs.length} PR(s) → ${OUT}`); }
  if (SLACK) process.stdout.write(slackSummary(prs));
  else if (!OUT) process.stdout.write(JSON.stringify(prs, null, 2) + '\n');
  const failed = prs.filter((p) => p.error).length;
  if (failed) console.error(`[pr-context] ${failed}/${prs.length} PR(s) could not be fetched (token access?)`);
}

main().catch((e) => { console.error(`[pr-context] FATAL ${e instanceof Error ? e.message : e}`); process.exit(1); });
