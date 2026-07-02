"""Tests for the CEF clip-history retrieval tool (retrieval only, no thresholds)."""

from __future__ import annotations

from typing import Any

import pytest

import app.tools.CefClipHistoryTool as mod
from app.tools.CefClipHistoryTool import cef_clip_history

_CREDS = {
    "vault_base_url": "https://vault-api.example",
    "vault_id": "v-1",
    "agent_id": "0xpub:hiring-coach-lab2",
    "wallet_path": "/wallet.json",
    "wallet_password": "cef-agents",
    "cubby_alias": "hiring",
}


class _FakeClient:
    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data
        self.last_sql: str | None = None
        self.last_params: list[Any] | None = None

    def cubby_query(
        self, _vault: str, _agent: str, sql: str, params: list[Any], **_kwargs: Any
    ) -> dict[str, Any]:
        self.last_sql = sql
        self.last_params = params
        return {"success": True, "data": self._data}

    def close(self) -> None:
        pass


def _patch(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(mod, "signer_from_file", lambda *_a, **_k: object())
    monkeypatch.setattr(mod, "CefVaultClient", lambda *_a, **_k: client)


def test_not_configured_is_unavailable() -> None:
    result = cef_clip_history(clip="HIA-C1")
    assert result["available"] is False
    assert "not configured" in result["error"]


def test_returns_history_as_row_dicts(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeClient(
        {
            "columns": ["candidate_id", "clarity_overall", "reading_likelihood"],
            "rows": [["HIA-C1", 0.88, 0.26], ["HIA-C1", 0.73, 0.26]],
        }
    )
    _patch(monkeypatch, client)
    result = cef_clip_history(clip="HIA-C1", limit=20, **_CREDS)

    assert result["available"] is True
    assert result["clip"] == "HIA-C1"
    assert result["count"] == 2
    assert result["runs"][0] == {
        "candidate_id": "HIA-C1",
        "clarity_overall": 0.88,
        "reading_likelihood": 0.26,
    }
    # the tool owns the query + filters by candidate_id (agent never writes SQL)
    assert "analysis_runs" in client.last_sql
    assert "candidate_id = ?" in client.last_sql
    assert client.last_params == ["HIA-C1", 20]


def test_query_failure_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Failing(_FakeClient):
        def cubby_query(
            self, _vault: str, _agent: str, _sql: str, _params: list[Any], **_kwargs: Any
        ) -> dict[str, Any]:
            return {"success": False, "error": "cubby 400"}

    _patch(monkeypatch, _Failing({}))
    result = cef_clip_history(clip="HIA-C1", **_CREDS)
    assert result["available"] is False
    assert result["error"] == "cubby 400"
