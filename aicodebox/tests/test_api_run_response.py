"""Unit tests for the /run response payload shape.

Targets ``aicodebox.modes.api.server._invoke`` directly. Verifies the
include_raw opt-in (default-off), the unconditional stderr-on-failure
inclusion, and the native event response contract.
"""

from __future__ import annotations

import sys

import pytest
from fastapi.testclient import TestClient

from aicodebox.adapters import base as adapter_base
from aicodebox.adapters.base import RunResult


class _RecordingAdapter(adapter_base.AgentAdapter):
    """Adapter whose run path is monkey-patched by the test."""

    name = "recording"
    binary = "/bin/true"

    def build_argv(self, req):
        del req
        return [self.binary]


@pytest.fixture
def recording_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._recording", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_api_run_response:_RecordingAdapter",
    )
    adapter_base.reset_adapter_cache()
    yield _RecordingAdapter
    adapter_base.reset_adapter_cache()


def _patch_run_agent(monkeypatch, result: RunResult):
    """Make _invoke's run_agent return a canned RunResult.

    Patches both the ``server`` re-export and the underlying
    ``shared.runner.run`` so the schema-retry path (which lives in
    ``shared.runner.run_with_json_retry``) is intercepted as well.
    """
    from aicodebox.modes.api import server as server_mod
    from aicodebox.shared import runner as runner_mod

    def fake(spec, proc_hook=None):
        del spec, proc_hook
        return result

    monkeypatch.setattr(server_mod, "run_agent", fake)
    monkeypatch.setattr(runner_mod, "run", fake)


def _build_spec(
    workspace: str,
    output_format: str = "text",
    event_mode: str = "none",
):
    from aicodebox.shared.runner import RunSpec
    return RunSpec(
        prompt="x",
        workspace=workspace,
        output_format=output_format,
        event_mode=event_mode,
    )


# ── default: stdout/stderr omitted ────────────────────────────────────────────


def test_default_omits_stdout_and_stderr(recording_adapter, monkeypatch, tmp_path):
    del recording_adapter
    _patch_run_agent(monkeypatch, RunResult(
        text="answer",
        raw_stdout="full stdout here",
        raw_stderr="",
        exit_code=0,
    ))
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_spec(str(tmp_path)), "rid-1", include_raw=False)
    assert payload["text"] == "answer"
    assert payload["exitCode"] == 0
    assert "stdout" not in payload
    assert "stderr" not in payload


# ── include_raw=true surfaces both ────────────────────────────────────────────


def test_include_raw_surfaces_stdout_and_stderr(
    recording_adapter, monkeypatch, tmp_path,
):
    del recording_adapter
    _patch_run_agent(monkeypatch, RunResult(
        text="answer",
        raw_stdout="full stdout here",
        raw_stderr="some stderr",
        exit_code=0,
    ))
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_spec(str(tmp_path)), "rid-2", include_raw=True)
    assert payload["stdout"] == "full stdout here"
    assert payload["stderr"] == "some stderr"


# ── stderr always included on non-zero exit ───────────────────────────────────


def test_nonzero_exit_always_includes_stderr(
    recording_adapter, monkeypatch, tmp_path,
):
    del recording_adapter
    _patch_run_agent(monkeypatch, RunResult(
        text="",
        raw_stdout="...",
        raw_stderr="boom: missing tool",
        exit_code=2,
    ))
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_spec(str(tmp_path)), "rid-3", include_raw=False)
    assert payload["exitCode"] == 2
    assert payload["stderr"] == "boom: missing tool"
    assert "stdout" not in payload  # still no stdout without include_raw


# ── text mode (no schema) is lean ────────────────────────────────────────────


def test_text_mode_is_lean(recording_adapter, monkeypatch, tmp_path):
    """No jsonSchema and no eventMode → response carries only text.
    Even if the adapter happens to populate sessionId / usage / events,
    they're suppressed on the wire because the caller didn't ask for the
    full surface."""
    _patch_run_agent(monkeypatch, RunResult(
        text="hi", raw_stdout="...", raw_stderr="", exit_code=0,
        session_id="sess-abc", usage={"input_tokens": 10},
        events=[{"type": "assistant", "text": "hi"}],
    ))
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_spec(str(tmp_path)), "rid-5", include_raw=False)
    assert payload["text"] == "hi"
    assert "events" not in payload
    assert "sessionId" not in payload
    assert "usage" not in payload
    assert "json" not in payload


# ── full native event mode ───────────────────────────────────────────────────


def test_full_event_mode_wraps_native_events(
    recording_adapter, monkeypatch, tmp_path,
):
    del recording_adapter
    _patch_run_agent(monkeypatch, RunResult(
        text="answer", raw_stdout="...", raw_stderr="", exit_code=0,
        session_id="sess-events", usage={"input_tokens": 3},
        events=[
            {"type": "tool_execution_start", "toolName": "bash"},
            {"type": "message_update", "thinking": "checking"},
        ],
    ))
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_spec(
        str(tmp_path), event_mode="full",
    ), "rid-6", include_raw=False)
    assert payload["sessionId"] == "sess-events"
    assert payload["usage"] == {"input_tokens": 3}
    assert payload["events"] == [
        {
            "sequence": 1,
            "attempt": 0,
            "backend": "recording",
            "eventType": "tool_execution_start",
            "event": {"type": "tool_execution_start", "toolName": "bash"},
        },
        {
            "sequence": 2,
            "attempt": 0,
            "backend": "recording",
            "eventType": "message_update",
            "event": {"type": "message_update", "thinking": "checking"},
        },
    ]


def test_post_run_full_event_mode_contract(
    recording_adapter, monkeypatch, tmp_path,
):
    """The public route returns the standardized full-event envelope."""
    del recording_adapter
    _patch_run_agent(monkeypatch, RunResult(
        text="answer", raw_stdout="...", raw_stderr="", exit_code=0,
        events=[{"type": "tool_execution_end", "toolName": "bash"}],
    ))
    from aicodebox.modes.api import server as server_mod

    monkeypatch.setattr(server_mod, "resolve_workspace", lambda _: str(tmp_path))
    with TestClient(server_mod.app) as client:
        response = client.post(
            "/run",
            json={"prompt": "x", "eventMode": "full"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "answer"
    assert payload["events"] == [{
        "sequence": 1,
        "attempt": 0,
        "backend": "recording",
        "eventType": "tool_execution_end",
        "event": {"type": "tool_execution_end", "toolName": "bash"},
    }]


# ── json mode: 3x retry on parse failure ─────────────────────────────────────


def _build_json_spec(
    workspace: str,
    schema: dict | None = None,
    event_mode: str = "full",
):
    """Construct a schema-driven request with independent event retention."""
    from aicodebox.shared.runner import RunSpec
    return RunSpec(
        prompt="give me json",
        workspace=workspace,
        output_format="json",
        json_schema=schema if schema is not None else {"type": "object"},
        event_mode=event_mode,
    )


def _patch_run_agent_sequence(monkeypatch, results: list[RunResult]):
    """Each call to run_agent returns the next RunResult from the list.

    Lets the test simulate multi-attempt sequences — initial run + retries.
    Patches both the ``server`` re-export and the underlying
    ``shared.runner.run`` so the schema-retry path (which lives in
    ``shared.runner.run_with_json_retry`` and calls ``run`` directly) is
    intercepted as well.
    """
    from aicodebox.modes.api import server as server_mod
    from aicodebox.shared import runner as runner_mod
    calls = {"n": 0}

    def fake(spec, proc_hook=None):
        del spec, proc_hook
        i = calls["n"]
        calls["n"] += 1
        if i >= len(results):
            return results[-1]
        return results[i]

    monkeypatch.setattr(server_mod, "run_agent", fake)
    monkeypatch.setattr(runner_mod, "run", fake)
    return calls


def test_json_mode_success_carries_full_surface(
    recording_adapter, monkeypatch, tmp_path,
):
    """Schema and full events coexist without one selecting the other."""
    del recording_adapter
    _patch_run_agent_sequence(monkeypatch, [RunResult(
        text='{"answer": 42}',
        raw_stdout='{"answer": 42}', raw_stderr="", exit_code=0,
        session_id="sess-jm", usage={"input_tokens": 7},
        events=[{"type": "assistant", "text": '{"answer": 42}'}],
    )])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_json_spec(str(tmp_path)), "rid-j1", include_raw=False)
    assert payload["json"] == {"answer": 42}
    assert payload["text"] == '{"answer": 42}'
    assert payload["events"] == [
        {
            "sequence": 1,
            "attempt": 0,
            "backend": "recording",
            "eventType": "assistant",
            "event": {"type": "assistant", "text": '{"answer": 42}'},
        },
    ]
    assert payload["sessionId"] == "sess-jm"
    assert payload["usage"] == {"input_tokens": 7}
    assert "parseError" not in payload
    assert "jsonRetries" not in payload


def test_json_mode_with_events_disabled_omits_events(
    recording_adapter, monkeypatch, tmp_path,
):
    """A schema request can retain its JSON result without event records."""
    del recording_adapter
    _patch_run_agent_sequence(monkeypatch, [RunResult(
        text='{"answer": "quiet"}',
        raw_stdout='{"answer": "quiet"}', raw_stderr="", exit_code=0,
        events=[{"type": "tool_execution_end", "toolName": "bash"}],
    )])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(
        _build_json_spec(str(tmp_path), event_mode="none"),
        "rid-j-none",
        include_raw=False,
    )

    assert payload["json"] == {"answer": "quiet"}
    assert "events" not in payload


def test_json_mode_succeeds_on_retry(recording_adapter, monkeypatch, tmp_path):
    del recording_adapter
    calls = _patch_run_agent_sequence(monkeypatch, [
        RunResult(
            text="not actually json {",
            raw_stdout="not actually json {", raw_stderr="", exit_code=0,
            events=[{"type": "attempt", "value": 0}],
        ),
        RunResult(
            text='{"ok": true}',
            raw_stdout='{"ok": true}', raw_stderr="", exit_code=0,
            events=[{"type": "attempt", "value": 1}],
        ),
    ])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_json_spec(str(tmp_path)), "rid-j2", include_raw=False)
    assert payload["json"] == {"ok": True}
    assert "parseError" not in payload
    assert payload["jsonRetries"] == 1
    assert calls["n"] == 2  # initial + 1 retry
    assert [event["attempt"] for event in payload["events"]] == [0, 1]
    assert [event["event"]["value"] for event in payload["events"]] == [0, 1]


def test_json_mode_exhausts_retries(recording_adapter, monkeypatch, tmp_path):
    del recording_adapter
    bad = RunResult(
        text="still not json",
        raw_stdout="still not json", raw_stderr="", exit_code=0,
    )
    calls = _patch_run_agent_sequence(monkeypatch, [bad, bad, bad, bad])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_json_spec(str(tmp_path)), "rid-j3", include_raw=False)
    assert "json" not in payload
    assert payload["parseError"]
    assert payload["text"] == "still not json"
    assert payload["jsonRetries"] == 3
    assert calls["n"] == 4  # initial + 3 retries


def test_json_mode_does_not_retry_on_nonzero_exit(
    recording_adapter, monkeypatch, tmp_path,
):
    del recording_adapter
    calls = _patch_run_agent_sequence(monkeypatch, [RunResult(
        text="",
        raw_stdout="", raw_stderr="binary crashed", exit_code=2,
    )])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_json_spec(str(tmp_path)), "rid-j4", include_raw=False)
    assert payload["exitCode"] == 2
    assert "jsonRetries" not in payload
    assert calls["n"] == 1  # no retries — the agent itself died


def test_json_mode_retry_aborts_when_attempt_exits_nonzero(
    recording_adapter, monkeypatch, tmp_path,
):
    """First attempt parses badly, second attempt crashes — we abort and
    surface the crashed result without trying further retries."""
    del recording_adapter
    calls = _patch_run_agent_sequence(monkeypatch, [
        RunResult(
            text="garbage", raw_stdout="garbage", raw_stderr="", exit_code=0,
        ),
        RunResult(
            text="", raw_stdout="", raw_stderr="oops", exit_code=2,
        ),
    ])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(_build_json_spec(str(tmp_path)), "rid-j5", include_raw=False)
    assert payload["exitCode"] == 2
    assert payload["jsonRetries"] == 1
    assert payload["stderr"] == "oops"  # auto-included on non-zero exit
    assert calls["n"] == 2


# ── final output and event mode are separate ─────────────────────────────────


def test_derive_output_format_text_when_no_schema():
    from aicodebox.modes.api.server import RunBody, _derive_output_format
    body = RunBody(prompt="x")
    assert _derive_output_format(body) == "text"


def test_derive_output_format_json_when_schema_set():
    from aicodebox.modes.api.server import RunBody, _derive_output_format
    body = RunBody(prompt="x", jsonSchema={"type": "object"})
    assert _derive_output_format(body) == "json"


def test_derive_event_mode_explicit_full_without_schema():
    from aicodebox.modes.api.server import RunBody, _derive_event_mode
    assert _derive_event_mode(RunBody(prompt="x", eventMode="full")) == "full"


def test_derive_event_mode_schema_compatibility_auto():
    from aicodebox.modes.api.server import RunBody, _derive_event_mode
    body = RunBody(prompt="x", jsonSchema={"type": "object"})
    assert _derive_event_mode(body) == "full"


def test_derive_event_mode_json_verbose_compatibility_alias():
    from aicodebox.modes.api.server import RunBody, _derive_event_mode
    body = RunBody(prompt="x", outputFormat="json-verbose")
    assert _derive_event_mode(body) == "full"


def test_derive_event_mode_none_overrides_schema_compatibility():
    from aicodebox.modes.api.server import RunBody, _derive_event_mode
    body = RunBody(
        prompt="x", jsonSchema={"type": "object"}, eventMode="none",
    )
    assert _derive_event_mode(body) == "none"


def test_runbody_drops_legacy_verbose_flag():
    """The v0.5.0 ``verbose`` flag is gone in v0.6.0 — schema-set requests
    are always verbose, schema-less requests are always lean. Pydantic's
    default ``extra=ignore`` policy means stale callers passing
    ``verbose=true`` get the same lean text response everyone else does,
    not a 422. If someone re-adds the field on the model this test fails
    immediately (regression catch)."""
    from aicodebox.modes.api.server import RunBody, _derive_output_format
    body = RunBody(prompt="x", verbose=True)  # type: ignore[call-arg]
    assert not hasattr(body, "verbose")
    assert _derive_output_format(body) == "text"


def test_json_mode_schema_validation_failure_retries(
    recording_adapter, monkeypatch, tmp_path,
):
    """Decode succeeds but schema validation fails — retry still kicks in."""
    pytest.importorskip("jsonschema")
    del recording_adapter
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    calls = _patch_run_agent_sequence(monkeypatch, [
        RunResult(
            text='{"n": "not an integer"}',
            raw_stdout='{"n": "not an integer"}', raw_stderr="", exit_code=0,
        ),
        RunResult(
            text='{"n": 7}',
            raw_stdout='{"n": 7}', raw_stderr="", exit_code=0,
        ),
    ])
    from aicodebox.modes.api.server import _invoke

    payload = _invoke(
        _build_json_spec(str(tmp_path), schema=schema),
        "rid-j6", include_raw=False,
    )
    assert payload["json"] == {"n": 7}
    assert payload["jsonRetries"] == 1
    assert calls["n"] == 2
