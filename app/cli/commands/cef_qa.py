"""``opensre cef-qa`` — the CLI door onto the shared CEF QA core.

Same request schema and same :func:`run_cef_qa` as the ``/investigate`` microservice, so a QA run
behaves identically whether it is fired from the CLI or the hosted API. Creds come from the input
JSON here (rather than the request body), which keeps the two surfaces in lockstep.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click


@click.command(name="cef-qa")
@click.option(
    "--input",
    "-i",
    "input_path",
    default=None,
    type=click.Path(),
    help="Path to a CefQaRequest JSON file. Use '-' to read from stdin.",
)
@click.option("--input-json", default=None, help="Inline CefQaRequest JSON string.")
@click.option(
    "--output", "-o", default=None, type=click.Path(), help="Write the full result JSON here."
)
def cef_qa_command(input_path: str | None, input_json: str | None, output: str | None) -> None:
    """Run a hiring-coach QA investigation from a CefQaRequest (verdict + beautified report)."""
    from app.services.cef.qa import CefQaRequest, run_cef_qa

    if input_json:
        raw = input_json
    elif input_path == "-":
        raw = sys.stdin.read()
    elif input_path:
        raw = Path(input_path).read_text(encoding="utf-8")
    else:
        raise click.UsageError("Provide --input-json or --input (a CefQaRequest JSON).")

    request = CefQaRequest.model_validate(json.loads(raw))
    result = run_cef_qa(request)

    click.echo(result.report)
    click.echo(f"\nverdict: {result.verdict}  ·  confidence: {result.confidence}")
    if result.delivery_error:
        click.echo(f"delivery error: {result.delivery_error}", err=True)
    if output:
        Path(output).write_text(result.model_dump_json(indent=2), encoding="utf-8")
