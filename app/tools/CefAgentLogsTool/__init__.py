"""CEF agent-log retrieval tool.

Retrieval only: fetches a CEF agent run's jobs, activities, and ``ctx.log`` lines from the
vault-api (via :mod:`app.services.cef`). It does not interpret the logs — the investigation
agent reads the returned evidence and reasons about the failure. Granularity is caller-driven:
resolve a run's job, list its activities, then fetch a specific activity's logs.
"""

from __future__ import annotations

from typing import Any, cast

from pydantic import BaseModel, Field

from app.services.cef.client import CefVaultClient
from app.services.cef.wallet_signer import signer_from_material
from app.tools._telemetry import report_run_error
from app.tools.tool_decorator import tool
from app.tools.utils.availability import cef_available_or_backend

_DEFAULT_LIMIT = 50
# Resolving conversation_id -> job scans the agent's jobs and matches on job.context. The vault-api
# jobs endpoint has no context filter and no working pagination (offset is ignored) — it returns the
# most-recent up-to-`limit` jobs — so the lookup window must be generous enough to include the run's
# job. Kept separate from `limit` (which bounds returned log lines), and truncation is surfaced, not
# silently swallowed, if the job isn't within the window.
_JOB_LOOKUP_LIMIT = 500


class CefAgentLogsInput(BaseModel):
    conversation_id: str | None = Field(
        default=None,
        description="Run/conversation id to inspect; resolved to its job via job.context.",
    )
    job_id: str | None = Field(
        default=None,
        description="Job id to read directly (skips conversation_id resolution).",
    )
    activity_id: str | None = Field(
        default=None,
        description="If set, retrieve this activity's ctx.log lines; otherwise list the job's activities.",
    )
    limit: int = Field(default=_DEFAULT_LIMIT, description="Max items to retrieve per call.")


class CefAgentLogsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether the CEF vault is configured and reachable.")
    conversation_id: str | None = Field(default=None, description="Conversation id inspected.")
    job_id: str | None = Field(default=None, description="Resolved job id, if any.")
    activity_id: str | None = Field(
        default=None, description="Activity id, when logs were fetched."
    )
    jobs: list[dict[str, Any]] | None = Field(default=None, description="Retrieved jobs (raw).")
    activities: list[dict[str, Any]] | None = Field(
        default=None, description="Retrieved activities (raw)."
    )
    logs: list[dict[str, Any]] | None = Field(
        default=None, description="Retrieved ctx.log lines (raw)."
    )
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _cef_is_available(sources: dict[str, dict]) -> bool:
    cef = sources.get("cef") or {}
    return bool(cef.get("vault_base_url") and cef.get("vault_id") and cef.get("wallet_path"))


def _cef_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    cef = sources.get("cef") or {}
    return {
        "vault_base_url": cef.get("vault_base_url", ""),
        "vault_id": cef.get("vault_id", ""),
        "agent_id": cef.get("agent_id", ""),
        "wallet_path": cef.get("wallet_path", ""),
        "wallet_json": cef.get("wallet_json", ""),
        "wallet_password": cef.get("wallet_password", ""),
        # Fixture backend for synthetic scenarios (mirrors eks_backend/datadog_backend).
        "cef_backend": cef.get("_backend"),
    }


def _list_field(result: dict[str, Any], key: str) -> list[dict[str, Any]]:
    data = result.get("data")
    value = data.get(key) if isinstance(data, dict) else None
    return value if isinstance(value, list) else []


def _unavailable(error: str | None) -> dict[str, Any]:
    return {"source": "cef_agent_logs", "available": False, "error": error}


@tool(
    name="cef_agent_logs",
    display_name="CEF agent logs",
    source="cef",
    source_id="cef_vault_api",
    evidence_type="logs",
    side_effect_level="read_only",
    description=(
        "Retrieve a CEF agent run's jobs, activities, and ctx.log lines from the vault-api. "
        "Retrieval only — returns raw evidence for the agent to analyse; it does not interpret it."
    ),
    use_cases=[
        "Reading a hiring-coach run's own execution trace (ctx.log)",
        "Listing a run's activities to see each pipeline step's status",
        "Fetching the log lines for a specific activity to root-cause a failure",
    ],
    requires=[],
    examples=[
        "List the activities for a conversation_id to see where the run stopped.",
        "Fetch the logs for the activity that did not complete.",
    ],
    anti_examples=[
        "Do not use for component/infra logs (orchestrator, s3-gateway) — use the Grafana tool.",
    ],
    input_model=CefAgentLogsInput,
    output_model=CefAgentLogsOutput,
    injected_params=(
        "vault_base_url",
        "vault_id",
        "agent_id",
        "wallet_path",
        "wallet_json",
        "wallet_password",
        "cef_backend",
    ),
    is_available=cef_available_or_backend,
    extract_params=_cef_extract_params,
)
def cef_agent_logs(
    conversation_id: str | None = None,
    job_id: str | None = None,
    activity_id: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    vault_base_url: str = "",
    vault_id: str = "",
    agent_id: str = "",
    wallet_path: str = "",
    wallet_json: str = "",
    wallet_password: str = "",
    cef_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Retrieve CEF agent jobs/activities/logs. Pure retrieval; the agent does the analysis.

    When ``cef_backend`` is provided (a FixtureCEFBackend from the synthetic harness) the call
    short-circuits and returns the backend's canned response — no live vault-api calls.
    """
    if cef_backend is not None:
        return cast(
            "dict[str, Any]",
            cef_backend.agent_logs(
                conversation_id=conversation_id,
                job_id=job_id,
                activity_id=activity_id,
                limit=limit,
            ),
        )
    if not (vault_base_url and vault_id and (wallet_path or wallet_json)):
        return _unavailable("CEF vault is not configured.")
    try:
        signer = signer_from_material(
            wallet_json=wallet_json, wallet_path=wallet_path, password=wallet_password
        )
    except Exception as exc:  # noqa: BLE001 - any wallet-load failure → report + unavailable
        report_run_error(
            exc,
            tool_name="cef_agent_logs",
            source="cef",
            component="app.tools.CefAgentLogsTool.cef_agent_logs",
            method="load_wallet",
        )
        return _unavailable(f"wallet load failed: {exc}")

    client = CefVaultClient(vault_base_url, signer)
    try:
        resolved_job = job_id
        if not resolved_job and conversation_id:
            jobs_result = client.list_jobs(vault_id, agent_id, limit=_JOB_LOOKUP_LIMIT)
            if not jobs_result.get("success"):
                return _unavailable(jobs_result.get("error"))
            jobs = _list_field(jobs_result, "items")
            match = next((j for j in jobs if j.get("context") == conversation_id), None)
            if match is None:
                # No job for this run in the scanned window. Distinguish "not created yet" from
                # "beyond the lookup window" so the agent doesn't misread a truncation as absence.
                truncated = len(jobs) >= _JOB_LOOKUP_LIMIT
                return {
                    "source": "cef_agent_logs",
                    "available": True,
                    "conversation_id": conversation_id,
                    "job_id": None,
                    "jobs": jobs,
                    "job_lookup_truncated": truncated,
                    "note": (
                        f"conversation not found among the {len(jobs)} most-recent jobs; more exist "
                        "beyond the lookup window (retry with an explicit job_id)"
                        if truncated
                        else "no job exists yet for this conversation (it may still be dispatching)"
                    ),
                }
            resolved_job = match.get("jobId")

        if not resolved_job:
            jobs_result = client.list_jobs(vault_id, agent_id, limit=limit)
            if not jobs_result.get("success"):
                return _unavailable(jobs_result.get("error"))
            return {
                "source": "cef_agent_logs",
                "available": True,
                "jobs": _list_field(jobs_result, "items"),
            }

        if activity_id:
            logs_result = client.activity_logs(vault_id, resolved_job, activity_id, limit=limit)
            if not logs_result.get("success"):
                return _unavailable(logs_result.get("error"))
            return {
                "source": "cef_agent_logs",
                "available": True,
                "conversation_id": conversation_id,
                "job_id": resolved_job,
                "activity_id": activity_id,
                "logs": _list_field(logs_result, "logs"),
            }

        activities_result = client.list_activities(vault_id, resolved_job, limit=limit)
        if not activities_result.get("success"):
            return _unavailable(activities_result.get("error"))
        return {
            "source": "cef_agent_logs",
            "available": True,
            "conversation_id": conversation_id,
            "job_id": resolved_job,
            "activities": _list_field(activities_result, "items"),
        }
    finally:
        client.close()


__all__ = ["cef_agent_logs"]
