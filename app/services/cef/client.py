"""CEF Vault API client — signed read access to a CEF agent's jobs, activities, and logs.

Reproduces the vault-api request-signing scheme (ed25519 over a canonical
``{method, path, query, timestamp}`` preamble) so callers can read a CEF agent run's own
``ctx.log`` without the Vault SDK. Key material is injected via :class:`Signer`; this module
owns only the transport and the signing scheme — decoding a wallet into a signer lives elsewhere.
"""

from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote

import httpx

from app.services._error_helpers import capture_service_error

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 20
_SIGNATURE_TYPE = "ed25519"
_INTEGRATION = "cef_vault"


class Signer(Protocol):
    """Ed25519 signer for vault-api requests; the private key lives behind this."""

    @property
    def public_key_hex(self) -> str:
        """``0x``-prefixed hex of the signing public key."""

    def sign(self, message: bytes) -> bytes:
        """Return the 64-byte ed25519 signature over ``message``."""


def _now_millis_iso() -> str:
    """ISO-8601 timestamp, millisecond precision, ``Z`` suffix (matches JS ``toISOString``)."""
    now = datetime.now(UTC)
    return f"{now:%Y-%m-%dT%H:%M:%S}.{now.microsecond // 1000:03d}Z"


class CefVaultClient:
    """Synchronous client that reads vault-api jobs/activities/logs via signed GETs.

    The vault-api authenticates each request by verifying an ed25519 signature over a
    canonical preamble; the caller supplies the wallet-backed :class:`Signer`.
    """

    def __init__(self, base_url: str, signer: Signer, *, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._base_url = base_url.rstrip("/")
        self._signer = signer
        self._timeout = timeout
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        return self._client

    def _signed_headers(self, method: str, path: str, query: str) -> dict[str, str]:
        # The server re-derives (method, path, query) from the preamble and verifies the
        # signature over these exact bytes, so we sign precisely what we send.
        preamble = json.dumps(
            {
                "method": method.upper(),
                "path": path,
                "query": query,
                "timestamp": _now_millis_iso(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        signature = self._signer.sign(preamble)
        return {
            "X-Public-Key": self._signer.public_key_hex,
            "X-Signature": signature.hex(),
            "X-Signature-Type": _SIGNATURE_TYPE,
            "X-Signed-Preamble": base64.b64encode(preamble).decode("ascii"),
            "Accept": "application/json",
        }

    def _get(self, path: str, query: str, *, op: str) -> dict[str, Any]:
        url = f"{path}?{query}" if query else path
        try:
            response = self._get_client().get(url, headers=self._signed_headers("GET", path, query))
            response.raise_for_status()
            payload: Any = response.json()
        except Exception as exc:  # noqa: BLE001 - normalized into a result dict for the tool layer
            capture_service_error(
                exc, logger=logger, integration=_INTEGRATION, method=op, extras={"path": path}
            )
            return {"success": False, "error": str(exc)}
        return {"success": True, "data": payload}

    def _signed_body(self, body: dict[str, Any]) -> tuple[dict[str, str], bytes]:
        # POST auth signs the request body (with an injected timestamp), not the path;
        # the exact signed bytes must be the request body.
        payload = {**body, "timestamp": _now_millis_iso()}
        body_bytes = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        signature = self._signer.sign(body_bytes)
        headers = {
            "X-Public-Key": self._signer.public_key_hex,
            "X-Signature": signature.hex(),
            "X-Signature-Type": _SIGNATURE_TYPE,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        return headers, body_bytes

    def _post(self, path: str, body: dict[str, Any], *, op: str) -> dict[str, Any]:
        headers, body_bytes = self._signed_body(body)
        try:
            response = self._get_client().post(path, content=body_bytes, headers=headers)
            response.raise_for_status()
            payload: Any = response.json()
        except Exception as exc:  # noqa: BLE001 - normalized into a result dict for the tool layer
            capture_service_error(
                exc, logger=logger, integration=_INTEGRATION, method=op, extras={"path": path}
            )
            return {"success": False, "error": str(exc)}
        return {"success": True, "data": payload}

    def cubby_query(
        self,
        vault_id: str,
        agent_id: str,
        sql: str,
        params: list[Any],
        *,
        scope: str = "default",
        alias: str = "hiring",
    ) -> dict[str, Any]:
        """Run a read-only SQL query against an agent's cubby (e.g. ``analysis_runs``)."""
        # NB: the cubby endpoint takes the agent_id with its ':' un-encoded (unlike the
        # jobs endpoint), so keep the colon in `safe`.
        path = (
            f"/api/v1/vaults/{quote(vault_id, safe='')}/scopes/{quote(scope, safe='')}"
            f"/agents/{quote(agent_id, safe=':')}/cubbies/{quote(alias, safe='')}/query"
        )
        return self._post(path, {"sql": sql, "params": params}, op="cubby_query")

    def list_jobs(self, vault_id: str, agent_id: str, *, limit: int = 50) -> dict[str, Any]:
        """List jobs for an agent connection (``agent_id`` is ``<pubkey>:<alias>``)."""
        path = f"/api/v1/vaults/{quote(vault_id, safe='')}/agents/{quote(agent_id, safe='')}/jobs"
        return self._get(path, f"limit={limit}", op="list_jobs")

    def list_activities(self, vault_id: str, job_id: str, *, limit: int = 50) -> dict[str, Any]:
        """List a job's activities (the agent's pipeline steps)."""
        path = f"/api/v1/vaults/{quote(vault_id, safe='')}/jobs/{quote(job_id, safe='')}/activities"
        return self._get(path, f"limit={limit}", op="list_activities")

    def activity_logs(
        self, vault_id: str, job_id: str, activity_id: str, *, offset: int = 0, limit: int = 100
    ) -> dict[str, Any]:
        """Fetch an activity's ``ctx.log`` lines (``{logs: [{timestamp, message}]}``)."""
        path = (
            f"/api/v1/vaults/{quote(vault_id, safe='')}"
            f"/jobs/{quote(job_id, safe='')}/activities/{quote(activity_id, safe='')}/logs"
        )
        return self._get(path, f"offset={offset}&limit={limit}", op="activity_logs")

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None:
            self._client.close()
            self._client = None


__all__ = ["CefVaultClient", "Signer"]
