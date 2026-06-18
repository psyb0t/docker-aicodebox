"""Tests for /openai/v1/chat/completions schema-validation flow.

Covers the v0.7.0 additions:
  - x-aicodebox-json-schema header → runs the retry helper, schema-validates
  - success → content carries the canonical JSON re-serialized
  - validation failure after retries → 422
  - agent process crash in schema mode → 500 (NOT 422 — caller's schema
    isn't wrong, the server failed)
  - stream=true + json_schema → 400
  - other RunSpec headers (extra-args, timeout, tools-allowlist, no-tools,
    resume) plumb into both the non-stream and stream paths
"""

from __future__ import annotations

import json
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from aicodebox.adapters import base as adapter_base
from aicodebox.adapters.base import RunResult


class _OAIAdapter(adapter_base.AgentAdapter):
    name = "oai-test"
    binary = "/bin/true"
    available_models = ["m1"]

    def build_argv(self, req):
        del req
        return [self.binary]


@pytest.fixture
def oai_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._oai", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_oai_schema:_OAIAdapter",
    )
    monkeypatch.setenv("AICODEBOX_AVAILABLE_MODELS", "m1")
    adapter_base.reset_adapter_cache()
    yield _OAIAdapter
    adapter_base.reset_adapter_cache()


@pytest.fixture
def app(oai_adapter, tmp_path, monkeypatch):
    del oai_adapter
    monkeypatch.setenv("AICODEBOX_WORKSPACE", str(tmp_path))
    from aicodebox.modes.api.oai import router as oai_router
    app = FastAPI()
    app.include_router(oai_router)
    return app


@pytest.fixture
def client(app):
    return TestClient(app)


def _patch_runner_sequence(monkeypatch, results: list[RunResult]):
    """Patch shared.runner.run to return a sequence of canned RunResults.

    Also patches the ``oai.run_agent`` re-export (an at-import-time alias
    bound by ``from aicodebox.shared.runner import run as run_agent``) so
    the non-schema path is intercepted the same way as the schema-retry
    path (which calls ``runner.run`` via module-level lookup inside
    ``run_with_json_retry``).
    """
    from aicodebox.modes.api import oai as oai_mod
    from aicodebox.shared import runner as runner_mod
    calls = {"n": 0, "specs": []}

    def fake(spec, proc_hook=None):
        del proc_hook
        calls["specs"].append(spec)
        i = calls["n"]
        calls["n"] += 1
        if i >= len(results):
            return results[-1]
        return results[i]

    monkeypatch.setattr(runner_mod, "run", fake)
    monkeypatch.setattr(oai_mod, "run_agent", fake)
    return calls


# ── schema success ──────────────────────────────────────────────────────────


def test_schema_success_returns_canonical_json(client, monkeypatch):
    """Agent emits messy JSON-in-prose; the route's parse_json_response
    extracts + the OAI envelope's content carries the canonical
    re-serialized JSON."""
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    raw_llm_output = (
        "Sure! Here's the answer:\n\n"
        "```json\n"
        '{"n": 42}\n'
        "```"
    )
    _patch_runner_sequence(monkeypatch, [RunResult(
        text=raw_llm_output, raw_stdout=raw_llm_output, raw_stderr="",
        exit_code=0,
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "give me an int"}],
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 200
    body = resp.json()
    content = body["choices"][0]["message"]["content"]
    # content is the CANONICAL re-serialized JSON, not the messy LLM output
    assert json.loads(content) == {"n": 42}
    assert "```" not in content


def test_schema_retry_succeeds(client, monkeypatch):
    """First attempt fails schema, second attempt succeeds — the route
    returns the canonical second-attempt JSON. usage carries the SUM
    across both attempts (not just the last one). aicodebox_attempts
    has the per-attempt breakdown."""
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    calls = _patch_runner_sequence(monkeypatch, [
        RunResult(
            text='{"n": "wrong type"}',
            raw_stdout='{"n": "wrong type"}',
            raw_stderr="", exit_code=0,
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        RunResult(
            text='{"n": 7}', raw_stdout='{"n": 7}',
            raw_stderr="", exit_code=0,
            usage={"input_tokens": 110, "output_tokens": 30},
        ),
    ])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "give me an int"}],
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert json.loads(body["choices"][0]["message"]["content"]) == {"n": 7}
    assert calls["n"] == 2  # initial + 1 retry

    # usage is the SUM across both attempts — not just the last one.
    # Provider billed for both calls; reporting only the final attempt
    # would be lying to the caller.
    assert body["usage"]["prompt_tokens"] == 210
    assert body["usage"]["completion_tokens"] == 50
    assert body["usage"]["total_tokens"] == 260

    # Per-attempt breakdown surfaces under the vendor-extension key
    # aicodebox_attempts (OAI clients ignore unknown fields).
    attempts = body["aicodebox_attempts"]
    assert len(attempts) == 2
    assert attempts[0]["index"] == 0
    assert attempts[0]["usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert attempts[0]["parseError"] is not None
    assert attempts[1]["index"] == 1
    assert attempts[1]["usage"] == {"input_tokens": 110, "output_tokens": 30}
    assert attempts[1]["parseError"] is None


def test_schema_failure_after_retries_returns_422(client, monkeypatch):
    """Every attempt fails schema validation → 422 with detail.

    Also verifies the retry budget: helper does initial + 3 retries = 4
    calls before giving up (JSON_RETRY_MAX = 3).
    """
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    calls = _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"n": "still wrong"}',
        raw_stdout='{"n": "still wrong"}',
        raw_stderr="", exit_code=0,
    )] * 5)

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "give me an int"}],
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 422
    assert "json_schema validation failed" in resp.json()["detail"]
    assert "3 retries" in resp.json()["detail"]
    # initial + JSON_RETRY_MAX(3) retries = 4 attempts
    assert calls["n"] == 4


def test_schema_agent_crash_returns_500(client, monkeypatch):
    """Agent exits non-zero in schema mode → 500 (server error),
    NOT 422 (which would imply the caller's schema was wrong)."""
    schema = {"type": "object", "required": ["n"]}
    _patch_runner_sequence(monkeypatch, [RunResult(
        text="", raw_stdout="",
        raw_stderr="agent: connection refused",
        exit_code=2,
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert "exited with code 2" in detail
    assert "connection refused" in detail


# ── stream + schema is rejected ─────────────────────────────────────────────


def test_stream_with_schema_returns_400(client):
    schema = {"type": "object"}
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 400
    assert "stream=true" in resp.json()["detail"]


# ── other RunSpec headers plumb through ─────────────────────────────────────


def test_extra_args_and_no_tools_reach_spec(client, monkeypatch):
    calls = _patch_runner_sequence(monkeypatch, [RunResult(
        text="ok", raw_stdout="ok", raw_stderr="", exit_code=0,
    )])
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={
            "x-aicodebox-extra-args": '["--flag", "value"]',
            "x-aicodebox-no-tools": "1",
            "x-aicodebox-tools-allowlist": "read,write",
            "x-aicodebox-timeout-seconds": "30",
            "x-aicodebox-resume": "sess-abc",
        },
    )
    assert resp.status_code == 200
    spec = calls["specs"][0]
    assert spec.extra_args == ["--flag", "value"]
    assert spec.no_tools is True
    assert spec.tools_allowlist == ["read", "write"]
    assert spec.timeout_seconds == 30
    assert spec.resume == "sess-abc"


def test_malformed_json_schema_header_returns_400(client):
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-aicodebox-json-schema": "not json"},
    )
    assert resp.status_code == 400
    assert "x-aicodebox-json-schema" in resp.json()["detail"]


def test_malformed_timeout_header_returns_400(client):
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-aicodebox-timeout-seconds": "ten"},
    )
    assert resp.status_code == 400
    assert "x-aicodebox-timeout-seconds" in resp.json()["detail"]


# ── streaming path plumbs the same RunSpec knobs ────────────────────────────


def test_stream_plumbs_runspec_headers(client, monkeypatch):
    """Streaming path receives the same six knobs from headers as the
    non-streaming path. Regression catch for the kwargs threading through
    _stream_response → its inner RunSpec construction.
    """
    from aicodebox.modes.api import oai as oai_mod
    from aicodebox.shared import runner as runner_mod

    captured_specs: list = []

    async def fake_stream(spec):
        captured_specs.append(spec)
        # Emit one delta then stop — minimum viable stream.
        from aicodebox.adapters.base import StreamEvent
        yield StreamEvent(type="delta", text="ok")
        yield StreamEvent(type="stop", data={"reason": "stop"})

    monkeypatch.setattr(runner_mod, "run_stream", fake_stream)
    monkeypatch.setattr(oai_mod, "run_stream", fake_stream)

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={
            "x-aicodebox-extra-args": "--flag1,--flag2",
            "x-aicodebox-no-tools": "true",
            "x-aicodebox-tools-allowlist": '["read"]',
            "x-aicodebox-timeout-seconds": "42",
            "x-aicodebox-resume": "sess-xyz",
        },
    )
    assert resp.status_code == 200
    assert len(captured_specs) == 1
    spec = captured_specs[0]
    assert spec.extra_args == ["--flag1", "--flag2"]
    assert spec.no_tools is True
    assert spec.tools_allowlist == ["read"]
    assert spec.timeout_seconds == 42
    assert spec.resume == "sess-xyz"
