"""CEF hiring-coach domain knowledge for interpreting run failures.

Reference content the investigation agent retrieves to reason over the evidence from
``cef_agent_logs`` (the agent's own ctx.log) and ``cef_component_logs`` (platform logs).
It is descriptive knowledge — what the pipeline is, what can fail, and what the log markers
mean — not a rule engine: the agent still does the analysis.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CefKnowledgeTopic:
    """A CEF knowledge topic with match keywords and reference content."""

    name: str
    keywords: list[str]
    content: str
    source: str


CEF_TOPICS: dict[str, CefKnowledgeTopic] = {
    "investigation_procedure": CefKnowledgeTopic(
        name="Investigation procedure — verify ALL components before concluding",
        keywords=[
            "procedure",
            "coverage",
            "checklist",
            "verify",
            "conclude",
            "components",
            "thorough",
            "steps",
            "how",
        ],
        content="""Do NOT conclude a run is healthy from the agent logs alone. Even when
cef_agent_logs shows every activity 'completed' / a 'finalize done' line, you MUST still verify
the other components before reporting success — a run can finalize while a component was degraded.

For every run, check ALL of these, then report:
  1. Agent stages  — cef_agent_logs: did all activities complete? which (if any) failed?
     Note the failing/last activity's start/end timestamps -> this is the run's time window.
  2. Audio fetch   — cef_component_logs(service="ddc-s3-gateway") over the run's window: any 4xx/5xx?
  3. Inference     — cef_component_logs(service="orchestrator") over the run's window: any
     "Inference failed" / "all inference nodes failed for model ..."?
  4. Runtime       — cef_component_logs(service="agent-runtime") over the run's window: any crash/OOM?

Use the run's time window (from step 1's activity timestamps), NOT a blind "last hour".
Only report GREEN if ALL four are clean. In the report, state what you checked for EACH component
(green or red) — even on a pass — so coverage is auditable.""",
        source="CEF QA investigation procedure",
    ),
    "pipeline_overview": CefKnowledgeTopic(
        name="Hiring-coach pipeline & CEF execution path",
        keywords=["overview", "pipeline", "hiring", "coach", "architecture", "flow", "cef"],
        content="""The hiring-coach agent analyses interview audio as a durable map-reduce pipeline:

  analyze.audio -> PLAN (validate, fan out one chunk per audio URL)
    -> MAP: per-chunk ASR transcription + VAD + speaker embedding
    -> REDUCE: stitch/diarise, then LLM judges (authenticity, clarity, engagement) + SER
    -> per-turn ENRICH -> FINALIZE (writes the final result; logs "finalize done")

CEF execution path for each event:
  publish -> Vault (NATS) -> Orchestrator (creates a Job, resolves model aliases, dispatches
  inference) -> Agent Gateway -> Agent Runtime (V8 isolate runs the handler; the handler calls
  ctx.publish / ctx.cubby / ctx.models[alias].infer).

A run == a Job. The Job has state (running/terminal) and a context == the conversation_id.""",
        source="CEF hiring-coach agent (agent-catalog) + CEF stack",
    ),
    "stages_and_activities": CefKnowledgeTopic(
        name="Stages, activities and completion",
        keywords=[
            "stage",
            "activity",
            "handler",
            "onturn",
            "finalize",
            "plan",
            "reduce",
            "chunk",
            "status",
            "state",
            "step",
        ],
        content="""A Job is made of activities (one per handler invocation / pipeline step). Each
activity has a handlerName (e.g. onTurn, finalize), a status (completed / failed), and start/end
times. Read them via cef_agent_logs (list activities for the job).

Completion signal: the run is healthy when it reaches a "finalize done" log line. If no activity
is failed but there is no "finalize done" and the run went idle/terminal, it stalled (e.g. isolate
timeout) rather than erroring cleanly. A failed activity's status + its ctx.log lines pinpoint the
stage and reason.""",
        source="CEF hiring-coach pipeline + live activity shape",
    ),
    "failure_modes": CefKnowledgeTopic(
        name="Failure modes",
        keywords=["failure", "error", "fail", "timeout", "stall", "regression", "broken", "stuck"],
        content="""Common ways a run fails:

1. Inference node down (most common): the ASR/LLM model has no healthy inference nodes, so the
   chunk (transcription) or reduce (judge) step errors. Surfaces in the orchestrator logs.
2. Audio unreachable: a clip's audio can't be fetched from the DDC S3 gateway (4xx/5xx), so the
   chunk step fails.
3. Judge issue: the LLM judge (gemma) times out or returns invalid output in reduce/turn.
4. Isolate/stall: the V8 isolate crashes or exceeds its time/memory limit; the run never reaches
   "finalize done" and goes idle/terminal.
5. Score regression: the run completes but scores deviate from the clip's expected calibration
   (a quality problem, judged against a golden set — not an execution failure).""",
        source="CEF stack failure surfaces (verified live)",
    ),
    "log_markers": CefKnowledgeTopic(
        name="Log markers and where to find them",
        keywords=[
            "log",
            "marker",
            "ctx.log",
            "grep",
            "inference failed",
            "retry",
            "finalize",
            "message",
            "signal",
        ],
        content="""Agent's own ctx.log (via cef_agent_logs -> activity logs):
  - "finalize done"                     -> the run completed successfully
  - "retry exhausted after N attempts"  -> a step gave up after retries (often ASR/embedder)
  - per-chunk / per-stage error lines   -> the failing step's reason

Platform/component logs (via cef_component_logs):
  - orchestrator: level=error msg="Inference failed" ... error="no inference node ..." OR
      "all inference nodes failed for model <alias>"  -> that model has no healthy nodes
      Also: "Inference dispatch failed, trying next node" (transient).
  - ddc-s3-gateway: JSON request logs with a `status` field and `uri`; status 4xx/5xx on a
      clip's audio path == an audio-fetch failure.
  - agent-runtime: "Execution succeeded" wrapper lines; isolate crash / OOM / timeout appear here.

Note: matches are case-sensitive — use the exact casing (e.g. "Inference failed", capital I).""",
        source="CEF Grafana Loki (verified live)",
    ),
    "model_aliases": CefKnowledgeTopic(
        name="Model aliases mapped to pipeline stage",
        keywords=[
            "model",
            "whisper",
            "parakeet",
            "gemma",
            "asr",
            "embedder",
            "ser",
            "inference",
            "alias",
            "canary",
            "pyannote",
            "eres2netv2",
        ],
        content="""When the orchestrator reports a failing model, map it to the stage:

  ASR / transcription (chunk step):  whisper_large_v3, whisper_large_v3_turbo,
      parakeet-tdt-0.6b-v2, parakeet-tdt-0.6b-v3, canary-1b-flash
  Speaker embedding (chunk step):    eres2netv2-3dspeaker, campplus-3dspeaker
  Voice activity detection (chunk):  pyannote-segmentation-3.0
  LLM judges (reduce/turn step):     gemma4_31b (primary), qwen2-vl-7b-instruct (fallback)
  Speech emotion (vocal):            ser_odyssey_wavlm

So "all inference nodes failed for model parakeet-tdt-0.6b-v2" == the transcription stage is
down for runs that selected that ASR alias.""",
        source="CEF hiring-coach cef.config models registry",
    ),
    "correlation": CefKnowledgeTopic(
        name="Correlating agent logs with component logs",
        keywords=[
            "correlation",
            "conversation",
            "conversation_id",
            "job",
            "context",
            "time",
            "window",
            "tie",
            "link",
            "pivot",
        ],
        content="""The two log rails correlate differently:

  cef_agent_logs (agent ctx.log): keyed by the run. Resolve conversation_id -> Job
    (job.context == conversation_id) -> activities -> that activity's logs.

  cef_component_logs (platform): NOT tagged with conversation_id. Correlate by TIME WINDOW
    (the failing activity's start/end times) + the model name / marker.

Recommended flow: use cef_agent_logs to find the failing stage and its timestamps, then query
cef_component_logs for that component over that window, filtered by the model/marker you saw.""",
        source="CEF stack log labelling (verified live)",
    ),
}


def get_topics_for_keywords(keywords: list[str]) -> list[str]:
    """Return topic names matching the keywords, most-relevant first."""
    if not keywords:
        return []
    keywords_lower = [kw.lower() for kw in keywords]
    scored: list[tuple[str, int]] = []
    for topic_name, topic in CEF_TOPICS.items():
        score = sum(
            1
            for kw in keywords_lower
            if any(kw in topic_kw or topic_kw in kw for topic_kw in topic.keywords)
        )
        if score > 0:
            scored.append((topic_name, score))
    scored.sort(key=lambda item: -item[1])
    return [name for name, _ in scored]


def _render(topic_names: list[str]) -> dict:
    return {
        "success": True,
        "topics": topic_names,
        "guidance": [
            {
                "topic": CEF_TOPICS[n].name,
                "content": CEF_TOPICS[n].content,
                "source": CEF_TOPICS[n].source,
            }
            for n in topic_names
        ],
        "sources": [CEF_TOPICS[n].source for n in topic_names],
    }


def get_cef_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict:
    """Retrieve CEF hiring-coach domain knowledge by topic or keywords.

    Falls back to the pipeline overview when nothing matches, so the agent always gets context.
    """
    if topic and topic in CEF_TOPICS:
        return _render([topic])
    if keywords:
        matching = get_topics_for_keywords(keywords)[:max_topics]
        if matching:
            return _render(matching)
    return _render(["pipeline_overview"])


__all__ = ["CEF_TOPICS", "CefKnowledgeTopic", "get_cef_guidance", "get_topics_for_keywords"]
