"""CEF knowledge retrieval tool for hiring-coach run-failure investigation."""

from __future__ import annotations

from typing import Any

from app.tools.CEFGuidanceTool.knowledge_base import (
    get_cef_guidance as _get_cef_guidance,
)
from app.tools.CEFGuidanceTool.knowledge_base import (
    get_topics_for_keywords,
)
from app.tools.tool_decorator import tool


def _extract_guidance_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"keywords": sources.get("problem_keywords", [])}


@tool(
    name="get_cef_guidance",
    display_name="CEF runbook",
    source="knowledge",
    description="Retrieve CEF hiring-coach domain knowledge to interpret a run's logs.",
    use_cases=[
        "Understanding the hiring-coach pipeline stages and completion signal",
        "Mapping a failing model alias to the pipeline stage it belongs to",
        "Knowing which log markers mean what (agent ctx.log vs component logs)",
        "Correlating agent logs with component logs (by conversation_id vs time window)",
    ],
    tags=("safe", "fast", "no-credentials"),
    cost_tier="cheap",
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "Specific topic: investigation_procedure, pipeline_overview, "
                    "stages_and_activities, failure_modes, log_markers, model_aliases, correlation"
                ),
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords to match against CEF content (e.g., ['parakeet', 'audio']).",
            },
            "max_topics": {"type": "integer", "default": 3},
        },
        "required": [],
    },
    extract_params=_extract_guidance_params,
)
def get_cef_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict[str, Any]:
    """Retrieve CEF hiring-coach domain knowledge to interpret a run's logs."""
    return _get_cef_guidance(topic=topic, keywords=keywords, max_topics=max_topics)


__all__ = ["get_cef_guidance", "get_topics_for_keywords"]
