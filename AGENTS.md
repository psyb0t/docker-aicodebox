# aicodebox

Instructions for AI coding agents (Codex, Claude Code and others) working in this repository. Human contributors can read it too; the user documentation is the site under `docs/`.

## Agent setup

`AGENTS.md` is the source of these instructions. `CLAUDE.md` is a committed symlink to `AGENTS.md`, and `.claude` is a committed symlink to `.agents`, so Claude Code reads the same instructions, rules and skills as Codex. Claude's Edit and Write tools refuse to write through a symlink, so edit `AGENTS.md` and `.agents/rules/*.md` directly.

## What it is

`psyb0t/aicodebox` is an agent-agnostic Docker base image. A child image (claudebox, codexbox, pibox) installs one terminal coding agent, adds a Python adapter class that turns a canonical `RunRequest` into that agent's command line, and sets `AICODEBOX_ADAPTER=pkg.module:Class`. The base then gives it an HTTP API, an OpenAI-compatible chat completions endpoint, an MCP server, a Telegram bot and a cron scheduler. The base is not a host launcher and ships no wrapper. A second image, `latest-full`, adds a pinned development toolchain on top of the minimal one.

## Architecture

- `entrypoint.sh` runs as root on every start: remaps `aicode` to the workspace owner's UID and GID, aligns the `docker` group with a mounted socket, writes the Git identity, runs init scripts once per container (marker `/var/lib/aicodebox/init-done`), sources `$HOME/.aicodebox/.<container>-auth`, then `setpriv`s to `aicode` and execs one foreground process. Priority is API, then Telegram (with cron in-thread when both flags are set), then cron, then passthrough to `AICODEBOX_AGENT_BINARY`. MCP is mounted in the API app, or started as a background `python3 -m aicodebox.modes.mcp` otherwise.
- `aicodebox/adapters/base.py`: the contract. `RunRequest`, `RunResult`, `StreamEvent`, `AgentAdapter`, `get_adapter()` (imports and caches the class named by `AICODEBOX_ADAPTER`), and the tolerant JSON parser `parse_json_response`.
- `aicodebox/shared/runner.py`: every mode calls the agent through here. `RunSpec` (mode-facing) becomes `RunRequest`, `run()` spawns the agent in its own session with the prompt on stdin, `run_stream()` yields `StreamEvent`s, and `run_with_json_retry()` re-prompts up to `JSON_RETRY_MAX` times and sums usage over attempts. Native event retention (`event_mode="full"`) lives here, not in adapters.
- `aicodebox/shared/choices.py`: model and effort lists, env override first, then the adapter's class attributes.
- `aicodebox/modes/api/`: FastAPI app (`server.py`: `/run`, `/run/result`, `/run/{id}`, `/status`, `/healthz`), `files.py` (`/files`), `oai.py` (`/openai/v1/*`, schema mode, client-executed tools, buffered SSE), `mcp_server.py` (FastMCP app plus `MCPWithAuth`), `runs.py` (in-memory run registry and per-workspace lock), `workspace.py` (subpath resolution), `auth.py` (bearer check).
- `aicodebox/modes/telegram/`: `bot.py` (python-telegram-bot polling app), `config.py` (`telegram.yml`), `overrides.py` (per-chat JSON store), `utils.py` (Markdown to Telegram HTML, chunked send), `cron_inbox.py` (reads the cron message map).
- `aicodebox/modes/cron/`: `config.py` (YAML validation) and `scheduler.py` (croniter loop, one thread per job, history directories, Telegram notifications).
- `aicodebox/modes/mcp/`: the standalone MCP process for non-API modes.

Data flow: request (HTTP, MCP tool, Telegram message, cron tick) -> mode builds a `RunSpec` -> `runner` -> `AgentAdapter.build_argv` / `build_env` -> agent subprocess in the workspace -> `AgentAdapter.parse_output` or `parse_stream_event` -> mode renders the result.

## Stack

- Python 3.14.8 in the image (`requires-python >=3.12`), built from source in `Dockerfile` with a pinned SHA-256. Node.js 24.21.0, uv 0.13.0, Docker CE.
- FastAPI, uvicorn, pydantic, python-telegram-bot, croniter, the MCP Python SDK (1.x), PyYAML, all pinned in `pyproject.toml` and locked in `uv.lock` with `exclude-newer`.
- Tests: pytest with pytest-asyncio (`asyncio_mode = "auto"`). Lint: flake8 and pyright from the `lint` dependency group.
- Image: `ubuntu:24.04` by digest, user `aicode` (UID 1000) with passwordless sudo. Platforms `linux/amd64` and `linux/arm64`.

## Layout

| Path | Contents |
| --- | --- |
| `aicodebox/` | The Python package. Shipped in both images. |
| `aicodebox/tests/` | Unit tests and the stub adapter (`conftest.py`). Excluded from the wheel. |
| `entrypoint.sh` | Startup and mode dispatch, installed as `/usr/local/bin/aicodebox-entrypoint`. |
| `Dockerfile` | The minimal image. |
| `Dockerfile.full` | The full image, `FROM ${BASE_IMAGE}` (the minimal image). |
| `full-go/`, `full-node/`, `full-python/` | Locked tool sets for the full image. |
| `scripts/` | Docker integration tests (`test-entrypoint-hooks.sh`, `test-entrypoint-identity.sh`, `test-full-image.sh`) and their fixture. |
| `docs/` | The documentation site (Jekyll, just-the-docs), published by GitHub Pages from `main` at https://psyb0t.github.io/docker-aicodebox/. |
| `.agents/rules/` | Rules for every change. |
| `.github/workflows/pipeline.yml` | CI: lint and tests, image build, tag promotion, release. |
| `.env.example` | Every runtime setting. Keep it in step with the code. |

## Commands

Docker and Make are enough for lint and CI tests.

```bash
make help              # every target
make lint              # flake8 + pyright in the pinned uv container
make test-ci           # unit tests in the pinned uv container (what CI runs)
make test              # unit tests with the host's uv
make build             # minimal image, tagged v<version> and latest
make build-full        # minimal, then full on top of it
make test-integration  # build, then entrypoint hook and identity tests
make test-full-image   # build full, then check every listed tool
make docs              # build the site into .testing/docs-site
make version V=X.Y.Z   # set the package version, no commit
```

## Project-specific landmines

- Several modules read environment variables at import time: `ROOT_WORKSPACE` in `modes/api/workspace.py` and `modes/telegram/bot.py`, `UPLOAD_DIR` in `oai.py`, `BOT_TOKEN` in `modes/telegram/utils.py`, `HISTORY_ROOT` in `modes/cron/scheduler.py`. Tests that change these must patch the module attribute, not only the environment. `make test-ci` sets `AICODEBOX_WORKSPACE=/tmp/workspace` for this reason.
- The entrypoint execs through `setpriv` and keeps the whole environment, so every container variable, and every variable from the auth file, reaches the agent. The `AICODEBOX_*` export list in `entrypoint.sh` only re-exports values; it is not a filter.
- Mode flags are compared to the string `1` in both `entrypoint.sh` and the Python modes.
- Agent subprocesses start with `start_new_session=True` so a group signal from the agent cannot stop the API server (PID 1). Keep it on any new spawn.
- An empty `AICODEBOX_MCP_MODE_ALLOWED_HOSTS` or `..._ORIGINS` raises `ValueError` in `mcp_server._allowlist_from_environment`. Unset means default.
- `.env.example` keeps those two MCP variables commented out so a copy of it leaves them unset. `test_env_example_copied_as_is_keeps_mcp_working` checks this.

## Rules

Rules for every change: `.agents/rules/`. Read the ones that match what you are touching before editing.
