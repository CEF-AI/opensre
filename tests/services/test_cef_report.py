"""Tests for the beautified CEF QA Telegram report."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.cef.report import format_cef_qa_telegram, send_cef_qa_report

_REGRESSION_RESULT: dict[str, Any] = {
    "root_cause": "linguistic judge under-rated strong language despite clean execution.",
    "root_cause_category": "code_defect",
    "validity_score": 1.0,
    "validated_claims": [
        {"claim": "linguistic_score 0.20 vs baseline 0.85"},
        {"claim": "all agent stages completed"},
    ],
    "non_validated_claims": [{"claim": "prompt change vs scoring bug"}],
    "investigation_recommendations": ["diff E040 vs E039 prompt", "re-run to confirm"],
}


def test_no_go_report_has_verdict_findings_and_actions() -> None:
    text = format_cef_qa_telegram(
        _REGRESSION_RESULT, subtitle="E040 → E039 · HIA-C1 · testnet", footer="conv d05225a7"
    )
    assert text.startswith("🔴  hiring-coach QA · NO-GO")
    assert "E040 → E039 · HIA-C1 · testnet  ·  confidence: high" in text
    assert "FINDINGS" in text and "• linguistic_score 0.20 vs baseline 0.85" in text
    assert "DO" in text and "1  diff E040 vs E039 prompt" in text
    assert "NOT VERIFIED" in text and "• prompt change vs scoring bug" in text
    assert text.rstrip().endswith("conv d05225a7")


def test_pass_report_when_healthy() -> None:
    result = {
        "root_cause": "No failure detected; run completed successfully.",
        "root_cause_category": "healthy",
        "validity_score": 1.0,
        "validated_claims": [{"claim": "all stages completed"}],
    }
    text = format_cef_qa_telegram(result)
    assert text.startswith("🟢  hiring-coach QA · PASS")
    assert "confidence: high" in text


def test_confidence_tiers() -> None:
    assert "confidence: high" in format_cef_qa_telegram({"validity_score": 0.9})
    assert "confidence: medium" in format_cef_qa_telegram({"validity_score": 0.6})
    assert "confidence: low" in format_cef_qa_telegram({"validity_score": 0.2})
    assert "confidence: unknown" in format_cef_qa_telegram({})


def test_send_uses_telegram_helper(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: dict[str, Any] = {}

    def fake_post(chat_id: str, text: str, bot_token: str, **_kwargs: Any) -> tuple[bool, str, str]:
        sent.update(chat_id=chat_id, text=text, bot_token=bot_token)
        return True, "", "42"

    monkeypatch.setattr("app.services.cef.report.post_telegram_message", fake_post)
    ok, error = send_cef_qa_report(
        _REGRESSION_RESULT, bot_token="tok", chat_id="123", subtitle="E040"
    )
    assert ok is True and error == ""
    assert sent["chat_id"] == "123" and sent["bot_token"] == "tok"
    assert sent["text"].startswith("🔴  hiring-coach QA · NO-GO")
