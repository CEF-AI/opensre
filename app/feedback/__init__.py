"""Closed-loop learning: capture investigation misses and convert them into evals.

The feedback prompt in :mod:`app.cli.support.feedback` collects an accuracy
rating after every investigation. When a user marks a result as ``partial`` or
``inaccurate`` we additionally classify the failure into a triage taxonomy
(see :class:`~app.feedback.misses.MissTaxonomy`) and persist it to a separate
``misses.jsonl`` store. The :mod:`app.cli.commands.misses` command group reads
that store to surface trends, recurrence, and to export reproducible benchmark
scenarios that close the production-to-eval loop.
"""

from __future__ import annotations

from app.feedback.misses import (
    MissRecord,
    MissTaxonomy,
    compute_recurrence,
    compute_stats,
    load_misses,
    misses_path,
    record_miss,
    taxonomy_choices,
    to_benchmark_scenario,
)

__all__ = [
    "MissRecord",
    "MissTaxonomy",
    "compute_recurrence",
    "compute_stats",
    "load_misses",
    "misses_path",
    "record_miss",
    "taxonomy_choices",
    "to_benchmark_scenario",
]
