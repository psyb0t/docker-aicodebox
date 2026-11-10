# docker-aicodebox

[![CI](https://github.com/psyb0t/docker-aicodebox/actions/workflows/pipeline.yml/badge.svg?branch=main)](https://github.com/psyb0t/docker-aicodebox/actions/workflows/pipeline.yml)
[![version](https://raw.githubusercontent.com/psyb0t/docker-aicodebox/badges/version.svg)](https://github.com/psyb0t/docker-aicodebox/releases)
[![license](https://raw.githubusercontent.com/psyb0t/docker-aicodebox/badges/license.svg)](https://github.com/psyb0t/docker-aicodebox/blob/main/LICENSE)
[![Docker Pulls](https://img.shields.io/docker/pulls/psyb0t/aicodebox?style=flat-square)](https://hub.docker.com/r/psyb0t/aicodebox)

The agent-agnostic foundation for putting any terminal-shaped AI coding agent on the network. Pick your poison (`claude-code`, `pi`, `opencode`, `hermes`, whatever vibes), bolt on a short adapter class, and out the other end you get an **HTTP API**, an **OpenAI-compatible chat completions endpoint**, an **MCP server**, a **Telegram bot**, and a **cron scheduler** that fires the agent on whatever schedule you can dream up. One container. Same interfaces. Swap the brain.

You don't fork this. You `FROM` it.

```dockerfile
FROM psyb0t/aicodebox

RUN npm install -g @earendil-works/pi-coding-agent@0.74.0
COPY mypkg /opt/mypkg
RUN pip3 install --break-system-packages /opt/mypkg

ENV AICODEBOX_ADAPTER=mypkg.adapter:MyAdapter \
    AICODEBOX_AGENT_BINARY=pi
```

That's it. The base owns the modes. Your adapter translates "run this prompt" into whatever your agent's CLI expects. New agent lands in an afternoon.

`aicodebox` is a base image, not a host launcher. For local agent work, use a child wrapper such as [`claudebox`](https://github.com/psyb0t/docker-claudebox), [`codexbox`](https://github.com/psyb0t/docker-codexbox) or [`pibox`](https://github.com/psyb0t/docker-pibox). Those wrappers mount the requested workspace and the matching agent state. A child agent calls a sibling wrapper directly when it is installed beside its own wrapper. It must not rebuild that wrapper's Docker command or mount another agent's state itself.

## Table of Contents

- [Documentation](#documentation)
- [What's in the box](#whats-in-the-box)
- [Quick start](#quick-start)
- [Modes](#modes)
- [Development](#development)
- [Sponsor](#sponsor)
- [License](#license)

## Documentation

The full documentation lives at **[psyb0t.github.io/docker-aicodebox](https://psyb0t.github.io/docker-aicodebox/)**.

| Section | What's there |
| --- | --- |
| [Child images](https://psyb0t.github.io/docker-aicodebox/child-images/) | Build an image for your agent, minimal or full parent, passthrough. |
| [Adapter contract](https://psyb0t.github.io/docker-aicodebox/adapter-contract/) | `AgentAdapter`, `RunRequest`, `RunResult`, stream and native events. |
| [Modes](https://psyb0t.github.io/docker-aicodebox/modes/) | How the flags pick a mode, where MCP goes, ports. |
| [HTTP API](https://psyb0t.github.io/docker-aicodebox/api/) | `/run`, schema retries, events, async runs, files, `/status`, `/healthz`. |
| [OpenAI-compatible endpoint](https://psyb0t.github.io/docker-aicodebox/openai/) | Streaming, `response_format`, tool calling, images, `x-aicodebox-*` headers. |
| [MCP server](https://psyb0t.github.io/docker-aicodebox/mcp/) | Tools, tokens, host and origin allowlists. |
| [Telegram bot](https://psyb0t.github.io/docker-aicodebox/telegram/) | `telegram.yml`, commands, files, overrides. |
| [Cron scheduler](https://psyb0t.github.io/docker-aicodebox/cron/) | Jobs file, schedules, run history, notifications. |
| [Configuration](https://psyb0t.github.io/docker-aicodebox/configuration/) | Every environment variable and its default. |
| [Container startup](https://psyb0t.github.io/docker-aicodebox/container-startup/) | UID remapping, Docker socket, Git identity, init scripts, user `bin/`. |
| [Full image](https://psyb0t.github.io/docker-aicodebox/full-image/) | The `latest-full` toolchain and its advisories. |
| [Security](https://psyb0t.github.io/docker-aicodebox/security/) | What the agent can reach and what to set before exposing it. |
| [Troubleshooting](https://psyb0t.github.io/docker-aicodebox/troubleshooting/) | Exact error messages and fixes. |
| [Development](https://psyb0t.github.io/docker-aicodebox/development/) | Layout, Make targets, tests, CI, releases. |

## What's in the box

| Layer | The goods |
| --- | --- |
| **OS** | Ubuntu 24.04. `aicode` user (UID 1000), passwordless sudo, docker group. |
| **Runtimes** | Node.js 24.21.0 (for agents that ship as npm), Python 3.14.8, uv, Docker CE with buildx and compose (in case your agent needs to spawn containers). |
| **Package** | `aicodebox`: the adapter contract plus four mode dispatchers (API, Telegram, cron, MCP). Pure Python, nothing runs until you boot a mode. |
| **Auth** | `AICODEBOX_API_MODE_TOKEN` guards the API, `AICODEBOX_MCP_MODE_TOKEN` guards MCP. One bearer each, no fallback between them. Empty means no auth. Telegram has its own allowlist. |
| **State** | Telegram overrides, cron history, your `bin/` and `init.d/` live under `$HOME/.aicodebox/`. Bind-mount it if you want it to outlive the container. |

`psyb0t/aicodebox:latest` is deliberately small. `psyb0t/aicodebox:latest-full` adds a shared development toolchain for agent images that need it: Go and its tools, pinned Node and Python developer tools, editors, database clients, `gh`, Terraform, kubectl and Helm. The full image keeps some optional Node tools with known dependency advisories; [the advisory list](https://psyb0t.github.io/docker-aicodebox/dependency-audit/) says which and why.

## Quick start

Build your child image as above, then boot it in API mode:

```bash
docker run --rm -p 127.0.0.1:8080:8080 \
  -e AICODEBOX_API_MODE=1 \
  -e AICODEBOX_API_MODE_TOKEN="$(openssl rand -hex 16)" \
  -e AICODEBOX_AVAILABLE_MODELS=model-a,model-b \
  -v "$PWD/workspace:/workspace" \
  your/child-image:latest
```

API mode refuses to boot without `AICODEBOX_AVAILABLE_MODELS` (or `available_models` on the adapter), because `/openai/v1/models` needs real model IDs. The adapter itself is one class with a `build_argv` method; the [adapter contract](https://psyb0t.github.io/docker-aicodebox/adapter-contract/) has a complete example.

## Modes

Set a flag to `1` and the entrypoint starts that mode. No flag, no mode: the container runs your agent's CLI with its arguments.

| Flag | Mode |
| --- | --- |
| `AICODEBOX_API_MODE` | FastAPI on `:8080`: `/run`, files, and `/openai/v1/chat/completions`. |
| `AICODEBOX_TELEGRAM_MODE` | A Telegram bot that runs the agent per chat, with uploads, attachments and per-chat overrides. |
| `AICODEBOX_CRON_MODE` | Jobs from a YAML file on five or six field cron schedules, with run history and optional Telegram notifications. |
| `AICODEBOX_MCP_MODE` | An MCP server, at `/mcp/` inside API mode or on `:8081` beside anything else. |

API, Telegram and cron are foreground modes and don't stack: API wins over the rest, and Telegram plus cron share one process. MCP coexists with any of them. Details on the [modes page](https://psyb0t.github.io/docker-aicodebox/modes/).

## Development

```bash
make help        # list targets
make build       # build psyb0t/aicodebox:latest
make build-full  # build latest-full on top of the local minimal image
make lint        # flake8 + pyright, in a pinned uv container
make test-ci     # unit tests, in the same container (what CI runs)
make docs        # build the documentation site into .testing/docs-site
```

Lint and the CI tests need only Docker. The rest of the targets, the integration tests and the CI pipeline are on the [development page](https://psyb0t.github.io/docker-aicodebox/development/).

## Sponsor

<a href="https://www.buymeacoffee.com/psyb0t"><img src="https://cdn.buymeacoffee.com/buttons/v2/default-blue.png" alt="Buy Me a Coffee" height="60" width="217"></a>

## License

WTFPL, see [LICENSE](https://github.com/psyb0t/docker-aicodebox/blob/main/LICENSE). Do what the fuck you want.
