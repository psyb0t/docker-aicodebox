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
