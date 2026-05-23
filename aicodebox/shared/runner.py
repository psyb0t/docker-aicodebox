"""Translate a mode-side request (API JSON body, cron job, telegram message)
into a RunRequest and invoke the configured agent adapter."""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import AsyncIterator

from aicodebox.adapters import (
    ProcHook,
    RunRequest,
    RunResult,
    StreamEvent,
    get_adapter,
)

log = logging.getLogger("runner")


@dataclass
class RunSpec:
    """Mode-facing request — what an API caller / cron job / telegram chat
    sends in. Translated into a RunRequest for the adapter."""

    prompt: str
    workspace: str
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    json_schema: dict | None = None
    output_format: str = "text"
    no_continue: bool = False
    resume: str | None = None
    timeout_seconds: int | None = None
    thinking: str | None = None
    tools_allowlist: list[str] | None = None
    no_tools: bool = False
    extra_args: list[str] = field(default_factory=list)


def validate_spec(spec: RunSpec) -> None:
    """Raise ValueError if spec is invalid. Pure check — no side effects."""
    req = _to_request(spec)
    get_adapter().validate(req)


def _to_request(spec: RunSpec, proc_hook: ProcHook = None) -> RunRequest:
    model = spec.model or os.environ.get("ANTHROPIC_MODEL") or None
    return RunRequest(
        prompt=spec.prompt,
        workspace=spec.workspace,
        model=model,
        system_prompt=spec.system_prompt,
        append_system_prompt=spec.append_system_prompt,
        json_schema=spec.json_schema,
        output_format=spec.output_format,
        no_continue=spec.no_continue,
        resume=spec.resume,
        extra_args=list(spec.extra_args),
        timeout_seconds=spec.timeout_seconds,
        proc_hook=proc_hook,
        thinking=spec.thinking,
        tools_allowlist=spec.tools_allowlist,
        no_tools=spec.no_tools,
    )


def run(spec: RunSpec, proc_hook: ProcHook = None) -> RunResult:
    adapter = get_adapter()
    req = _to_request(spec, proc_hook=proc_hook)
    adapter.validate(req)

    argv = adapter.build_argv(req)
    env = adapter.build_env(req)
    cwd = req.workspace or os.getcwd()
    log.info("running adapter=%s workspace=%s", adapter.name, cwd)
    return _run_popen(argv, cwd, env, req, adapter)


def _run_popen(
    argv: list[str],
    cwd: str,
    env: dict[str, str],
    req: RunRequest,
    adapter,
) -> RunResult:
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return RunResult(
            text="",
            raw_stdout="",
            raw_stderr=f"binary not found: {exc}",
            exit_code=127,
        )
    if req.proc_hook is not None:
        try:
            req.proc_hook(proc)
        except Exception:  # noqa: BLE001
            pass
    try:
        stdout, stderr = proc.communicate(
            input=req.prompt, timeout=req.timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        return RunResult(
            text="",
            raw_stdout=stdout or "",
            raw_stderr=f"timeout after {req.timeout_seconds}s",
            exit_code=124,
        )
    result = adapter.parse_output(stdout or "", req)
    result.raw_stdout = stdout or ""
    result.raw_stderr = stderr or ""
    result.exit_code = proc.returncode
    return result


# ── async streaming ──────────────────────────────────────────────────────────


async def run_stream(spec: RunSpec) -> AsyncIterator[StreamEvent]:
    """Run the adapter and yield StreamEvents as stdout arrives.

    Uses ``asyncio.create_subprocess_exec`` so stdout is consumed line-by-line
    while the agent is still running — the synchronous ``run()`` path buffers
    everything via ``proc.communicate()`` and only returns on exit, which
    defeats OAI streaming.

    The adapter's ``parse_stream_event`` decides what to emit per line. After
    the process exits, a terminal ``stop`` event is yielded (with reason
    ``"error"`` if the exit code is non-zero, otherwise ``"stop"``). If
    cancelled mid-flight (e.g. client disconnect on a StreamingResponse), the
    subprocess is killed in the ``finally`` block so we don't leak child
    processes.
    """
    adapter = get_adapter()
    req = _to_request(spec)
    adapter.validate(req)

    argv = adapter.build_argv(req)
    env = adapter.build_env(req)
    cwd = req.workspace or os.getcwd()
    log.info("streaming adapter=%s workspace=%s", adapter.name, cwd)

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=cwd,
            env=env,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        yield StreamEvent(
            type="error",
            text=f"binary not found: {exc}",
            data={"exit_code": 127},
        )
        yield StreamEvent(type="stop", data={"reason": "error"})
        return

    if proc.stdin is not None:
        try:
            if req.prompt:
                proc.stdin.write(req.prompt.encode())
                await proc.stdin.drain()
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, ConnectionResetError):
                pass

    stderr_buf = bytearray()

    async def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        while True:
            chunk = await proc.stderr.read(4096)
            if not chunk:
                return
            stderr_buf.extend(chunk)

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        if proc.stdout is None:
            await proc.wait()
        else:
            while True:
                try:
                    line_bytes = await asyncio.wait_for(
                        proc.stdout.readline(),
                        timeout=req.timeout_seconds,
                    )
                except asyncio.TimeoutError:
                    proc.kill()
                    yield StreamEvent(
                        type="error",
                        text=f"timeout after {req.timeout_seconds}s",
                        data={"exit_code": 124},
                    )
                    yield StreamEvent(type="stop", data={"reason": "timeout"})
                    return
                if not line_bytes:
                    break
                line = line_bytes.decode(errors="replace").rstrip("\r\n")
                event = adapter.parse_stream_event(line, req)
                if event is not None:
                    yield event
        rc = await proc.wait()
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
                await proc.wait()
            except ProcessLookupError:
                pass
        await stderr_task

    if rc != 0:
        yield StreamEvent(
            type="error",
            text=stderr_buf.decode(errors="replace")[:500],
            data={"exit_code": rc},
        )
        yield StreamEvent(type="stop", data={"reason": "error"})
        return
    yield StreamEvent(type="stop", data={"reason": "stop"})
