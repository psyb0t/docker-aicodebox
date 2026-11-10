---
title: "OpenAI-compatible endpoint"
description: "POST /openai/v1/chat/completions in API mode: streaming, response_format JSON schemas with retries, client-executed tool calls, images and x-aicodebox headers."
nav_order: 2
parent: "Modes"
---

# OpenAI-compatible endpoint

API mode also serves `POST /openai/v1/chat/completions` and `GET /openai/v1/models`, so any client that speaks the OpenAI Chat Completions API can drive the agent. Both routes use the API bearer token. The code is [`aicodebox/modes/api/oai.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/modes/api/oai.py).

```bash
curl -s http://localhost:8080/openai/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "model-a",
    "messages": [
      {"role": "user", "content": "What does this repository do?"}
    ]
  }'
```

With an SDK, set the base URL to `http://<host>:8080/openai/v1` and the API key to the API mode token.

## Models

`GET /openai/v1/models` lists `AICODEBOX_AVAILABLE_MODELS`, or the adapter's `available_models` when the variable is unset. It returns 503 if the list is empty.

In a request, a `provider/model` value is cut to `model`. A model equal to the adapter's name, or no model, means the agent's own default.

## Request fields

| Field | Handling |
| --- | --- |
| `messages` | Required. See [How messages become a prompt](#how-messages-become-a-prompt). |
| `model` | As above. |
| `stream` | `true` for server-sent events. |
| `reasoning_effort` | Passed to the adapter as the thinking level. |
| `response_format` | `text`, `json_object` or `json_schema`. See [Structured output](#structured-output). |
| `tools`, `tool_choice` | Client-executed tool calling. See [Tool calling](#tool-calling). |
| `stream_options.include_aicodebox_events` | `true` adds the agent's native events to a stream. Needs `stream: true`. |

Other OpenAI fields are accepted and ignored.

## Headers

The `x-aicodebox-*` headers set run options that have no OpenAI field. A malformed value gives a 400 that names the header.

| Header | Value | Effect |
| --- | --- | --- |
| `x-aicodebox-workspace` | subpath | Run in this subdirectory of the workspace root. |
| `x-aicodebox-continue` | `1`, `true` or `yes` | Continue the last session in the workspace. Without it every request starts a fresh session. |
| `x-aicodebox-append-system-prompt` | text | Append to the system prompt. |
| `x-aicodebox-json-schema` | JSON object | Schema for the answer, for clients that cannot set `response_format`. The body field wins when both are set. |
| `x-aicodebox-resume` | session ID | Resume that session. |
| `x-aicodebox-extra-args` | JSON array or comma-separated list | Extra command-line arguments. |
| `x-aicodebox-timeout-seconds` | integer | Kill the agent after this many seconds. |
| `x-aicodebox-tools-allowlist` | JSON array or comma-separated list | Restrict the agent's own tools. |
| `x-aicodebox-no-tools` | `1`, `true` or `yes` | Disable the agent's own tools. Any other value enables them. |

`x-claude-workspace`, `x-claude-continue` and `x-claude-append-system-prompt` are accepted as older names for the first three.

## How messages become a prompt

- `system` messages are joined into the system prompt.
- A single user message is sent as the prompt as it is.
- Several messages are flattened into a transcript with `[USER]` and `[ASSISTANT]` tags, headed by "Continue this conversation. Respond to the last user message." A message with `content: null`, such as an assistant turn that only carried `tool_calls`, counts as empty and is left out.
- `image_url` parts are saved under `_oai_uploads/` in the workspace root. A `data:` URL is decoded. An `http` or `https` URL is downloaded, up to 50 MB with a 30 second timeout, only if its host does not resolve to a private, loopback, link-local, multicast or reserved address. Redirects are checked the same way. When images are present, the whole conversation is written to a JSON file in the same directory and the prompt tells the agent to read it. Uploads older than 24 hours are deleted.

## Response

A normal answer is a standard `chat.completion` object. `usage.prompt_tokens` and `usage.completion_tokens` come from the adapter's `usage` (`input_tokens`, `inputTokens` or `input`, and the matching output keys), and are 0 when the adapter reports none.

A non-zero exit from the agent in plain chat still returns 200, with whatever text the agent printed. An adapter that sets `provider_error` turns it into a 400.

## Streaming

Plain chat with `stream: true` streams for real: each `delta` event from the adapter becomes a `chat.completion.chunk`, followed by a finish chunk and `data: [DONE]`. An adapter error is sent as a `[error: <message>]` content chunk with finish reason `error`. A timeout ends with finish reason `timeout`.

Tool calling and structured output need the whole answer before they can reply. With `stream: true` they compute the full answer, then replay it as one short stream: a role chunk, one content or `tool_calls` delta with its `index`, a finish chunk and `[DONE]`. It is a valid stream, just not token by token.

### Native events in a stream

```json
{
  "model": "model-a",
  "stream": true,
  "stream_options": {
    "include_aicodebox_events": true
  },
  "messages": [
    {"role": "user", "content": "Run the tests."}
  ]
}
```

The stream then also carries named SSE records, `event: aicodebox.native`, with the same `{sequence, attempt, backend, eventType, event}` envelope as [`/run` events](api.md#event-retention). The ordinary chunks and `[DONE]` do not change. The option must be a boolean, and it gives a 400 without `stream: true`.

## Structured output

Send the standard `response_format`:

| `response_format` | Effect |
| --- | --- |
| `{"type": "text"}` or absent | Plain text. |
| `{"type": "json_object"}` | The answer must be a JSON object. |
| `{"type": "json_schema", "json_schema": {"name": "...", "schema": {...}}}` | The answer must match `schema`. |

```json
{
  "model": "model-a",
  "messages": [
    {"role": "user", "content": "Summarize the README."}
  ],
  "response_format": {
    "type": "json_schema",
    "json_schema": {
      "name": "summary",
      "schema": {
        "type": "object",
        "properties": {
          "summary": {"type": "string"}
        },
        "required": ["summary"]
      }
    }
  }
}
```

Schema mode retries up to three times, like [`/run`](api.md#json-schema-retries). On success, `message.content` is the canonical JSON with no fences or prose. The response also carries `aicodebox_attempts: [{index, usage, exitCode, parseError}]`, and `usage` is the sum over all attempts.

When the request has no `x-aicodebox-workspace`, schema mode runs in a private temporary directory under `/tmp/aicodebox/<id>/` (mode `0700`), removed when the request ends. Retries there continue the agent's own session with a short correction message instead of resending the whole prompt, which is much cheaper on large inputs. With a caller workspace, each retry starts a fresh session and repeats the task, because other sessions may live in that directory. Leftover temporary directories older than an hour are removed.

| Status | Cause |
| --- | --- |
| 422 | Every attempt failed validation. |
| 500 | The agent exited non-zero. `detail` holds the exit code and the start of stderr. |
| 400 | A malformed `response_format`, or a provider error. |

## Tool calling

Send `tools` (and optionally `tool_choice`) and the agent acts as a function-calling model whose tools your client runs. The endpoint adds the tool list and a reply protocol to the system prompt. When the agent wants a tool, the response has `message.tool_calls` and `finish_reason: "tool_calls"`. Your client runs the tool and sends the result back as a `role: "tool"` message with the full history, as with OpenAI. The server keeps no state between rounds.

`tool_choice` accepts `auto`, `none`, `required` and `{"type": "function", "function": {"name": "..."}}`. `none`, or an empty `tools` list, means plain chat.

In tool mode the agent's own tools (shell, file edits) default to off, so it does not do the job itself instead of asking for your tool. Send `x-aicodebox-no-tools: 0` to keep both.

`tools` and `response_format` work together. A turn that calls a tool returns `tool_calls` and skips the schema check. The final turn, without a tool call, must match the schema and is retried like any schema answer.

## Errors

| Status | Cause |
| --- | --- |
| 400 | No user message, a malformed header, a bad `response_format`, a bad `stream_options` value, or a provider error. |
| 401 | Missing or wrong bearer token. |
| 409 | `workspace busy, retry later`: another run holds the workspace. |
| 422 | Schema validation failed after all retries. |
| 500 | The agent exited non-zero in schema mode, or the server raised an exception. |
| 503 | No models configured. |
