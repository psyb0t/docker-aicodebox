---
title: "Configuration"
description: "Every aicodebox environment variable with its default: adapter selection, workspace, Git identity, model lists, mode flags and per-mode settings."
nav_order: 5
---

# Configuration

Everything is an environment variable. The base sets defaults, a child image overrides what it needs, and `docker run -e` overrides both. [`.env.example`](https://github.com/psyb0t/docker-aicodebox/blob/main/.env.example) lists the same variables.

The naming follows one rule: `AICODEBOX_<MODE>_MODE` turns a mode on, `AICODEBOX_<MODE>_MODE_<SETTING>` configures it, and settings shared by every mode have no mode in the name.

## Adapter and container

| Variable | Default | Meaning |
| --- | --- | --- |
| `AICODEBOX_ADAPTER` | none | `package.module:ClassName` of your `AgentAdapter` subclass. Every mode needs it. |
| `AICODEBOX_AGENT_BINARY` | `pi` | The command passthrough runs when no mode is on. |
| `AICODEBOX_WORKSPACE` | `/workspace` | Root for every workspace. Startup remaps `aicode` to its owner, and every mode keeps its workspaces inside it. |
| `AICODEBOX_CONTAINER_NAME` | `aicodebox` | Names the auth file `$HOME/.aicodebox/.<name>-auth` that startup loads. Startup still reads the older name `AICODE_CONTAINER_NAME` when this one is unset. |
| `AICODEBOX_GIT_NAME` | unset | Written to the runtime user's global Git `user.name` on every start, before init scripts. |
| `AICODEBOX_GIT_EMAIL` | unset | The same for `user.email`. |
| `AICODEBOX_AVAILABLE_MODELS` | adapter's `available_models` | Comma-separated model IDs for `/openai/v1/models` and the Telegram `/model` picker. API mode needs a non-empty list from here or the adapter. |
| `AICODEBOX_AVAILABLE_EFFORTS` | adapter's `available_thinking_levels` | Comma-separated levels for the Telegram `/effort` picker. |
| `DEBUG` | unset | `true`, `1` or `yes` gives JSON log lines at DEBUG level from the Python modes. Only `true` turns on the entrypoint's own debug lines. |

Empty Git identity values leave the existing configuration alone. A value with a newline or carriage return stops the container at startup. Child images map their own Git identity variables to these names; an explicit `AICODEBOX_GIT_*` value wins.

`ANTHROPIC_MODEL`, when set, is the model for any run that does not name one, in every mode and for every adapter.

## Mode flags

Each flag turns its mode on only with the value `1`. See [Modes](modes.md) for how they combine.

| Variable | Default | Mode |
| --- | --- | --- |
| `AICODEBOX_API_MODE` | `0` | [HTTP API](api.md) and [OpenAI-compatible endpoint](openai.md). |
| `AICODEBOX_TELEGRAM_MODE` | `0` | [Telegram bot](telegram.md). |
| `AICODEBOX_CRON_MODE` | `0` | [Cron scheduler](cron.md), in a thread of the bot when Telegram is on too. |
| `AICODEBOX_MCP_MODE` | `0` | [MCP server](mcp.md), at `/mcp/` in API mode or as a second process otherwise. |

## API mode

| Variable | Default | Meaning |
| --- | --- | --- |
| `AICODEBOX_API_MODE_PORT` | `8080` | Listening port. Must be a number. |
| `AICODEBOX_API_MODE_TOKEN` | empty | Bearer token for every API route except `/healthz`. Empty means no auth. |

## Telegram mode

| Variable | Default | Meaning |
| --- | --- | --- |
| `AICODEBOX_TELEGRAM_MODE_TOKEN` | none | Bot token from @BotFather. Required for the bot and for cron notifications. |
| `AICODEBOX_TELEGRAM_MODE_CONFIG` | `$HOME/.aicodebox/telegram.yml` | Path to the bot's YAML file. |
| `AICODEBOX_TELEGRAM_MODE_OVERRIDES` | `$HOME/.aicodebox/telegram_overrides.json` | Where `/model`, `/effort` and the prompt commands store per-chat overrides. |
| `TELEGRAM_CHAT_ID` | unset | The only allowed chat when the YAML file does not exist. |

## Cron mode

| Variable | Default | Meaning |
| --- | --- | --- |
| `AICODEBOX_CRON_MODE_FILE` | none | Path to the jobs YAML. Required. |
| `AICODEBOX_CRON_MODE_HISTORY_DIR` | `$HOME/.aicodebox/cron` | State root for run history, job summaries and the Telegram message map. |

## MCP mode

| Variable | Default | Meaning |
| --- | --- | --- |
| `AICODEBOX_MCP_MODE_PORT` | `8081` | Port of the standalone MCP process. Ignored in API mode. |
| `AICODEBOX_MCP_MODE_TOKEN` | empty | Bearer token for MCP. Empty means no auth. No fallback to the API token. |
| `AICODEBOX_MCP_MODE_ALLOWED_HOSTS` | loopback hosts | Comma-separated `Host` values MCP accepts. Leave it unset for the default; an empty value is an error. |
| `AICODEBOX_MCP_MODE_ALLOWED_ORIGINS` | loopback `http` origins | Comma-separated browser origins MCP accepts. Same empty-value rule. |

## Agent credentials

The container's environment reaches the agent process. The base does not read provider keys itself; the adapter's `translate_auth` can rename them for the agent. Keys a login flow saves can go in the auth file named by `AICODEBOX_CONTAINER_NAME`, which [startup](container-startup.md#auth-file) loads on every start.

## Variables set by the image

| Variable | Value | Meaning |
| --- | --- | --- |
| `AICODE_WORKSPACE` | `/workspace` | Older name for the workspace root, still read by startup and API mode. `AICODEBOX_WORKSPACE` wins when both are set. Set `AICODEBOX_WORKSPACE` to move the root. |
| `PATH` | starts with `/home/aicode/.aicodebox/bin` | Your scripts come first. See [Container startup](container-startup.md#user-bin-directory). |
| `PYTHONUNBUFFERED` | `1` | Unbuffered Python output for container logs. |
| `UV_COMPILE_BYTECODE` | `1` | uv compiles bytecode on install. |
| `AICODEBOX_IMAGE_VARIANT` | `full` | Set only in the full image. |
