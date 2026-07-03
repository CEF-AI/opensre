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


def _confidence(validity: Any) -> str:
    try:
        value = float(validity)
    except (TypeError, ValueError):
        return "unknown"
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    return "low"


def _claims(items: Any) -> list[str]:
    texts: list[str] = []
    for item in items or []:
        text = (
            str(item.get("claim") or item.get("text") or "").strip()
            if isinstance(item, dict)
            else str(item).strip()
        )
        if text:
            texts.append(text)
    return texts


def _is_pass(category: str, findings: list[str], root_cause: str) -> bool:
    if category.lower() in _HEALTHY_CATEGORIES:
        return True
    if any(phrase in root_cause.lower() for phrase in _PASS_PHRASES):
        return True
    return not findings and category.lower() in ("", "unknown")


def format_cef_qa_telegram(result: dict[str, Any], *, subtitle: str = "", footer: str = "") -> str:
    """Render a CEF QA investigation result as a compact Telegram report."""
    root_cause = str(result.get("root_cause") or "").strip()
    category = str(result.get("root_cause_category") or "")
    findings = _claims(result.get("validated_claims"))
    caveats = _claims(result.get("non_validated_claims"))
    actions = [
        str(a).strip()
        for a in (
            result.get("investigation_recommendations") or result.get("remediation_steps") or []
        )
        if str(a).strip()
    ]

    passed = _is_pass(category, findings, root_cause)
    header = "🟢  hiring-coach QA · PASS" if passed else "🔴  hiring-coach QA · NO-GO"

    meta = [
        part
        for part in (subtitle, f"confidence: {_confidence(result.get('validity_score'))}")
        if part
    ]
    lines: list[str] = [header, _DIVIDER, "  ·  ".join(meta)]
    if root_cause:
        lines += ["", root_cause]
    if findings:
        lines += ["", "FINDINGS", *[f"  • {f}" for f in findings]]
    if actions:
        lines += ["", "DO", *[f"  {i}  {a}" for i, a in enumerate(actions, 1)]]
    if caveats:
        lines += ["", "NOT VERIFIED", *[f"  • {c}" for c in caveats]]
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
