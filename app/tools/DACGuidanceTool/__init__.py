"""DAC pipeline knowledge retrieval tool for investigation drill-down."""

from __future__ import annotations

from typing import Any

from app.tools.DACGuidanceTool.knowledge_base import (
    get_dac_guidance as _get_dac_guidance,
)
from app.tools.DACGuidanceTool.knowledge_base import (
    get_topics_for_keywords,
)
from app.tools.tool_decorator import tool


def _extract_guidance_params(sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    return {"keywords": sources.get("problem_keywords", [])}


@tool(
    name="get_dac_guidance",
    display_name="DAC pipeline runbook",
    source="knowledge",
    description="Retrieve Cere DAC pipeline domain knowledge (stages, budgets, failure modes).",
    use_cases=[
        "Understanding the DAC pipeline stages (Collection, Aggregation, Inspection, Payout)",
        "Interpreting TCA/PHD/EHD aggregation latency against per-minute and era budgets",
        "Diagnosing inspector-participation drops and IRF consensus",
        "Diagnosing stalled or falling-behind payout/settlement",
        "Mapping a DAC health symptom to the right stage and signals",
    ],
    tags=("safe", "fast", "no-credentials"),
    cost_tier="cheap",
    input_schema={
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": "Specific topic: pipeline_overview, collection, aggregation_tca_phd_ehd, inspection_irf, payout, thresholds_and_budgets, failure_modes",
            },
            "keywords": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Keywords to match against DAC content (e.g., ['inspector', 'era', 'payout'])",
            },
            "max_topics": {"type": "integer", "default": 3},
        },
        "required": [],
    },
    extract_params=_extract_guidance_params,
)
def get_dac_guidance(
    topic: str | None = None,
    keywords: list[str] | None = None,
    max_topics: int = 3,
) -> dict[str, Any]:
    """Retrieve Cere DAC pipeline domain knowledge for investigation."""
    return _get_dac_guidance(topic=topic, keywords=keywords, max_topics=max_topics)


__all__ = ["get_dac_guidance", "get_topics_for_keywords"]
