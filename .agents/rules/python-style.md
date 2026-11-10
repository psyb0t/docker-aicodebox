# Python style

Prefix: PY. Applies to everything under `aicodebox/`.

- **PY1:** Every module with code (not the `__init__.py` and `__main__.py` shims) starts with `from __future__ import annotations` and uses built-in generics and `X | None`, as `aicodebox/shared/runner.py` does.
- **PY2:** `make lint` must pass: flake8 with `.flake8` (120 columns) and pyright, both from the `lint` group in `pyproject.toml`. Do not add `|| true`, blanket `# noqa` or file-level ignores. A narrow `# pyright: ignore[rule]` needs a comment saying why.
- **PY3:** Log through `logging.getLogger("<area>")` (`runner`, `api.oai`, `telegram.bot`, `cron`). Each mode's `main()` calls `aicodebox.shared.logging.configure_logging()` first. Never `print` except the Telegram missing-token message that predates logging.
- **PY4:** Never log a token, a full prompt or a request body. Existing logs record lengths and the first 200 characters of stderr; keep to that.
- **PY5:** Broad `except Exception` is allowed only around best-effort work that must not kill a server loop (purge loops, Telegram sends, proc hooks), and carries `# noqa: BLE001`. Everywhere else catch the specific exception.
- **PY6:** Wire names in the HTTP API are camelCase through pydantic aliases with `populate_by_name`, as `RunBody` in `aicodebox/modes/api/server.py`. Python names stay snake_case.
- **PY7:** Pin every new runtime dependency with `==` in `pyproject.toml`, then refresh `uv.lock` with uv 0.13.0 (`make pkg-lock` or the pinned uv container) under the existing `exclude-newer` cutoff. Never edit `uv.lock` by hand.
