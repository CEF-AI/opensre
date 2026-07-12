"""Tests for the CEF agent-log retrieval tool (retrieval only, no interpretation)."""

from __future__ import annotations

from typing import Any

import pytest

import app.tools.CefAgentLogsTool as mod
from app.tools.CefAgentLogsTool import (
    _JOB_LOOKUP_LIMIT,
    _cef_extract_params,
    _cef_is_available,
    cef_agent_logs,
)

_CREDS = {
    "vault_base_url": "https://vault-api.example",
    "vault_id": "v-1",
    "agent_id": "0xpub:hiring-coach-lab2",
    "wallet_path": "/wallet.json",
    "wallet_password": "cef-agents",
}


class _FakeClient:
    """Stub CefVaultClient returning canned vault-api responses."""

    def __init__(self, *, jobs: list[dict[str, Any]] | None = None) -> None:
        self._jobs = jobs if jobs is not None else [{"jobId": "J1", "context": "conv-1"}]
        self.closed = False
        self.job_lookup_limit: int | None = None

    def list_jobs(self, _vault: str, _agent: str, **kwargs: Any) -> dict[str, Any]:
        self.job_lookup_limit = kwargs.get("limit")
        return {"success": True, "data": {"items": self._jobs}}

    def list_activities(self, _vault: str, _job: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "data": {"items": [{"activityId": "A1", "handlerName": "onTurn", "status": "failed"}]},
        }

    def activity_logs(
        self, _vault: str, _job: str, _activity: str, **_kwargs: Any
    ) -> dict[str, Any]:
        return {"success": True, "data": {"logs": [{"timestamp": 1, "message": "[ERROR] boom"}]}}

    def close(self) -> None:
        self.closed = True


def _patch(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(mod, "signer_from_file", lambda *_a, **_k: object())
    monkeypatch.setattr(mod, "CefVaultClient", lambda *_a, **_k: client)


def test_availability_and_extract_params() -> None:
    sources = {"cef": _CREDS}
    assert _cef_is_available(sources) is True
    assert _cef_is_available({"cef": {}}) is False
    assert _cef_extract_params(sources)["agent_id"] == "0xpub:hiring-coach-lab2"


def test_not_configured_is_unavailable() -> None:
    result = cef_agent_logs(conversation_id="conv-1")
    assert result["available"] is False
    assert "not configured" in result["error"]


def test_resolves_conversation_to_job_then_lists_activities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeClient()
    _patch(monkeypatch, client)
    result = cef_agent_logs(conversation_id="conv-1", **_CREDS)
    assert result["available"] is True
    assert result["job_id"] == "J1"
    # job resolution must scan a generous window (endpoint has no context filter / pagination)
    assert client.job_lookup_limit == _JOB_LOOKUP_LIMIT
    # returns raw activities; the tool does NOT pick a "failed stage"
    assert result["activities"][0]["status"] == "failed"
    assert "logs" not in result
    assert client.closed is True


def test_activity_id_fetches_raw_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch(monkeypatch, _FakeClient())
    result = cef_agent_logs(job_id="J1", activity_id="A1", **_CREDS)
    assert result["job_id"] == "J1"
    assert result["activity_id"] == "A1"
    assert result["logs"][0]["message"] == "[ERROR] boom"


def test_no_job_for_conversation_returns_jobs_and_null_job(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(jobs=[{"jobId": "J9", "context": "other-conv"}])
    _patch(monkeypatch, client)
    result = cef_agent_logs(conversation_id="missing", **_CREDS)
    assert result["available"] is True
    assert result["job_id"] is None
    assert result["jobs"] == [{"jobId": "J9", "context": "other-conv"}]
    # small window, no match -> genuinely not created yet, not a truncation
    assert result["job_lookup_truncated"] is False
    assert "no job exists yet" in result["note"]


def test_job_beyond_lookup_window_flags_truncation(monkeypatch: pytest.MonkeyPatch) -> None:
    # window is full (>= limit) and the conversation isn't in it -> must flag truncation,
    # never silently report the run as absent.
    full = [{"jobId": f"J{i}", "context": f"c{i}"} for i in range(_JOB_LOOKUP_LIMIT)]
    _patch(monkeypatch, _FakeClient(jobs=full))
    result = cef_agent_logs(conversation_id="way-older-conv", **_CREDS)
    assert result["job_id"] is None
    assert result["job_lookup_truncated"] is True
    assert "beyond the lookup window" in result["note"]


def test_service_error_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FailingClient(_FakeClient):
        def list_activities(self, _vault: str, _job: str, **_kwargs: Any) -> dict[str, Any]:
            return {"success": False, "error": "AUTH_MISSING"}

    _patch(monkeypatch, _FailingClient())
    result = cef_agent_logs(job_id="J1", **_CREDS)
    assert result["available"] is False
    assert result["error"] == "AUTH_MISSING"
