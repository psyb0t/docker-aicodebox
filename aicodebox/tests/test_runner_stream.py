"""Tests for the streaming runner + default parse_stream_event contract."""

from __future__ import annotations

import sys

import pytest

from aicodebox.adapters import base as adapter_base
from aicodebox.adapters.base import RunRequest, StreamEvent
from aicodebox.shared.runner import RunSpec, run_stream


# ── default parse_stream_event ───────────────────────────────────────────────


def test_default_parse_stream_event_emits_delta_per_line():
    adapter = adapter_base.AgentAdapter()
    req = RunRequest()
    event = adapter.parse_stream_event("hello", req)
    assert event is not None
    assert event.type == "delta"
    assert event.text == "hello\n"


def test_default_parse_stream_event_skips_blank_lines():
    adapter = adapter_base.AgentAdapter()
    assert adapter.parse_stream_event("", RunRequest()) is None


# ── run_stream end-to-end via a tiny shell adapter ───────────────────────────


class _EchoAdapter(adapter_base.AgentAdapter):
    """Spawns /bin/sh which echoes three lines, then exits 0."""

    name = "echo"
    binary = "/bin/sh"

    def build_argv(self, req):
        del req
        script = "printf 'line one\\nline two\\nline three\\n'"
        return [self.binary, "-c", script]


class _FailAdapter(adapter_base.AgentAdapter):
    """Spawns /bin/sh which writes to stderr then exits 3."""

    name = "fail"
    binary = "/bin/sh"

    def build_argv(self, req):
        del req
        return [self.binary, "-c", "echo boom >&2; exit 3"]


@pytest.fixture
def echo_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._echo", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_runner_stream:_EchoAdapter",
    )
    adapter_base.reset_adapter_cache()
    yield _EchoAdapter()
    adapter_base.reset_adapter_cache()


@pytest.fixture
def fail_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._fail", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_runner_stream:_FailAdapter",
    )
    adapter_base.reset_adapter_cache()
    yield _FailAdapter()
    adapter_base.reset_adapter_cache()


async def _collect(spec: RunSpec) -> list[StreamEvent]:
    return [event async for event in run_stream(spec)]


@pytest.mark.asyncio
async def test_run_stream_emits_delta_per_line_then_stop(echo_adapter, tmp_path):
    spec = RunSpec(prompt="", workspace=str(tmp_path))
    events = await _collect(spec)

    deltas = [e for e in events if e.type == "delta"]
    assert [e.text for e in deltas] == ["line one\n", "line two\n", "line three\n"]

    assert events[-1].type == "stop"
    assert events[-1].data == {"reason": "stop"}


@pytest.mark.asyncio
async def test_run_stream_surfaces_nonzero_exit_as_error(fail_adapter, tmp_path):
    spec = RunSpec(prompt="", workspace=str(tmp_path))
    events = await _collect(spec)

    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert errors[0].data is not None
    assert errors[0].data["exit_code"] == 3
    assert "boom" in errors[0].text

    assert events[-1].type == "stop"
    assert events[-1].data == {"reason": "error"}


@pytest.mark.asyncio
async def test_run_stream_handles_missing_binary(monkeypatch, tmp_path):
    class _MissingAdapter(adapter_base.AgentAdapter):
        name = "missing"
        binary = "/no/such/binary/anywhere"

        def build_argv(self, req):
            del req
            return [self.binary]

    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._missing", sys.modules[__name__],
    )
    # Inject into THIS module's namespace so the import path resolves.
    globals()["_MissingAdapter"] = _MissingAdapter
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_runner_stream:_MissingAdapter",
    )
    adapter_base.reset_adapter_cache()
    try:
        spec = RunSpec(prompt="", workspace=str(tmp_path))
        events = await _collect(spec)
    finally:
        adapter_base.reset_adapter_cache()

    errors = [e for e in events if e.type == "error"]
    assert len(errors) == 1
    assert errors[0].data == {"exit_code": 127}
    assert "binary not found" in errors[0].text
    assert events[-1].type == "stop"
    assert events[-1].data == {"reason": "error"}
