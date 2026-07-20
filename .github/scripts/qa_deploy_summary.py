#!/usr/bin/env python3
"""Assemble the WHOLE deploy-QA verdict (Functional RCA + Quality + UX) into a Slack-mrkdwn message.

Reads the per-dimension result files produced by the QA jobs (all optional — a missing/failed job is
shown as such rather than crashing):
  --functional qa-result.json  (opensre investigate --output: verdict, confidence, root_cause, report)
  --quality    quality-row.json (quality bar: aggregate.pass_ratio, clips_passed/total, per_clip)
  --ux         ux-result.json    (build-ux-result: verdict, summary/report)
plus --prs, --component, --run-url. Prints the message to stdout; the workflow posts it to the
#code-review thread. Overall = the worst real verdict (no_go > needs_review > pass).
"""

from __future__ import annotations

import argparse
import json
from typing import Any

RANK = {"no_go": 0, "needs_review": 1, "pass": 2, "unknown": 3}
SEV_EMOJI = {"pass": "🟢 pass", "no_go": "🔴 no-go", "needs_review": "🟡 needs review", "unknown": "⚪ n/a"}


def load(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def functional(row: dict[str, Any] | None) -> tuple[str, str, str]:
    """→ (sev, one-line, full RCA report)."""
    if not row:
        return "unknown", "Functional: ⚪ not available", ""
    sev = str(row.get("verdict", "unknown"))
    conf = str(row.get("confidence", ""))
    report = str(row.get("report", "") or row.get("slack_message", "")).strip()
    line = f"Functional: {SEV_EMOJI.get(sev, sev)}" + (f" ({conf})" if conf else "")
    return sev, line, report


def quality(row: dict[str, Any] | None, green: float = 0.9, yellow: float = 0.75) -> tuple[str, str]:
    if not row:
        return "unknown", "Quality: ⚪ not available"
    agg = row.get("aggregate", {}) or {}
    r = agg.get("pass_ratio")
    passed, total = agg.get("clips_passed"), agg.get("clips_total")
    if not isinstance(r, (int, float)):
        return "unknown", "Quality: ⚪ n/a"
    sev = "pass" if r >= green else ("needs_review" if r >= yellow else "no_go")
    emoji = {"pass": "🟢", "needs_review": "🟡", "no_go": "🔴"}[sev]
    fails = [c.get("clip") for c in (row.get("per_clip") or []) if not c.get("pass")]
    tail = f"  (failed: {', '.join(str(x) for x in fails[:8])})" if fails else ""
    return sev, f"Quality: {emoji} {passed}/{total} · {round(r * 100)}%{tail}"


def ux(row: dict[str, Any] | None) -> tuple[str, str]:
    if not row:
        return "unknown", "UX: ⚪ not available"
    raw = str(row.get("verdict", "unknown"))
    sev = "pass" if raw == "pass" else ("no_go" if raw in ("no_go", "fail") else raw)
    return (sev if sev in RANK else "unknown"), f"UX: {SEV_EMOJI.get(sev, raw)}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--functional")
    ap.add_argument("--quality")
    ap.add_argument("--ux")
    ap.add_argument("--prs", default="")
    ap.add_argument("--component", default="")
    ap.add_argument("--run-url", default="")
    a = ap.parse_args()

    f_sev, f_line, f_report = functional(load(a.functional))
    q_sev, q_line = quality(load(a.quality))
    u_sev, u_line = ux(load(a.ux))

    worst = min((f_sev, q_sev, u_sev), key=lambda s: RANK.get(s, 3))
    overall = {"pass": "🟢 PASS", "no_go": "🔴 NO-GO", "needs_review": "🟡 NEEDS REVIEW"}.get(worst, "⚪ UNKNOWN")
    head = f"🧪 *Deploy QA {overall}*" + (f" — {a.component}" if a.component else "")

    prs = [p.strip() for p in a.prs.replace(",", " ").split() if p.strip()]
    pr_block = "\n".join(f"• {p}" for p in prs) or "_(none)_"

    parts = [head, f_line + "   ·   " + q_line + "   ·   " + u_line]
    if a.run_url:
        parts.append(f"<{a.run_url}|QA run>")
    parts.append(f"\n*Deployed PRs:*\n{pr_block}")
    if f_report:
        parts.append("\n*Functional RCA:*\n" + f_report)
    print("\n".join(parts))


if __name__ == "__main__":
    main()
