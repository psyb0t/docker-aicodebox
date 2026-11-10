# Modes and HTTP API

Prefix: API. Covers `aicodebox/modes/` and the mode dispatch in `entrypoint.sh`.

- **API1:** Every route except `/healthz` depends on `check_bearer` (`aicodebox/modes/api/auth.py`), either on the router or on the route. A new route does the same. MCP checks its own token in `MCPWithAuth` and never falls back to the API token, or the other way round.
- **API2:** Workspace paths from callers go through `workspace.resolve` (`/run`, OpenAI, MCP `run_prompt`), `files._resolve` or `mcp_server._resolve_path` (file access), the cron and Telegram resolvers. A new path input reuses one of them and refuses paths that leave `AICODEBOX_WORKSPACE`.
- **API3:** A run in API mode holds the workspace lock from `RUNS.acquire_workspace` until it ends, and answers 409 when the workspace is busy. Release it in `finally` (sync) or in the async wrapper. Register the process with `RUNS.register_proc` so `DELETE /run/{id}` can kill it.
- **API4:** The OpenAI endpoint keeps OpenAI's wire shapes. Extensions go in `x-aicodebox-*` headers or `aicodebox_*` response keys, and a malformed header returns 400 naming the header. The `x-claude-*` aliases stay for older clients.
- **API5:** Schema mode without a caller workspace uses an ephemeral directory under `/tmp/aicodebox/` and session-continuation retries. With a caller workspace it uses fresh-session retries. Do not switch a caller workspace to continuation.
- **API6:** Foreground priority (API, Telegram with optional in-thread cron, cron, passthrough) and MCP placement are decided in `entrypoint.sh`. A change there updates `docs/modes.md` and the `usage()` text.
- **API7:** Every new setting is an `AICODEBOX_<MODE>_MODE_<SETTING>` or bare `AICODEBOX_<SETTING>` variable, added to `.env.example`, `docs/configuration.md` and the export list in `entrypoint.sh`.
- **API8:** A change to a route, field, status code, Telegram command or cron key updates its page in `docs/` (`api.md`, `openai.md`, `mcp.md`, `telegram.md`, `cron.md`) in the same change.
