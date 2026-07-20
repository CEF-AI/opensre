"""Correlate a QA-failure RCA against the PRs deployed in the same release.

Given a QA failure's root cause and the set of PRs that shipped in the deploy, ask the agent LLM to
rank which PR(s) most likely caused it (with reasoning). Reuses :func:`get_agent_llm` — no new LLM
plumbing. Best-effort: LLM or parse failures return an empty ranking with an ``error`` note rather
than raising, so a correlation hiccup never masks the underlying QA failure.
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, Field

_LIKELIHOODS = ("high", "medium", "low")


class PrSummary(BaseModel):
    """One deployed PR, as consumed by the correlator (shape of ``pr-context.ts`` output)."""

    url: str
    repo: str = ""  # "owner/repo"
    number: int | None = None
    title: str = ""
    body: str = ""
    author: str = ""
    files: list[str] = Field(default_factory=list)

    @property
    def ref(self) -> str:
        return f"{self.repo}#{self.number}" if self.repo and self.number else self.url


class Suspect(BaseModel):
    """A ranked culprit-PR candidate."""

    pr_url: str
    ref: str = ""
    likelihood: str = "low"  # high | medium | low
    reasoning: str = ""


class CorrelationResult(BaseModel):
    """The correlation outcome. ``error`` is set (non-fatal) if the LLM/parse step failed."""

    suspects: list[Suspect] = Field(default_factory=list)
    summary: str = ""
    error: str = ""


def prs_from_context(raw: Any) -> list[PrSummary]:
    """Adapt the ``pr-context.ts`` JSON (list of ``{owner,repo,number,url,title,body,author,files}``)
    into :class:`PrSummary` objects. Skips entries that failed to fetch (they carry an ``error``)."""
    items = raw if isinstance(raw, list) else []
    out: list[PrSummary] = []
    for it in items:
        if not isinstance(it, dict) or it.get("error"):
            continue
        owner = str(it.get("owner", "")).strip()
        repo = str(it.get("repo", "")).strip()
        out.append(
            PrSummary(
                url=str(it.get("url", "")),
                repo=f"{owner}/{repo}".strip("/"),
                number=it.get("number") if isinstance(it.get("number"), int) else None,
                title=str(it.get("title", "")),
                body=str(it.get("body", "")),
                author=str(it.get("author", "")),
                files=[str(f) for f in it.get("files", []) if isinstance(f, str)],
            )
        )
    return out


_SYSTEM = (
    "You are a release-QA regression correlator. You are given a QA failure's root-cause analysis "
    "(RCA) and the set of PRs that shipped in the deploy under test. Rank which PR(s) are the most "
    "likely cause of the failure, judging by how the PR's title, description and changed files relate "
    "to the failing area/component in the RCA. Be conservative: if the RCA points at an infrastructure "
    "or upstream/platform problem (e.g. missing inference nodes, a service outage) that no PR could "
    "cause, say so and return no suspects. Output STRICT JSON only, no prose, no code fences: "
    '{"summary": string, "suspects": [{"pr_url": string, "likelihood": "high"|"medium"|"low", '
    '"reasoning": string}]}. Include only PRs from the provided list; omit PRs that are not plausible.'
)


def _prompt(rca: str, prs: list[PrSummary], top: int) -> str:
    lines: list[str] = [
        f"## QA failure RCA\n{rca.strip() or '(no RCA text provided)'}",
        "",
        "## Deployed PRs",
    ]
    for p in prs:
        body = p.body.strip().replace("\r", "")
        body = (body[:600] + "…") if len(body) > 600 else body
        files = p.files[:40]
        lines.append(
            f"\n### {p.ref} — {p.title}\n"
            f"- url: {p.url}\n- author: {p.author}\n"
            f"- changed files ({len(p.files)}): {', '.join(files) or '(none listed)'}\n"
            f"- description: {body or '(empty)'}"
        )
    lines.append(f"\nReturn at most {top} suspects, most likely first.")
    return "\n".join(lines)


def _json_object(s: str) -> dict[str, Any]:
    loaded: Any = json.loads(s)
    if not isinstance(loaded, dict):
        raise json.JSONDecodeError("expected a JSON object", s, 0)
    return loaded


def _parse_json(text: str) -> dict[str, Any]:
    """Parse the model's JSON object, tolerating stray code fences / surrounding prose."""
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.IGNORECASE).strip()
    try:
        return _json_object(t)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", t, flags=re.DOTALL)
        if m:
            return _json_object(m.group(0))
        raise


def correlate_failure(rca: str, prs: list[PrSummary], *, top: int = 3) -> CorrelationResult:
    """Rank the deployed PRs by how likely each caused the QA failure described by ``rca``."""
    if not prs:
        return CorrelationResult(summary="No PRs supplied to correlate against.")

    by_url = {p.url: p for p in prs}
    try:
        from app.services.agent_llm_client import get_agent_llm

        llm = get_agent_llm()
        resp = llm.invoke([{"role": "user", "content": _prompt(rca, prs, top)}], system=_SYSTEM)
        data = _parse_json(resp.content or "")
    except Exception as exc:  # noqa: BLE001 - best-effort; never mask the underlying QA failure
        return CorrelationResult(error=f"correlation LLM/parse failed: {exc}", summary="")

    suspects: list[Suspect] = []
    for raw in data.get("suspects", []) or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("pr_url", "")).strip()
        pr = by_url.get(url)
        if pr is None:  # keep the model honest — only PRs we actually gave it
            continue
        lk = str(raw.get("likelihood", "low")).lower()
        suspects.append(
            Suspect(
                pr_url=url,
                ref=pr.ref,
                likelihood=lk if lk in _LIKELIHOODS else "low",
                reasoning=str(raw.get("reasoning", "")).strip(),
            )
        )
        if len(suspects) >= top:
            break
    return CorrelationResult(suspects=suspects, summary=str(data.get("summary", "")).strip())


def format_correlation_slack(result: CorrelationResult, *, report_url: str = "") -> str:
    """Render the correlation as a Slack-mrkdwn block for posting into the deploy thread."""
    emoji = {"high": "🔴", "medium": "🟠", "low": "🟡"}
    if result.error:
        return f"🧭 *PR correlation unavailable* — {result.error}"
    if not result.suspects:
        head = "🧭 *PR correlation:* no deployed PR is a likely cause"
        return f"{head}{(' — ' + result.summary) if result.summary else ' (looks upstream/infra).'}"
    lines = ["🧭 *Most likely culprit PR(s):*"]
    if result.summary:
        lines.append(f"_{result.summary}_")
    for s in result.suspects:
        lines.append(
            f"{emoji.get(s.likelihood, '🟡')} <{s.pr_url}|{s.ref or s.pr_url}> — *{s.likelihood}* · {s.reasoning}"
        )
    if report_url:
        lines.append(f"<{report_url}|QA run>")
    return "\n".join(lines)
