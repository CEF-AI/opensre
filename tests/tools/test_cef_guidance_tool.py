"""Tests for the CEF guidance (knowledge) tool."""

from __future__ import annotations

from app.tools.CEFGuidanceTool import get_cef_guidance, get_topics_for_keywords
from app.tools.CEFGuidanceTool.knowledge_base import CEF_TOPICS


def test_specific_topic_returned() -> None:
    result = get_cef_guidance(topic="model_aliases")
    assert result["success"] is True
    assert result["topics"] == ["model_aliases"]
    assert "parakeet" in result["guidance"][0]["content"].lower()


def test_keywords_match_relevant_topics() -> None:
    result = get_cef_guidance(keywords=["parakeet", "inference"], max_topics=3)
    assert "model_aliases" in result["topics"]
    assert all(t in CEF_TOPICS for t in result["topics"])


def test_keyword_ranking_prefers_more_matches() -> None:
    ranked = get_topics_for_keywords(["conversation_id", "time", "window"])
    assert ranked[0] == "correlation"


def test_falls_back_to_overview_when_no_match() -> None:
    # no topic, no keywords -> overview
    assert get_cef_guidance()["topics"] == ["pipeline_overview"]
    # unknown keywords -> overview (never empty, so the agent always has context)
    assert get_cef_guidance(keywords=["zzz-nonsense"])["topics"] == ["pipeline_overview"]


def test_procedure_topic_enforces_multi_component_coverage() -> None:
    content = get_cef_guidance(topic="investigation_procedure")["guidance"][0]["content"].lower()
    # must tell the agent NOT to conclude from agent logs alone, and to check each component
    assert "agent logs alone" in content
    for component in ("ddc-s3-gateway", "orchestrator", "agent-runtime"):
        assert component in content
    # keyword lookup also surfaces it
    assert "investigation_procedure" in get_cef_guidance(keywords=["coverage"])["topics"]


def test_score_regression_topic_is_history_based_no_thresholds() -> None:
    content = get_cef_guidance(topic="score_regression")["guidance"][0]["content"].lower()
    assert "cef_clip_history" in content  # points at the history tool
    assert "baseline" in content and "history" in content
    assert "threshold" in content  # explicitly says: without fixed thresholds
    assert "score_regression" in get_cef_guidance(keywords=["regression", "drift"])["topics"]


def test_all_topics_are_wellformed() -> None:
    for name, topic in CEF_TOPICS.items():
        assert topic.name and topic.content and topic.source
        assert topic.keywords, f"{name} has no keywords"
