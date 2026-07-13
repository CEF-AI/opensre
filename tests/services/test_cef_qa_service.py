"""Tests for the shared CEF QA entrypoint (CLI + microservice core) and the /investigate route."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.cef import qa as qa_mod
from app.services.cef.qa import (
    CefCreds,
    CefQaRequest,
    GrafanaCreds,
    TelegramTarget,
    build_cef_resolved_integrations,
    run_cef_qa,
)
from app.tools.utils.availability import cef_available_or_backend

_CEF = CefCreds(
    vault_base_url="https://vault-api.example.com",
    vault_id="v-123",
    agent_id="pub:hiring-coach-qa",
    wallet_json='{"encoded":"x","encoding":{"content":["ed25519"]}}',
    wallet_password="pw",
)

# A canned investigation state (what run_investigation would return) — a real failure.
_STATE: dict[str, Any] = {
    "root_cause": "ASR activity a-map-0 failed; run never finalized.",
    "root_cause_category": "upstream_service_outage",
    "validity_score": 0.86,
    "validated_claims": [{"claim": "a-map-0 status=failed", "evidence_ids": ["e1"]}],
    "non_validated_claims": [{"claim": "model fleet outage vs quota"}],
    "investigation_recommendations": ["check whisper node availability"],
    "evidence_catalog": {"e1": {"display_id": "E1"}},
}


def test_build_resolved_integrations_from_wallet_json_is_available() -> None:
    resolved = build_cef_resolved_integrations(
        _CEF, GrafanaCreds(endpoint="https://g", api_key="k")
    )
    assert resolved["cef"]["wallet_json"].startswith("{")
    assert resolved["cef"]["vault_id"] == "v-123"
    assert resolved["grafana"]["endpoint"] or resolved["grafana"].get("grafana_endpoint")
    # A wallet_json-only CEF (no file path) must count as available.
    assert cef_available_or_backend(resolved) is True


def _patch_pipeline(monkeypatch: pytest.MonkeyPatch, state: dict[str, Any]) -> None:
    monkeypatch.setattr("app.pipeline.runners.run_investigation", lambda *_a, **_k: state)
    monkeypatch.setattr(
        "app.agent.stages.publish_findings.context.build.build_report_context",
        lambda s: s,
    )


def test_run_cef_qa_maps_state_to_verdict_and_report(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, _STATE)
    req = CefQaRequest(context_id="conv-1", clip="HIA-C1", model="gemma4_31b", cef=_CEF)
    result = run_cef_qa(req)
    assert result.verdict == "no_go"
    assert result.confidence == "high"
    assert result.validity_score == 0.86
    assert result.findings == ["a-map-0 status=failed"]
    assert result.actions == ["check whisper node availability"]
    assert "NO-GO" in result.report and "[E1]" in result.report  # citation carried through
    assert result.delivered is False


def test_run_cef_qa_low_confidence_gates_to_needs_review(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {**_STATE, "validity_score": 0.3}
    _patch_pipeline(monkeypatch, state)
    # `conversation_id` here exercises the legacy alias → still maps to context_id.
    result = run_cef_qa(CefQaRequest(conversation_id="conv-2", cef=_CEF))
    assert result.verdict == "needs_review"
    assert result.confidence == "low"


def test_run_cef_qa_posts_to_telegram_when_target_given(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_pipeline(monkeypatch, _STATE)
    sent: dict[str, Any] = {}

    def fake_send(result: dict[str, Any], **kw: Any) -> tuple[bool, str]:
        sent.update(kw)
        return True, ""

    monkeypatch.setattr(qa_mod, "send_cef_qa_report", fake_send)
    req = CefQaRequest(
        context_id="conv-3",
        cef=_CEF,
        deliver_telegram=TelegramTarget(bot_token="tok", chat_id="42"),
    )
    result = run_cef_qa(req)
    assert result.delivered is True
    assert sent["bot_token"] == "tok" and sent["chat_id"] == "42"


def test_investigate_endpoint_requires_auth() -> None:
    from fastapi.testclient import TestClient

    from app.webapp import app

    client = TestClient(app)
    # No bearer token → HTTPBearer rejects before the handler runs.
    resp = client.post("/investigate", json={"context_id": "x", "cef": _CEF.model_dump()})
    assert resp.status_code in (401, 403)


def test_investigate_endpoint_runs_qa_when_authorized(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app import webapp

    monkeypatch.setattr(
        webapp,
        "run_cef_qa",
        lambda _req: qa_mod.CefQaResult(verdict="pass", confidence="high", report="🟢 PASS"),
    )
    webapp.app.dependency_overrides[webapp.require_auth] = lambda: None
    try:
        client = TestClient(webapp.app)
        resp = client.post(
            "/investigate",
            json={"context_id": "conv-9", "cef": _CEF.model_dump()},
        )
        assert resp.status_code == 200
        assert resp.json()["verdict"] == "pass"
    finally:
        webapp.app.dependency_overrides.clear()
