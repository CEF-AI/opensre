"""Tests for the CEF vault-api signed client."""

from __future__ import annotations

import base64
import json
import re
from typing import Any

import httpx
import pytest
from nacl.signing import SigningKey, VerifyKey

from app.services.cef.client import CefVaultClient, _now_millis_iso


class _Ed25519Signer:
    """Real ed25519 signer so tests can verify the emitted signature."""

    def __init__(self) -> None:
        self._sk = SigningKey.generate()

    @property
    def public_key_hex(self) -> str:
        return "0x" + bytes(self._sk.verify_key).hex()

    def sign(self, message: bytes) -> bytes:
        return self._sk.sign(message).signature


def _client(
    monkeypatch: pytest.MonkeyPatch, signer: _Ed25519Signer, handler: Any
) -> CefVaultClient:
    client = CefVaultClient("https://vault-api.example", signer)
    mock = httpx.Client(
        base_url="https://vault-api.example", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(client, "_get_client", lambda: mock)
    return client


def test_now_millis_iso_matches_js_toisostring_shape() -> None:
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", _now_millis_iso())


def test_signed_request_scheme_and_url(monkeypatch: pytest.MonkeyPatch) -> None:
    signer = _Ed25519Signer()
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        h = request.headers
        preamble = base64.b64decode(h["x-signed-preamble"])
        # signature must verify over the exact preamble bytes with the sent public key
        VerifyKey(bytes.fromhex(h["x-public-key"][2:])).verify(
            preamble, bytes.fromhex(h["x-signature"])
        )
        seen["url"] = str(request.url)
        seen["preamble"] = json.loads(preamble)
        seen["sigtype"] = h["x-signature-type"]
        seen["pubkey"] = h["x-public-key"]
        return httpx.Response(200, json={"items": [{"activityId": "a1"}]})

    result = _client(monkeypatch, signer, handler).list_activities("v-1", "job-1", limit=50)

    assert result == {"success": True, "data": {"items": [{"activityId": "a1"}]}}
    assert (
        seen["url"] == "https://vault-api.example/api/v1/vaults/v-1/jobs/job-1/activities?limit=50"
    )
    assert seen["sigtype"] == "ed25519"
    assert seen["pubkey"] == signer.public_key_hex
    # canonical preamble: exact key order + fields matching the request
    assert list(seen["preamble"].keys()) == ["method", "path", "query", "timestamp"]
    assert seen["preamble"]["method"] == "GET"
    assert seen["preamble"]["path"] == "/api/v1/vaults/v-1/jobs/job-1/activities"
    assert seen["preamble"]["query"] == "limit=50"


def test_agent_id_is_url_encoded_consistently(monkeypatch: pytest.MonkeyPatch) -> None:
    signer = _Ed25519Signer()
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["path"] = json.loads(base64.b64decode(request.headers["x-signed-preamble"]))["path"]
        return httpx.Response(200, json={"items": []})

    _client(monkeypatch, signer, handler).list_jobs("v-1", "0xpub:hiring-coach-lab2")

    # ':' percent-encoded, and the signed path must equal the request path exactly
    assert "0xpub%3Ahiring-coach-lab2" in seen["url"]
    assert seen["path"] == "/api/v1/vaults/v-1/agents/0xpub%3Ahiring-coach-lab2/jobs"


def test_activity_logs_returns_ctx_log_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    signer = _Ed25519Signer()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/activities/act-1/logs")
        return httpx.Response(
            200, json={"logs": [{"timestamp": 1, "message": "[INFO] finalize done"}]}
        )

    result = _client(monkeypatch, signer, handler).activity_logs("v-1", "job-1", "act-1")
    assert result["success"] is True
    assert result["data"]["logs"][0]["message"].endswith("finalize done")


def test_http_error_is_captured_as_result(monkeypatch: pytest.MonkeyPatch) -> None:
    signer = _Ed25519Signer()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": "AUTH_MISSING"}})

    result = _client(monkeypatch, signer, handler).activity_logs("v-1", "job-1", "act-1")
    assert result["success"] is False
    assert "error" in result
