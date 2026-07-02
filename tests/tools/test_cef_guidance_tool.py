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


def test_all_topics_are_wellformed() -> None:
    for name, topic in CEF_TOPICS.items():
        assert topic.name and topic.content and topic.source
        assert topic.keywords, f"{name} has no keywords"
