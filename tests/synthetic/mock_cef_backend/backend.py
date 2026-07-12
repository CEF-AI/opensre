"""Fixture CEF backend for scored synthetic QA scenarios.

Mirrors ``mock_eks_backend``: a scenario directory carries the evidence (agent activities + logs,
component logs) and an answer key; ``FixtureCEFBackend`` serves that evidence to the CEF tools via
the injected ``cef_backend`` hook, so ``run_investigation`` exercises the real agent + runbook end
to end without touching the live vault / Grafana. Deterministic evidence in, LLM verdict out — the
verdict is then scored against the answer key by the suite runner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CefScenario:
    """One CEF QA scenario loaded from a directory."""

    scenario_id: str
    alert: dict[str, Any]
    conversation_id: str
    job_id: str
    activities: list[dict[str, Any]]  # [{activityId, handlerName, status, startedAt, endedAt}]
    activity_logs: dict[str, list[dict[str, Any]]]  # activityId -> [{timestamp, message}]
    component_logs: dict[str, list[dict[str, Any]]]  # service -> [{timestamp, message}]
    answer: dict[str, Any]  # answer.yml (root_cause_category, required_keywords, forbidden_*, ...)
    metadata: dict[str, Any] = field(default_factory=dict)


def load_scenario(scenario_dir: str | Path) -> CefScenario:
    """Load a CEF scenario directory (alert.json, agent.json, component.json, answer.yml)."""
    d = Path(scenario_dir)
    alert = json.loads((d / "alert.json").read_text(encoding="utf-8"))
    agent = json.loads((d / "agent.json").read_text(encoding="utf-8"))
    component = (
        json.loads((d / "component.json").read_text(encoding="utf-8"))
        if (d / "component.json").exists()
        else {}
    )
    answer = yaml.safe_load((d / "answer.yml").read_text(encoding="utf-8")) or {}
    meta = (
        yaml.safe_load((d / "scenario.yml").read_text(encoding="utf-8"))
        if (d / "scenario.yml").exists()
        else {}
    )
    return CefScenario(
        scenario_id=d.name,
        alert=alert,
        conversation_id=str(agent.get("conversation_id", "")),
        job_id=str(agent.get("job_id", "")),
        activities=list(agent.get("activities") or []),
        activity_logs=dict(agent.get("activity_logs") or {}),
        component_logs=dict(component or {}),
        answer=answer,
        metadata=meta or {},
    )


class FixtureCEFBackend:
    """Serves a CefScenario's evidence to the CEF tools (the injected ``cef_backend``)."""

    def __init__(self, scenario: CefScenario) -> None:
        self._s = scenario

    def agent_logs(
        self,
        conversation_id: str | None = None,
        job_id: str | None = None,
        activity_id: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        s = self._s
        if activity_id:
            logs = s.activity_logs.get(activity_id, [])[:limit]
            return {
                "source": "cef_agent_logs",
                "available": True,
                "conversation_id": conversation_id,
                "job_id": job_id or s.job_id,
                "activity_id": activity_id,
                "logs": logs,
            }
        # Resolve conversation_id -> job (the scenario has exactly one run).
        resolved = job_id or (s.job_id if conversation_id == s.conversation_id else None)
        if conversation_id and resolved is None:
            return {
                "source": "cef_agent_logs",
                "available": True,
                "conversation_id": conversation_id,
                "job_id": None,
                "jobs": [{"jobId": s.job_id, "context": s.conversation_id}],
                "job_lookup_truncated": False,
                "note": "no job exists for this conversation in the fixture",
            }
        if resolved is None:
            return {
                "source": "cef_agent_logs",
                "available": True,
                "jobs": [{"jobId": s.job_id, "context": s.conversation_id}],
            }
        return {
            "source": "cef_agent_logs",
            "available": True,
            "conversation_id": conversation_id,
            "job_id": resolved,
            "activities": s.activities[:limit],
        }

    def component_logs(
        self,
        service: str,
        time_range_minutes: int = 60,  # noqa: ARG002 — mirrors real tool signature; fixtures are windowless
        contains: str | None = None,
        level: str | None = None,
        tenant_scoped: bool = True,
        limit: int = 100,
    ) -> dict[str, Any]:
        lines = list(self._s.component_logs.get(service, []))
        if contains:
            lines = [ln for ln in lines if contains in str(ln.get("message", ""))]
        if level:
            lines = [ln for ln in lines if level.lower() in str(ln.get("message", "")).lower()]
        lines = lines[:limit]
        return {
            "source": "cef",
            "available": True,
            "service": service,
            "query": f"<fixture:{service} tenant_scoped={tenant_scoped}>",
            "count": len(lines),
            "truncated": False,
            "logs": lines,
        }


__all__ = ["CefScenario", "FixtureCEFBackend", "load_scenario"]
