"""DAC pipeline health tool — the agent's selectable DAC health check.

The model picks *which* check to run (or "all"); the queries and thresholds live
in ``probes.py`` so the model never authors PromQL/LogQL. Returns a per-dimension
snapshot with deterministic green/amber/red status for the agent to reason over.

Grafana credentials are resolved from the investigation's integrations the same
way as the other Grafana tools (``query_grafana_logs``/``query_grafana_metrics``).
"""

from __future__ import annotations

from typing import Any

from app.services.grafana import get_grafana_client_from_credentials
from app.tools.DacHealthTool.probes import (
    CHECK_GROUPS,
    Status,
    build_probes,
    evaluate_probe,
    failure_samples,
    worst,
)
from app.tools.tool_decorator import tool


def _grafana_source(sources: dict) -> dict:
    grafana = sources.get("grafana") or sources.get("grafana_local") or {}
    return grafana if isinstance(grafana, dict) else {}


def _dac_health_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _grafana_source(sources)
    return {
        "grafana_endpoint": grafana.get("grafana_endpoint") or grafana.get("endpoint"),
        "grafana_api_key": grafana.get("grafana_api_key") or grafana.get("api_key"),
        "grafana_username": grafana.get("username", ""),
        "grafana_password": grafana.get("password", ""),
    }


def _dac_health_available(sources: dict[str, dict]) -> bool:
    grafana = _grafana_source(sources)
    return bool(grafana.get("grafana_endpoint") or grafana.get("endpoint"))


@tool(
    name="dac_health_check",
    display_name="DAC pipeline health",
    source="grafana",
    evidence_type="metrics",
    side_effect_level="read_only",
    description="Check Cere DAC pipeline health (Collection→Aggregation→Inspection→Payout).",
    use_cases=[
        "Confirming the DAC pipeline is healthy end-to-end",
        "Checking whether activity records are still being produced",
        "Checking per-minute aggregation latency and on-time era reports (EHD)",
        "Checking inspector participation and settlement (payout) progress",
    ],
    outputs={
        "available": "Whether the check ran (false if Grafana is not configured)",
        "env": "The DAC network checked (testnet/mainnet/devnet)",
        "overall_status": "Worst status across dimensions: green | amber | red",
        "dimensions": (
            "Per-dimension list of {key, stage, label, value, unit, status, gate, help}; "
            "red gating dimensions also include failure_samples (raw error lines explaining why)"
        ),
    },
    input_schema={
        "type": "object",
        "properties": {
            "check": {
                "type": "string",
                "enum": [
                    "all",
                    "inflow",
                    "aggregation",
                    "era_report",
                    "inspector_participation",
                    "payout",
                ],
                "description": "Which dimension to check; 'all' runs every dimension.",
                "default": "all",
            },
            "env": {
                "type": "string",
                "enum": ["testnet", "mainnet", "devnet"],
                "description": "DAC network to check.",
                "default": "testnet",
            },
            "grafana_endpoint": {"type": "string"},
            "grafana_api_key": {"type": "string"},
            "grafana_username": {"type": "string"},
            "grafana_password": {"type": "string"},
        },
        "required": [],
    },
    is_available=_dac_health_available,
    extract_params=_dac_health_extract_params,
)
def dac_health_check(
    check: str = "all",
    env: str = "testnet",
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    **_kwargs: Any,
) -> dict:
    """Run the selected DAC health probe(s) and return a per-dimension snapshot."""
    if not grafana_endpoint:
        return {
            "source": "dac_health",
            "available": False,
            "error": "Grafana is not configured; cannot read DAC signals.",
        }

    client = get_grafana_client_from_credentials(
        endpoint=grafana_endpoint,
        api_key=grafana_api_key or "",
        username=grafana_username,
        password=grafana_password,
    )

    probes = build_probes(env)
    if check != "all":
        keys = CHECK_GROUPS.get(check)
        if not keys:
            return {
                "source": "dac_health",
                "available": False,
                "error": f"Unknown check '{check}'. Use one of: all, {', '.join(CHECK_GROUPS)}.",
            }
        probes = [p for p in probes if p.key in keys]

    dimensions = [evaluate_probe(client, probe) for probe in probes]
    # When a gating dimension is red, attach the matching error lines so the verdict
    # explains *why* the stage failed (e.g. "FailedToFetchProcessedEras") instead of
    # leaving the agent to chase it down with a separate log query.
    for dim in dimensions:
        if dim["status"] == Status.RED.value and dim.get("gate", True):
            samples = failure_samples(client, env, dim["key"])
            if samples:
                dim["failure_samples"] = samples
    # The verdict is driven only by gating dimensions; context-only probes
    # (gate=False, e.g. the noisy EHD build-event counter) are reported but do
    # not flip the overall status.
    overall = worst([Status(d["status"]) for d in dimensions if d.get("gate", True)])
    return {
        "source": "dac_health",
        "available": True,
        "env": env,
        "check": check,
        "overall_status": overall.value,
        "dimensions": dimensions,
    }


__all__ = ["dac_health_check"]
