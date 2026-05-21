"""MCP server exposed via streamable-http at /mcp.

Tools:
  - run_prompt(prompt, ...) → invoke the active agent, return its text
  - list_files(path)        → list workspace tree
  - read_file(path)         → return text content
  - write_file(path, text)  → write content (creates parents)
  - delete_file(path)       → remove file (refuses directories)

Auth: bearer token via AICODEBOX_MCP_MODE_TOKEN, checked at ASGI scope level
because FastMCP does not have built-in auth. Token may also arrive as the
``apiToken`` query parameter for clients that can't set headers.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from aicodebox.modes.api.workspace import ROOT_WORKSPACE, resolve as resolve_workspace
from aicodebox.shared.runner import RunSpec, run as run_agent

log = logging.getLogger("api.mcp")

_TOKEN_ENV = "AICODEBOX_MCP_MODE_TOKEN"


def mcp_token() -> str:
    return os.environ.get(_TOKEN_ENV, "") or ""


def _resolve_path(path: str) -> str:
    root = Path(ROOT_WORKSPACE).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cleaned = (path or "").lstrip("/")
    if not cleaned:
        return str(root)
    target = (root / cleaned).resolve()
    try:
        target.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"path escapes workspace root: {path!r}") from exc
    return str(target)


def build_mcp_app() -> Any:
    """Construct the FastMCP ASGI app. Imported lazily so the api module
    doesn't pay the import cost / dep cost when MCP is disabled."""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP("aicodebox", streamable_http_path="/")

    @mcp.tool()
    async def run_prompt(
        prompt: str,
        workspace: str = "",
        model: str = "",
        system_prompt: str = "",
        append_system_prompt: str = "",
        no_continue: bool = True,
        resume: str = "",
        thinking: str = "",
        json_schema: dict | None = None,
    ) -> str:
        """Run a prompt through the configured agent. Returns the assistant's
        textual response. Files in the workspace can be read, written, or
        executed by the agent — prefer file-based input/output for large
        payloads instead of cramming everything into the prompt.

        Args:
            prompt: The instruction to send to the agent.
            workspace: Subpath under /workspace to use as cwd. Default: root.
            model: Override the configured model.
            system_prompt: Replace the agent's default system prompt.
            append_system_prompt: Append text to the system prompt.
            no_continue: If True (default), start fresh.
            resume: Resume a specific session id.
            thinking: Reasoning level (off/minimal/low/medium/high/xhigh).
            json_schema: If provided, instructs the agent to emit JSON matching
                the schema (post-validated).
        """
        ws = resolve_workspace(workspace) if workspace else resolve_workspace(None)
        spec = RunSpec(
            prompt=prompt,
            workspace=ws,
            model=model or None,
            system_prompt=system_prompt or None,
            append_system_prompt=append_system_prompt or None,
            no_continue=no_continue,
            resume=resume or None,
            thinking=thinking or None,
            json_schema=json_schema,
        )
        result = run_agent(spec)
        return result.text or ""

    @mcp.tool()
    async def list_files(path: str = "") -> str:
        """List files and directories under the workspace.

        Args:
            path: Subpath relative to /workspace. Empty (default) lists the root.
                  Returns a JSON object with `path` and `entries` (each entry has `name` and `type`).
        """
        try:
            full = _resolve_path(path)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        if not os.path.exists(full):
            return json.dumps({"error": f"not found: {path}"})
        if not os.path.isdir(full):
            return json.dumps({"error": "not a directory"})
        entries = []
        for name in sorted(os.listdir(full)):
            ep = os.path.join(full, name)
            entries.append({"name": name, "type": "dir" if os.path.isdir(ep) else "file"})
        return json.dumps({"path": path or "/", "entries": entries})

    @mcp.tool()
    async def read_file(path: str) -> str:
        """Read a file from the workspace and return its text contents.

        Args:
            path: Path relative to /workspace (e.g. "src/main.py").
        """
        try:
            full = _resolve_path(path)
        except ValueError as exc:
            return f"error: {exc}"
        if not os.path.isfile(full):
            return f"error: not found: {path}"
        with open(full, encoding="utf-8", errors="replace") as f:
            return f.read()

    @mcp.tool()
    async def write_file(path: str, content: str) -> str:
        """Write content to a file in the workspace. Creates parents.

        Args:
            path: Path relative to /workspace.
            content: Full text content; overwrites any existing file.
        """
        try:
            full = _resolve_path(path)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        parent = os.path.dirname(full)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return json.dumps({"status": "ok", "path": path, "size": len(content)})

    @mcp.tool()
    async def delete_file(path: str) -> str:
        """Delete a file from the workspace (directories refused).

        Args:
            path: Path relative to /workspace.
        """
        try:
            full = _resolve_path(path)
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        if not os.path.exists(full):
            return json.dumps({"error": f"not found: {path}"})
        if os.path.isdir(full):
            return json.dumps({"error": "cannot delete directories"})
        os.remove(full)
        return json.dumps({"status": "ok", "path": path})

    return mcp.streamable_http_app()


def _scope_auth_ok(scope: dict[str, Any]) -> bool:
    token = mcp_token()
    if not token:
        return True
    headers = {k: v for k, v in scope.get("headers", [])}
    auth = headers.get(b"authorization", b"").decode()
    if auth == f"Bearer {token}":
        return True
    qs = scope.get("query_string", b"").decode()
    for part in qs.split("&"):
        if part.startswith("apiToken=") and part[len("apiToken="):] == token:
            return True
    return False


class MCPWithAuth:
    """Tiny ASGI wrapper that enforces bearer-token auth before passing to MCP."""

    def __init__(self, app: Any) -> None:
        self._app = app

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self._app(scope, receive, send)
            return
        if not _scope_auth_ok(scope):
            log.warning("mcp: auth failed")
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [[b"content-type", b"application/json"]],
            })
            await send({"type": "http.response.body", "body": b'{"detail":"unauthorized"}'})
            return
        await self._app(scope, receive, send)
