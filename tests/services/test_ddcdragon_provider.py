"""Tests for the DDC Dragon LLM provider (wrapped inference API, keyless)."""

from __future__ import annotations

import typing
from typing import Any

import pytest

from app.config import KEYLESS_LLM_PROVIDERS, LLMProvider, LLMSettings


class _Resp:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_provider_registered_in_config() -> None:
    assert "ddcdragon" in typing.get_args(LLMProvider)
    assert "ddcdragon" in KEYLESS_LLM_PROVIDERS
    settings = LLMSettings(provider="ddcdragon")
    assert settings.provider == "ddcdragon"  # validator accepts it
    assert settings.ddcdragon_model
    assert settings.ddcdragon_bucket


def test_llm_client_speaks_the_envelope(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.llm_client import DdcDragonLLMClient

    captured: dict[str, Any] = {}

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _Resp:
        captured["url"] = url
        captured["payload"] = json
        return _Resp({"output": {"text": "hello", "usage": {"inputTokens": 3, "outputTokens": 1}}})

    monkeypatch.setattr("requests.post", fake_post)
    client = DdcDragonLLMClient(
        endpoint="https://x", bucket=1338, name="gemma4_31b", version="v1.0.0", max_tokens=8
    )
    result = client.invoke("hi")

    assert result.content == "hello"
    assert result.input_tokens == 3
    assert result.output_tokens == 1
    assert captured["payload"]["model"] == {
        "bucket": 1338,
        "name": "gemma4_31b",
        "version": "v1.0.0",
    }
    assert captured["payload"]["input"]["max_tokens"] == 8


def test_agent_client_parses_tool_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.agent_llm_client import DdcDragonAgentClient

    def fake_post(url: str, json: dict[str, Any], timeout: float) -> _Resp:
        assert json["input"]["messages"][0]["role"] == "system"  # system prepended
        return _Resp(
            {
                "output": {
                    "text": "",
                    "tool_calls": [
                        {
                            "id": "t1",
                            "function": {
                                "name": "cef_agent_logs",
                                "arguments": '{"conversation_id":"c"}',
                            },
                        }
                    ],
                }
            }
        )

    monkeypatch.setattr("requests.post", fake_post)
    client = DdcDragonAgentClient(
        endpoint="https://x", bucket=1338, name="gemma4_31b", version="v1.0.0"
    )
    result = client.invoke(
        [{"role": "user", "content": "go"}], system="sys", tools=[{"type": "function"}]
    )

    assert result.stop_reason == "tool_use"
    assert len(result.tool_calls) == 1
    call = result.tool_calls[0]
    assert call.id == "t1"
    assert call.name == "cef_agent_logs"
    assert call.input == {"conversation_id": "c"}


def test_repair_mangled_tool_args_recovers_gemma_followup_calls() -> None:
    from app.services.agent_llm_client import _repair_mangled_tool_args

    # gemma4_31b sometimes emits args as a JSON object whose keys are JSON fragments
    # (the shape of json.dumps(real) split on ", " then ": "). Repair must reverse it.
    two = {'{"conversation_id"': '"c-1"', '"job_id"': '"j-1"'}
    assert _repair_mangled_tool_args(two) == {"conversation_id": "c-1", "job_id": "j-1"}
    one = {'{"job_id"': '"j-1"'}
    assert _repair_mangled_tool_args(one) == {"job_id": "j-1"}
    # clean args are returned untouched (no false repairs)
    assert _repair_mangled_tool_args({"topic": "x"}) == {"topic": "x"}
    assert _repair_mangled_tool_args({}) == {}


def test_get_agent_llm_routes_to_ddcdragon(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.config as config
    import app.services.agent_llm_client as mod
    from app.services.agent_llm_client import DdcDragonAgentClient

    monkeypatch.setattr(mod, "_agent_client", None)
    monkeypatch.setattr(config, "resolve_llm_settings", lambda: LLMSettings(provider="ddcdragon"))

    agent = mod.get_agent_llm()
    assert isinstance(agent, DdcDragonAgentClient)
