"""FastAPI server for aicodebox.

Endpoints:
  GET  /healthz                          → { ok, adapter }
  POST /run                              → sync or async run via active adapter
  GET  /run/result?runId=<id>            → poll async run

Auth: optional bearer token via AICODEBOX_API_MODE_TOKEN.
Port: AICODEBOX_API_MODE_PORT (default 8080).
"""

from __future__ import annotations

import asyncio
import dataclasses
import json as _json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from aicodebox.adapters import get_adapter
from aicodebox.adapters.base import parse_json_response
from aicodebox.modes.api.auth import check_bearer
from aicodebox.modes.api.files import router as files_router
from aicodebox.modes.api.oai import (
    purge_stale_uploads as _purge_oai_uploads,
    router as oai_router,
)
from aicodebox.modes.api.runs import REGISTRY as RUNS
from aicodebox.modes.api.workspace import WorkspaceError, resolve as resolve_workspace
from aicodebox.shared.logging import configure_logging
from aicodebox.shared.runner import (
    RunSpec,
    run as run_agent,
    spec_to_request,
    validate_spec,
)

log = logging.getLogger("api")

PURGE_INTERVAL_SECONDS = 600
JSON_RETRY_MAX = 3


_mcp_lifespan_cm: Any = None


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    task = asyncio.create_task(_purge_loop())
    try:
        if _mcp_lifespan_cm is not None:
            async with _mcp_lifespan_cm:
                yield
        else:
            yield
    finally:
        task.cancel()


async def _purge_loop() -> None:
    while True:
        try:
            n = RUNS.purge_stale()
            if n:
                log.info("purged %d stale runs", n)
        except Exception:  # noqa: BLE001
            log.exception("purge loop error")
        try:
            u = _purge_oai_uploads()
            if u:
                log.info("purged %d stale oai uploads", u)
        except Exception:  # noqa: BLE001
            log.exception("oai upload purge error")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


app = FastAPI(lifespan=_lifespan)
app.include_router(files_router)
app.include_router(oai_router)


def _maybe_mount_mcp() -> None:
    """Mount /mcp inside the API server when AICODEBOX_MCP_MODE=1.

    Auth is gated by AICODEBOX_MCP_MODE_TOKEN (no fallback to API token —
    MCP runs as its own surface, with its own bearer)."""
    global _mcp_lifespan_cm
    if os.environ.get("AICODEBOX_MCP_MODE") != "1":
        return
    try:
        from aicodebox.modes.api.mcp_server import MCPWithAuth, build_mcp_app
        mcp_app = build_mcp_app()
    except Exception:  # noqa: BLE001
        log.exception("mcp: failed to build MCP app — /mcp not mounted")
        return
    app.mount("/mcp", MCPWithAuth(mcp_app))
    _mcp_lifespan_cm = mcp_app.router.lifespan_context(mcp_app)
    log.info("mcp: mounted /mcp")


_maybe_mount_mcp()


class RunBody(BaseModel):
    prompt: str = Field(..., min_length=1)
    workspace: str | None = None
    model: str | None = None
    system_prompt: str | None = Field(default=None, alias="systemPrompt")
    append_system_prompt: str | None = Field(
        default=None, alias="appendSystemPrompt",
    )
    # When set, the agent is invoked in JSON mode (under the hood: the
    # adapter's ``json-verbose`` output_format, so the event stream is
    # available too) and the response carries the full diagnostic surface:
    # ``text`` + ``json`` (decoded + schema-validated) + ``events``
    # (tool calls / thinking / per-turn metadata) + ``sessionId`` + ``usage``.
    # On parse / validation failure the wrapper retries up to 3 times,
    # surfacing ``parseError`` + ``jsonRetries`` in place of ``json`` if all
    # attempts fail. When unset the response is lean: just ``text``.
    json_schema: dict | None = Field(default=None, alias="jsonSchema")
    no_continue: bool = Field(default=False, alias="noContinue")
    resume: str | None = None
    async_: bool = Field(default=False, alias="async")
    fire_and_forget: bool = Field(default=False, alias="fireAndForget")
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")
    thinking: str | None = None
    tools_allowlist: list[str] | None = Field(default=None, alias="toolsAllowlist")
    no_tools: bool = Field(default=False, alias="noTools")
    extra_args: list[str] | None = Field(default=None, alias="extraArgs")
    # Default-off — raw stdout/stderr are large (especially when the adapter
    # ran in json-verbose mode for a schema-driven call) and most callers
    # don't need the bytes. Opt in to receive them. On non-zero exit
    # ``stderr`` is always included, since that's the only diagnostic when
    # text is empty.
    include_raw: bool = Field(default=False, alias="includeRaw")

    model_config = {"populate_by_name": True}


def _derive_output_format(body: RunBody) -> str:
    """Pick the adapter's ``output_format`` for this request.

    Only one bit drives the choice: did the caller pass a ``jsonSchema``?

      jsonSchema set → ``json-verbose`` (full event stream, schema-validated
                       final assistant text on top — the response carries
                       text + json + events + sessionId + usage)
      no jsonSchema  → ``text``         (lean prose, no events)"""
    if body.json_schema is not None:
        return "json-verbose"
    return "text"


def _build_spec(body: RunBody) -> tuple[RunSpec, str]:
    try:
        workspace_path = resolve_workspace(body.workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    output_format = _derive_output_format(body)
    try:
        spec = RunSpec(
            prompt=body.prompt,
            workspace=workspace_path,
            model=body.model,
            system_prompt=body.system_prompt,
            append_system_prompt=body.append_system_prompt,
            json_schema=body.json_schema,
            output_format=output_format,
            no_continue=body.no_continue,
            resume=body.resume,
            timeout_seconds=body.timeout_seconds,
            thinking=body.thinking,
            tools_allowlist=body.tools_allowlist,
            no_tools=body.no_tools,
            extra_args=list(body.extra_args or []),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        validate_spec(spec)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return spec, workspace_path


def _retry_prompt(prev_text: str, parse_error: str, schema: dict | None) -> str:
    """Build the re-prompt sent to the agent after a JSON parse failure.

    The agent gets its own prior output back verbatim plus the specific
    decode/schema-validation error, so it can self-correct rather than
    guess what went wrong. Schema (when present) is re-stated to keep the
    correction context-complete — the prior turn may have been many tokens
    ago for the model."""
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
            f"The JSON must conform to this schema: {_json.dumps(schema)}",
        )
    return "\n".join(parts)


def _run_json_with_retry(
    spec: RunSpec, run_id: str, max_retries: int = JSON_RETRY_MAX,
) -> tuple[Any, Any, str | None, int]:
    """Run a schema-validated spec with up to ``max_retries`` re-prompts
    on parse failure. Returns ``(result, parsed, parse_error, retries_used)``.

    Callable for both ``output_format=json`` and ``output_format=json-verbose``
    — the schema validation runs against ``result.text`` (the final
    assistant turn) regardless of which mode the adapter ran in. Retries
    abort early if any attempt exits non-zero — that means the agent itself
    failed (timeout, missing binary, internal error) and replaying the prompt
    won't help. Each retry uses ``no_continue=True`` so the model gets a
    fresh session whose only history is the corrective prompt; mixing the
    bad turn into ongoing context tends to make models double down on it.
    """
    def hook(proc: Any) -> None:
        RUNS.register_proc(run_id, proc)

    result = run_agent(spec, proc_hook=hook)

    parsed = result.parsed
    parse_error = result.parse_error
    if parsed is None and parse_error is None and result.exit_code == 0:
        parsed, parse_error = parse_json_response(
            result.text or "", spec.json_schema,
        )

    retries = 0
    while parse_error and result.exit_code == 0 and retries < max_retries:
        retries += 1
        log.info(
            "json retry %d/%d for run %s (error: %s)",
            retries, max_retries, run_id, parse_error,
        )
        retry_spec = dataclasses.replace(
            spec,
            prompt=_retry_prompt(result.text or "", parse_error, spec.json_schema),
            no_continue=True,
            resume=None,
        )
        result = run_agent(retry_spec, proc_hook=hook)
        if result.exit_code != 0:
            break
        parsed = result.parsed
        parse_error = result.parse_error
        if parsed is None and parse_error is None:
            parsed, parse_error = parse_json_response(
                result.text or "", spec.json_schema,
            )

    return result, parsed, parse_error, retries


def _invoke(
    spec: RunSpec, run_id: str, include_raw: bool,
) -> dict[str, Any]:
    def hook(proc: Any) -> None:
        RUNS.register_proc(run_id, proc)

    # ── dispatch ────────────────────────────────────────────────────────
    # Schema-driven runs go through the retry helper — it re-prompts the
    # agent up to 3 times on decode / schema-validation failure. Plain
    # text runs are a single invocation.
    has_schema = spec.json_schema is not None
    if has_schema:
        result, parsed, parse_error, retries = _run_json_with_retry(
            spec, run_id,
        )
    else:
        result = run_agent(spec, proc_hook=hook)
        parsed = None
        parse_error = None
        retries = 0

    # ── payload shaping ─────────────────────────────────────────────────
    # Two wire shapes, picked by jsonSchema:
    #
    #   no schema  → {exitCode, text}
    #   schema set → {exitCode, text, events, sessionId, usage,
    #                 json | (parseError + jsonRetries)}
    #
    # Schema mode is always-verbose: the caller gets the full diagnostic
    # surface (events, sessionId, usage) alongside the validated json.
    payload: dict[str, Any] = {"exitCode": result.exit_code, "text": result.text}

    if has_schema:
        try:
            events = get_adapter().parse_events(
                result.raw_stdout or "", spec_to_request(spec),
            )
        except Exception:  # noqa: BLE001
            log.exception("parse_events failed for run %s", run_id)
            events = []
        payload["events"] = events
        if result.session_id:
            payload["sessionId"] = result.session_id
        if result.usage:
            payload["usage"] = result.usage
        if parse_error:
            payload["parseError"] = parse_error
        elif parsed is not None:
            payload["json"] = parsed
        if retries:
            payload["jsonRetries"] = retries

    # ── raw bytes (opt-in; stderr also when exit != 0) ──────────────────
    # json-verbose adapters can dump megabytes of stream into stdout — the
    # default keeps the wire lean. ``stderr`` is the only diagnostic when
    # ``text`` comes back empty on failure, so it's auto-included then.
    if include_raw:
        payload["stdout"] = result.raw_stdout
        payload["stderr"] = result.raw_stderr
    elif result.exit_code != 0:
        payload["stderr"] = result.raw_stderr

    return payload


@app.get("/status", dependencies=[Depends(check_bearer)])
def get_status() -> dict[str, Any]:
    return RUNS.snapshot()


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    try:
        adapter = get_adapter()
    except RuntimeError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "adapter": adapter.name}


@app.post("/run", dependencies=[Depends(check_bearer)])
def post_run(body: RunBody) -> dict[str, Any]:
    spec, workspace_path = _build_spec(body)

    if not RUNS.acquire_workspace(workspace_path):
        raise HTTPException(
            status_code=409,
            detail=f"workspace {workspace_path} is busy with another run",
        )

    include_raw = body.include_raw
    if body.async_ or body.fire_and_forget:
        run_id = RUNS.submit_async(
            workspace_path,
            lambda rid: _invoke(spec, rid, include_raw),
        )
        return {
            "runId": run_id,
            "workspace": workspace_path,
            "status": "running",
            "fireAndForget": body.fire_and_forget,
        }

    try:
        run_id, result, err = RUNS.run_sync(
            workspace_path,
            lambda rid: _invoke(spec, rid, include_raw),
        )
    finally:
        RUNS.release_workspace(workspace_path)
    if err is not None:
        raise HTTPException(status_code=500, detail=err)
    return {"runId": run_id, "workspace": workspace_path, **result}


@app.delete("/run/{run_id}", dependencies=[Depends(check_bearer)])
def delete_run(run_id: str = Path(...)) -> dict[str, Any]:
    if not RUNS.cancel(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"runId": run_id, "status": "cancelled"}


@app.get("/run/result", dependencies=[Depends(check_bearer)])
def get_run_result(runId: str = Query(...)) -> dict[str, Any]:  # noqa: N803
    entry = RUNS.get(runId)
    if entry is None:
        raise HTTPException(status_code=404, detail="run not found")
    if entry.status == "running":
        return {
            "runId": entry.run_id,
            "workspace": entry.workspace,
            "status": "running",
        }
    payload: dict[str, Any] = {
        "runId": entry.run_id,
        "workspace": entry.workspace,
        "status": entry.status,
    }
    if entry.status == "completed" and isinstance(entry.result, dict):
        payload.update(entry.result)
    if entry.error:
        payload["error"] = entry.error
    return payload


def main() -> int:
    configure_logging()
    import uvicorn

    from aicodebox.shared.choices import available_models

    port_raw = os.environ.get("AICODEBOX_API_MODE_PORT", "8080")
    try:
        port = int(port_raw)
    except ValueError:
        log.error("AICODEBOX_API_MODE_PORT must be a number, got %r", port_raw)
        return 1
    if not available_models():
        log.error(
            "api: no models configured — set AICODEBOX_AVAILABLE_MODELS "
            "(comma-separated) or have the adapter declare available_models. "
            "/v1/models has no usable fallback (the adapter name is not a "
            "model name)."
        )
        return 1
    try:
        adapter_name = get_adapter().name
    except RuntimeError:
        adapter_name = "?"
    log.info("api: starting on :%d (adapter=%s)", port, adapter_name)
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
