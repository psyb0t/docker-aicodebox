"""Standalone MCP server mode.

Runs the same MCP ASGI app that gets mounted inside API mode at /mcp, but as
its own uvicorn process on AICODEBOX_MCP_MODE_PORT. Designed to coexist with
any foreground mode (telegram / cron / passthrough) — the entrypoint spawns
this in the background, exec's the foreground; the container exits when the
foreground exits.

Bearer auth via AICODEBOX_MCP_MODE_TOKEN (no fallback to API token).
"""
