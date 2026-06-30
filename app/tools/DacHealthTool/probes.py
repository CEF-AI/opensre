"""DAC pipeline health probes — the queries + thresholds behind the tool.

Each probe owns its query (so the model never authors PromQL/LogQL) plus the
threshold logic that turns a value into a green/amber/red status. Calibrated
against live testnet: inflow ~110/min, TCA p95 ~0.01s, EHD avg ~20.6s (≈ the
20s PHD-fetch deadline, so ~20s is normal), ~1 EHD/era, 6 inspectors, payout alive.

Prometheus probes run as instant PromQL (full expressions pass through verbatim).
Loki probes use the existing log-line query and count client-side — the DAC
markers are low-volume over a 70-minute window, so no metric LogQL is needed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

_LOKI_WINDOW_MINUTES = 70
_LOKI_LIMIT = 500


class Status(StrEnum):
    GREEN = "green"
    AMBER = "amber"
    RED = "red"
    UNKNOWN = "unknown"


_SEVERITY = {Status.GREEN: 0, Status.UNKNOWN: 0, Status.AMBER: 1, Status.RED: 2}


def worst(statuses: list[Status]) -> Status:
    """Most severe status; GREEN if empty or all-unknown."""
    ranked = [s for s in statuses if s != Status.UNKNOWN]
    return max(ranked, key=lambda s: _SEVERITY[s]) if ranked else Status.GREEN


@dataclass(frozen=True)
class Probe:
    """One DAC health signal: a query plus how to classify its scalar value."""

    key: str
    stage: str
    label: str
    source: str  # "prometheus" | "loki"
    query: str
    unit: str
    higher_is_better: bool
    green_at: float
    amber_at: float
    help: str
    none_is_red: bool = True
    loki_count: str = ""  # "distinct_host" | "occurrences" (loki probes only)
    gate: bool = True  # False => shown for context but does not affect the verdict

    def classify(self, value: float | None) -> Status:
        if value is None:
            return Status.RED if self.none_is_red else Status.UNKNOWN
        if self.higher_is_better:
            if value >= self.green_at:
                return Status.GREEN
            return Status.AMBER if value >= self.amber_at else Status.RED
        if value <= self.green_at:
            return Status.GREEN
        return Status.AMBER if value <= self.amber_at else Status.RED


# check name -> probe keys it selects
CHECK_GROUPS: dict[str, tuple[str, ...]] = {
    "inflow": ("inflow",),
    "aggregation": ("tca_latency",),
    "era_report": ("ehd_built", "ehd_build_applied", "ehd_produced", "ehd_ontime"),
    "inspector_participation": ("inspection_verified", "inspectors"),
    "payout": ("payout",),
}


# Expected number of participating validators per network (calibrated live).
_EXPECTED_INSPECTORS: dict[str, int] = {"testnet": 6, "devnet": 4, "mainnet": 6}

# DAC storage nodes (service_name="storage-N") are the only place EHDs are actually
# *built*, but their Loki streams carry no `env` label — they are scoped by the DAC
# cluster id instead. This maps each network to its cluster id so we can read the
# build-side EHD signal directly (verified live 2026-06-29).
_CLUSTER_IDS: dict[str, str] = {
    "testnet": "825c4b2352850de9986d9d28568db6f0c023a1e3",
    "devnet": "7f82864e4f097e63d04cc279e4d8d2eb45a42ffa",
    "mainnet": "0059f5ada35eee46802d80750d5ca4a490640511",
}


def build_probes(
    env: str,
    *,
    expected_inspectors: int | None = None,
    tca_budget_seconds: int = 60,
    ehd_fetch_deadline_seconds: int = 20,
) -> list[Probe]:
    """Construct the env-templated probe set from the timing budgets.

    ``expected_inspectors`` defaults to a per-env value (some networks run fewer
    validators). Prometheus probes use ``none_is_red=False``: a network may simply
    not export the ``ddc_dac_*`` metrics (e.g. devnet), and "no metric" is reported
    as *unknown*, not a false failure. A genuine stall where the exporter stays up
    still reads as ``0`` and trips the threshold.
    """
    inspectors_expected = (
        expected_inspectors if expected_inspectors is not None else _EXPECTED_INSPECTORS.get(env, 6)
    )
    probes = [
        Probe(
            key="inflow",
            stage="Collection",
            label="Activity-record inflow",
            source="prometheus",
            query=f'sum(rate(ddc_dac_tca_records_processed_total{{job="{env}"}}[5m]))*60',
            unit="records/min",
            higher_is_better=True,
            green_at=10.0,
            amber_at=1.0,
            none_is_red=False,
            help=(
                "Records/min being produced; ~0 means Collection is dark. "
                "No data => metric not exported for this network (unknown, not a failure)."
            ),
        ),
        Probe(
            key="tca_latency",
            stage="Aggregation",
            label="Per-minute aggregation latency (p95)",
            source="prometheus",
            query=(
                "histogram_quantile(0.95, sum by (le)(rate("
                f'ddc_dac_tca_node_build_duration_seconds_bucket{{job="{env}"}}[15m])))'
            ),
            unit="s",
            higher_is_better=False,
            green_at=tca_budget_seconds * 0.5,
            amber_at=tca_budget_seconds * 0.9,
            none_is_red=False,
            help=f"TCA p95 build time vs the ~{tca_budget_seconds}s per-minute budget.",
        ),
        Probe(
            key="ehd_built",
            stage="Era report",
            label="Era reports (EHD) built & retrievable",
            source="loki",
            # Authoritative EHD signal: the runtime logs this once it has successfully
            # fetched an era's EHD root (ddc-payouts), which only exists if the DAC nodes
            # actually BUILT the Era Historical Document for that era. >0 => EHDs are being
            # produced; 0 => the aggregation/era-report stage is dark (the broken-devnet case,
            # where TCA/PHD/EHD logs are all absent), independent of inspection or payout.
            query=f'{{env="{env}"}} |= "Finish fetching EHD root for era"',
            unit="eras/70m",
            higher_is_better=True,
            green_at=1.0,
            amber_at=1.0,  # binary: >=1 EHD built+fetchable => producing, 0 => stalled
            loki_count="occurrences",
            help=(
                "Era Historical Documents built and retrievable in 70m (EHD root fetched by "
                "ddc-payouts). THE era-report success signal: 0 means EHDs are not being "
                "produced at all (aggregation dark), separate from whether inspection/payout work."
            ),
        ),
        Probe(
            key="ehd_produced",
            stage="Era report",
            label="Era reports (EHD) build events (context)",
            source="prometheus",
            query=f'sum(increase(ddc_dac_ehd_build_duration_seconds_count{{job="{env}"}}[2h]))',
            unit="builds/2h",
            higher_is_better=True,
            green_at=1.5,
            amber_at=1.0,
            gate=False,
            none_is_red=False,
            help=(
                "EHD build events seen over 2h. Context only, NOT a gate: this metric "
                "is per-node (only the grouping collector that builds an EHD increments "
                "it), so scrape/window timing can read low even when eras are closing "
                "and being inspected normally. A real aggregation stall surfaces via the "
                "Inspection and Payout dimensions (no EHDs -> nothing to inspect or pay)."
            ),
        ),
        Probe(
            key="ehd_ontime",
            stage="Era report",
            label="Era report build time",
            source="prometheus",
            query=(
                f'sum(rate(ddc_dac_ehd_build_duration_seconds_sum{{job="{env}"}}[1h]))'
                f'/sum(rate(ddc_dac_ehd_build_duration_seconds_count{{job="{env}"}}[1h]))'
            ),
            unit="s",
            higher_is_better=False,
            green_at=ehd_fetch_deadline_seconds * 1.5,
            amber_at=ehd_fetch_deadline_seconds * 2.5,
            none_is_red=False,
            help=f"EHD build time vs the {ehd_fetch_deadline_seconds}s peer-PHD fetch deadline.",
        ),
        Probe(
            key="inspection_verified",
            stage="Inspection",
            label="Inspection receipts verified",
            source="loki",
            # The authoritative success signal: ddc-payouts logs this once per era when an
            # era's inspection receipt passes signature + state-hash verification. >0 proves
            # inspection actually COMPLETED (not just that validators started it). Validators
            # can "start" inspection yet fail every attempt (e.g. AssignmentsError/ClusterApiError),
            # producing 0 verified receipts — which is exactly the broken-devnet case.
            query=f'{{env="{env}"}} |= "Inspection receipt verified"',
            unit="receipts/70m",
            higher_is_better=True,
            green_at=1.0,
            amber_at=1.0,  # binary: >=1 era inspected+verified => working, 0 => broken
            loki_count="occurrences",
            help=(
                "Inspection receipts verified (ddc-payouts) in 70m. THE inspection-success "
                "signal: 0 means inspection is not completing (validators may be starting but "
                "every attempt fails, e.g. AssignmentsError(ClusterApiError))."
            ),
        ),
        Probe(
            key="inspectors",
            stage="Inspection",
            label="Inspector participation (attempts, context)",
            source="loki",
            query=f'{{env="{env}"}} |= "Starting OCW inspection processing"',
            unit="validators",
            higher_is_better=True,
            green_at=float(inspectors_expected),
            amber_at=float(max(inspectors_expected - 2, 1)),
            loki_count="distinct_host",
            gate=False,
            help=(
                f"Distinct validators ATTEMPTING inspection in 70m (expect {inspectors_expected}). "
                "Context only — starting is not succeeding; the verdict gates on "
                "'inspection_verified'."
            ),
        ),
        Probe(
            key="payout",
            stage="Payout",
            label="Settlement (payout steps completing)",
            source="loki",
            # Success signal: ddc-payouts logs this when it actually advances an era's
            # payout state machine (charge/reward/finalize). Like inspection, the bare
            # "payout_step" heartbeat fires even on idle "no era to pay" cycles
            # (charging_batches=0), so we gate on real progress, not the tick.
            query=f'{{env="{env}"}} |= "Successfully sent \'step_end"',
            unit="steps/70m",
            higher_is_better=True,
            green_at=1.0,
            amber_at=1.0,  # binary: >=1 payout step succeeded => settling, 0 => stalled
            loki_count="occurrences",
            help=(
                "Payout state-machine steps successfully completing in 70m (ddc-payouts). "
                "0 means settlement is not progressing — the bare payout OCW heartbeat "
                "('payout_step') fires even when there is nothing to pay, so it is not used here."
            ),
        ),
        Probe(
            key="payout_heartbeat",
            stage="Payout",
            label="Payout OCW heartbeat (context)",
            source="loki",
            query=f'{{env="{env}"}} |= "payout_step"',
            unit="ticks/70m",
            higher_is_better=True,
            green_at=1.0,
            amber_at=1.0,
            loki_count="occurrences",
            gate=False,
            help="Payout OCW ticks in 70m. Context only (alive vs down); not proof of payment.",
        ),
    ]
    # Purest EHD signal: the storage nodes (where EHDs are actually constructed) log
    # "[DAC] EHD ApplyEra" as they build each era's document — upstream of the runtime
    # fetching the EHD root. Their streams are scoped by ddc_cluster_id, not env, so it
    # is only available for networks we have a cluster id for. Context only (gate=False):
    # it is sparse (~1/era), so a quiet 70m window can legitimately read 0; the env-scoped
    # `ehd_built` fetch remains the gate. Together they corroborate (both 0 => aggregation
    # is genuinely dark, as on devnet; both >0 => EHDs are built and consumed).
    cluster_id = _CLUSTER_IDS.get(env)
    if cluster_id:
        probes.insert(
            3,
            Probe(
                key="ehd_build_applied",
                stage="Era report",
                label="EHD build events on DAC nodes (context)",
                source="loki",
                query=f'{{ddc_cluster_id="{cluster_id}"}} |= "EHD ApplyEra"',
                unit="builds/70m",
                higher_is_better=True,
                green_at=1.0,
                amber_at=1.0,
                loki_count="occurrences",
                gate=False,
                none_is_red=False,
                help=(
                    "Build-side EHD construction events on the DAC storage nodes in 70m "
                    "(scoped by cluster). Context only — sparse (~1/era), so 0 over a short "
                    "window is not proof of failure; corroborates the gating 'ehd_built' signal."
                ),
            ),
        )
    return probes


# When a gating dimension is RED, the tool pulls a few matching error lines so the
# verdict carries its own "why" instead of leaving the agent to re-derive it. Keyed by
# probe key -> LogQL line filter (env-templated). Only stages with a meaningful failure
# log are listed; Prometheus-valued stages (inflow/tca) speak through the value itself.
_FAILURE_QUERIES: dict[str, str] = {
    "ehd_built": '{{env="{env}"}} |~ `Fetching processed eras error|FailedToFetch|❌.*EHD`',
    "inspection_verified": '{{env="{env}"}} |~ `Inspection error|Fetching processed eras error`',
    "payout": '{{env="{env}"}} |~ `no era for payout|Skipping .*payout|payout.*[Ee]rror`',
}
_FAILURE_SAMPLE_LIMIT = 3


def failure_samples(client: Any, env: str, key: str) -> list[str]:
    """Up to a few raw error lines explaining why a red dimension failed (or [])."""
    template = _FAILURE_QUERIES.get(key)
    if not template:
        return []
    result = client.query_loki(
        template.format(env=env), time_range_minutes=_LOKI_WINDOW_MINUTES, limit=50
    )
    if not result.get("success"):
        return []
    seen: list[str] = []
    for entry in result.get("logs") or []:
        message = (entry.get("message") or entry.get("line") or "").strip()
        if message and message not in seen:
            seen.append(message[:300])
        if len(seen) >= _FAILURE_SAMPLE_LIMIT:
            break
    return seen


def _prom_scalar(result: dict[str, Any]) -> float | None:
    if not result.get("success"):
        return None
    metrics = result.get("metrics") or []
    if not metrics:
        return None
    value = metrics[0].get("value") or []
    if len(value) < 2:
        return None
    try:
        scalar = float(value[1])
    except (TypeError, ValueError):
        return None
    return None if math.isnan(scalar) else scalar


def _loki_value(client: Any, probe: Probe) -> float | None:
    result = client.query_loki(
        probe.query, time_range_minutes=_LOKI_WINDOW_MINUTES, limit=_LOKI_LIMIT
    )
    if not result.get("success"):
        return None
    logs = result.get("logs") or []
    if probe.loki_count == "distinct_host":
        return float(len({entry.get("labels", {}).get("host", "") for entry in logs} - {""}))
    return float(len(logs))


def evaluate_probe(client: Any, probe: Probe) -> dict[str, Any]:
    """Run one probe against the Grafana client and return a structured result."""
    value = (
        _prom_scalar(client.query_mimir(probe.query))
        if probe.source == "prometheus"
        else _loki_value(client, probe)
    )
    return {
        "key": probe.key,
        "stage": probe.stage,
        "label": probe.label,
        "value": round(value, 3) if value is not None else None,
        "unit": probe.unit,
        "status": probe.classify(value).value,
        "gate": probe.gate,
        "help": probe.help,
    }


__all__ = [
    "CHECK_GROUPS",
    "Probe",
    "Status",
    "build_probes",
    "evaluate_probe",
    "failure_samples",
    "worst",
]
