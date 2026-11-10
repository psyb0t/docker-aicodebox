---
title: "Modes"
description: "How aicodebox picks a mode from environment flags: API, Telegram and cron as foreground modes, MCP beside any of them, and passthrough when none is set."
nav_order: 4
has_children: true
---

# Modes

A mode is a way to reach the agent. Each one is off until its flag is `1`. The entrypoint in [`entrypoint.sh`](https://github.com/psyb0t/docker-aicodebox/blob/main/entrypoint.sh) reads the flags on every start and runs one foreground process.

| Flag | Mode | Runs as |
| --- | --- | --- |
| `AICODEBOX_API_MODE=1` | [HTTP API](api.md) and [OpenAI-compatible endpoint](openai.md) | `python3 -m aicodebox.modes.api`, foreground |
| `AICODEBOX_TELEGRAM_MODE=1` | [Telegram bot](telegram.md) | `python3 -m aicodebox.modes.telegram`, foreground |
| `AICODEBOX_CRON_MODE=1` | [Cron scheduler](cron.md) | `python3 -m aicodebox.modes.cron`, foreground, or a thread inside the Telegram process |
| `AICODEBOX_MCP_MODE=1` | [MCP server](mcp.md) | Mounted inside API mode, otherwise a background process |
| none of the above | Passthrough | The agent binary itself, with the container's arguments |

## Choosing the foreground mode

Foreground modes do not run side by side. The entrypoint picks one, in this order:

1. API, if `AICODEBOX_API_MODE=1`. Any Telegram or cron flag is ignored.
2. Telegram, if `AICODEBOX_TELEGRAM_MODE=1`. If `AICODEBOX_CRON_MODE=1` is also set, the bot starts the cron scheduler in a thread of its own process.
3. Cron, if `AICODEBOX_CRON_MODE=1`.
4. Passthrough: `AICODEBOX_AGENT_BINARY` (default `pi`) with the arguments given to the container.

Only the value `1` turns a flag on. `true` or `yes` leave the mode off.

## Where MCP goes

MCP is independent of the foreground mode:

| Foreground | MCP placement |
| --- | --- |
| API | Mounted at `/mcp/` on the API port, in the same process. |
| Telegram, cron or passthrough | A second process serving `/` on `AICODEBOX_MCP_MODE_PORT` (default `8081`), started in the background before the foreground process. |

The container lives as long as the foreground process. When it exits, the background MCP process goes with the container.

## Ports

| Port | Used by |
| --- | --- |
| `8080` | API mode, including `/mcp/` when MCP is on. Change it with `AICODEBOX_API_MODE_PORT`. |
| `8081` | The standalone MCP process. Change it with `AICODEBOX_MCP_MODE_PORT`. |

Both servers bind `0.0.0.0` inside the container. Publish only the ports you need, and read [Security](security.md) before publishing one beyond localhost.

## Shared behavior

- Every mode resolves workspaces as subdirectories of `AICODEBOX_WORKSPACE` (default `/workspace`), creates them on first use, and refuses a path that leaves that root.
- Every mode runs the agent as a child process in its own process group, so a signal the agent sends to its group does not reach the server.
- `DEBUG=true` (or `1`, `yes`) switches the Python modes to one JSON object per log line at DEBUG level. Otherwise they log `LEVEL logger: message` lines at INFO.
