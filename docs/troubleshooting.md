---
title: "Troubleshooting"
description: "Exact aicodebox startup and runtime error messages, what causes each one and how to fix it, for the entrypoint, API, MCP, Telegram and cron modes."
nav_order: 9
---

# Troubleshooting

Each entry starts with the text you see in the container log or the response, so you can search for it. `docker logs <container>` shows the log. Set `DEBUG=true` for more detail.

## Startup

### `AICODEBOX_ADAPTER not set; image must export it`

A mode started without an adapter. The child image must set `AICODEBOX_ADAPTER=package.module:ClassName`. `/healthz` reports the same text with `"ok": false`.

### `AICODEBOX_ADAPTER='...' malformed; expected pkg.module:Class`

The value has no colon. Use `your_adapter.adapter:YourAdapter`, not a file path.

### `... is not an AgentAdapter`, or `ModuleNotFoundError`

The class does not subclass `aicodebox.adapters.base.AgentAdapter`, or the package is not installed in the system Python. Install it with `pip3 install --break-system-packages` or `uv pip install --system` in the child image.

### `api: no models configured`

API mode needs a model list. Set `AICODEBOX_AVAILABLE_MODELS=model-a,model-b`, or declare `available_models` on the adapter.

### `AICODEBOX_API_MODE_PORT must be a number`, `AICODEBOX_MCP_MODE_PORT must be a number`

The port variable is set to something that is not an integer, or to an empty string. Unset it to get the default.

### `AICODEBOX_MCP_MODE_ALLOWED_HOSTS must contain at least one value`

The variable is set but empty, often from copying `.env.example` as it is. Unset it to keep the loopback default, or list real host names. The same goes for `AICODEBOX_MCP_MODE_ALLOWED_ORIGINS`. In API mode this shows up as `mcp: failed to build MCP app. MCP endpoint not mounted`: the API keeps running and `/mcp/` returns 404. The standalone MCP process exits instead.

### `AICODEBOX_GIT_NAME must be a single line`

`AICODEBOX_GIT_NAME` or `AICODEBOX_GIT_EMAIL` contains a newline or carriage return. The container stops before anything else runs.

### `[entrypoint] init script <path> failed`

That init script exited non-zero. The others still ran and the container started. Init runs only on a container's first start, so fix the script and create a new container to run it again. See [Container startup](container-startup.md#init-scripts).

### The agent cannot write to the workspace

Startup takes over the workspace owner's UID and GID only when both are non-zero. A workspace owned by root keeps `aicode` at UID 1000, which may not be allowed to write there. `chown` the host directory to your own user.

### Passthrough exits with `command not found`

No mode flag is set, so the container tried to run `AICODEBOX_AGENT_BINARY`, which defaults to `pi`. Set the variable in the child image, or turn a mode on. Mode flags need the value `1`; `true` does not count.

## Telegram

### `AICODEBOX_TELEGRAM_MODE_TOKEN not set`

Telegram mode is on without a bot token. Get one from @BotFather.

### `invalid telegram config: ...`

`telegram.yml` is not valid YAML, or `allowed_chats` is not a list, or `chats` or `default` is not a mapping. The message names the problem.

### The bot ignores my messages

The chat or user is not allowed. Group chat IDs are negative numbers. Check `allowed_chats` and the chat's `allowed_users`, then send `/reload`.

### `busy, try again later`

This chat already has a run going. Wait, or send `/cancel`.

### `no models configured. set AICODEBOX_AVAILABLE_MODELS (comma-separated) or have the adapter declare the list.`

`/model` has nothing to offer. Set the variable, or declare `available_models` on the adapter. `/effort` says the same about `AICODEBOX_AVAILABLE_EFFORTS`.

## Cron

### `AICODEBOX_CRON_MODE_FILE not set`

Cron mode is on without a jobs file. In Telegram mode with cron the message is `AICODEBOX_CRON_MODE=1 set but AICODEBOX_CRON_MODE_FILE missing`.

### `invalid cron file: ...`

The reason follows: `cron file not found`, `invalid YAML`, `'jobs' must be a non-empty list`, `duplicate job name`, `invalid cron expression`, or a field of the wrong type. See [Cron scheduler](cron.md#the-jobs-file).

### `[<job>] previous run still active`

The scheduler skipped a tick because the job's last run outlasted its interval. Lengthen the schedule or shorten the job.

### `telegram_chat_id set but AICODEBOX_TELEGRAM_MODE_TOKEN missing`

A job wants to notify Telegram, but the container has no bot token. The run itself succeeded.

## Requests

### `409 workspace ... is busy with another run`, `409 workspace busy, retry later`

One run per workspace at a time. Wait for the first run, or send parallel requests to different workspaces.

### `exitCode` 127 with `binary not found`

The command from `build_argv` does not exist in the image. Check the agent install and the binary name.

### `exitCode` 124 with `timeout after <n>s`

The run hit `timeoutSeconds` (or `x-aicodebox-timeout-seconds`) and was killed.

### `422 json_schema validation failed after <n> retries: ...`

The agent never produced JSON that matched the schema. Simplify the schema, or say more about the expected shape in the prompt.

### MCP answers 421 or 403

421 means the `Host` header is not in `AICODEBOX_MCP_MODE_ALLOWED_HOSTS`. 403 means the `Origin` header is not in `AICODEBOX_MCP_MODE_ALLOWED_ORIGINS`. Add the exact values your proxy forwards. See [MCP server](mcp.md#host-and-origin-checks).

### `401 {"detail":"unauthorized"}` from MCP

The MCP token is wrong or missing. MCP does not accept the API token.
