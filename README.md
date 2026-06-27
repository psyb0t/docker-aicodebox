# docker-aicodebox

[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/aicodebox?style=flat-square)](https://hub.docker.com/r/psyb0t/aicodebox)
[![License: WTFPL](https://img.shields.io/badge/License-WTFPL-brightgreen.svg?style=flat-square)](http://www.wtfpl.net/)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg?style=flat-square)](https://www.python.org/downloads/)

The agent-agnostic foundation for putting any terminal-shaped AI coding agent on the network. Pick your poison — `claude-code`, `pi`, `opencode`, `hermes`, whatever vibes — bolt on a 20-line adapter, and out the other end you get an **HTTP API**, an **OpenAI-compatible chat completions endpoint**, an **MCP server**, a **Telegram bot**, and a **cron scheduler** that fires the agent on whatever schedule you can dream up. One container. Same surfaces. Swap the brain.

You don't fork this. You `FROM` it.

```dockerfile
FROM psyb0t/aicodebox

RUN npm install -g @earendil-works/pi-coding-agent@0.74.0
COPY mypkg /opt/mypkg
RUN pip3 install --break-system-packages /opt/mypkg

ENV AICODEBOX_ADAPTER=mypkg.adapter:MyAdapter \
    AICODEBOX_AGENT_BINARY=pi
```

That's it. The base owns the surfaces. Your adapter translates "run this prompt" into whatever your agent's CLI expects. New agent lands in an afternoon.

## Table of Contents

- [What's in the box](#whats-in-the-box)
- [The adapter contract](#the-adapter-contract)
- [Modes](#modes)
  - [API mode](#api-mode)
  - [Telegram mode](#telegram-mode)
  - [Cron mode](#cron-mode)
  - [MCP server](#mcp-server)
- [Configuration](#configuration)
- [Child image recipe](#child-image-recipe)
- [Development](#development)
- [License](#license)

## What's in the box

| Layer       | The goods                                                                                                                                                                                          |
| ----------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **OS**      | Ubuntu 24.04. `aicode` user (UID 1000), passwordless sudo, docker group.                                                                                                                          |
| **Runtimes**| Node.js 22 LTS (for agents that ship as npm), Python 3.12, Docker CE + buildx + compose (in case your agent needs to spawn containers).                                                            |
| **Package** | `aicodebox` — the adapter contract + four mode dispatchers (api / telegram / cron / mcp). Pure Python, zero side effects until you boot a mode.                                                    |
| **Modes**   | All optional, all opt-in via env vars. Run none or one per container. Exception: telegram + cron can share a container — cron runs in-thread inside the telegram process.                          |
| **Auth**    | `AICODEBOX_API_MODE_TOKEN` gates API mode; `AICODEBOX_MCP_MODE_TOKEN` gates MCP. Single bearer per surface, no fallback between them. Empty = no auth. Telegram has its own allowlist.              |
| **State**   | Per-chat overrides + cron history go under `$HOME/.aicodebox/`. Bind-mount that path if you want it to outlive the container. The package itself stores nothing.                                    |

## The adapter contract

Everything routes through one interface. You implement it once per agent.

```python
# mypkg/adapter.py
from aicodebox.adapters.base import AgentAdapter, RunRequest, RunResult, StreamEvent

class MyAdapter(AgentAdapter):
    name = "my-agent"
    available_models = ["fast", "smart"]
    available_thinking_levels = ["off", "low", "high"]

    def build_argv(self, req: RunRequest) -> list[str]:
        argv = ["my-agent", "-p", req.prompt]
        if req.model: argv += ["--model", req.model]
        if req.workspace: argv += ["--cwd", req.workspace]
        return argv

    def parse_output(self, stdout: str, req: RunRequest) -> RunResult:
        # Required. Convert raw stdout into a normalized result. raw_stdout /
        # raw_stderr / exit_code are filled in by the runner — set ``text``
        # (and optionally ``parsed`` / ``usage`` / ``session_id``).
        return RunResult(text=stdout.strip(), raw_stdout="", raw_stderr="", exit_code=0)

    # Optional: structured stream events. The default is "one delta per
    # stdout line" — override only if your binary emits a JSON event stream
    # and you want per-token / per-tool granularity in OAI streaming.
    def parse_stream_event(self, line: str, req: RunRequest) -> StreamEvent | None:
        return StreamEvent(type="delta", text=line + "\n") if line else None

    # Optional: post-hoc events surfaced in /run's ``events`` field when
    # the caller passes ``jsonSchema`` (the schema mode is what triggers
    # the full diagnostic surface). Runs once over completed stdout.
    # Plain-text adapters return [] (the default) — schema mode will just
    # not carry events for those adapters.
    def parse_events(self, stdout: str, req: RunRequest) -> list[dict]:
        return []
```

```dockerfile
ENV AICODEBOX_ADAPTER=mypkg.adapter:MyAdapter
```

The package gets resolved at first call, cached for the process lifetime. Every mode pulls the same adapter — what gets exposed over HTTP / MCP / Telegram / cron is exactly what your `build_argv` knows how to drive.

## Modes

Modes are controlled by env vars. Set the flag, the entrypoint starts that mode. No flag, no mode. **Foreground modes** (API / Telegram / Cron) are mutually exclusive — except telegram + cron, which share a process (cron runs in-thread inside telegram). API wins if set alongside anything else. **MCP mode** is independent — it coexists with any foreground mode, served on its own port (or mounted at `/mcp` inside API).

### API mode

`AICODEBOX_API_MODE=1`. Boots a FastAPI server on `:8080` (override with `AICODEBOX_API_MODE_PORT`) with:

> **Required:** `AICODEBOX_AVAILABLE_MODELS=<csv>` — `/v1/models` needs a real list, and there's no safe fallback (the adapter name isn't a model name). API mode refuses to boot without it. Pick the model ids your configured provider actually serves.

- `POST /run` — sync agent run. The response shape is driven by a single request flag — `jsonSchema`:
  - **No `jsonSchema`** → `{runId, workspace, exitCode, text}`. Lean — just the assistant's prose.
  - **`"jsonSchema": {...}`** → full diagnostic surface: `{runId, workspace, exitCode, text, json, events, sessionId, usage, attempts}`. The agent is invoked in json-verbose mode under the hood — its output is decoded, validated against your schema, and the parsed object is surfaced as `json`; `events` carries the adapter's structured event log (tool calls, thinking blocks, per-turn metadata); `sessionId` + `usage` come from the agent. `usage` is the **sum** of token counts across every retry attempt (provider bills per attempt; reporting only the last would lie); `attempts` is the per-attempt breakdown `[{index, usage, exitCode, parseError}, ...]` so callers can render "retry 2/3 cost X" or bill per attempt. On parse / schema-validation failure the wrapper re-prompts the agent up to 3 times with the prior bad output + the specific error; if all attempts still fail, `parseError` + `jsonRetries` replace `json` (everything else — `text`, `events`, `sessionId`, `usage`, `attempts` — still surfaces). One flag, two wire shapes. No `verbose` dial — schema = full surface, no schema = lean.

  Set `"includeRaw": true` on the request to also receive `stdout` + `stderr`. `stderr` is always included automatically when `exitCode != 0` so the failure has a diagnostic.
- `POST /run` with `"async": true` or `"fireAndForget": true` — returns `{runId, status: "running"}` immediately
- `GET /run/result?runId=<id>` — poll an async run (same payload shape as sync)
- `DELETE /run/{id}` — kill an in-flight run
- `GET|PUT|DELETE /files/{path}` — workspace file CRUD
- `POST /v1/chat/completions` — OpenAI-compatible (streaming + non-streaming). Plug it into anything that speaks OpenAI. **Schema-validated JSON output**: stock OpenAI clients drive it via the standard `response_format` body field (`{"type":"json_object"}` for permissive, `{"type":"json_schema","json_schema":{"name":"...","schema":{...}}}` for structured outputs); the proprietary `x-aicodebox-json-schema` header is supported as a fallback. Body field wins if both are set. Schema mode runs up to 3 self-correction retries — success → canonical JSON in `message.content`, retries exhausted → **422**, agent process crash → **500** with exit code + stderr in `detail`, combined with `stream=true` → **400**. Additional RunSpec knobs via `x-aicodebox-*` headers: `workspace`, `continue`, `append-system-prompt`, `resume`, `extra-args`, `timeout-seconds`, `tools-allowlist`, `no-tools`. Malformed header values surface as 400 with the offending header name. When schema mode runs retries, `usage` is the **sum** across all attempts (input/output/total/cache fields all summed), and the envelope carries a vendor-extension `aicodebox_attempts: [{index, usage, exitCode, parseError}, ...]` so callers can see the per-attempt breakdown (OAI-only clients ignore the unknown field). **Cheap retries via session continuation**: schema requests that omit `x-aicodebox-workspace` get a per-request ephemeral workspace under `/tmp/aicodebox/<uuid>/` (cleaned up in `finally`); retries then run with `no_continue=False` and a minimal corrective prompt (error + directive + schema) instead of replaying the full original input, cutting per-retry input cost roughly 100x on large prompts. Callers that DO provide their own workspace fall back to fresh-session retries that re-state the original task — safe across any workspace, but more expensive.
- `GET /v1/models` — model list from the adapter
- `POST /mcp` — MCP server (mounted only when `AICODEBOX_MCP_MODE=1`; auth via `AICODEBOX_MCP_MODE_TOKEN`, separate from the API bearer)

Bearer auth for the API surface: `AICODEBOX_API_MODE_TOKEN=<one-token>`. Single token, no rotation list. Empty = no auth.

### Telegram mode

`AICODEBOX_TELEGRAM_MODE=1` + `AICODEBOX_TELEGRAM_MODE_TOKEN=<bot:token>`. Drop the bot into a chat, talk to it, get answers. Features:

- Text in → agent run → response chunked + Markdown→HTML rendered for Telegram.
- File uploads (document / photo / video / voice) land in the chat's workspace.
- `[SEND_FILE: relative/path]` in agent output delivers workspace files back as Telegram attachments.
- Per-chat overrides: `/model`, `/effort`, `/system_prompt`, `/append_system_prompt`. Persisted to disk.
- `/cancel` kills the in-flight run for the chat. `/reload` re-reads the yaml. `/config` dumps merged chat config. `/fetch <path>` downloads a workspace file. `/status` lists busy chats.
- Replies to cron-fired messages inject the job's instruction + result so follow-ups make sense.

Config lives at `$HOME/.aicodebox/telegram.yml`:

```yaml
allowed_chats: [-100123, 42]
default:
  model: glm-4.5-air
  workspace: shared
chats:
  -100123:
    workspace: alpha
    model: claude-sonnet
    allowed_users: [10, 20]
```

### Cron mode

`AICODEBOX_CRON_MODE=1` + `AICODEBOX_CRON_MODE_FILE=/path/to/cron.yaml`. 6-field croniter schedules, per-job workspace, optional telegram notification.

```yaml
jobs:
  - name: morning-report
    schedule: "0 0 9 * * *"
    instruction: |
      Summarize yesterday's git activity in {workspace}.
    workspace: shared
    telegram_chat_id: -100123
    model: claude-sonnet
```

Each run gets its own history dir under `$HOME/.aicodebox/cron/history/<workspace-slug>/<YYYYmmdd-HHMMSS>-<job>/` with `meta.json`, `stdout.log`, `stderr.log`, `result.txt`, and (if telegram-notified) `telegram.json`. The next run's prompt gets a "prior runs" hint pointing at that directory — your agent can read its own past output without you wiring it up.

### MCP mode

`AICODEBOX_MCP_MODE=1`. Exposes the MCP (Model Context Protocol) surface. Coexists with any foreground mode:

| Foreground | MCP placement |
|---|---|
| API mode (`AICODEBOX_API_MODE=1`) | mounted at `/mcp` on the API port — no extra process |
| Telegram / Cron / passthrough | runs as a sidecar uvicorn on `AICODEBOX_MCP_MODE_PORT` (default `8081`) |

Auth: `AICODEBOX_MCP_MODE_TOKEN=<one-token>` — bearer token in the `Authorization: Bearer …` header, or `?apiToken=…` for clients that can't set headers. Empty = no auth. **No fallback to `API_MODE_TOKEN`** — MCP is its own surface with its own bearer.

Point Claude Desktop / Cursor / whatever at the MCP endpoint and the agent shows up as a set of tools (`run_prompt`, `list_files`, `read_file`, `write_file`, `delete_file`).

## Configuration

Everything's an env var. The base sets sane defaults, your child image overrides.

Env var convention: `<MODE>_MODE` is the on/off flag for that mode; `<MODE>_MODE_<KNOB>` is its config. Vars that aren't mode-scoped (workspace, adapter, container) are bare.

### Adapter & container

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_ADAPTER` | *required* | `pkg.module:Class` reference to your `AgentAdapter` subclass |
| `AICODEBOX_AGENT_BINARY` | *required* | Name of the agent's CLI binary (for `which` checks, version reports) |
| `AICODEBOX_WORKSPACE` | `/workspace` | Root dir for all per-chat / per-job workspaces |
| `AICODEBOX_CONTAINER_NAME` | `aicodebox` | Display name in `/status`, logs, and per-container state files |
| `AICODEBOX_AVAILABLE_MODELS` | — | **Required for API mode.** CSV list returned by `/v1/models` and shown in the telegram `/model` picker. API mode refuses to boot without it; telegram `/model` picker degrades to a "set this env var" reply. |
| `AICODEBOX_AVAILABLE_EFFORTS` | adapter list | Override the effort/`--thinking` list exposed via `/effort` (comma-separated) |

### Mode flags

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_API_MODE` | `0` | Boot the HTTP API server (foreground) |
| `AICODEBOX_TELEGRAM_MODE` | `0` | Boot the Telegram bot (foreground) |
| `AICODEBOX_CRON_MODE` | `0` | Boot the cron scheduler (foreground; runs in-thread if telegram is also on) |
| `AICODEBOX_MCP_MODE` | `0` | Expose the MCP server — mounted at `/mcp` in API mode, or as a sidecar elsewhere |

### API mode config

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_API_MODE_PORT` | `8080` | Port the API server binds to |
| `AICODEBOX_API_MODE_TOKEN` | empty | Bearer token for the API surface. Empty = no auth |

### Telegram mode config

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_TELEGRAM_MODE_TOKEN` | — | Bot token from @BotFather |
| `AICODEBOX_TELEGRAM_MODE_CONFIG` | `$HOME/.aicodebox/telegram.yml` | Path to the telegram config yaml |
| `AICODEBOX_TELEGRAM_MODE_OVERRIDES` | `$HOME/.aicodebox/telegram_overrides.json` | Per-chat override store (model/effort/system prompts) |

### Cron mode config

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_CRON_MODE_FILE` | — | Path to the cron yaml |
| `AICODEBOX_CRON_MODE_HISTORY_DIR` | `$HOME/.aicodebox/cron/history` | Where each run writes `meta.json`, `stdout.log`, `stderr.log`, `result.txt` |

### MCP mode config

| Var | Default | What it does |
|-----|---------|--------------|
| `AICODEBOX_MCP_MODE_PORT` | `8081` | Port the sidecar MCP server binds to (ignored when MCP is mounted inside API) |
| `AICODEBOX_MCP_MODE_TOKEN` | empty | Bearer token for MCP. Empty = no auth. **No fallback to `API_MODE_TOKEN`** |

## Child image recipe

Minimal adapter that wires up an npm-shipped agent:

```dockerfile
FROM psyb0t/aicodebox:latest

# Your agent — pin the version.
ARG AGENT_VERSION=0.74.0
RUN npm install -g @your-org/your-agent@${AGENT_VERSION}

# Your adapter package — implements aicodebox.adapters.base.AgentAdapter.
COPY your_adapter /opt/your_adapter
RUN pip3 install --no-cache-dir --break-system-packages /opt/your_adapter

ENV AICODEBOX_ADAPTER=your_adapter.adapter:YourAdapter \
    AICODEBOX_AGENT_BINARY=your-agent
```

Boot it:

```bash
docker run --rm -p 8080:8080 \
  -e AICODEBOX_API_MODE=1 \
  -e AICODEBOX_API_MODE_TOKEN=$(openssl rand -hex 16) \
  -v "$PWD/workspace:/workspace" \
  your/child-image:latest
```

A reference child image lives at [psyb0t/pibox](https://github.com/psyb0t/docker-pibox) — wraps [pi-coding-agent](https://github.com/earendil-works/pi-coding-agent) and uses this base verbatim.

## Development

```bash
make help            # list targets
make build           # docker build .
make test            # python unit tests (113 cases — adapter contract, modes, helpers)
make test-unit       # same as test
make lint            # flake8 + pyright
make format          # isort + black
make clean           # nuke caches + the built image
```

Tests run in-process — no docker required. The suite stubs out the adapter via `AICODEBOX_ADAPTER=aicodebox.tests.conftest:_StubAdapter` so the modes can be exercised without a real agent on disk.

For integration testing with a real agent + real Telegram chat, see the e2e harness in the [pibox](https://github.com/psyb0t/docker-pibox) repo — it uses [psyb0t/telethon-plus](https://github.com/psyb0t/docker-telethon) as a userbot driver.

## License

WTFPL — see [LICENSE](LICENSE). Do what the fuck you want.
