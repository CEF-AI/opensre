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
    "full_qa": CefKnowledgeTopic(
        name="Full QA of a run — execution health AND score quality",
        keywords=["qa", "full", "both", "complete", "run", "review", "audit", "check"],
        content="""A complete QA of a run has TWO parts. Do BOTH, and report both — a run is only
GREEN if it executed cleanly AND its scores are in line with the clip's history.

PART A — Execution health (did it run correctly):
  Follow `investigation_procedure`. Verify agent stages (cef_agent_logs), then audio (s3-gateway),
  inference (orchestrator), and runtime (agent-runtime) over the run's window. Don't conclude from
  agent logs alone.

PART B — Score quality (did it score right):
  Follow `score_regression`. EVEN IF execution is clean, pull the clip's history
  (cef_clip_history) and judge whether this run's scores deviate from the clip's OWN baseline
  (no thresholds) and are consistent with the transcript.

Report both outcomes explicitly: execution status per component AND any score regression (with the
run's value vs the clip's historical range). State which part — A, B, both, or neither — is the issue.""",
        source="CEF QA full-run procedure",
    ),
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
        content="""VERDICT PRECEDENCE — decide pass/fail from the RUN'S OWN outcome, never from
ambient errors. This is the most important rule:
- The authoritative signal is the run's activities + cubby status. If every activity is 'completed'
  and the cubby row is status='completed', the run SUCCEEDED -> report PASS, even if some component
  logged errors during the window. A completed run is not a failure.
- Component / inference errors are CONTEXT, not the verdict. An error the platform retried past
  (e.g. a "trying next node" line) after which the run still completed is a degraded-but-survived
  note — report it as an observation, NOT a NO-GO.
- Report NO-GO only when the run itself did not succeed: an activity 'failed', cubby status is
  'failed' or missing, or the run never finalized. THEN use the component evidence to explain WHY.
- You will often see conflicting evidence (all activities completed, yet a model errored mid-window).
  Reconcile toward the run's outcome: completed run + transient infra error = PASS with a note. It is
  wrong to call a completed run failed because a shared model blipped during its window.

Do NOT conclude from the agent logs alone that everything was perfect — still check the other
components so you can NOTE degradation and, on a real failure, explain it. You read the raw lines and
reason yourself; the tools only retrieve. Do not pattern-match a fixed error string.

Scope to THIS run; never attribute another tenant's noise to it:
- For orchestrator / agent-runtime / vault-api / nats / cef-core, cef_component_logs is
  tenant_scoped by default: it returns only lines that reference this run's vault/agent. If a line
  does not reference our vault, agent, or conversation, it is NOT this run's evidence — ignore it.

Checks (use the run's time window from step 1, not a blind "last hour"):
  1. Agent stages — cef_agent_logs: did all activities complete? which (if any) failed? This decides
     the verdict (see precedence above). Record the failing/last activity start/end -> run window.
  2. Audio fetch  — cef_component_logs(service="ddc-s3-gateway"): plain HTTP access logs with NO
     vault/agent id (tenant scoping does nothing), so correlate by the audio path + window.
  3. Inference    — inference failures are SHARED infrastructure: they carry a model name, not a
     vault/agent id, so tenant scoping would hide them. Query
     cef_component_logs(service="orchestrator", tenant_scoped=false, contains="<model>") for each
     model THIS agent uses (listed in the alert under 'agent_models'; if absent, read them from the
     agent's manifest/activities — do not assume a fixed set). A model error here is at most a
     PLATFORM/infra note; it only becomes a run failure if an activity actually failed because of it.
  4. Runtime      — cef_component_logs(service="agent-runtime"): tenant-scoped; any crash/restart
     affecting our agent in the window?

State what you checked for EACH component (green / degraded / red) so coverage is auditable — but
the PASS/NO-GO verdict follows the run's activity+cubby outcome, per precedence above.""",
        source="CEF QA investigation procedure",
    ),
    "score_regression": CefKnowledgeTopic(
        name="Score regression — deviation from the clip's own history (no thresholds)",
        keywords=[
            "regression",
            "score",
            "quality",
            "baseline",
            "history",
            "deviation",
            "drift",
            "calibration",
            "wrong",
        ],
        content="""A run can COMPLETE successfully yet still be wrong — the scores drifted. Detect
this WITHOUT fixed thresholds: judge the run against the clip's OWN history, and against its
transcript.

Procedure:
  1. cef_clip_history(clip) — pull this clip's prior runs (its scores over time). That history IS
     the baseline; nobody hard-codes a "good" number.
  2. Compare THIS run's scores to that baseline. A regression = a clear outlier vs. the clip's own
     track record (e.g. a clip that has produced clarity ~0.88 across many runs now returns ~0.5),
     not "below some cutoff". Small run-to-run wobble is normal; a break from the pattern is not.
  3. Consistency check — read the transcript (timeline turns from cef_agent_logs) against the score:
     does the score make sense for what was actually said? (A clearly-structured answer scoring very
     low clarity is internally inconsistent.)
  4. Use the clip's intent from its name only as a sanity direction, never as a numeric gate:
     *_clarity_good = should read as clear; *_clarity_zero = should read as unclear;
     *reading_ai* = reading; *spontaneous*/*not_reading* = not reading; *_fp = a false-positive guard.

Report a regression as: this run's value, the clip's historical range, and why it's a deviation —
so it is auditable. The baseline self-adjusts as the agent legitimately improves.""",
        source="CEF QA score-regression procedure",
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
