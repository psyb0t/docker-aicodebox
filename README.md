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
| **Auth**    | `AICODEBOX_AUTH_TOKENS` (comma-separated bearer list) gates the API + MCP. Empty list = wide open. Telegram has its own allowlist.                                                                  |
| **State**   | Per-chat overrides + cron history go under `$HOME/.aicodebox/`. Bind-mount that path if you want it to outlive the container. The package itself stores nothing.                                    |

## The adapter contract

Everything routes through one interface. You implement it once per agent.

```python
# mypkg/adapter.py
from aicodebox.adapters.base import AgentAdapter
from aicodebox.shared.runner import RunRequest, RunResult

class MyAdapter(AgentAdapter):
    name = "my-agent"
    available_models = ["fast", "smart"]
    available_thinking_levels = ["off", "low", "high"]

    def build_argv(self, req: RunRequest) -> list[str]:
        argv = ["my-agent", "-p", req.prompt]
        if req.model: argv += ["--model", req.model]
        if req.workspace: argv += ["--cwd", req.workspace]
        return argv

    def parse_result(self, stdout: str, stderr: str, code: int) -> RunResult:
        return RunResult(text=stdout, raw_stdout=stdout, raw_stderr=stderr, exit_code=code)
```

```dockerfile
ENV AICODEBOX_ADAPTER=mypkg.adapter:MyAdapter
```

The package gets resolved at first call, cached for the process lifetime. Every mode pulls the same adapter — what gets exposed over HTTP / MCP / Telegram / cron is exactly what your `build_argv` knows how to drive.

## Modes

Modes are controlled by env vars. Set the flag, the entrypoint starts that mode. No flag, no mode. One mode per container — except telegram + cron, which share a process (cron runs in-thread inside telegram). API always wins if set alongside anything else.

### API mode

`AICODEBOX_MODE_API=1`. Boots a FastAPI server on `:8080` with:

- `POST /run` — sync agent run; returns `{text, raw_stdout, raw_stderr, exit_code}`
- `POST /run/async` — fire-and-forget; returns a job id
- `GET /run/{id}` — poll an async job
- `POST /run/{id}/cancel` — kill an in-flight run
- `POST /v1/chat/completions` — OpenAI-compatible (streaming + non-streaming). Plug it into anything that speaks OpenAI.
- `GET /v1/models` — model list from the adapter
- `POST /mcp` — MCP server (streamable HTTP transport)

Bearer auth is shared across all of them. Pass `AICODEBOX_AUTH_TOKENS=t1,t2,t3` for rotation.

### Telegram mode

`AICODEBOX_MODE_TELEGRAM=1` + `AICODEBOX_TELEGRAM_BOT_TOKEN=<bot:token>`. Drop the bot into a chat, talk to it, get answers. Features:

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

`AICODEBOX_MODE_CRON=1` + `AICODEBOX_MODE_CRON_FILE=/path/to/cron.yaml`. 6-field croniter schedules, per-job workspace, optional telegram notification.

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

### MCP server

`AICODEBOX_MODE_MCP=1`. Standalone MCP server (stdio or HTTP). Same adapter, same auth. Point Claude Desktop / Cursor / whatever at it and the agent shows up as tools.

## Configuration

Everything's an env var. The base sets sane defaults, your child image overrides.

| Var                              | Default      | What it does                                                                  |
| -------------------------------- | ------------ | ----------------------------------------------------------------------------- |
| `AICODEBOX_ADAPTER`              | *required*   | `pkg.module:Class` reference to your `AgentAdapter` subclass                  |
| `AICODEBOX_AGENT_BINARY`         | *required*   | Name of the agent's CLI binary (for `which` checks, version reports)          |
| `AICODEBOX_WORKSPACE`            | `/workspace` | Root dir for all per-chat / per-job workspaces                                |
| `AICODEBOX_AUTH_TOKENS`          | empty        | Comma-separated bearer tokens. Empty = no auth (don't expose to the public)   |
| `AICODEBOX_MODE_API`             | `0`          | Boot the HTTP API                                                             |
| `AICODEBOX_MODE_TELEGRAM`        | `0`          | Boot the Telegram bot                                                         |
| `AICODEBOX_MODE_CRON`            | `0`          | Boot the cron scheduler                                                       |
| `AICODEBOX_MODE_MCP`             | `0`          | Boot the standalone MCP server                                                |
| `AICODEBOX_MODE_CRON_FILE`       | -            | Path to the cron yaml (when cron mode is on)                                  |
| `AICODEBOX_TELEGRAM_BOT_TOKEN`   | -            | Bot token from @BotFather (when telegram mode is on)                          |
| `AICODEBOX_TELEGRAM_CONFIG`      | `$HOME/.aicodebox/telegram.yml` | Telegram bot config yaml                                   |
| `AICODEBOX_AVAILABLE_MODELS`     | adapter list | Override the model list exposed via `/v1/models` and `/model` picker          |
| `AICODEBOX_CONTAINER_NAME`       | -            | Display name in `/status` and logs                                            |

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
  -e AICODEBOX_MODE_API=1 \
  -e AICODEBOX_AUTH_TOKENS=$(openssl rand -hex 16) \
  -v "$PWD/workspace:/workspace" \
  your/child-image:latest
```

A reference child image lives at [psyb0t/pibox](https://github.com/psyb0t/docker-pibox) — wraps [pi-coding-agent](https://github.com/earendil-works/pi-coding-agent) and uses this base verbatim.

## Development

```bash
make help            # list targets
make build           # docker build .
make test            # python unit tests (94 cases — adapter contract, modes, helpers)
make test-unit       # same as test
make lint            # flake8 + pyright
make format          # isort + black
make clean           # nuke caches + the built image
```

Tests run in-process — no docker required. The suite stubs out the adapter via `AICODEBOX_ADAPTER=aicodebox.tests.conftest:_StubAdapter` so the modes can be exercised without a real agent on disk.

For integration testing with a real agent + real Telegram chat, see the e2e harness in the [pibox](https://github.com/psyb0t/docker-pibox) repo — it uses [psyb0t/telethon-plus](https://github.com/psyb0t/docker-telethon) as a userbot driver.

## License

WTFPL — see [LICENSE](LICENSE). Do what the fuck you want.
