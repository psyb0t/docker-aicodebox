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


def test_schema_provider_error_returns_400_not_422(client, monkeypatch):
    """Provider rejects the request (e.g. content-safety filter) — the
    agent exits 0 with empty text, so parse_json_response would normally
    fail and this would look like a 422 schema failure. provider_error
    must take priority: 400 with the provider's message, no retries."""
    schema = {"type": "object", "required": ["n"]}
    calls = _patch_runner_sequence(monkeypatch, [RunResult(
        text="", raw_stdout="", raw_stderr="", exit_code=0,
        provider_error="[1301] content policy violation",
    )] * 5)

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "[1301] content policy violation"
    # no retries — replaying the same prompt into the same filter is
    # pointless, so only the initial attempt should have run
    assert calls["n"] == 1


def test_plain_provider_error_returns_400(client, monkeypatch):
    """Same provider-error signal on the non-schema chat path."""
    _patch_runner_sequence(monkeypatch, [RunResult(
        text="", raw_stdout="", raw_stderr="", exit_code=0,
        provider_error="[1301] content policy violation",
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
        },
    )
    assert resp.status_code == 400
    assert resp.json()["detail"] == "[1301] content policy violation"


# ── stream + schema is rejected ─────────────────────────────────────────────


def test_stream_with_schema_buffers_to_sse(client, monkeypatch):
    """stream=true + schema doesn't 400 — the schema-validated answer is
    buffered and replayed as a single-shot SSE stream."""
    schema = {
        "type": "object",
        "properties": {"a": {"type": "string"}},
        "required": ["a"],
    }
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"a": "hi"}', raw_stdout='{"a": "hi"}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 5, "output_tokens": 3},
    )])
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "stream": True,
        },
        headers={"x-aicodebox-json-schema": json.dumps(schema)},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "chat.completion.chunk" in body
    assert '"content"' in body  # schema JSON emitted as a content delta
    assert "hi" in body
    assert '"finish_reason": "stop"' in body
    assert "data: [DONE]" in body


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


# ── OpenAI standard response_format body field ──────────────────────────────


def test_response_format_json_schema_drives_validation(client, monkeypatch):
    """OpenAI's standard response_format=json_schema body field triggers
    the schema-retry path the same way the x-aicodebox-json-schema header
    does. No custom header needed."""
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"n": 7}', raw_stdout='{"n": 7}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 100, "output_tokens": 20},
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "give me an int"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "schema": {
                        "type": "object",
                        "properties": {"n": {"type": "integer"}},
                        "required": ["n"],
                    },
                    "strict": True,
                },
            },
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert json.loads(body["choices"][0]["message"]["content"]) == {"n": 7}
    # aicodebox_attempts is set whenever the schema-retry helper ran —
    # confirms the standard body field actually drove the path.
    assert "aicodebox_attempts" in body
    assert len(body["aicodebox_attempts"]) == 1


def test_response_format_json_object_forces_json(client, monkeypatch):
    """response_format=json_object is OpenAI's 'just emit JSON, any
    shape' mode. We treat it as a permissive {"type":"object"} schema —
    enough to drive the retry helper into validating parseability
    without rejecting any particular structure."""
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"anything": "goes"}',
        raw_stdout='{"anything": "goes"}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 50, "output_tokens": 10},
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "any json"}],
            "response_format": {"type": "json_object"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert json.loads(body["choices"][0]["message"]["content"]) == {
        "anything": "goes",
    }
    assert "aicodebox_attempts" in body  # retry helper ran


def test_response_format_text_no_schema(client, monkeypatch):
    """response_format=text is the OAI default — no schema. The retry
    helper does NOT run; aicodebox_attempts is absent."""
    _patch_runner_sequence(monkeypatch, [RunResult(
        text="just prose", raw_stdout="just prose", raw_stderr="",
        exit_code=0, usage={"input_tokens": 30, "output_tokens": 5},
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {"type": "text"},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["choices"][0]["message"]["content"] == "just prose"
    assert "aicodebox_attempts" not in body


def test_response_format_body_wins_over_header(client, monkeypatch):
    """If BOTH response_format body field AND x-aicodebox-json-schema
    header are set, body wins (OAI standard takes precedence). Spec is
    verified by the agent receiving the BODY's schema, not the header's."""
    calls = _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"from_body": true}',
        raw_stdout='{"from_body": true}', raw_stderr="",
        exit_code=0,
    )])

    body_schema = {
        "type": "object",
        "required": ["from_body"],
    }
    header_schema = {
        "type": "object",
        "required": ["from_header"],
    }

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "b", "schema": body_schema},
            },
        },
        headers={"x-aicodebox-json-schema": json.dumps(header_schema)},
    )
    # Agent's response satisfies the BODY schema; if the route had used
    # the header schema instead, retries would have fired (no from_header
    # key in the response).
    assert resp.status_code == 200
    # First spec passed to run() should carry the body schema, not header
    first_spec = calls["specs"][0]
    assert first_spec.json_schema == body_schema


def test_response_format_unknown_type_returns_400(client):
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {"type": "html"},
        },
    )
    assert resp.status_code == 400
    assert "html" in resp.json()["detail"]


def test_response_format_json_schema_missing_inner_returns_400(client):
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "x"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x"},  # no inner "schema" key
            },
        },
    )
    assert resp.status_code == 400
    assert "schema" in resp.json()["detail"]


def test_response_format_stream_buffers_to_sse(client, monkeypatch):
    """stream=true + response_format=json_schema buffers to a single-shot
    SSE stream (same graceful path as the header form)."""
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"ok": true}', raw_stdout='{"ok": true}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 5, "output_tokens": 3},
    )])
    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": {"type": "object"}},
            },
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    body = resp.text
    assert "chat.completion.chunk" in body
    assert '"finish_reason": "stop"' in body
    assert "data: [DONE]" in body


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


# ── ephemeral workspace + session-continuation retry ────────────────────────


def test_schema_allocates_ephemeral_workspace_and_uses_continuation(
    client, monkeypatch, tmp_path,
):
    """Schema mode + no caller workspace → route allocates an ephemeral
    /tmp/aicodebox/<uuid>/ workspace AND tells run_with_json_retry to use
    session-continuation retries. The first run sees the allocated
    workspace; if a retry fires, it would run with no_continue=False."""
    from aicodebox.modes.api import oai as oai_mod

    # Redirect ephemeral root into pytest's tmp so cleanup is contained.
    monkeypatch.setattr(
        oai_mod, "EPHEMERAL_WORKSPACE_ROOT", tmp_path / "aicodebox-eph",
    )

    captured: dict = {"specs": [], "continue_flag": None, "workspace": None}

    real_run_with = oai_mod.run_with_json_retry

    def wrapped(spec, proc_hook=None, max_retries=3,
                continue_session_on_retry=False, early_accept=None):
        captured["continue_flag"] = continue_session_on_retry
        captured["workspace"] = spec.workspace
        return real_run_with(
            spec,
            proc_hook=proc_hook,
            max_retries=max_retries,
            continue_session_on_retry=continue_session_on_retry,
            early_accept=early_accept,
        )

    monkeypatch.setattr(oai_mod, "run_with_json_retry", wrapped)

    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"ok": true}', raw_stdout='{"ok": true}', raw_stderr="",
        exit_code=0,
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": {"type": "object"}},
            },
        },
    )
    assert resp.status_code == 200
    assert captured["continue_flag"] is True, (
        "schema mode + no caller workspace should use session-continuation"
    )
    workspace = captured["workspace"]
    assert workspace is not None
    assert workspace.startswith(
        str(tmp_path / "aicodebox-eph") + "/",
    ), f"workspace should live under ephemeral root, got {workspace}"

    # Cleanup: the ephemeral dir must be gone after the request returns.
    from pathlib import Path
    assert not Path(workspace).exists(), (
        f"ephemeral workspace {workspace} should be cleaned up after request"
    )


def test_schema_with_caller_workspace_does_not_allocate_ephemeral(
    client, monkeypatch, tmp_path,
):
    """When the caller provides `x-aicodebox-workspace`, the route uses
    that as-is and disables session-continuation retries (we can't know
    whether other sessions live in the caller's workspace)."""
    from aicodebox.modes.api import oai as oai_mod

    caller_ws = str(tmp_path / "caller-ws")
    (tmp_path / "caller-ws").mkdir()

    # Bypass the production workspace.resolve sandboxing — that's its own
    # test surface and isn't what this case is checking. We just want to
    # verify the route's branch logic: caller header set → use as-is +
    # disable continuation.
    monkeypatch.setattr(
        oai_mod, "resolve_workspace", lambda x: x or "/workspace",
    )

    # Don't actually let the route allocate one even if it tried.
    monkeypatch.setattr(
        oai_mod, "EPHEMERAL_WORKSPACE_ROOT", tmp_path / "should-not-exist",
    )

    captured: dict = {"continue_flag": None, "workspace": None}
    real_run_with = oai_mod.run_with_json_retry

    def wrapped(spec, proc_hook=None, max_retries=3,
                continue_session_on_retry=False, early_accept=None):
        captured["continue_flag"] = continue_session_on_retry
        captured["workspace"] = spec.workspace
        return real_run_with(
            spec,
            proc_hook=proc_hook,
            max_retries=max_retries,
            continue_session_on_retry=continue_session_on_retry,
            early_accept=early_accept,
        )

    monkeypatch.setattr(oai_mod, "run_with_json_retry", wrapped)
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"ok": true}', raw_stdout='{"ok": true}', raw_stderr="",
        exit_code=0,
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "hi"}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": {"type": "object"}},
            },
        },
        headers={"x-aicodebox-workspace": caller_ws},
    )
    assert resp.status_code == 200
    assert captured["continue_flag"] is False, (
        "caller-provided workspace should disable continuation "
        "(we can't guarantee isolation)"
    )
    assert captured["workspace"] == caller_ws
    # Ephemeral root must NOT have been touched.
    assert not (tmp_path / "should-not-exist").exists()


def test_stream_plus_schema_buffered_cleans_up_ephemeral(
    client, monkeypatch, tmp_path,
):
    """schema + stream=true (no caller workspace) buffers to SSE and still
    cleans up the ephemeral workspace afterwards — no /tmp/aicodebox/<uuid>/
    left behind."""
    from aicodebox.modes.api import oai as oai_mod
    eph_root = tmp_path / "aicodebox-eph"
    monkeypatch.setattr(oai_mod, "EPHEMERAL_WORKSPACE_ROOT", eph_root)
    _patch_runner_sequence(monkeypatch, [RunResult(
        text='{"ok": true}', raw_stdout='{"ok": true}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 5, "output_tokens": 3},
    )])

    resp = client.post(
        "/openai/v1/chat/completions",
        json={
            "model": "m1",
            "messages": [{"role": "user", "content": "x"}],
            "stream": True,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "x", "schema": {"type": "object"}},
            },
        },
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # reading resp.text drains the stream → the finally-block cleanup ran
    assert "data: [DONE]" in resp.text
    if eph_root.exists():
        assert not any(eph_root.iterdir()), (
            "buffered stream must clean up its ephemeral workspace"
        )


def test_purge_stale_workspaces_removes_orphans_older_than_ttl(
    monkeypatch, tmp_path,
):
    """Orphans (dirs older than TTL) get removed. Fresh dirs survive.
    The safety net for SIGKILL / crash / container restart cases the
    per-request `finally` block can't cover."""
    import os
    from aicodebox.modes.api import oai as oai_mod

    root = tmp_path / "aicodebox-eph"
    monkeypatch.setattr(oai_mod, "EPHEMERAL_WORKSPACE_ROOT", root)
    monkeypatch.setattr(oai_mod, "EPHEMERAL_WORKSPACE_TTL_SECONDS", 3600)
    root.mkdir()

    # Two orphans + one fresh dir.
    old1 = root / "abc"
    old1.mkdir()
    (old1 / "session.json").write_text("{}")
    old2 = root / "def"
    old2.mkdir()
    fresh = root / "live"
    fresh.mkdir()

    now = 1_000_000.0
    old_mtime = now - 7200  # 2h old, past 1h TTL
    fresh_mtime = now - 60  # 1m old, fresh

    os.utime(old1, (old_mtime, old_mtime))
    os.utime(old2, (old_mtime, old_mtime))
    os.utime(fresh, (fresh_mtime, fresh_mtime))

    purged = oai_mod.purge_stale_workspaces(now=now)
    assert purged == 2
    assert not old1.exists()
    assert not old2.exists()
    assert fresh.exists()


def test_purge_stale_workspaces_handles_missing_root(monkeypatch, tmp_path):
    """If the root dir doesn't exist yet (no schema requests ever
    fired), purge is a no-op — must not crash."""
    from aicodebox.modes.api import oai as oai_mod
    monkeypatch.setattr(
        oai_mod, "EPHEMERAL_WORKSPACE_ROOT", tmp_path / "never-created",
    )
    assert oai_mod.purge_stale_workspaces() == 0


def test_purge_stale_workspaces_ignores_files(monkeypatch, tmp_path):
    """Non-directory entries (someone dropped a file in the root) are
    skipped — purge only touches the per-request subdirs it owns."""
    import os
    from aicodebox.modes.api import oai as oai_mod

    root = tmp_path / "aicodebox-eph"
    monkeypatch.setattr(oai_mod, "EPHEMERAL_WORKSPACE_ROOT", root)
    monkeypatch.setattr(oai_mod, "EPHEMERAL_WORKSPACE_TTL_SECONDS", 3600)
    root.mkdir()

    stray_file = root / "stray.txt"
    stray_file.write_text("not ours")
    now = 1_000_000.0
    old = now - 7200
    os.utime(stray_file, (old, old))

    assert oai_mod.purge_stale_workspaces(now=now) == 0
    assert stray_file.exists()


def test_ephemeral_cleanup_refuses_path_outside_root(monkeypatch, tmp_path):
    """Safety: _cleanup_ephemeral_workspace must refuse to rmtree anything
    outside EPHEMERAL_WORKSPACE_ROOT (defense in depth — even if the
    path passed in came from a bug, we don't wreck the filesystem)."""
    from aicodebox.modes.api import oai as oai_mod
    monkeypatch.setattr(
        oai_mod, "EPHEMERAL_WORKSPACE_ROOT", tmp_path / "aicodebox-eph",
    )

    victim = tmp_path / "important-stuff"
    victim.mkdir()
    (victim / "do-not-delete.txt").write_text("precious")

    # The helper must refuse and the file must still exist after.
    oai_mod._cleanup_ephemeral_workspace(str(victim))
    assert (victim / "do-not-delete.txt").exists(), (
        "_cleanup_ephemeral_workspace must refuse paths outside the root"
    )
