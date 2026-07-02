"""Tests for the CEF component-log retrieval tool (retrieval only)."""

from __future__ import annotations

from typing import Any

import pytest

import app.tools.CefComponentLogsTool as mod
from app.tools.CefComponentLogsTool import (
    _build_query,
    _cef_logs_extract_params,
    _cef_logs_is_available,
    cef_component_logs,
)

_CREDS = {
    "grafana_endpoint": "https://dashboards.example",
    "grafana_api_key": "glsa_x",
    "grafana_username": "",
    "grafana_password": "",
    "cluster": "dragon1-testnet",
}


class _FakeGrafana:
    def __init__(self, logs: list[dict[str, Any]]) -> None:
        self._logs = logs
        self.last_query: str | None = None

    def query_loki(self, query: str, **_kwargs: Any) -> dict[str, Any]:
        self.last_query = query
        return {"success": True, "logs": self._logs}


def _patch(monkeypatch: pytest.MonkeyPatch, client: _FakeGrafana) -> None:
    monkeypatch.setattr(mod, "get_grafana_client_from_credentials", lambda **_k: client)


def test_build_query_owns_topology_per_service() -> None:
    # cef-system components
    assert _build_query("orchestrator", "dragon1-testnet", None, None) == (
        '{namespace="cef-system", cluster="dragon1-testnet", service_name="orchestrator"}'
    )
    # s3-gateway lives in a different namespace — the tool must know that
    assert _build_query("ddc-s3-gateway", "dragon1-testnet", None, None) == (
        '{namespace="ddc", cluster="dragon1-testnet", service_name="ddc-s3-gateway"}'
    )


def test_build_query_appends_contains_and_level() -> None:
    q = _build_query("orchestrator", "dragon1-testnet", "inference failed", "error")
    assert "|= `inference failed`" in q
    assert "level" in q and "error" in q


def test_availability_and_extract_params() -> None:
    sources = {"grafana": {"grafana_endpoint": "https://x", "grafana_api_key": "k"}, "cef": {}}
    assert _cef_logs_is_available(sources) is True
    assert _cef_logs_is_available({"grafana": {}}) is False
    params = _cef_logs_extract_params(sources)
    assert params["grafana_endpoint"] == "https://x"
    assert params["cluster"] == "dragon1-testnet"  # default when cef.cluster absent


def test_not_configured_is_unavailable() -> None:
    result = cef_component_logs(service="orchestrator")
    assert result["available"] is False
    assert "not configured" in result["error"]


def test_returns_raw_logs_and_query(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeGrafana([{"message": "all inference nodes failed for model parakeet"}])
    _patch(monkeypatch, client)
    result = cef_component_logs(service="orchestrator", contains="inference failed", **_CREDS)
    assert result["available"] is True
    assert result["service"] == "orchestrator"
    assert result["count"] == 1
    assert result["truncated"] is False
    assert result["logs"][0]["message"].startswith("all inference nodes failed")
    assert "|= `inference failed`" in client.last_query


def test_truncated_flag_set_when_limit_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _FakeGrafana([{"message": "x"}] * 5)
    _patch(monkeypatch, client)
    result = cef_component_logs(service="orchestrator", limit=5, **_CREDS)
    assert result["truncated"] is True


def test_query_failure_is_surfaced(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Failing(_FakeGrafana):
        def query_loki(self, _query: str, **_kwargs: Any) -> dict[str, Any]:
            return {"success": False, "error": "loki 400"}

    _patch(monkeypatch, _Failing([]))
    result = cef_component_logs(service="orchestrator", **_CREDS)
    assert result["available"] is False
    assert result["error"] == "loki 400"
