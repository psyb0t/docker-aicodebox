"""Translate a mode-side request (API JSON body, cron job, telegram message)
into a RunRequest and invoke the configured agent adapter."""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from aicodebox.adapters import (
    ProcHook,
    RunRequest,
    RunResult,
    StreamEvent,
    get_adapter,
    parse_json_response,
)

log = logging.getLogger("runner")

JSON_RETRY_MAX = 3


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


def spec_to_request(
    spec: RunSpec, proc_hook: ProcHook = None,
) -> RunRequest:
    """Convert a mode-facing RunSpec into an adapter-facing RunRequest.

    Public — modes that need a RunRequest for non-run code paths (e.g.
    ``adapter.parse_events`` invoked after a completed run) reuse this so
    the conversion stays in one place.
    """
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


# Backwards-compat alias for internal callers.
_to_request = spec_to_request


def _json_retry_prompt(
    prev_text: str, parse_error: str, schema: dict | None,
) -> str:
    """Build the re-prompt sent to the agent after a JSON parse failure.

    The agent gets its own prior output back verbatim plus the specific
    decode / schema-validation error, so it can self-correct rather than
    guess what went wrong. Schema (when present) is re-stated to keep the
    correction context-complete — the prior turn may have been many tokens
    ago for the model.
    """
    parts = [
        "Your previous response could not be parsed as JSON.",
        f"Error: {parse_error}",
        "",
        "Previous response:",
        prev_text or "(empty)",
        "",
        "Re-emit ONLY valid JSON. No prose, no markdown, no code fences, "
        "no commentary before or after.",
    ]
    if schema is not None:
        parts.append("")
        parts.append(
            f"The JSON must conform to this schema: {json.dumps(schema)}",
        )
    return "\n".join(parts)


def _accumulate_usage(
    target: dict[str, Any], src: dict[str, Any] | None,
) -> None:
    """Sum compatible numeric usage fields from ``src`` into ``target``.

    Each retry runs the agent again and the provider bills for every
    attempt. Without this accumulator the caller sees only the FINAL
    attempt's tokens — under-counting the actual cost. Non-numeric or
    type-mismatched fields keep the first occurrence (caller can still
    surface things like model id / request id sensibly).
    """
    if not src:
        return
    for key, val in src.items():
        if isinstance(val, bool):
            # bool is a subclass of int — treat as non-numeric.
            target.setdefault(key, val)
            continue
        if isinstance(val, (int, float)):
            existing = target.get(key, 0)
            if isinstance(existing, bool) or not isinstance(
                existing, (int, float),
            ):
                target.setdefault(key, val)
                continue
            target[key] = existing + val
            continue
        target.setdefault(key, val)


def run_with_json_retry(
    spec: RunSpec,
    proc_hook: ProcHook = None,
    max_retries: int = JSON_RETRY_MAX,
) -> tuple[RunResult, Any, str | None, int]:
    """Run a schema-validated spec with up to ``max_retries`` re-prompts on
    parse / validation failure. Returns
    ``(final_result, parsed, parse_error, retries_used)``.

    Callable for both ``output_format=json`` and
    ``output_format=json-verbose`` — schema validation runs against
    ``result.text`` (the final assistant turn) regardless of which mode
    the adapter ran in. Retries abort early if any attempt exits non-zero
    — that means the agent itself failed (timeout, missing binary,
    internal error) and replaying the prompt won't help. Each retry uses
    ``no_continue=True`` so the model gets a fresh session whose only
    history is the corrective prompt; mixing the bad turn into ongoing
    context tends to make models double down on it.

    ``final_result.usage`` is the SUMMED usage across every attempt
    (initial + each retry). Reporting only the last attempt's usage
    would under-count what the provider actually billed, since every
    attempt is its own paid LLM call.
    """
    result = run(spec, proc_hook=proc_hook)
    accumulated_usage: dict[str, Any] = {}
    _accumulate_usage(accumulated_usage, result.usage)

    parsed = result.parsed
    parse_error = result.parse_error
    if parsed is None and parse_error is None and result.exit_code == 0:
        parsed, parse_error = parse_json_response(
            result.text or "", spec.json_schema,
        )

    # Per-attempt breakdown — initial + each retry. Caller can render
    # "retry 2/3 cost X tokens" / debug which attempts failed which way.
    attempts: list[dict[str, Any]] = [{
        "index": 0,
        "usage": dict(result.usage) if result.usage else None,
        "exitCode": result.exit_code,
        "parseError": parse_error,
    }]

    retries = 0
    while parse_error and result.exit_code == 0 and retries < max_retries:
        retries += 1
        log.info(
            "json retry %d/%d (error: %s)", retries, max_retries, parse_error,
        )
        retry_spec = dataclasses.replace(
            spec,
            prompt=_json_retry_prompt(
                result.text or "", parse_error, spec.json_schema,
            ),
            no_continue=True,
            resume=None,
        )
        result = run(retry_spec, proc_hook=proc_hook)
        _accumulate_usage(accumulated_usage, result.usage)
        if result.exit_code != 0:
            attempts.append({
                "index": retries,
                "usage": dict(result.usage) if result.usage else None,
                "exitCode": result.exit_code,
                "parseError": None,
            })
            break
        parsed = result.parsed
        parse_error = result.parse_error
        if parsed is None and parse_error is None:
            parsed, parse_error = parse_json_response(
                result.text or "", spec.json_schema,
            )
        attempts.append({
            "index": retries,
            "usage": dict(result.usage) if result.usage else None,
            "exitCode": result.exit_code,
            "parseError": parse_error,
        })

    # Overwrite final result's usage with the cross-attempt total so
    # downstream callers (server.py /run payload, oai.py OAI envelope)
    # see the real billable cost without needing to know retries
    # happened. The per-attempt breakdown stays available via
    # result.attempts.
    if accumulated_usage:
        result.usage = accumulated_usage
    result.attempts = attempts

    return result, parsed, parse_error, retries


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
