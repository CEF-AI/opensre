"""CEF component/infra log retrieval tool.

Retrieval only: fetches CEF platform-component logs (orchestrator, s3-gateway, agent-runtime,
vault-api, nats, cef-core) from Grafana Loki. The tool owns the CEF log topology (namespace /
cluster / service_name — which is *not* uniform across components) so the agent never authors
LogQL; it returns raw lines for the agent to analyse. Complements ``cef_agent_logs`` (the
agent's own ctx.log). This is where the infra cause of a run failure lives — e.g. the
orchestrator's "all inference nodes failed for model ..." or the s3-gateway's audio 4xx/5xx.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.services.grafana import get_grafana_client_from_credentials
from app.tools._telemetry import report_run_error
from app.tools.tool_decorator import tool

# Each CEF component maps to its own Loki namespace; cluster is env-scoped (dragon1-testnet/…).
_NAMESPACE_BY_SERVICE: dict[str, str] = {
    "orchestrator": "cef-system",
    "agent-runtime": "cef-system",
    "vault-api": "cef-system",
    "nats": "cef-system",
    "cef-core": "cef-system",
    "ddc-s3-gateway": "ddc",
}
_DEFAULT_CLUSTER = "dragon1-testnet"
_DEFAULT_LIMIT = 100

CefService = Literal[
    "orchestrator", "agent-runtime", "vault-api", "nats", "cef-core", "ddc-s3-gateway"
]


class CefComponentLogsInput(BaseModel):
    service: CefService = Field(description="CEF component to read logs from.")
    time_range_minutes: int = Field(
        default=60, description="Lookback window in minutes (from now)."
    )
    contains: str | None = Field(
        default=None,
        description="Literal substring the log line must contain (e.g. 'inference failed').",
    )
    level: str | None = Field(
        default=None, description="Log level to match, e.g. 'error' or 'warning'."
    )
    limit: int = Field(default=_DEFAULT_LIMIT, description="Max log lines to return.")


class CefComponentLogsOutput(BaseModel):
    source: str = Field(description="Evidence source label.")
    available: bool = Field(description="Whether Grafana is configured and the query ran.")
    service: str | None = Field(default=None, description="Component queried.")
    query: str | None = Field(default=None, description="The LogQL selector that was run.")
    count: int = Field(default=0, description="Number of log lines returned.")
    truncated: bool = Field(default=False, description="True if results hit the limit.")
    logs: list[dict[str, Any]] = Field(default_factory=list, description="Raw log lines.")
    error: str | None = Field(default=None, description="Error details when unavailable.")


def _cef_grafana_source(sources: dict[str, dict]) -> dict:
    grafana = sources.get("grafana") or sources.get("grafana_local") or {}
    return grafana if isinstance(grafana, dict) else {}


def _cef_logs_is_available(sources: dict[str, dict]) -> bool:
    grafana = _cef_grafana_source(sources)
    return bool(grafana.get("grafana_endpoint") or grafana.get("endpoint"))


def _cef_logs_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    grafana = _cef_grafana_source(sources)
    cef = sources.get("cef") or {}
    return {
        "grafana_endpoint": grafana.get("grafana_endpoint") or grafana.get("endpoint"),
        "grafana_api_key": grafana.get("grafana_api_key") or grafana.get("api_key"),
        "grafana_username": grafana.get("username", ""),
        "grafana_password": grafana.get("password", ""),
        "cluster": cef.get("cluster") or _DEFAULT_CLUSTER,
    }


def _build_query(service: str, cluster: str, contains: str | None, level: str | None) -> str:
    namespace = _NAMESPACE_BY_SERVICE[service]
    query = f'{{namespace="{namespace}", cluster="{cluster}", service_name="{service}"}}'
    if contains:
        query += f" |= `{contains}`"  # literal line-contains (non-regex)
    if level:
        # Match the level token across formats: logfmt `level=error` and JSON `"level":"error"`.
        query += f'|~ `(?i)level"?[=:] ?"?{level}`'
    return query


@tool(
    name="cef_component_logs",
    display_name="CEF component logs",
    source="cef",
    source_id="cef_grafana_loki",
    evidence_type="logs",
    side_effect_level="read_only",
    description=(
        "Retrieve CEF platform-component logs (orchestrator, s3-gateway, agent-runtime, "
        "vault-api, nats, cef-core) from Grafana Loki. Retrieval only — returns raw lines "
        "for the agent to analyse. The tool owns the CEF log topology (namespace/cluster)."
    ),
    use_cases=[
        "Finding why inference failed (orchestrator 'all inference nodes failed for model ...')",
        "Checking audio-fetch failures on the s3-gateway (4xx/5xx)",
        "Looking for isolate/runtime errors in the launch time window",
    ],
    requires=[],
    examples=[
        "Read orchestrator logs containing 'inference failed' over the run's time window.",
        "Read ddc-s3-gateway logs filtered to the audio path around the failure.",
    ],
    anti_examples=[
        "Do not use for the agent's own ctx.log — use cef_agent_logs (it is per-run).",
    ],
    input_model=CefComponentLogsInput,
    output_model=CefComponentLogsOutput,
    injected_params=(
        "grafana_endpoint",
        "grafana_api_key",
        "grafana_username",
        "grafana_password",
        "cluster",
    ),
    is_available=_cef_logs_is_available,
    extract_params=_cef_logs_extract_params,
)
def cef_component_logs(
    service: str,
    time_range_minutes: int = 60,
    contains: str | None = None,
    level: str | None = None,
    limit: int = _DEFAULT_LIMIT,
    grafana_endpoint: str | None = None,
    grafana_api_key: str | None = None,
    grafana_username: str = "",
    grafana_password: str = "",
    cluster: str = _DEFAULT_CLUSTER,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Retrieve CEF component logs from Loki. Pure retrieval; the agent does the analysis."""
    if not grafana_endpoint:
        return {"source": "cef", "available": False, "error": "Grafana is not configured."}
    if service not in _NAMESPACE_BY_SERVICE:
        return {"source": "cef", "available": False, "error": f"Unknown CEF service '{service}'."}

    query = _build_query(service, cluster, contains, level)
    try:
        client = get_grafana_client_from_credentials(
            endpoint=grafana_endpoint,
            api_key=grafana_api_key or "",
            username=grafana_username,
            password=grafana_password,
        )
        result = client.query_loki(query, time_range_minutes=time_range_minutes, limit=limit)
    except Exception as exc:  # noqa: BLE001 - any query failure → report + unavailable
        report_run_error(
            exc,
            tool_name="cef_component_logs",
            source="cef",
            component="app.tools.CefComponentLogsTool.cef_component_logs",
            method="query_loki",
        )
        return {"source": "cef", "available": False, "service": service, "error": str(exc)}

    if not result.get("success"):
        return {
            "source": "cef",
            "available": False,
            "service": service,
            "query": query,
            "error": result.get("error"),
        }
    logs = result.get("logs") or []
    return {
        "source": "cef",
        "available": True,
        "service": service,
        "query": query,
        "count": len(logs),
        "truncated": len(logs) >= limit,
        "logs": logs,
    }


__all__ = ["cef_component_logs"]
