// Turn a Playwright JSON report into a ux-result.json that notion-push.ts consumes (same shape as a
// Functional result: verdict + report markdown). Verdict = pass iff every non-skipped test passed.
// Usage: node scripts/build-ux-result.mjs <playwright-json> [out]
import { readFileSync, writeFileSync } from 'node:fs';

const inPath = process.argv[2] ?? 'ux-pw.json';
const outPath = process.argv[3] ?? 'ux-result.json';

let pw;
try {
  pw = JSON.parse(readFileSync(inPath, 'utf8'));
} catch {
  // No/invalid Playwright report → the run crashed before producing results. Record a no_go so the
  // dashboard reflects the failure instead of the step erroring out silently.
  const report = `## 🔴 UX NO-GO — Hiring Coach widget\n\nThe UX suite did not produce a report (\`${inPath}\` missing) — the run likely crashed during setup/login. See the CI run.`;
  writeFileSync(outPath, JSON.stringify(
    { verdict: 'no_go', confidence: 'low', validity_score: 0, root_cause: 'UX suite produced no report (crash before results).', report },
    null, 2,
  ));
  console.log(`ux verdict=no_go (no report at ${inPath}) → ${outPath}`);
  process.exit(0);
}

// Playwright nests suites; flatten to specs.
const specs = [];
const walk = (s) => {
  (s.specs ?? []).forEach((sp) => specs.push(sp));
  (s.suites ?? []).forEach(walk);
};
(pw.suites ?? []).forEach(walk);

const rows = specs.map((sp) => {
  const res = sp.tests?.[0]?.results?.[0] ?? {};
  const status = res.status ?? (sp.ok ? 'passed' : 'unknown');
  const skipped = status === 'skipped' || sp.tests?.[0]?.status === 'skipped';
  return {
    title: sp.title,
    skipped,
    ok: sp.ok === true && !skipped,
    secs: Math.round((res.duration ?? 0) / 1000),
  };
});

const ran = rows.filter((r) => !r.skipped);
const failed = ran.filter((r) => !r.ok);
const verdict = ran.length === 0 ? 'needs_review' : failed.length === 0 ? 'pass' : 'no_go';
const emoji = verdict === 'pass' ? '🟢' : verdict === 'no_go' ? '🔴' : '🟡';

const lines = rows.map((r) =>
  r.skipped ? `- ⏭ ${r.title} (deferred)` : `- ${r.ok ? '✓' : '✗'} ${r.title} (${r.secs}s)`,
);
const report =
  `## ${emoji} UX ${verdict.toUpperCase()} — Hiring Coach widget\n\n` +
  `### Checks\n${lines.join('\n')}\n\n` +
  `_Vision-driven (Midscene). Scope: up to "analyse audio". Full HTML report + trace: see the CI run._`;

const result = {
  verdict,
  confidence: verdict === 'pass' ? 'high' : 'low',
  validity_score: verdict === 'pass' ? 1 : 0,
  root_cause:
    verdict === 'pass'
      ? 'Widget loads and accepts a clip; analysis starts.'
      : `UX checks failed: ${failed.map((f) => f.title).join('; ') || '(no tests ran)'}`,
  report,
};
writeFileSync(outPath, JSON.stringify(result, null, 2));
console.log(`ux verdict=${verdict} (ran ${ran.length}, failed ${failed.length}) → ${outPath}`);
