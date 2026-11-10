# Security

Prefix: SEC. The image runs an agent that can execute anything as a sudo-capable user, so these hold for every change.

- **SEC1:** Auth defaults stay as documented: empty token means no auth, a set token is compared exactly, API and MCP tokens are independent (`modes/api/auth.py`, `mcp_server._scope_auth_ok`). Do not add a second token source or a fallback between them.
- **SEC2:** MCP keeps DNS rebinding protection on (`_transport_security`). Defaults stay loopback-only. Never disable it or default to a wildcard.
- **SEC3:** Caller-supplied paths are confined to `AICODEBOX_WORKSPACE` (API2). Use `Path.resolve()` with `relative_to`, as `files._resolve` does, for new code; a string prefix check is not enough.
- **SEC4:** Outbound fetches on behalf of a caller go through `_is_safe_remote_url` in `oai.py` with a size cap and a timeout. Do not add another fetch path without the same guard.
- **SEC5:** Values interpolated into the entrypoint's `bash -c` string go through `printf '%q'`.
- **SEC6:** Placeholder tokens in code, tests, docs and examples match the `.gitleaks.toml` allowlist, such as `your-mcp-token`.
- **SEC7:** The Telegram bot answers every chat when no allowlist is configured. Keep that behavior documented in `docs/security.md` and `docs/telegram.md` if it is not changed, and never widen it.
