"""Beautified CEF QA report rendering + Telegram delivery.

Turns an investigation result (root cause + findings + actions + caveats) into a compact,
scannable message and posts it via the shared Telegram helper. Deterministic layout — the
agent supplies the narrative, this owns the presentation.
"""

from __future__ import annotations

from typing import Any

from app.utils.telegram_delivery import post_telegram_message

_DIVIDER = "─" * 36
_HEALTHY_CATEGORIES = {"healthy", "no_issue"}
_PASS_PHRASES = ("no failure", "no regression", "completed successfully", "no issue")

# Below this validity_score the verdict is treated as provisional and gated to NEEDS REVIEW,
# mirroring OpenSRE's category_alignment precedent (lower confidence -> tell a human to review
# before acting). 0.5 is the native medium/low boundary (see app/services/cef report confidence
# buckets and app/agent/category_alignment.py).
_REVIEW_THRESHOLD = 0.5


def _validity(validity: Any) -> float | None:
    try:
        return float(validity)
    except (TypeError, ValueError):
        return None


def _confidence(validity: Any) -> str:
    value = _validity(validity)
    if value is None:
        return "unknown"
    if value >= 0.8:
        return "high"
    if value >= _REVIEW_THRESHOLD:
        return "medium"
    return "low"


def _confidence_label(validity: Any) -> str:
    """Native-style confidence line: a percentage (OpenSRE surfaces ``{validity_score:.0%}``)."""
    value = _validity(validity)
    if value is None:
        return "confidence: unknown"
    return f"confidence: {value:.0%} ({_confidence(validity)})"


def _claim_text(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("claim") or item.get("text") or "").strip()
    return str(item).strip()


def _claims(items: Any) -> list[str]:
    return [text for item in items or [] if (text := _claim_text(item))]


def _citation(item: Any, catalog: dict[str, Any]) -> str:
    """Render evidence citations for a validated claim as ``[E1, E2]`` (native provenance style)."""
    if not isinstance(item, dict):
        return ""
    refs = [
        str((catalog.get(eid) or {}).get("display_id", eid))
        for eid in item.get("evidence_ids") or []
    ]
    if not refs:
        refs = [str(label) for label in item.get("evidence_labels") or []]
    return f"  [{', '.join(refs)}]" if refs else ""


def _cited_findings(items: Any, catalog: dict[str, Any]) -> list[str]:
    findings: list[str] = []
    for item in items or []:
        text = _claim_text(item)
        if text:
            findings.append(f"{text}{_citation(item, catalog)}")
    return findings


def _is_pass(category: str, findings: list[str], root_cause: str) -> bool:
    if category.lower() in _HEALTHY_CATEGORIES:
        return True
    if any(phrase in root_cause.lower() for phrase in _PASS_PHRASES):
        return True
    return not findings and category.lower() in ("", "unknown")


def format_cef_qa_telegram(result: dict[str, Any], *, subtitle: str = "", footer: str = "") -> str:
    """Render a CEF QA investigation result as a compact, confidence-first Telegram report.

    Verdict precedence follows OpenSRE: the LLM decides PASS/NO-GO, and confidence
    (``validity_score``) gates it — a low-confidence verdict is reported as NEEDS REVIEW with the
    provisional call shown, mirroring ``category_alignment`` (lower confidence -> tell a human to
    review before acting). Findings carry ``[E#]`` evidence citations, and validated findings are
    kept separate from non-validated (inferred) claims, as in the native RCA report.
    """
    catalog = result.get("evidence_catalog") or {}
    root_cause = str(result.get("root_cause") or "").strip()
    category = str(result.get("root_cause_category") or "")
    findings = _cited_findings(result.get("validated_claims"), catalog)
    caveats = _claims(result.get("non_validated_claims"))
    actions = [
        str(a).strip()
        for a in (
            result.get("investigation_recommendations") or result.get("remediation_steps") or []
        )
        if str(a).strip()
    ]

    passed = _is_pass(category, _claims(result.get("validated_claims")), root_cause)
    validity = _validity(result.get("validity_score"))
    needs_review = validity is not None and validity < _REVIEW_THRESHOLD

    if needs_review:
        header = "🟡  hiring-coach QA · NEEDS REVIEW"
    elif passed:
        header = "🟢  hiring-coach QA · PASS"
    else:
        header = "🔴  hiring-coach QA · NO-GO"

    meta = [part for part in (subtitle, _confidence_label(result.get("validity_score"))) if part]
    lines: list[str] = [header, _DIVIDER, "  ·  ".join(meta)]
    if needs_review:
        provisional = "PASS" if passed else "NO-GO"
        lines += [
            "",
            f"⚠ Low confidence — provisional verdict {provisional}. Review the evidence before acting.",
        ]
    if root_cause:
        lines += ["", root_cause]
    if findings:
        lines += ["", "FINDINGS", *[f"  • {f}" for f in findings]]
    if caveats:
        lines += ["", "NOT VERIFIED", *[f"  • {c}" for c in caveats]]
    if actions:
        lines += ["", "DO", *[f"  {i}  {a}" for i, a in enumerate(actions, 1)]]
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def send_cef_qa_report(
    result: dict[str, Any],
    *,
    bot_token: str,
    chat_id: str,
    subtitle: str = "",
    footer: str = "",
) -> tuple[bool, str]:
    """Format a CEF QA result and post it to Telegram. Returns (success, error)."""
    text = format_cef_qa_telegram(result, subtitle=subtitle, footer=footer)
    success, error, _message_id = post_telegram_message(chat_id, text, bot_token)
    return success, error


__all__ = ["format_cef_qa_telegram", "send_cef_qa_report"]
