"""Tests for the CEF integration: config, env loading, classify, and verify."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations import _verification_adapters as adapters
from app.integrations.cef import classify
from app.integrations.config_models import CefIntegrationConfig

_CREDS = {
    "vault_base_url": "https://vault-api.compute.test.ddcdragon.com/",
    "vault_id": "v-1",
    "agent_id": "0xpub:hiring-coach-lab2",
    "wallet_path": "/wallet.json",
    "wallet_password": "cef-agents",
    "cluster": "dragon1-testnet",
}


def test_config_normalizes_url_and_reports_configured() -> None:
    cfg = CefIntegrationConfig.model_validate(_CREDS)
    assert (
        cfg.vault_base_url == "https://vault-api.compute.test.ddcdragon.com"
    )  # trailing / stripped
    assert cfg.is_configured is True
    assert (
        CefIntegrationConfig.model_validate({"vault_base_url": "https://x"}).is_configured is False
    )


def test_classify_flattens_credentials() -> None:
    config, service = classify(_CREDS, "env-cef")
    assert service == "cef"
    assert config is not None
    assert config["vault_id"] == "v-1"
    assert config["cluster"] == "dragon1-testnet"
    assert config["integration_id"] == "env-cef"


def test_classify_rejects_incomplete_credentials() -> None:
    config, service = classify({"vault_base_url": "https://x"}, "env-cef")
    assert config is None and service is None


def test_env_loading_and_effective_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "CEF_VAULT_BASE_URL",
        "CEF_VAULT_ID",
        "CEF_AGENT_ID",
        "CEF_WALLET_PATH",
        "CEF_WALLET_PASSWORD",
        "CEF_CLUSTER",
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("CEF_VAULT_BASE_URL", "https://vault-api.example")
    monkeypatch.setenv("CEF_VAULT_ID", "v-9")
    monkeypatch.setenv("CEF_AGENT_ID", "0xpub:lab2")
    monkeypatch.setenv("CEF_WALLET_PATH", "/w.json")
    monkeypatch.setenv("CEF_WALLET_PASSWORD", "pw")

    from app.integrations.catalog import load_env_integrations, resolve_effective_integrations

    records = load_env_integrations()
    cef = next((r for r in records if r.get("service") == "cef"), None)
    assert cef is not None
    assert cef["credentials"]["vault_id"] == "v-9"
    assert cef["credentials"]["cluster"] == "dragon1-testnet"  # default applied

    effective = resolve_effective_integrations()
    cfg = (effective.get("cef") or {}).get("config") or {}
    assert cfg.get("vault_id") == "v-9"
    assert cfg.get("wallet_path") == "/w.json"


def test_verify_missing_credentials() -> None:
    outcome = adapters._verify_cef("env", {"vault_base_url": "https://x"})
    assert outcome["status"] == "missing"


def test_verify_probes_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Client:
        def __init__(self, *_a: Any, **_k: Any) -> None:
            pass

        def list_jobs(self, *_a: Any, **_k: Any) -> dict[str, Any]:
            return {"success": True, "data": {"items": []}}

        def close(self) -> None:
            pass

    import app.services.cef as cef_service

    monkeypatch.setattr(cef_service, "signer_from_file", lambda *_a, **_k: object())
    monkeypatch.setattr(cef_service, "CefVaultClient", _Client)

    outcome = adapters._verify_cef("env", _CREDS)
    assert outcome["status"] == "passed"
