"""Scored CEF QA synthetic suite.

Runs the REAL investigation (``run_investigation``) against a scenario's fixture evidence and
scores the LLM verdict against the answer key — the OpenSRE eval pattern (see eks/run_suite.py),
scoped to CEF execution QA. Uses the configured LLM, so run it manually like ``make benchmark``:

    LLM_PROVIDER=ddcdragon uv run python -m tests.synthetic.cef.run_suite
    LLM_PROVIDER=ddcdragon uv run python -m tests.synthetic.cef.run_suite tests/synthetic/cef/010-healthy-inference-redherring

Exit code is non-zero if any scenario fails, so it can gate a benchmark run.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from app.pipeline.runners import run_investigation
from app.services.cef.report import format_cef_qa_telegram
from tests.synthetic.mock_cef_backend import CefScenario, FixtureCEFBackend, load_scenario

SUITE_DIR = Path(__file__).resolve().parent

# Verdict marker -> the header substring the CEF report emits for it (app/services/cef/report.py).
_VERDICT_MARKERS = {"pass": "· PASS", "no_go": "· NO-GO", "needs_review": "· NEEDS REVIEW"}


def _verdict(state: dict[str, Any]) -> str:
    """The verdict the CEF report would render for this state (pass / no_go / needs_review)."""
    header = format_cef_qa_telegram(state).splitlines()[0]
    for name, marker in _VERDICT_MARKERS.items():
        if marker in header:
            return name
    return "(none)"


def _output_text(state: dict[str, Any]) -> str:
    parts = [str(state.get("root_cause") or ""), str(state.get("root_cause_category") or "")]
    for claim in (state.get("validated_claims") or []) + (state.get("non_validated_claims") or []):
        parts.append(claim.get("claim", "") if isinstance(claim, dict) else str(claim))
    return " ".join(parts).lower()


def _tool_names(state: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for hyp in state.get("executed_hypotheses") or []:
        for action in hyp.get("actions") or []:
            names.add(str(action))
    for entry in state.get("evidence_entries") or []:
        if isinstance(entry, dict) and entry.get("tool_name"):
            names.add(str(entry["tool_name"]))
    return names


def score(scenario: CefScenario, state: dict[str, Any]) -> dict[str, Any]:
    ans = scenario.answer
    text = _output_text(state)
    category = str(state.get("root_cause_category") or "").lower()
    forbidden_cats = {c.lower() for c in ans.get("forbidden_categories") or []}
    forbidden_kws = [k.lower() for k in ans.get("forbidden_keywords") or []]
    required_kws = list(ans.get("required_keywords") or [])
    ruling_out = list(ans.get("ruling_out_keywords") or [])
    required_queries = set(ans.get("required_queries") or [])
    called = _tool_names(state)

    checks = {
        "category_not_forbidden": category not in forbidden_cats,
        "required_keywords_present": all(k.lower() in text for k in required_kws),
        "ruled_out_red_herring": all(k.lower() in text for k in ruling_out) if ruling_out else True,
        "required_queries_called": required_queries.issubset(called),
        "no_forbidden_keywords": not any(k in text for k in forbidden_kws),
    }

    # Confidence-gate checks (opt-in per scenario): the verdict the report would render, and a cap
    # on validity_score. Together they regression-cover the NEEDS REVIEW gate end to end.
    expected_verdict = str(ans.get("expected_verdict") or "").lower()
    verdict = _verdict(state)
    if expected_verdict:
        checks["verdict_matches"] = verdict == expected_verdict
    max_validity = ans.get("max_validity_score")
    if isinstance(max_validity, int | float):
        raw = state.get("validity_score")
        score = float(raw) if isinstance(raw, int | float) else 1.0
        checks["validity_within_cap"] = score <= float(max_validity)

    return {
        "scenario": scenario.scenario_id,
        "passed": all(checks.values()),
        "checks": checks,
        "category": category or "(none)",
        "verdict": verdict,
        "validity_score": state.get("validity_score"),
        "root_cause": str(state.get("root_cause") or "")[:220],
    }


def run_one(scenario_dir: Path) -> dict[str, Any]:
    scenario = load_scenario(scenario_dir)
    resolved = {"cef": {"_backend": FixtureCEFBackend(scenario)}}
    state = dict(run_investigation(scenario.alert, resolved_integrations=resolved))
    return score(scenario, state)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scored CEF QA synthetic suite.")
    parser.add_argument("scenarios", nargs="*", help="scenario dirs (default: all in this suite)")
    args = parser.parse_args(argv)
    dirs = [Path(s) for s in args.scenarios] or sorted(
        d for d in SUITE_DIR.iterdir() if d.is_dir() and (d / "answer.yml").exists()
    )
    results = [run_one(d) for d in dirs]
    passed = sum(1 for r in results if r["passed"])
    for r in results:
        vs = r["validity_score"]
        vs_str = f"{float(vs):.0%}" if isinstance(vs, int | float) else "n/a"
        print(
            f"[{'PASS' if r['passed'] else 'FAIL'}] {r['scenario']}  "
            f"category={r['category']}  verdict={r['verdict']}  validity={vs_str}"
        )
        for name, ok in r["checks"].items():
            print(f"        {'ok' if ok else 'XX'}  {name}")
        if not r["passed"]:
            print(f"        root_cause: {r['root_cause']}")
    print(f"\n{passed}/{len(results)} scenarios passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
