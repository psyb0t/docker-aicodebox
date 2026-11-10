---
title: "HTTP API"
description: "API mode reference: POST /run with JSON schema retries and native events, async runs and polling, cancellation, workspace file routes, /status and /healthz."
nav_order: 1
parent: "Modes"
---

# HTTP API

`AICODEBOX_API_MODE=1` starts a FastAPI server on port `8080` (`AICODEBOX_API_MODE_PORT`). It serves the routes on this page, the [OpenAI-compatible endpoint](openai.md), and the [MCP server](mcp.md) at `/mcp/` when `AICODEBOX_MCP_MODE=1`. The code is in [`aicodebox/modes/api/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/modes/api).

API mode refuses to start without a model list. Set `AICODEBOX_AVAILABLE_MODELS` to the model IDs your provider serves, or have the adapter declare `available_models`. `/openai/v1/models` has no fallback, because the adapter name is not a model name.

## Authentication

Set `AICODEBOX_API_MODE_TOKEN` and send it on every request:

```bash
curl -s http://localhost:8080/run \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"prompt": "List the files in this workspace."}'
```

One token, compared exactly. A missing header gives `401 missing bearer token`, a wrong one `401 invalid bearer token`. With the variable empty or unset, every route is open. `/healthz` never asks for a token.

## Routes

| Method and path | Auth | Purpose |
| --- | --- | --- |
| `POST /run` | yes | Run the agent, synchronously or in the background. |
| `GET /run/result?runId=<id>` | yes | Poll a background run. |
| `DELETE /run/{id}` | yes | Kill a run. |
| `GET /files`, `GET /files/{path}` | yes | List a directory or download a file. |
| `PUT /files/{path}` | yes | Write the raw request body to a file. |
| `DELETE /files/{path}` | yes | Delete a file. |
| `GET /status` | yes | Busy workspaces and tracked runs. |
| `GET /healthz` | no | Whether an adapter is configured. |
| `POST /openai/v1/chat/completions`, `GET /openai/v1/models` | yes | See [OpenAI-compatible endpoint](openai.md). |
| `/mcp/` | MCP token | See [MCP server](mcp.md). |

## POST /run

The body is JSON. Field names are camelCase; the snake_case names (`system_prompt`, `json_schema` and so on) are accepted too.

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `prompt` | string | required | The instruction. Must not be empty. |
| `workspace` | string | root | Subdirectory of `AICODEBOX_WORKSPACE`. Created if missing. Absolute paths and `..` give a 400. |
| `model` | string | adapter default | Model to use. |
| `systemPrompt` | string | | Replace the agent's system prompt. |
| `appendSystemPrompt` | string | | Append to the system prompt. |
| `jsonSchema` | object | | Validate the answer as JSON against this schema, with retries. |
| `eventMode` | `auto`, `none`, `full` | `auto` | Whether to return the agent's native events. See [Event retention](#event-retention). |
| `outputFormat` | `text`, `json`, `json-verbose` | | Legacy. Only `json-verbose` has an effect, and only while `eventMode` is `auto`. |
| `noContinue` | boolean | `false` | Start a fresh session instead of continuing the last one in the workspace. |
| `resume` | string | | Resume a specific session ID. |
| `async` | boolean | `false` | Return at once and run in the background. |
| `fireAndForget` | boolean | `false` | Same as `async`. The response echoes the flag. |
| `timeoutSeconds` | integer | none | Kill the agent after this many seconds. |
| `thinking` | string | | Reasoning level. |
| `toolsAllowlist` | array of strings | | Restrict the agent's own tools. |
| `noTools` | boolean | `false` | Disable the agent's own tools. |
| `extraArgs` | array of strings | | Extra command-line arguments for the agent. |
| `includeRaw` | boolean | `false` | Add `stdout` and `stderr` to the response. |

What each field does depends on the adapter. The base passes them through in the `RunRequest`; see [Adapter contract](adapter-contract.md#runrequest).

### Response

A plain run returns:

```json
{
  "runId": "3f0c9b2e6d0a4c1f9a7e2b5d8c4f6a1e",
  "workspace": "/workspace",
  "exitCode": 0,
  "text": "README.md\nsrc/"
}
```

Extra fields appear when they apply:

| Field | When |
| --- | --- |
| `sessionId`, `usage` | With `jsonSchema` or `eventMode: "full"`, when the adapter reports them. |
| `json` | With `jsonSchema`, when an attempt produced valid JSON. |
| `parseError` | With `jsonSchema`, when every attempt failed validation. |
| `attempts` | With `jsonSchema`: `[{index, usage, exitCode, parseError}]`, one entry per attempt. |
| `jsonRetries` | With `jsonSchema`, the number of retries used, when there was at least one. |
| `events` | With `eventMode: "full"`. |
| `stdout`, `stderr` | With `includeRaw`. `stderr` is always included when `exitCode` is not 0. |

A non-zero exit code from the agent is not an HTTP error. The response is still 200 with `exitCode` and `stderr` set.

### JSON schema retries

With `jsonSchema`, the run goes through the retry helper in [`aicodebox/shared/runner.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/shared/runner.py). It tries to read JSON out of the answer even with code fences or prose around it. If that fails or the value does not match the schema, it asks the agent again, up to three times, with the original task, the bad answer, the error and the schema. Retries stop early when an attempt exits non-zero or reports a provider error. `usage` is the sum over all attempts, because the provider bills every one.

```json
{
  "prompt": "Inspect the workspace and return a short report.",
  "jsonSchema": {
    "type": "object",
    "properties": {
      "summary": {"type": "string"}
    },
    "required": ["summary"]
  }
}
```

### Event retention

`eventMode` decides whether the response carries the agent's own event stream:

| `eventMode` | Events returned |
| --- | --- |
| `none` | Never, even with `jsonSchema`. |
| `full` | Always. |
| `auto` (default) | When `jsonSchema` is set or `outputFormat` is `json-verbose`. Kept for older callers. |

Each record has a stable envelope around the untouched provider record:

```json
{
  "sequence": 1,
  "attempt": 0,
  "backend": "your-agent",
  "eventType": "message_start",
  "event": {
    "type": "message_start"
  }
}
```

`sequence` counts across all attempts, `attempt` is the schema retry index, `backend` is the adapter name, and `eventType` is the record's own `type` field. A stdout line that is not a JSON object is kept as `{"line": "..."}` with `eventType` `raw_stdout`. New callers should use `eventMode`, because a JSON schema and event retention are separate choices.

## Background runs

With `async` or `fireAndForget`, `POST /run` answers at once:

```json
{
  "runId": "3f0c9b2e6d0a4c1f9a7e2b5d8c4f6a1e",
  "workspace": "/workspace",
  "status": "running",
  "fireAndForget": false
}
```

Poll `GET /run/result?runId=<id>`. While the run is going it returns `status: "running"`. When it ends, the response has `status` (`completed`, `failed` or `cancelled`) plus the same fields a synchronous run returns, or `error` for a failed run. A finished result is deleted the first time it is read, so a second poll gives 404. A sweep every ten minutes drops any run, read or not, six hours after it finished, or six hours after it started if it never finished. Up to eight background runs execute at once; more wait in a queue.

`DELETE /run/{id}` kills the agent process and returns `{"runId": "...", "status": "cancelled"}`, or 404 for an unknown ID. A run that already finished also answers `cancelled`, without changing its stored result.

## One run per workspace

A workspace runs one agent at a time, across `/run` and the OpenAI endpoint. A second request for a busy workspace gets `409 workspace <path> is busy with another run` from `/run` and `409 workspace busy, retry later` from the OpenAI endpoint. Give parallel callers different `workspace` values.

## Files

The file routes work on paths relative to `AICODEBOX_WORKSPACE`. A path that resolves outside it, including through a symlink, gives `400 path escapes workspace root`.

| Request | Result |
| --- | --- |
| `GET /files` | `{"path": "/", "entries": [...]}` for the root. Each entry has `name`, `type` (`file` or `dir`) and, for files, `size`. |
| `GET /files/{path}` | The same listing for a directory, or the file's bytes. 404 if missing. |
| `PUT /files/{path}` | Writes the raw body, creating parent directories. Returns `{"status": "ok", "path": ..., "size": <bytes>}`. 400 if the path is a directory. |
| `DELETE /files/{path}` | Removes a file. 400 for a directory, 404 if missing. |

```bash
curl -s -X PUT http://localhost:8080/files/notes/todo.md \
  -H "Authorization: Bearer $TOKEN" \
  --data-binary @todo.md
```

## Status and health

`GET /status` returns `{"busyWorkspaces": [...], "runs": [{"runId", "workspace", "status"}]}` for the runs the server still holds.

`GET /healthz` returns `{"ok": true, "adapter": "<name>"}`, or `{"ok": false, "error": "..."}` with status 200 when no adapter is configured. It does not start the agent.

## Errors

| Status | Cause |
| --- | --- |
| 400 | Bad workspace path, or a file path outside the workspace. |
| 401 | Missing or wrong bearer token. |
| 404 | Unknown run ID, or a missing file. |
| 409 | The workspace is busy with another run. |
| 422 | Invalid body, or the adapter's `validate` rejected the request. |
| 500 | The run raised an exception inside the server. |
