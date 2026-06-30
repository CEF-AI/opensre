"""Tests for the DAC pipeline health tool."""

from __future__ import annotations

from typing import Any

import pytest

from app.tools import DacHealthTool as tool_mod
from app.tools.DacHealthTool import dac_health_check
from app.tools.DacHealthTool.probes import (
    Status,
    build_probes,
    evaluate_probe,
    worst,
)


class FakeGrafana:
    """Stub Grafana client: PromQL scalars + Loki log lines keyed by substring."""

    def __init__(self, scalars: dict[str, float], loki: dict[str, list[dict[str, Any]]]):
        self._scalars = scalars
        self._loki = loki

    def query_mimir(self, query: str) -> dict[str, Any]:
        for needle, value in self._scalars.items():
            if needle in query:
                return {"success": True, "metrics": [{"metric": {}, "value": [0, str(value)]}]}
        return {"success": True, "metrics": []}

    def query_loki(self, query: str, **_kwargs: Any) -> dict[str, Any]:
        for needle, logs in self._loki.items():
            if needle in query:
                return {"success": True, "logs": logs}
        return {"success": True, "logs": []}


_HEALTHY_SCALARS = {
    "tca_records_processed_total": 110.0,
    "tca_node_build_duration_seconds_bucket": 0.01,
    "increase(ddc_dac_ehd_build_duration_seconds_count": 2.0,
    "rate(ddc_dac_ehd_build_duration_seconds_sum": 20.6,
}
_HEALTHY_LOKI = {
    "Starting OCW inspection processing": [
        {"labels": {"host": f"node-{i}.validator.blockchain.testnet"}, "message": "..."}
        for i in range(6)
    ],
    "Finish fetching EHD root for era": [
        {"labels": {"host": "node-0"}, "message": "Finish fetching EHD root for era 495206"}
    ]
    * 7,
    "EHD ApplyEra": [{"labels": {"host": "storage-1"}, "message": "[DAC] EHD ApplyEra"}] * 4,
    "Successfully sent 'step_end": [
        {"labels": {"host": "node-0"}, "message": "Successfully sent 'step_end_payout' call"}
    ]
    * 5,
    "payout_step": [{"labels": {"host": "node-0"}, "message": "payout_step ..."}] * 30,
    "Inspection receipt verified": [{"labels": {"host": "node-0"}, "message": "verified"}] * 7,
}


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: FakeGrafana) -> None:
    monkeypatch.setattr(tool_mod, "get_grafana_client_from_credentials", lambda **_: client)


def test_classify_directions() -> None:
    probes = {p.key: p for p in build_probes("testnet")}
    assert probes["inflow"].classify(110) == Status.GREEN
    assert probes["inflow"].classify(0) == Status.RED
    assert probes["tca_latency"].classify(0.01) == Status.GREEN
    assert probes["tca_latency"].classify(58) == Status.RED
    assert probes["inspectors"].classify(6) == Status.GREEN
    assert probes["inspectors"].classify(3) == Status.RED


def test_ehd_ontime_no_data_is_unknown_not_red() -> None:
    probe = next(p for p in build_probes("testnet") if p.key == "ehd_ontime")
    assert probe.classify(None) == Status.UNKNOWN


def test_build_probes_env_templating() -> None:
    probes = build_probes("mainnet", expected_inspectors=8)
    inflow = next(p for p in probes if p.key == "inflow")
    inspectors = next(p for p in probes if p.key == "inspectors")
    assert 'job="mainnet"' in inflow.query
    assert inspectors.green_at == 8.0


def test_per_env_inspectors_and_absent_prometheus_is_unknown() -> None:
    dev = {p.key: p for p in build_probes("devnet")}
    test = {p.key: p for p in build_probes("testnet")}
    # devnet runs fewer validators than testnet — its threshold must reflect that
    assert dev["inspectors"].green_at == 4.0
    assert test["inspectors"].green_at == 6.0
    # A network that doesn't export the ddc_dac_* metrics reads UNKNOWN, not red
    assert dev["inflow"].classify(None) == Status.UNKNOWN
    assert dev["tca_latency"].classify(None) == Status.UNKNOWN


def test_evaluate_probe_loki_distinct_host_and_occurrences() -> None:
    client = FakeGrafana(_HEALTHY_SCALARS, _HEALTHY_LOKI)
    probes = {p.key: p for p in build_probes("testnet")}
    insp = evaluate_probe(client, probes["inspectors"])
    assert insp["value"] == 6.0  # distinct hosts
    assert insp["status"] == "green"
    payout = evaluate_probe(client, probes["payout"])
    assert payout["value"] == 5.0  # "Successfully sent 'step_end" occurrences
    assert payout["status"] == "green"
    heartbeat = evaluate_probe(client, probes["payout_heartbeat"])
    assert heartbeat["value"] == 30.0  # "payout_step" occurrences (context)


def test_worst_severity() -> None:
    assert worst([Status.GREEN, Status.UNKNOWN]) == Status.GREEN
    assert worst([Status.GREEN, Status.RED]) == Status.RED


def test_tool_all_dimensions_healthy(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, _HEALTHY_LOKI))
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    assert result["available"] is True
    assert result["overall_status"] == "green"
    assert {d["key"] for d in result["dimensions"]} == {
        "inflow",
        "tca_latency",
        "ehd_built",
        "ehd_build_applied",
        "ehd_produced",
        "ehd_ontime",
        "inspection_verified",
        "inspectors",
        "payout",
        "payout_heartbeat",
    }


def test_tool_detects_degraded(monkeypatch: pytest.MonkeyPatch) -> None:
    loki = dict(_HEALTHY_LOKI)
    loki["Inspection receipt verified"] = []  # inspection not completing
    loki["Successfully sent 'step_end"] = []  # no payout steps completing
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, loki))
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    assert result["overall_status"] == "red"
    statuses = {d["key"]: d["status"] for d in result["dimensions"]}
    assert statuses["inspection_verified"] == "red"
    assert statuses["payout"] == "red"


def test_payout_heartbeat_does_not_rescue_stalled_settlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression mirroring the inspection trap on the payout side: the payout OCW keeps
    # ticking ("payout_step") on idle "no era to pay" cycles, but if zero payout steps
    # actually COMPLETE, settlement is stalled and the verdict must be red.
    loki = dict(_HEALTHY_LOKI)
    loki["Successfully sent 'step_end"] = []  # nothing settling
    loki["payout_step"] = [{"labels": {"host": "node-0"}, "message": "tick"}] * 90  # OCW alive
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, loki))
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["payout_heartbeat"]["value"] == 90.0  # OCW looks alive...
    assert by_key["payout_heartbeat"]["gate"] is False  # ...but it's context
    assert by_key["payout"]["status"] == "red"  # the real signal
    assert result["overall_status"] == "red"


def test_no_ehd_built_is_red_independent_of_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The era-report/EHD stage is gated on its own signal: if no EHDs are built &
    # retrievable, era_report is red regardless of the (also failing) downstream stages.
    loki = dict(_HEALTHY_LOKI)
    loki["Finish fetching EHD root for era"] = []  # aggregation dark, no EHDs produced
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, loki))
    result = dac_health_check(
        check="era_report", env="devnet", grafana_endpoint="https://x", grafana_api_key="glsa_y"
    )
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["ehd_built"]["status"] == "red"
    assert by_key["ehd_built"]["gate"] is True
    assert result["overall_status"] == "red"


def test_red_gating_dimension_attaches_failure_samples(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # When a gating stage is red, the tool pulls the matching error lines so the verdict
    # carries its own "why" (here: the FailedToFetchProcessedEras root cause).
    loki = dict(_HEALTHY_LOKI)
    loki["Inspection receipt verified"] = []
    loki["Inspection error|Fetching processed eras error"] = [
        {"labels": {"host": "node-0"}, "message": "❌ Fetching processed eras error FailedToFetch"}
    ]
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, loki))
    result = dac_health_check(
        check="inspector_participation", grafana_endpoint="https://x", grafana_api_key="glsa_y"
    )
    insp = next(d for d in result["dimensions"] if d["key"] == "inspection_verified")
    assert insp["status"] == "red"
    assert any("FailedToFetch" in s for s in insp["failure_samples"])


def test_inspection_starts_without_verified_receipts_is_red(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression for the devnet false-green: validators START inspection (participation
    # looks fine) but produce ZERO verified receipts -> inspection is NOT working, so the
    # verdict must be red. The participation count must NOT rescue it (it's context-only).
    loki = dict(_HEALTHY_LOKI)
    loki["Starting OCW inspection processing"] = [
        {"labels": {"host": f"node-{i}"}, "message": "start"} for i in range(4)
    ]
    loki["Inspection receipt verified"] = []  # every attempt failed -> no receipts
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, loki))
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    by_key = {d["key"]: d for d in result["dimensions"]}
    assert by_key["inspectors"]["value"] == 4.0  # participation looks fine...
    assert by_key["inspectors"]["gate"] is False  # ...but it's context, not a gate
    assert by_key["inspection_verified"]["status"] == "red"  # the real signal
    assert result["overall_status"] == "red"


def test_ehd_build_count_is_context_only_and_never_flips_verdict(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: the per-node EHD build-event counter reading low/zero must NOT
    # make a healthy pipeline look degraded (it false-alarmed when the only era
    # without an EHD was simply the in-progress one). It's context, not a gate.
    scalars = dict(_HEALTHY_SCALARS)
    scalars["increase(ddc_dac_ehd_build_duration_seconds_count"] = 0.0  # would be red if gated
    _patch_client(monkeypatch, FakeGrafana(scalars, _HEALTHY_LOKI))
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    ehd = next(d for d in result["dimensions"] if d["key"] == "ehd_produced")
    assert ehd["gate"] is False
    assert ehd["status"] == "red"  # raw signal still surfaced for context
    assert result["overall_status"] == "green"  # but the verdict stays green


def test_ehd_build_applied_is_cluster_scoped_context_probe() -> None:
    # The build-side EHD signal lives on the DAC storage nodes, whose Loki streams are
    # scoped by ddc_cluster_id (not env). It is context-only (sparse ~1/era) and only
    # present for networks we have a cluster id for.
    probes = {p.key: p for p in build_probes("devnet")}
    applied = probes["ehd_build_applied"]
    assert applied.gate is False
    assert 'ddc_cluster_id="7f82864e4f097e63d04cc279e4d8d2eb45a42ffa"' in applied.query
    assert "EHD ApplyEra" in applied.query
    # An unknown network simply omits the build-side probe (no cluster id to scope it).
    assert "ehd_build_applied" not in {p.key for p in build_probes("staging")}


def test_tool_check_filter(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, _HEALTHY_LOKI))
    result = dac_health_check(check="inspector_participation", grafana_endpoint="https://x")
    assert [d["key"] for d in result["dimensions"]] == ["inspection_verified", "inspectors"]


def test_tool_unknown_check(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_client(monkeypatch, FakeGrafana(_HEALTHY_SCALARS, _HEALTHY_LOKI))
    result = dac_health_check(check="bogus", grafana_endpoint="https://x")
    assert result["available"] is False


def test_tool_not_configured() -> None:
    result = dac_health_check(grafana_endpoint=None)
    assert result["available"] is False
    assert "not configured" in result["error"]


class FailingGrafana:
    """Stub whose queries all fail (simulates Grafana 5xx / unreachable)."""

    def query_mimir(self, _query: str) -> dict[str, Any]:
        return {"success": False, "error": "boom", "metrics": []}

    def query_loki(self, _query: str, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "boom", "logs": []}


def test_tool_query_failure_degrades_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    # Upstream failures must produce an investigation-friendly verdict, not an exception.
    _patch_client(monkeypatch, FailingGrafana())  # type: ignore[arg-type]
    result = dac_health_check(grafana_endpoint="https://x", grafana_api_key="glsa_y")
    assert result["available"] is True
    statuses = {d["key"]: d["status"] for d in result["dimensions"]}
    # Prometheus "no data" reads unknown (could just be a network that doesn't export it)...
    assert statuses["inflow"] == "unknown"
    assert statuses["ehd_ontime"] == "unknown"
    # ...but the Loki signals genuinely going dark (inspection/payout) is a real failure,
    # so the overall verdict still degrades to red.
    assert statuses["inspectors"] == "red"
    assert statuses["payout"] == "red"
    assert result["overall_status"] == "red"


def test_tool_registered_on_investigation_surface() -> None:
    from app.tools.registry import _load_registry_snapshot

    by_name = {t.name: t for t in _load_registry_snapshot()}
    assert "dac_health_check" in by_name
    assert "investigation" in by_name["dac_health_check"].surfaces


def test_tool_schema_constrains_check_and_env_and_declares_outputs() -> None:
    from app.tools.registry import _load_registry_snapshot

    tool = {t.name: t for t in _load_registry_snapshot()}["dac_health_check"]
    props = tool.public_input_schema["properties"]
    assert set(props["check"]["enum"]) == {
        "all",
        "inflow",
        "aggregation",
        "era_report",
        "inspector_participation",
        "payout",
    }
    assert "testnet" in props["env"]["enum"]
    assert "overall_status" in tool.outputs
