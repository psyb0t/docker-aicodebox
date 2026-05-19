"""FastAPI server for aicodebox.

Endpoints:
  GET  /healthz                          → { ok, adapter }
  POST /run                              → sync or async run via active adapter
  GET  /run/result?runId=<id>            → poll async run

Auth: optional bearer token via AICODEBOX_MODE_API_TOKEN.
Port: AICODEBOX_MODE_API_PORT (default 8080).
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Path, Query
from pydantic import BaseModel, Field

from aicodebox.adapters import get_adapter
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
    validate_spec,
)

log = logging.getLogger("api")

PURGE_INTERVAL_SECONDS = 600


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
    """Mount /mcp unconditionally — the adapter contract assumes the agent
    can run; the MCP server itself just exposes tools that invoke run()."""
    global _mcp_lifespan_cm
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
    json_schema: dict | None = Field(default=None, alias="jsonSchema")
    output_format: str = Field(default="text", alias="outputFormat")
    no_continue: bool = Field(default=False, alias="noContinue")
    resume: str | None = None
    async_: bool = Field(default=False, alias="async")
    fire_and_forget: bool = Field(default=False, alias="fireAndForget")
    timeout_seconds: int | None = Field(default=None, alias="timeoutSeconds")
    thinking: str | None = None
    tools_allowlist: list[str] | None = Field(default=None, alias="toolsAllowlist")
    no_tools: bool = Field(default=False, alias="noTools")
    extra_args: list[str] | None = Field(default=None, alias="extraArgs")

    model_config = {"populate_by_name": True}


def _build_spec(body: RunBody) -> tuple[RunSpec, str]:
    try:
        workspace_path = resolve_workspace(body.workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        spec = RunSpec(
            prompt=body.prompt,
            workspace=workspace_path,
            model=body.model,
            system_prompt=body.system_prompt,
            append_system_prompt=body.append_system_prompt,
            json_schema=body.json_schema,
            output_format=body.output_format,
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


def _invoke(spec: RunSpec, run_id: str) -> dict[str, Any]:
    def hook(proc: Any) -> None:
        RUNS.register_proc(run_id, proc)

    result = run_agent(spec, proc_hook=hook)
    payload: dict[str, Any] = {
        "exitCode": result.exit_code,
        "stdout": result.raw_stdout,
        "stderr": result.raw_stderr,
        "text": result.text,
    }
    if result.parsed is not None:
        payload["parsed"] = result.parsed
    if result.parse_error is not None:
        payload["parseError"] = result.parse_error
    if result.session_id:
        payload["sessionId"] = result.session_id
    if result.usage:
        payload["usage"] = result.usage
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

    if body.async_ or body.fire_and_forget:
        run_id = RUNS.submit_async(
            workspace_path, lambda rid: _invoke(spec, rid),
        )
        return {
            "runId": run_id,
            "workspace": workspace_path,
            "status": "running",
            "fireAndForget": body.fire_and_forget,
        }

    try:
        run_id, result, err = RUNS.run_sync(
            workspace_path, lambda rid: _invoke(spec, rid),
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

    port_raw = os.environ.get("AICODEBOX_MODE_API_PORT", "8080")
    try:
        port = int(port_raw)
    except ValueError:
        log.error("AICODEBOX_MODE_API_PORT must be a number, got %r", port_raw)
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
