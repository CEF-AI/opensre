"""DAC pipeline knowledge base for investigation drill-down.

Domain knowledge for Cere's DAC (Data Activity Capture) pipeline, distilled from
company-memory-bank:
- 03-dragon-1/dac/{overview,activity-collection,activity-aggregation,inspection-shared-memory}.md
- 02-cere-protocol/blockchain/{inspection,payouts}.md

Mirrors the SREGuidanceTool pattern: indexed topics the investigation agent can
pull by name or keyword while reasoning over DAC health signals.
"""

from dataclasses import dataclass


@dataclass
class DACKnowledgeTopic:
    """A DAC pipeline topic with associated keywords and content."""

    name: str
    keywords: list[str]
    content: str
    source: str


DAC_TOPICS: dict[str, DACKnowledgeTopic] = {
    "pipeline_overview": DACKnowledgeTopic(
        name="DAC Pipeline Overview",
        keywords=[
            "dac",
            "pipeline",
            "overview",
            "stages",
            "activity",
            "record",
            "end-to-end",
            "health",
            "status",
            "testnet",
            "mainnet",
            "devnet",
        ],
        content="""DAC (Data Activity Capture) Overview:

DAC is the trust layer between cluster usage claims and on-chain payment. Every
metered operation (storage written, bytes served, compute-seconds, inference
tokens) becomes a signed Activity Record. The pipeline aggregates those records,
validators inspect a statistical sample, and the payout pallet settles payment.

End-to-end stages:
  Collection -> Aggregation -> Inspection -> Payout

A pipeline is healthy end-to-end when: records keep flowing (Collection), each
minute's aggregation finishes within budget and era reports are produced on time
(Aggregation), enough validators inspect each era and reach consensus
(Inspection), and eras get paid so last_paid_era keeps advancing (Payout).

Signals: call dac_health_check for the per-stage verdict. DAC metrics are in
Prometheus (ddc_dac_*); validator OCW logs (inspection/payout) are in Loki under
service_name="pos-node" — read them via query_grafana_logs(service_name="pos-node").""",
        source="company-memory-bank 03-dragon-1/dac/overview.md",
    ),
    "collection": DACKnowledgeTopic(
        name="Collection Stage",
        keywords=[
            "collection",
            "activity record",
            "inflow",
            "records",
            "ingest",
            "badgerdb",
            "nats",
        ],
        content="""Collection Stage:

Storage and compute nodes capture every metered operation as a multi-signed
ActivityRecord (operation type, units, request + ACK signatures, timestamp) and
persist it (BadgerDB + NATS JetStream, ~72h retention).

Healthy: new records are continuously produced across nodes.
Failure modes:
- Inflow drops to ~0: nodes stopped serving or stopped recording (deploy, crash,
  network partition, upstream outage).
- Signature/persistence errors: records dropped before aggregation.

Primary signal: ddc_dac_tca_records_processed_total (rate over a window). A
near-zero rate means Collection is dark and everything downstream will starve.""",
        source="company-memory-bank 03-dragon-1/dac/activity-collection.md",
    ),
    "aggregation_tca_phd_ehd": DACKnowledgeTopic(
        name="Aggregation (TCA / PHD / EHD)",
        keywords=[
            "aggregation",
            "tca",
            "phd",
            "ehd",
            "merkle",
            "era report",
            "budget",
            "latency",
            "collector",
        ],
        content="""Aggregation Stage (three tiers):

1. TCA (Time Capture Aggregate) - per-minute Merkle aggregate per node and per
   bucket. The runner processes closed TCAs each ~1m tick; a TCA should finish
   well within its per-minute processing budget.
2. PHD (Per-collector era aggregate) - folds the era's TCAs per collector.
3. EHD (Era report) - cluster-wide aggregate built by grouping collectors at era
   close; fetches peer PHDs under a ~20s deadline (ACTIVITY_PROCESSING_EHD_PHD_
   FETCH_DEADLINE). On timeout it finalizes with partial PHDs or retries.

Healthy: TCA p95 build time comfortably below the 1-minute budget; an EHD is
produced each era with build time below the fetch deadline.
Failure modes:
- TCA latency creeping toward 60s: aggregation falling behind; era reports slip.
- No EHD builds in an era / build time >= deadline: era report not produced on
  time, usually slow or missing peer PHDs.

Signals: ddc_dac_tca_node_build_duration_seconds, ddc_dac_tca_bucket_build_
duration_seconds, ddc_dac_ehd_build_duration_seconds, ddc_dac_ehd_phd_fetch_
duration_seconds (labelled by source_node to find the slow peer).""",
        source="company-memory-bank 03-dragon-1/dac/activity-aggregation.md",
    ),
    "inspection_irf": DACKnowledgeTopic(
        name="Inspection and Inspector Participation",
        keywords=[
            "inspection",
            "inspector",
            "irf",
            "ocw",
            "validator",
            "participation",
            "quorum",
            "verify",
            "etcd",
        ],
        content="""Inspection Stage:

Validator off-chain workers (OCW) independently re-derive a statistical sample of
the era's aggregates and compare against published roots. A path is verified when
IRF inspectors (typically 3) submit a matching result hash. Main inspectors run
first; backup inspectors activate after a block delay if the main set stalls.
Coordination uses an embedded etcd cluster (/itm/v1/*).

Healthy: enough validators run inspection each era (e.g. ~6 on testnet) and paths
reach IRF before the deadline.
Failure modes:
- Fewer participating inspectors than expected: a validator is not running its
  OCW inspection; the era risks IRF_UNREACHED (those TCAs become un-billable).
- Persistent "Inspection error" / InspError: transient by nature, but a sustained
  spike alongside low participation indicates a real inspection-layer problem.

Signals (logs, not Prometheus): "Starting OCW inspection processing" emitted once
per participating validator host per era (count distinct hosts = participation);
"Inspection path results submitted" (rare); "Inspection error" (transient).
The DAC validator OCW logs live under service_name="pos-node" — to read them
directly call query_grafana_logs(service_name="pos-node") and filter for these
markers. Coordination health: ddc_etcd_quorum_healthy_members / ddc_etcd_quorum_size.""",
        source="company-memory-bank 02-cere-protocol/blockchain/inspection.md",
    ),
    "payout": DACKnowledgeTopic(
        name="Payout / Settlement",
        keywords=[
            "payout",
            "settlement",
            "fingerprint",
            "charge",
            "reward",
            "last_paid_era",
            "finalize",
            "billing",
        ],
        content="""Payout Stage:

After inspection, validators each derive a PayoutFingerprint (EHD root, receipt
hash, payers/payees Merkle roots). At validator quorum, begin_payout is accepted
and customers are charged / providers rewarded in batches verified by MMR proofs.
The state machine ends at Finalized and last_paid_era advances.

Healthy: payout OCW keeps stepping each era; last_paid_era advances; no era stays
unpaid for more than ~1 era period.
Failure modes:
- Settlement stalled: no payout activity / last_paid_era frozen -> eras pile up
  unpaid (fingerprint divergence, missing inspected eras, OCW not triggering).
- Falling behind: settlement consuming most of the era budget -> payouts risk
  slipping past the era deadline.

Signals (logs): "payout_step ... wall_ms=... charge_* reward_* charging_batches=
... rewarding_batches=..." (per-era OCW heartbeat with timing); "last_paid_era="
(settlement progress / lag in eras). These live under service_name="pos-node" —
read them with query_grafana_logs(service_name="pos-node") filtered on the markers.""",
        source="company-memory-bank 02-cere-protocol/blockchain/payouts.md",
    ),
    "thresholds_and_budgets": DACKnowledgeTopic(
        name="Timing Budgets and Thresholds",
        keywords=["budget", "deadline", "threshold", "era", "timing", "slo", "interval"],
        content="""DAC Timing Budgets (testnet defaults):

- TCA duration: 1 minute (smallest aggregate/inspect/payout unit).
- Payment era: 1 hour = 60 TCAs.
- Aggregation processing period: ~1 minute (per-minute budget for a TCA).
- EHD peer-PHD fetch deadline: 20 seconds. An EHD build time near 20s is
  borderline, not comfortable.
- Processing threshold: ~0.55 of nodes must report before a TCA is marked done.
- Inspector participation: ~6 validators expected on testnet (IRF consensus = 3).

Interpretation: judge aggregation latency against the 1-minute budget, EHD build
time against the 20s fetch deadline, and inspector count against the expected
participation for the environment. mainnet/devnet may differ from testnet.""",
        source="company-memory-bank 03-dragon-1/dac/activity-aggregation.md",
    ),
    "failure_modes": DACKnowledgeTopic(
        name="DAC Failure Modes and Triage",
        keywords=["failure", "triage", "rca", "stalled", "behind", "dark", "drop", "debug"],
        content="""DAC Failure-Mode Triage:

Map the symptom to the stage, then drill into that stage's signals:

1. Inflow ~0 (Collection dark): check node/cluster health, recent deploys, and
   upstream availability. Everything downstream starves, so fix this first.
2. TCA p95 -> 60s (Aggregation behind): check collector CPU/IO, peer fetch
   latency, and record volume spikes; era reports slip next.
3. No EHD / EHD >= 20s (era report late): inspect ddc_dac_ehd_phd_fetch_duration_
   seconds by source_node for a slow/unreachable grouping-collector peer.
4. Inspectors < expected (Inspection): identify which validator host stopped
   emitting "Starting OCW inspection processing"; check that node's OCW + etcd
   quorum (ddc_etcd_quorum_healthy_members).
5. No payout_step / last_paid_era frozen (Payout stalled): check fingerprint
   quorum, whether inspected eras exist to pay, and the payout OCW interval.

General principle: stale data is better than wrong data — confirm one root-cause
mechanism with evidence before recommending remediation.""",
        source="company-memory-bank 03-dragon-1/dac + 02-cere-protocol/blockchain",
    ),
    "prometheus_queries": DACKnowledgeTopic(
        name="DAC Prometheus Queries (for query_grafana_metrics)",
        keywords=["query", "promql", "metric", "grafana", "mimir", "prometheus", "drill"],
        content="""Ready-to-run PromQL for deeper/correlated metric inspection.

Use the dac_health_check tool first for the verdict. When you want to drill into a
metric yourself, pass one of these as the `metric_name` argument to
query_grafana_metrics (replace <env> with testnet|mainnet|devnet). Never call
query_grafana_metrics with an empty metric_name.

- Activity-record inflow (records/min):
  sum(rate(ddc_dac_tca_records_processed_total{job="<env>"}[5m]))*60
- Per-minute aggregation p95 latency (s):
  histogram_quantile(0.95, sum by (le)(rate(ddc_dac_tca_node_build_duration_seconds_bucket{job="<env>"}[15m])))
- Era report (EHD) average build time (s):
  sum(rate(ddc_dac_ehd_build_duration_seconds_sum{job="<env>"}[1h])) / sum(rate(ddc_dac_ehd_build_duration_seconds_count{job="<env>"}[1h]))
- Era report cadence (EHD builds over 2h; <1 means none produced):
  sum(increase(ddc_dac_ehd_build_duration_seconds_count{job="<env>"}[2h]))
- Slow peer during EHD build (PHD fetch p95 by source_node, s):
  histogram_quantile(0.95, sum by (source_node, le)(rate(ddc_dac_ehd_phd_fetch_duration_seconds_bucket{job="<env>"}[1h])))

Inspection participation and payout are NOT in Prometheus — they come from Loki
validator logs under service_name="pos-node" ("Starting OCW inspection processing",
"payout_step"). Use dac_health_check for the verdict; for log drill-down call
query_grafana_logs(service_name="pos-node") and filter on those markers.""",
        source="ddc-node internal/storage/metric/dac_metric.go",
    ),
}


def get_topics_for_keywords(keywords: list[str]) -> list[str]:
    """Find topic names matching the given keywords, most relevant first."""
    if not keywords:
        return []

    keywords_lower = [kw.lower() for kw in keywords]
    topic_scores: list[tuple[str, int]] = []

    for topic_name, topic in DAC_TOPICS.items():
        score = sum(
            1
            for kw in keywords_lower
            if any(kw in topic_kw or topic_kw in kw for topic_kw in topic.keywords)
        )
        if score > 0:
            topic_scores.append((topic_name, score))

    topic_scores.sort(key=lambda x: -x[1])
    return [name for name, _ in topic_scores]


def get_dac_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict:
    """Retrieve DAC pipeline domain knowledge for investigation.

    Args:
        topic: Specific topic name to retrieve (e.g., "inspection_irf").
        keywords: Keywords to match against topic content.
        max_topics: Maximum number of topics to return when using keywords.

    Returns:
        Dictionary with matched topics, content, and source references.
    """
    result: dict = {
        "success": True,
        "topics": [],
        "guidance": [],
        "sources": [],
    }

    if topic and topic in DAC_TOPICS:
        dac_topic = DAC_TOPICS[topic]
        result["topics"] = [topic]
        result["guidance"] = [
            {
                "topic": dac_topic.name,
                "content": dac_topic.content,
                "source": dac_topic.source,
            }
        ]
        result["sources"] = [dac_topic.source]
        return result

    if keywords:
        for topic_name in get_topics_for_keywords(keywords)[:max_topics]:
            dac_topic = DAC_TOPICS[topic_name]
            result["topics"].append(topic_name)
            result["guidance"].append(
                {
                    "topic": dac_topic.name,
                    "content": dac_topic.content,
                    "source": dac_topic.source,
                }
            )
            result["sources"].append(dac_topic.source)

    if not result["topics"]:
        # No keyword match — fall back to the pipeline overview so the agent
        # always gets useful DAC context instead of an empty result.
        fallback = DAC_TOPICS["pipeline_overview"]
        result["topics"] = ["pipeline_overview"]
        result["guidance"] = [
            {"topic": fallback.name, "content": fallback.content, "source": fallback.source}
        ]
        result["sources"] = [fallback.source]
        result["note"] = "No keyword match; returning the DAC pipeline overview."

    return result
