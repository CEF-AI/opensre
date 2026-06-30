"""Tests for the DAC pipeline knowledge tool."""

from __future__ import annotations

from app.tools.DACGuidanceTool.knowledge_base import (
    DAC_TOPICS,
    get_dac_guidance,
    get_topics_for_keywords,
)


def test_all_topics_have_content_and_source() -> None:
    assert DAC_TOPICS
    for name, topic in DAC_TOPICS.items():
        assert topic.content.strip(), f"{name} has empty content"
        assert topic.source.strip(), f"{name} has empty source"
        assert topic.keywords, f"{name} has no keywords"


def test_get_by_explicit_topic() -> None:
    result = get_dac_guidance(topic="inspection_irf")
    assert result["success"] is True
    assert result["topics"] == ["inspection_irf"]
    assert "IRF" in result["guidance"][0]["content"]


def test_get_by_keywords_ranks_matches() -> None:
    result = get_dac_guidance(keywords=["payout", "settlement"], max_topics=2)
    assert result["success"] is True
    assert "payout" in result["topics"]
    assert len(result["topics"]) <= 2


def test_keyword_helper_matches_inspector() -> None:
    names = get_topics_for_keywords(["inspector"])
    assert "inspection_irf" in names


def test_unknown_keywords_fall_back_to_overview() -> None:
    # No keyword match must still return useful DAC context (the overview),
    # not an empty result — otherwise the agent's guidance lookup whiffs.
    result = get_dac_guidance(keywords=["totally-unrelated-xyz"])
    assert result["success"] is True
    assert result["topics"] == ["pipeline_overview"]
    assert "note" in result


def test_env_keyword_matches_overview() -> None:
    # The model often queries by env name (e.g. "testnet"); that must match.
    result = get_dac_guidance(keywords=["testnet"])
    assert result["success"] is True
    assert "pipeline_overview" in result["topics"]


def test_guidance_points_logs_tool_at_validator_service_name() -> None:
    # DAC inspection + payout logs live under service_name="pos-node" (verified live).
    # Guidance must surface that so the agent calls query_grafana_logs correctly
    # instead of passing an empty service_name and getting a Loki 400.
    assert 'service_name="pos-node"' in DAC_TOPICS["inspection_irf"].content
    assert 'service_name="pos-node"' in DAC_TOPICS["payout"].content


def test_prometheus_queries_topic_gives_runnable_promql() -> None:
    assert "prometheus_queries" in DAC_TOPICS
    result = get_dac_guidance(keywords=["promql"])
    assert "prometheus_queries" in result["topics"]
    content = DAC_TOPICS["prometheus_queries"].content
    # Real metric names the agent can pass as query_grafana_metrics(metric_name=...)
    assert "ddc_dac_tca_records_processed_total" in content
    assert "query_grafana_metrics" in content
