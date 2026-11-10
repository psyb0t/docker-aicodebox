"""Contract tests for the API-mounted streamable MCP endpoint."""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from fastapi.testclient import TestClient


_ALLOWED_HOST = "mcp.test"
_INIT_HEADERS = {
    "accept": "application/json, text/event-stream",
    "content-type": "application/json",
    "host": _ALLOWED_HOST,
}
_INIT_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-03-26",
        "capabilities": {},
        "clientInfo": {"name": "aicodebox-test", "version": "1"},
    },
}


@pytest.fixture
def api_server_with_mcp(monkeypatch) -> Any:
    monkeypatch.setenv("AICODEBOX_MCP_MODE", "1")
    monkeypatch.setenv("AICODEBOX_MCP_MODE_ALLOWED_HOSTS", _ALLOWED_HOST)
    from aicodebox.modes.api import server as server_module

    server_module = importlib.reload(server_module)
    yield server_module
    monkeypatch.delenv("AICODEBOX_MCP_MODE", raising=False)
    monkeypatch.delenv("AICODEBOX_MCP_MODE_ALLOWED_HOSTS", raising=False)
    importlib.reload(server_module)


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_api_mcp_initialize_accepts_both_path_spellings(
    api_server_with_mcp,
    path: str,
) -> None:
    with TestClient(api_server_with_mcp.app, follow_redirects=False) as client:
        response = client.post(path, headers=_INIT_HEADERS, json=_INIT_BODY)

    assert response.status_code == 200
    assert response.headers.get("location") is None


def test_api_mcp_rejects_unallowed_host(api_server_with_mcp) -> None:
    headers = {**_INIT_HEADERS, "host": "untrusted.test"}
    with TestClient(api_server_with_mcp.app, follow_redirects=False) as client:
        response = client.post("/mcp/", headers=headers, json=_INIT_BODY)

    assert response.status_code == 421


def test_api_mcp_rejects_unallowed_origin(api_server_with_mcp) -> None:
    headers = {**_INIT_HEADERS, "origin": "https://untrusted.test"}
    with TestClient(api_server_with_mcp.app, follow_redirects=False) as client:
        response = client.post("/mcp/", headers=headers, json=_INIT_BODY)

    assert response.status_code == 403


def test_standalone_mcp_accepts_an_allowed_host(monkeypatch) -> None:
    monkeypatch.setenv("AICODEBOX_MCP_MODE_ALLOWED_HOSTS", _ALLOWED_HOST)
    from aicodebox.modes.api.mcp_server import MCPWithAuth, build_mcp_app

    app = MCPWithAuth(build_mcp_app())
    with TestClient(app, follow_redirects=False) as client:
        response = client.post("/", headers=_INIT_HEADERS, json=_INIT_BODY)

    assert response.status_code == 200


def test_env_example_copied_as_is_keeps_mcp_working(monkeypatch) -> None:
    # Copying .env.example unchanged must not set an MCP allowlist to an
    # empty value, which the allowlist parser rejects.
    from pathlib import Path

    from aicodebox.modes.api.mcp_server import _transport_security

    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    for raw in env_example.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        monkeypatch.setenv(key.strip(), value.strip())

    settings = _transport_security()

    assert "localhost" in settings.allowed_hosts
    assert "http://localhost:*" in settings.allowed_origins


def _call_run_prompt(app: Any, arguments: dict[str, Any]) -> dict[str, Any]:
    """Initialize an MCP session and call run_prompt; return the result."""
    import json

    with TestClient(app, follow_redirects=False) as client:
        init = client.post("/", headers=_INIT_HEADERS, json=_INIT_BODY)
        assert init.status_code == 200
        headers = {
            **_INIT_HEADERS,
            "mcp-session-id": init.headers["mcp-session-id"],
        }
        client.post(
            "/",
            headers=headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        )
        response = client.post(
            "/",
            headers=headers,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "run_prompt", "arguments": arguments},
            },
        )
    assert response.status_code == 200
    for line in response.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):])["result"]
    return response.json()["result"]


@pytest.fixture
def mcp_app(monkeypatch, tmp_workspace) -> Any:
    from aicodebox.modes.api import workspace
    from aicodebox.modes.api.mcp_server import MCPWithAuth, build_mcp_app

    monkeypatch.setenv("AICODEBOX_MCP_MODE_ALLOWED_HOSTS", _ALLOWED_HOST)
    monkeypatch.setattr(workspace, "ROOT_WORKSPACE", str(tmp_workspace))
    return MCPWithAuth(build_mcp_app())


def _fake_agent(monkeypatch, texts: list[str]) -> list[Any]:
    from aicodebox.adapters.base import RunResult
    from aicodebox.modes.api import mcp_server
    from aicodebox.shared import runner

    specs: list[Any] = []

    def fake(spec, proc_hook=None):
        del proc_hook
        specs.append(spec)
        text = texts[min(len(specs), len(texts)) - 1]
        return RunResult(text=text, raw_stdout=text, raw_stderr="", exit_code=0)

    monkeypatch.setattr(runner, "run", fake)
    monkeypatch.setattr(mcp_server, "run_agent", fake)
    return specs


def test_run_prompt_refuses_busy_workspace(monkeypatch, mcp_app, tmp_workspace):
    from aicodebox.modes.api.runs import REGISTRY

    specs = _fake_agent(monkeypatch, ["hello"])
    workspace = str(tmp_workspace)
    assert REGISTRY.acquire_workspace(workspace)
    try:
        result = _call_run_prompt(mcp_app, {"prompt": "hi"})
    finally:
        REGISTRY.release_workspace(workspace)

    assert result["isError"] is True
    assert "busy" in result["content"][0]["text"]
    assert specs == []


def test_run_prompt_releases_workspace_lock(monkeypatch, mcp_app, tmp_workspace):
    from aicodebox.modes.api.runs import REGISTRY

    _fake_agent(monkeypatch, ["hello"])
    result = _call_run_prompt(mcp_app, {"prompt": "hi"})

    assert result["content"][0]["text"] == "hello"
    assert REGISTRY.acquire_workspace(str(tmp_workspace))
    REGISTRY.release_workspace(str(tmp_workspace))


def test_run_prompt_retries_schema_failure(monkeypatch, mcp_app):
    specs = _fake_agent(monkeypatch, ["not json", '{"answer": "x"}'])
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    result = _call_run_prompt(mcp_app, {"prompt": "hi", "json_schema": schema})

    assert len(specs) == 2
    assert result.get("isError") is not True
    assert result["content"][0]["text"] == '{"answer": "x"}'


def test_run_prompt_runs_agent_off_the_event_loop(monkeypatch, mcp_app):
    # The agent call blocks for the whole run. In API mode the MCP app shares
    # the server's event loop, so it must run in a worker thread.
    import asyncio

    from aicodebox.adapters.base import RunResult
    from aicodebox.modes.api import mcp_server

    seen: list[bool] = []

    def fake(spec, proc_hook=None):
        del spec, proc_hook
        try:
            asyncio.get_running_loop()
            seen.append(True)
        except RuntimeError:
            seen.append(False)
        return RunResult(text="ok", raw_stdout="ok", raw_stderr="", exit_code=0)

    monkeypatch.setattr(mcp_server, "run_agent", fake)
    result = _call_run_prompt(mcp_app, {"prompt": "hi"})

    assert result["content"][0]["text"] == "ok"
    assert seen == [False]
