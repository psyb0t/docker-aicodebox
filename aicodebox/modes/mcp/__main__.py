from __future__ import annotations

import logging
import os

from aicodebox.adapters import get_adapter
from aicodebox.modes.api.mcp_server import MCPWithAuth, build_mcp_app
from aicodebox.shared.logging import configure_logging

log = logging.getLogger("mcp")


def main() -> int:
    configure_logging()
    import uvicorn

    port_raw = os.environ.get("AICODEBOX_MCP_MODE_PORT", "8081")
    try:
        port = int(port_raw)
    except ValueError:
        log.error("AICODEBOX_MCP_MODE_PORT must be a number, got %r", port_raw)
        return 1
    try:
        adapter_name = get_adapter().name
    except RuntimeError:
        adapter_name = "?"

    app = MCPWithAuth(build_mcp_app())
    log.info("mcp: starting on :%d (adapter=%s)", port, adapter_name)
    uvicorn.run(app, host="0.0.0.0", port=port, log_config=None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
