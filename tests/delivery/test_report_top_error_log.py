"""Regression: the report must surface a real error line, not an arbitrary first log."""

from __future__ import annotations

from app.agent.stages.publish_findings.formatters.report import _get_top_error_log


def test_skips_unrelated_first_log_and_picks_the_error() -> None:
    evidence = {
        "grafana_logs": [
            {"message": "TRACE runtime::election-provider: current phase Signed, next election"},
            {"message": "INFO offchain-worker: Inspection OCW triggered at block 7"},
            {"message": "ERROR runtime::ddc-verification: ❌ Fetching processed eras error"},
        ]
    }
    assert _get_top_error_log(evidence) == (
        "ERROR runtime::ddc-verification: ❌ Fetching processed eras error"
    )


def test_returns_none_when_no_error_line_present() -> None:
    # Previously returned logs[0] (a TRACE heartbeat) and spliced it into the narrative.
    evidence = {"grafana_logs": [{"message": "TRACE election-provider: current phase Signed"}]}
    assert _get_top_error_log(evidence) is None


def test_prefiltered_error_source_uses_first_entry_directly() -> None:
    evidence = {"grafana_error_logs": [{"message": "boom: connection refused"}]}
    assert _get_top_error_log(evidence) == "boom: connection refused"
