"""Shared CEF QA entrypoint — one core for both the CLI and the microservice.

Both surfaces build the same request, call :func:`run_cef_qa`, and get the same verdict. The only
difference is where credentials come from: the CLI resolves them from env, the microservice takes
them per request (so it is multi-tenant and never needs a wallet on the server config). Under the
hood this just assembles ``resolved_integrations`` and calls the normal ``run_investigation`` — no
forked investigation logic.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.integrations.catalog import classify_integrations
from app.services.cef.report import (
    _confidence,
    _validity,
    cef_verdict,
    format_cef_qa_telegram,
    send_cef_qa_report,
)


class CefCreds(BaseModel):
    """Signed vault-api access for one tenant's CEF agent. Provide wallet_json OR wallet_path."""

    vault_base_url: str
    vault_id: str
    agent_id: str = ""
    # In-memory keyring JSON (preferred for the microservice; key never touches disk).
    wallet_json: str = ""
    wallet_path: str = ""
    wallet_password: str = ""
    cluster: str = "dragon1-testnet"


class GrafanaCreds(BaseModel):
    """Grafana/Loki access for component + inference logs (optional but recommended)."""

    endpoint: str
    api_key: str = ""


class TelegramTarget(BaseModel):
    """Where to post the beautified report, if the caller wants a channel post."""

    bot_token: str
    chat_id: str


class CefQaRequest(BaseModel):
    """A single QA request: which run to check, whose vault, and where to report."""

    conversation_id: str
    clip: str = ""
    variant: str = ""
    cluster: str = ""
    model: str = ""
    agent_models: str = ""
    severity: str = "high"
    description: str = ""
    cef: CefCreds
    grafana: GrafanaCreds | None = None
    deliver_telegram: TelegramTarget | None = None


class CefQaResult(BaseModel):
    """The QA outcome — same shape regardless of which door produced it."""

    verdict: str  # pass | no_go | needs_review
    root_cause_category: str = ""
    validity_score: float | None = None
    confidence: str = "unknown"  # high | medium | low | unknown
    root_cause: str = ""
    findings: list[str] = Field(default_factory=list)
    not_verified: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    report: str = ""  # the beautified plain-text report
    delivered: bool = False
    delivery_error: str = ""


def build_cef_resolved_integrations(
    cef: CefCreds, grafana: GrafanaCreds | None = None
) -> dict[str, Any]:
    """Turn per-request creds into a ``resolved_integrations`` dict via the normal classifier.

    Reuses :func:`classify_integrations` so the shape is identical to the env/store path — the
    tools cannot tell whether creds came from env or a request.
    """
    records: list[dict[str, Any]] = [
        {
            "id": "request-cef",
            "service": "cef",
            "status": "active",
            "credentials": {
                "vault_base_url": cef.vault_base_url,
                "vault_id": cef.vault_id,
                "agent_id": cef.agent_id,
                "wallet_json": cef.wallet_json,
                "wallet_path": cef.wallet_path,
                "wallet_password": cef.wallet_password,
                "cluster": cef.cluster or "dragon1-testnet",
            },
        }
    ]
    if grafana and grafana.endpoint:
        records.append(
            {
                "id": "request-grafana",
                "service": "grafana",
                "status": "active",
                "credentials": {"endpoint": grafana.endpoint, "api_key": grafana.api_key},
            }
        )
    return classify_integrations(records)


def _build_alert(req: CefQaRequest) -> dict[str, Any]:
    """Assemble the CEF investigation alert (alert_source=cef routes the beautified report)."""
    annotations = {
        "summary": "E2E execution QA of a hiring-coach run.",
        "description": req.description
        or (
            f"QA of hiring-coach run conversation_id {req.conversation_id}. Call get_cef_guidance "
            "topic investigation_procedure. Verify the run's own activities via cef_agent_logs, "
            "then sweep components with cef_component_logs scoped to this run's window."
        ),
        "context_sources": "cef,grafana" if req.grafana else "cef",
        "conversation_id": req.conversation_id,
    }
    for key in ("variant", "clip", "cluster", "model", "agent_models"):
        value = getattr(req, key)
        if value:
            annotations[key] = value
    return {
        "alert_name": "CEF hiring-coach QA (execution)",
        "severity": req.severity,
        "alert_source": "cef",
        "commonAnnotations": annotations,
    }


def _subtitle_footer(req: CefQaRequest) -> tuple[str, str]:
    subtitle = " · ".join(v for v in (req.variant, req.clip, req.cluster, req.model) if v)
    footer = f"conv {req.conversation_id}" if req.conversation_id else ""
    return subtitle, footer


def run_cef_qa(req: CefQaRequest) -> CefQaResult:
    """Run one CEF QA investigation and return the verdict. The shared core for CLI + microservice."""
    from app.agent.stages.publish_findings.context.build import build_report_context
    from app.pipeline.runners import run_investigation

    resolved = build_cef_resolved_integrations(req.cef, req.grafana)
    state = run_investigation(_build_alert(req), resolved_integrations=resolved)

    ctx: dict[str, Any] = dict(build_report_context(state))  # enriches claims with [E#] citations
    subtitle, footer = _subtitle_footer(req)
    report_text = format_cef_qa_telegram(ctx, subtitle=subtitle, footer=footer)

    def _claim_texts(items: Any) -> list[str]:
        out: list[str] = []
        for item in items or []:
            text = str(item.get("claim") or "").strip() if isinstance(item, dict) else str(item)
            if text:
                out.append(text)
        return out

    result = CefQaResult(
        verdict=cef_verdict(ctx),
        root_cause_category=str(ctx.get("root_cause_category") or ""),
        validity_score=_validity(ctx.get("validity_score")),
        confidence=_confidence(ctx.get("validity_score")),
        root_cause=str(ctx.get("root_cause") or ""),
        findings=_claim_texts(ctx.get("validated_claims")),
        not_verified=_claim_texts(ctx.get("non_validated_claims")),
        actions=[
            str(a)
            for a in (ctx.get("investigation_recommendations") or ctx.get("remediation_steps") or [])
            if str(a).strip()
        ],
        report=report_text,
    )

    if req.deliver_telegram:
        ok, err = send_cef_qa_report(
            ctx,
            bot_token=req.deliver_telegram.bot_token,
            chat_id=req.deliver_telegram.chat_id,
            subtitle=subtitle,
            footer=footer,
        )
        result.delivered = ok
        result.delivery_error = err

    return result


__all__ = [
    "CefCreds",
    "CefQaRequest",
    "CefQaResult",
    "GrafanaCreds",
    "TelegramTarget",
    "build_cef_resolved_integrations",
    "run_cef_qa",
]
