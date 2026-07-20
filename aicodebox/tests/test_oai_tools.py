"""Tests for OpenAI-style client-executed tool calling on
/openai/v1/chat/completions.

Covers:
  - the pure helpers (_normalize_tool_choice, _tools_directive,
    _messages_to_tool_prompt, _parse_tool_calls)
  - the handler: tool_calls response shape, text fallback, multi-turn
    history rendering, tool_choice variants, and the tools+schema /
    tools+stream 400s
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aicodebox.adapters import base as adapter_base
from aicodebox.adapters.base import RunResult
from aicodebox.modes.api import oai as oai_mod


class _OAIToolsAdapter(adapter_base.AgentAdapter):
    name = "oai-tools-test"
    binary = "/bin/true"
    available_models = ["m1"]

    def build_argv(self, req):
        del req
        return [self.binary]


@pytest.fixture
def oai_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._oai_tools", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_oai_tools:_OAIToolsAdapter",
    )
    monkeypatch.setenv("AICODEBOX_AVAILABLE_MODELS", "m1")
    adapter_base.reset_adapter_cache()
    yield _OAIToolsAdapter
    adapter_base.reset_adapter_cache()


@pytest.fixture
def app(oai_adapter, tmp_path, monkeypatch):
    del oai_adapter
    monkeypatch.setenv("AICODEBOX_WORKSPACE", str(tmp_path))
    from aicodebox.modes.api.oai import router as oai_router
    fast = FastAPI()
    fast.include_router(oai_router)
    return fast


@pytest.fixture
def client(app):
    return TestClient(app)


def _patch_run(monkeypatch, text, usage=None):
    """Patch the agent run to return a canned text (the model output the
    route parses for tool calls). Captures the specs passed in."""
    from aicodebox.shared import runner as runner_mod
    calls = {"specs": []}

    def fake(spec, proc_hook=None):
        del proc_hook
        calls["specs"].append(spec)
        return RunResult(
            text=text,
            raw_stdout=text,
            raw_stderr="",
            exit_code=0,
            usage=usage or {"input_tokens": 10, "output_tokens": 5},
        )

    monkeypatch.setattr(runner_mod, "run", fake)
    monkeypatch.setattr(oai_mod, "run_agent", fake)
    return calls


_WEATHER_TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get the current weather for a city.",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


# ── pure helpers ─────────────────────────────────────────────────────────────


def test_normalize_tool_choice():
    assert oai_mod._normalize_tool_choice(None) == "auto"
    assert oai_mod._normalize_tool_choice("auto") == "auto"
    assert oai_mod._normalize_tool_choice("none") == "none"
    assert oai_mod._normalize_tool_choice("required") == "required"
    assert oai_mod._normalize_tool_choice("garbage") == "auto"
    assert oai_mod._normalize_tool_choice(
        {"type": "function", "function": {"name": "get_weather"}},
    ) == {"name": "get_weather"}
    assert oai_mod._normalize_tool_choice({"type": "required"}) == "required"


def test_tools_directive_lists_tools_and_protocol():
    d = oai_mod._tools_directive([_WEATHER_TOOL], "auto")
    assert '{"tool_calls":' in d
    assert "get_weather" in d
    assert "Get the current weather" in d
    # the JSON Schema for parameters is embedded
    assert '"city"' in d


def test_tools_directive_required_and_forced():
    assert "MUST call at least one tool" in oai_mod._tools_directive(
        [_WEATHER_TOOL], "required",
    )
    forced = oai_mod._tools_directive([_WEATHER_TOOL], {"name": "get_weather"})
    assert "MUST call the tool named 'get_weather'" in forced


def test_messages_to_tool_prompt_renders_history():
    from aicodebox.modes.api.oai import _OAIMessage
    msgs = [
        _OAIMessage(role="system", content="be terse"),
        _OAIMessage(role="user", content="weather in SF?"),
        _OAIMessage(
            role="assistant",
            content=None,
            tool_calls=[{
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "get_weather",
                    "arguments": '{"city": "SF"}',
                },
            }],
        ),
        _OAIMessage(
            role="tool",
            tool_call_id="call_1",
            name="get_weather",
            content="62F, foggy",
        ),
    ]
    prompt, system_prompt = oai_mod._messages_to_tool_prompt(msgs)
    assert system_prompt == "be terse"
    assert "weather in SF?" in prompt
    assert "get_weather" in prompt
    assert "62F, foggy" in prompt
    assert "TOOL RESULT get_weather (call_1)" in prompt


def test_parse_tool_calls_clean():
    out = oai_mod._parse_tool_calls(
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}',
    )
    assert out is not None
    assert len(out) == 1
    assert out[0]["type"] == "function"
    assert out[0]["function"]["name"] == "get_weather"
    # OpenAI wire format: arguments is a JSON STRING, not an object
    assert out[0]["function"]["arguments"] == '{"city": "SF"}'
    assert out[0]["id"].startswith("call_")


def test_parse_tool_calls_wrapped_in_prose_and_fences():
    text = (
        "Sure, let me check that.\n```json\n"
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}\n```'
    )
    out = oai_mod._parse_tool_calls(text)
    assert out is not None and out[0]["function"]["name"] == "get_weather"


def test_parse_tool_calls_none_on_plain_text():
    assert oai_mod._parse_tool_calls("It is sunny today.") is None


def test_parse_tool_calls_none_on_empty_array():
    assert oai_mod._parse_tool_calls('{"tool_calls": []}') is None


def test_parse_tool_calls_skips_entries_missing_name():
    out = oai_mod._parse_tool_calls(
        '{"tool_calls": [{"arguments": {"x": 1}}, '
        '{"name": "ok", "arguments": {}}]}',
    )
    assert out is not None and len(out) == 1
    assert out[0]["function"]["name"] == "ok"
    assert out[0]["function"]["arguments"] == "{}"


# ── handler: tool-call response shape ────────────────────────────────────────


def test_handler_emits_tool_calls(client, monkeypatch):
    calls = _patch_run(
        monkeypatch,
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}',
    )
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "weather in SF?"}],
            "tools": [_WEATHER_TOOL],
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    tcs = choice["message"]["tool_calls"]
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "get_weather"
    assert json.loads(tcs[0]["function"]["arguments"]) == {"city": "SF"}
    # directive was injected into the agent's system prompt
    assert "tool_calls" in (calls["specs"][0].system_prompt or "")
    assert "get_weather" in (calls["specs"][0].system_prompt or "")


def test_handler_text_fallback_when_no_tool_needed(client, monkeypatch):
    _patch_run(monkeypatch, "It is sunny and 70F.")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_WEATHER_TOOL],
        },
    )
    assert resp.status_code == 200
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "It is sunny and 70F."
    assert "tool_calls" not in choice["message"]


def test_handler_multi_turn_history_reaches_agent(client, monkeypatch):
    calls = _patch_run(monkeypatch, "It's 62F and foggy in SF.")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [
                {"role": "user", "content": "weather in SF?"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "SF"}',
                        },
                    }],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call_1",
                    "name": "get_weather",
                    "content": "62F, foggy",
                },
            ],
            "tools": [_WEATHER_TOOL],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["finish_reason"] == "stop"
    # the agent saw the prior tool call + its result in the transcript
    prompt = calls["specs"][0].prompt
    assert "62F, foggy" in prompt
    assert "get_weather" in prompt


def test_tool_mode_disables_internal_tools_by_default(client, monkeypatch):
    """In tool mode the harness acts as a pure function-caller, so its own
    internal tools default OFF (no_tools=True on the spec)."""
    calls = _patch_run(
        monkeypatch,
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}',
    )
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "weather in SF?"}],
            "tools": [_WEATHER_TOOL],
        },
    )
    assert resp.status_code == 200
    assert calls["specs"][0].no_tools is True


def test_tool_mode_internal_tools_override_re_enables(client, monkeypatch):
    """An explicit x-aicodebox-no-tools: 0 re-enables the harness's own
    tools even in tool mode (the hybrid)."""
    calls = _patch_run(monkeypatch, "answered directly")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_WEATHER_TOOL],
        },
        headers={"x-aicodebox-no-tools": "0"},
    )
    assert resp.status_code == 200
    assert calls["specs"][0].no_tools is False


def test_non_tool_mode_keeps_internal_tools_on(client, monkeypatch):
    """Plain chat (no tools) keeps the prior default: internal tools on
    (no_tools=False) unless the header disables them."""
    calls = _patch_run(monkeypatch, "hello")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 200
    assert calls["specs"][0].no_tools is False


def test_handler_tool_choice_none_is_plain_chat(client, monkeypatch):
    calls = _patch_run(monkeypatch, "plain answer")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_WEATHER_TOOL],
            "tool_choice": "none",
        },
    )
    assert resp.status_code == 200
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert choice["message"]["content"] == "plain answer"
    # no tool directive injected when tool_choice=none
    assert "tool_calls" not in (calls["specs"][0].system_prompt or "")


def test_handler_empty_tools_is_plain_chat(client, monkeypatch):
    calls = _patch_run(monkeypatch, "hello there")
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [],
        },
    )
    assert resp.status_code == 200
    assert resp.json()["choices"][0]["message"]["content"] == "hello there"
    assert "tool_calls" not in (calls["specs"][0].system_prompt or "")


# ── combined tools + schema (agentic flow ending in structured JSON) ─────────

_ANSWER_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "answer",
        "schema": {
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    },
}


def test_combined_tool_turn_is_not_schema_validated(client, monkeypatch):
    """tools + response_format together: a tool-call turn is returned as
    tool_calls / finish_reason 'tool_calls' and is NOT checked against the
    final-answer schema (it isn't the final answer)."""
    calls = _patch_run(
        monkeypatch,
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}',
    )
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "weather in SF?"}],
            "tools": [_WEATHER_TOOL],
            "response_format": _ANSWER_SCHEMA,
        },
    )
    assert resp.status_code == 200
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["content"] is None
    assert choice["message"]["tool_calls"][0]["function"]["name"] == \
        "get_weather"
    # the final-answer schema instruction was injected alongside the tools
    sp = calls["specs"][0].system_prompt or ""
    assert "FINAL answer" in sp
    assert '"answer"' in sp


def test_combined_final_answer_is_schema_validated(client, monkeypatch):
    """The FINAL answer turn (no tool call) is schema-validated and returned
    as canonical JSON with finish_reason 'stop'."""
    _patch_run(monkeypatch, '{"answer": "it is sunny and 70F"}')
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_WEATHER_TOOL],
            "response_format": _ANSWER_SCHEMA,
        },
    )
    assert resp.status_code == 200
    choice = resp.json()["choices"][0]
    assert choice["finish_reason"] == "stop"
    assert json.loads(choice["message"]["content"]) == {
        "answer": "it is sunny and 70F",
    }
    # schema retry helper ran → per-attempt breakdown surfaced
    assert "aicodebox_attempts" in resp.json()


def test_combined_final_answer_schema_invalid_422(client, monkeypatch):
    """A final-answer turn that doesn't satisfy the schema exhausts retries
    and returns 422 (same as pure schema mode)."""
    _patch_run(monkeypatch, '{"wrong_field": "nope"}')
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "tools": [_WEATHER_TOOL],
            "response_format": _ANSWER_SCHEMA,
        },
    )
    assert resp.status_code == 422
    assert "validation failed" in resp.json()["detail"]


def test_handler_tools_plus_stream_buffers_to_sse(client, monkeypatch):
    """tools + stream=true doesn't 400 — it buffers the tool-call answer and
    replays it as a single-shot SSE stream (valid stream, not incremental)."""
    _patch_run(
        monkeypatch,
        '{"tool_calls": [{"name": "get_weather", '
        '"arguments": {"city": "SF"}}]}',
    )
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "weather?"}],
            "tools": [_WEATHER_TOOL],
            "stream": True,
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "chat.completion.chunk" in body
    assert "tool_calls" in body
    assert '"index": 0' in body  # streaming tool_calls carry an index
    assert '"finish_reason": "tool_calls"' in body
    assert "data: [DONE]" in body
