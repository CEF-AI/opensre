"""CEF clip-history retrieval tool — a clip's own past scores, for regression reasoning.

Retrieval only: reads the agent's ``analysis_runs`` cubby for every prior run of a given clip
(``candidate_id``) and returns the raw score rows. The agent uses this to judge whether a new
run deviates from the clip's *own* historical baseline — no thresholds; the baseline is the data.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.services.cef.client import CefVaultClient
from app.services.cef.wallet_signer import signer_from_material
from app.tools._telemetry import report_run_error
from app.tools.tool_decorator import tool

_DEFAULT_LIMIT = 20
_DEFAULT_ALIAS = "hiring"
# The tool owns the query so the agent never authors SQL.
_SCORE_COLUMNS = (
    "candidate_id, variant, status, reading_likelihood, prosodic_score, linguistic_score, "
    "clarity_structure, clarity_expression, clarity_overall, vocal_arousal, engagement_overall, "
    "engagement_question_quality, engagement_curiosity, engagement_questions_asked"
)


class CefClipHistoryInput(BaseModel):
    clip: str = Field(description="Clip / candidate_id to fetch history for (e.g. 'HIA-C1').")
    limit: int = Field(
        default=_DEFAULT_LIMIT, description="Max prior runs to return (newest first)."
    )


class CefClipHistoryOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether the CEF vault is configured and reachable.")
    clip: str | None = Field(default=None, description="Clip inspected.")
    count: int = Field(default=0, description="Number of prior runs returned.")
    runs: list[dict[str, Any]] = Field(
        default_factory=list, description="Raw per-run score rows (newest first)."
    )
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _cef_is_available(sources: dict[str, dict]) -> bool:
    cef = sources.get("cef") or {}
    return bool(
        cef.get("vault_base_url")
        and cef.get("vault_id")
        and (cef.get("wallet_path") or cef.get("wallet_json"))
    )


def _cef_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    cef = sources.get("cef") or {}
    return {
        "vault_base_url": cef.get("vault_base_url", ""),
        "vault_id": cef.get("vault_id", ""),
        "agent_id": cef.get("agent_id", ""),
        "wallet_path": cef.get("wallet_path", ""),
        "wallet_json": cef.get("wallet_json", ""),
        "wallet_password": cef.get("wallet_password", ""),
        "cubby_alias": cef.get("cubby_alias", "") or _DEFAULT_ALIAS,
    }


def _rows_as_dicts(result: dict[str, Any]) -> list[dict[str, Any]]:
    data = result.get("data")
    if not isinstance(data, dict):
        return []
    columns = data.get("columns")
    rows = data.get("rows")
    if isinstance(columns, list) and isinstance(rows, list):
        return [dict(zip(columns, row, strict=False)) for row in rows if isinstance(row, list)]
    if isinstance(rows, list):  # already row-dicts
        return [r for r in rows if isinstance(r, dict)]
    return []


@tool(
    name="cef_clip_history",
    display_name="CEF clip history",
    source="cef",
    source_id="cef_cubby",
    evidence_type="metrics",
    side_effect_level="read_only",
    description=(
        "Retrieve a clip's own prior run scores from the analysis_runs cubby (newest first). "
        "Retrieval only — returns raw rows so the agent can judge whether a new run deviates "
        "from this clip's historical baseline."
    ),
    use_cases=[
        "Establishing a clip's normal score range from its own past runs",
        "Detecting a score regression: this run vs. the clip's own history",
    ],
    requires=[],
    examples=[
        "Fetch the last 20 runs of HIA-C1 to see its normal clarity/authenticity/engagement.",
    ],
    anti_examples=[
        "Do not use for a single run's logs — use cef_agent_logs.",
    ],
    input_model=CefClipHistoryInput,
    output_model=CefClipHistoryOutput,
    injected_params=(
        "vault_base_url",
        "vault_id",
        "agent_id",
        "wallet_path",
        "wallet_json",
        "wallet_password",
        "cubby_alias",
    ),
    is_available=_cef_is_available,
    extract_params=_cef_extract_params,
)
def cef_clip_history(
    clip: str,
    limit: int = _DEFAULT_LIMIT,
    vault_base_url: str = "",
    vault_id: str = "",
    agent_id: str = "",
    wallet_path: str = "",
    wallet_json: str = "",
    wallet_password: str = "",
    cubby_alias: str = _DEFAULT_ALIAS,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Retrieve a clip's prior run scores. Pure retrieval; the agent reasons over the baseline."""
    if not (vault_base_url and vault_id and (wallet_path or wallet_json)):
        return {"source": "cef", "available": False, "error": "CEF vault is not configured."}
    try:
        signer = signer_from_material(
            wallet_json=wallet_json, wallet_path=wallet_path, password=wallet_password
        )
    except Exception as exc:  # noqa: BLE001 - any wallet-load failure → report + unavailable
        report_run_error(
            exc,
            tool_name="cef_clip_history",
            source="cef",
            component="app.tools.CefClipHistoryTool.cef_clip_history",
            method="load_wallet",
        )
        return {"source": "cef", "available": False, "error": f"wallet load failed: {exc}"}

    client = CefVaultClient(vault_base_url, signer)
    try:
        sql = (
            f"SELECT {_SCORE_COLUMNS} FROM analysis_runs "
            "WHERE candidate_id = ? ORDER BY rowid DESC LIMIT ?"
        )
        result = client.cubby_query(vault_id, agent_id, sql, [clip, limit], alias=cubby_alias)
    finally:
        client.close()

    if not result.get("success"):
        return {"source": "cef", "available": False, "clip": clip, "error": result.get("error")}
    runs = _rows_as_dicts(result)
    return {"source": "cef", "available": True, "clip": clip, "count": len(runs), "runs": runs}


__all__ = ["cef_clip_history"]
