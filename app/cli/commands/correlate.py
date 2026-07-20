"""``opensre correlate`` — rank which deployed PR most likely caused a QA failure.

Thin CLI over :func:`app.services.cef.correlate.correlate_failure`. Reads a QA-failure RCA and the
PR context emitted by ``qa-agent/pr-context.ts`` (list of PRs with title/body/changed-files), asks
the agent LLM to rank the likely culprit PR(s), prints/writes the result, and can post a summary to
Slack (the deploy thread) via the shared Slack delivery.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


@click.command(name="correlate")
@click.option(
    "--rca", default=None, help="RCA / root-cause text, or '@path' to read it from a file."
)
@click.option(
    "--prs",
    "prs_path",
    required=True,
    type=click.Path(),
    help="Path to the pr-context.json emitted by pr-context.ts (or '-' for stdin).",
)
@click.option("--top", default=3, show_default=True, help="Max number of suspect PRs to return.")
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Write the full result JSON here."
)
@click.option("--report-url", default=None, help="QA run URL to link in the Slack summary.")
@click.option(
    "--slack", is_flag=True, help="Post the correlation summary to Slack (env-configured channel)."
)
def correlate_command(
    rca: str | None,
    prs_path: str,
    top: int,
    output: str | None,
    report_url: str | None,
    slack: bool,
) -> None:
    """Correlate a QA failure to the PRs deployed in the same release."""
    from app.services.cef.correlate import (
        correlate_failure,
        format_correlation_slack,
        prs_from_context,
    )

    rca_text = ""
    if rca and rca.startswith("@"):
        rca_text = Path(rca[1:]).read_text(encoding="utf-8")
    elif rca:
        rca_text = rca

    raw = sys.stdin.read() if prs_path == "-" else Path(prs_path).read_text(encoding="utf-8")
    prs = prs_from_context(json.loads(raw or "[]"))

    result = correlate_failure(rca_text, prs, top=top)

    if output:
        Path(output).write_text(result.model_dump_json(indent=2), encoding="utf-8")

    summary = format_correlation_slack(result, report_url=report_url or "")
    click.echo(summary)

    if slack:
        from app.utils.slack_delivery import send_slack_report

        ok, err = send_slack_report(summary)
        if not ok:
            click.echo(f"[correlate] Slack delivery skipped/failed: {err}", err=True)
