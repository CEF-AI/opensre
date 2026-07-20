"""Tests for the deploy-QA PR correlator (app.services.cef.correlate)."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.cef import correlate as mod
from app.services.cef.correlate import (
    CorrelationResult,
    PrSummary,
    correlate_failure,
    format_correlation_slack,
    prs_from_context,
)

_CTX = [
    {
        "owner": "cere-io",
        "repo": "ddc-node",
        "number": 12,
        "url": "https://github.com/cere-io/ddc-node/pull/12",
        "title": "Swap ASR model to canary",
        "body": "Change default asrAlias to canary-1b-flash.",
        "author": "alice",
        "merged": True,
        "files": ["src/asr/config.ts"],
    },
    {
        "owner": "cere-io",
        "repo": "frontend",
        "number": 7,
        "url": "https://github.com/cere-io/frontend/pull/7",
        "title": "Tweak button color",
        "body": "CSS only.",
        "author": "bob",
        "merged": True,
        "files": ["src/App.css"],
    },
    {"error": "GET … 404", "owner": "x", "repo": "y", "number": 1, "url": "u"},  # skipped
]


class _FakeLLM:
    def __init__(self, content: str) -> None:
        self._content = content

    def invoke(self, _messages: list[dict[str, Any]], **_kw: Any) -> Any:
        class _R:
            content = self._content

        return _R()


def _patch_llm(monkeypatch: pytest.MonkeyPatch, content: str) -> None:
    monkeypatch.setattr("app.services.agent_llm_client.get_agent_llm", lambda: _FakeLLM(content))


def test_prs_from_context_adapts_and_skips_errors() -> None:
    prs = prs_from_context(_CTX)
    assert [p.ref for p in prs] == ["cere-io/ddc-node#12", "cere-io/frontend#7"]
    assert prs[0].repo == "cere-io/ddc-node"
    assert prs[0].files == ["src/asr/config.ts"]


def test_correlate_ranks_and_filters_to_supplied_prs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_llm(
        monkeypatch,
        '```json\n{"summary":"ASR swap is the cause","suspects":['
        '{"pr_url":"https://github.com/cere-io/ddc-node/pull/12","likelihood":"high","reasoning":"changes ASR model"},'
        '{"pr_url":"https://github.com/not/in/list/pull/99","likelihood":"high","reasoning":"hallucinated"}'
        "]}\n```",
    )
    res = correlate_failure(
        "ASR stage failed: no nodes for canary-1b-flash", prs_from_context(_CTX)
    )
    # code-fenced JSON parsed; hallucinated PR (not in the supplied set) dropped.
    assert [s.ref for s in res.suspects] == ["cere-io/ddc-node#12"]
    assert res.suspects[0].likelihood == "high"
    assert res.summary == "ASR swap is the cause"


def test_correlate_empty_prs_is_noop() -> None:
    res = correlate_failure("anything", [])
    assert res.suspects == [] and "No PRs" in res.summary


def test_correlate_llm_failure_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom() -> Any:
        raise RuntimeError("llm down")

    monkeypatch.setattr("app.services.agent_llm_client.get_agent_llm", _boom)
    res = correlate_failure("x", [PrSummary(url="u", repo="a/b", number=1, title="t")])
    assert res.suspects == [] and res.error and "llm down" in res.error


def test_format_slack_variants() -> None:
    infra = format_correlation_slack(CorrelationResult(summary="looks upstream"))
    assert "no deployed PR is a likely cause" in infra
    err = format_correlation_slack(CorrelationResult(error="boom"))
    assert "correlation unavailable" in err
    hit = format_correlation_slack(
        CorrelationResult(
            suspects=[mod.Suspect(pr_url="u", ref="a/b#1", likelihood="high", reasoning="why")]
        )
    )
    assert "a/b#1" in hit and "high" in hit
