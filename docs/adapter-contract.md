---
title: "Adapter contract"
description: "The AgentAdapter class a child image implements: RunRequest fields, build_argv, parse_output, stream events, auth translation and JSON helpers."
nav_order: 3
---

# Adapter contract

Every mode talks to the agent through one class, `AgentAdapter` in [`aicodebox/adapters/base.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/adapters/base.py). You subclass it once per agent and name it in `AICODEBOX_ADAPTER`. What the HTTP API, MCP, Telegram and cron can do is exactly what your `build_argv` knows how to drive.

## A minimal adapter

```python
# your_adapter/adapter.py
from aicodebox.adapters.base import (
    AgentAdapter,
    RunRequest,
    RunResult,
    StreamEvent,
)


class YourAdapter(AgentAdapter):
    name = "your-agent"
    binary = "your-agent"
    available_models = ["fast", "smart"]
    available_thinking_levels = ["off", "low", "high"]

    def build_argv(self, req: RunRequest) -> list[str]:
        argv = [self.binary, "-p", req.prompt]
        if req.model:
            argv += ["--model", req.model]
        return argv

    def parse_output(self, stdout: str, req: RunRequest) -> RunResult:
        # The runner fills raw_stdout, raw_stderr and exit_code afterwards.
        return RunResult(
            text=stdout.strip(),
            raw_stdout="",
            raw_stderr="",
            exit_code=0,
        )

    def parse_stream_event(self, line: str, req: RunRequest) -> StreamEvent | None:
        return StreamEvent(type="delta", text=line + "\n") if line else None
```

The runner starts the agent with the request's workspace as its working directory, writes `req.prompt` to its stdin, and collects stdout and stderr. An agent that reads the prompt from its arguments can ignore stdin.

## What to implement

| Member | Required | Default | Purpose |
| --- | --- | --- | --- |
| `name` | yes | `""` | Shown in logs, `/healthz`, Telegram and event envelopes. An OpenAI request whose `model` equals this name falls back to the agent's default model. |
| `binary` | no | `""` | Used by the default `interactive_argv` and `passthrough_argv`. |
| `available_models` | no | `[]` | Model list for `/openai/v1/models` and the Telegram `/model` picker. `AICODEBOX_AVAILABLE_MODELS` overrides it. |
| `available_thinking_levels` | no | `[]` | Effort list for the Telegram `/effort` picker. `AICODEBOX_AVAILABLE_EFFORTS` overrides it. |
| `build_argv(req)` | yes | raises `NotImplementedError` | Turn a `RunRequest` into the agent's command line. |
| `parse_output(stdout, req)` | no | plain text: `text=stdout.strip()` | Turn the agent's stdout into a `RunResult`. Set `text`, and `usage`, `session_id`, `parsed`, `parse_error` or `provider_error` when the agent reports them. |
| `parse_stream_event(line, req)` | no | one `delta` per non-empty line | Turn one stdout line of a streaming run into a `StreamEvent`, or `None` to skip it. |
| `validate(req)` | no | checks `output_format` and `event_mode` | Raise `ValueError` for a request the agent cannot honor. The HTTP API turns it into a 422. |
| `translate_auth(env)` | no | `{}` | Return extra variables to add to the agent's environment, for example a provider key under the name the agent expects. |
| `build_env(req)` | no | process environment, then `translate_auth`, then `req.env` | The full environment for the agent process. |
| `post_validate_json(result, req)` | no | | Helper: parse `result.text` as JSON and validate it against `req.json_schema`. Call it from `parse_output` if your agent does not enforce schemas itself. |
| `interactive_argv`, `passthrough_argv`, `auth_paths` | no | | Hooks child wrappers can use. The base modes do not call them. |
| `parse_events` | no | `[]` | Legacy hook. The runner no longer calls it. |

## RunRequest

Every mode builds a `RunRequest` from its own input. Your adapter decides which fields it supports.

| Field | Type | Meaning |
| --- | --- | --- |
| `prompt` | `str` | The instruction. Also written to the agent's stdin. |
| `workspace` | `str` | Absolute working directory for the run. |
| `model` | `str` or `None` | Requested model. When a mode passes none, the runner uses `ANTHROPIC_MODEL` from the environment if set. |
| `system_prompt` | `str` or `None` | Replace the agent's system prompt. |
| `append_system_prompt` | `str` or `None` | Append to the system prompt. Telegram and cron always use this for their own hints. |
| `json_schema` | `dict` or `None` | The caller wants JSON matching this schema. |
| `output_format` | `str` | `text`, `json` or `json-verbose`. A hint for how the agent should print its answer. |
| `no_continue` | `bool` | Start a fresh session instead of continuing the last one in this workspace. |
| `resume` | `str` or `None` | Resume a specific session ID. |
| `extra_args` | `list[str]` | Extra command-line arguments from the caller. |
| `env` | `dict[str, str]` | Extra environment variables, applied last. |
| `timeout_seconds` | `int` or `None` | The runner kills the agent after this many seconds and reports exit code 124. |
| `thinking` | `str` or `None` | Reasoning level, such as `off`, `low`, `high`. |
| `tools_allowlist` | `list[str]` or `None` | Restrict the agent's own tools. |
| `no_tools` | `bool` | Disable the agent's own tools. |
| `event_mode` | `str` | `none` or `full`. The runner handles `full` itself (see below). |
| `proc_hook` | callable or `None` | Called with the `Popen` object right after start. The modes use it for cancellation. |

## RunResult

| Field | Meaning |
| --- | --- |
| `text` | The final answer. |
| `raw_stdout`, `raw_stderr`, `exit_code` | Filled in by the runner after `parse_output` returns. A missing binary gives exit code 127. A timeout gives 124 and the stderr text `timeout after <n>s`. |
| `session_id` | The agent's session or conversation ID, when it reports one. |
| `usage` | Token counts as a dictionary. The OpenAI endpoint reads `input_tokens`, `inputTokens` or `input`, and `output_tokens`, `outputTokens` or `output`. |
| `parsed`, `parse_error` | A decoded JSON answer, or why decoding failed. |
| `provider_error` | An upstream error such as a content-policy refusal. Schema retries stop on it, and the OpenAI endpoint returns it as a 400. |
| `attempts`, `events`, `event_attempts` | Filled in by the runner for schema retries and event retention. |

## Stream events

`parse_stream_event` returns a `StreamEvent(type, text, data)`. The OpenAI streaming path reads these types:

| Type | Fields | Effect |
| --- | --- | --- |
| `delta` | `text` | Sent as a content chunk. |
| `error` | `text` | Sent as a `[error: ...]` content chunk, and the finish reason becomes `error`. |
| `stop` | `data["reason"]` | Sets the finish reason. |
| `session`, `usage`, `raw` | `data` | Available to modes. The OpenAI stream ignores them. |

The runner adds its own `stop` event when the process exits, with reason `stop`, `error` (non-zero exit, preceded by an `error` event holding up to 500 characters of stderr) or `timeout`.

## Native events

When a caller asks for `eventMode: "full"` or streamed native events, the runner keeps every non-empty stdout line itself. Your adapter does nothing for this. A line that parses as a JSON object stays as that object. Diagnostics, malformed JSON and JSON values that are not objects become `{"line": "<the raw line>"}` records. See [HTTP API](api.md#event-retention) for the envelope.

## JSON helpers

`parse_json_response(text, schema)` decodes an answer that should be JSON. It tries the whole text, the text with code fences stripped, each fenced block from last to first, and the first balanced `{...}` or `[...]`, and returns the first candidate that parses and validates. It returns `(value, None)` on success and `(None, error)` otherwise. `strip_json_fences` and `parse_native_event_lines` are exported too.

`aicodebox.shared.json_envelope.parse_envelope(raw)` reads the JSON output formats of Claude Code, pi and opencode and returns `(text, usage, stop_reason)`. Use it if your agent prints one of those shapes.

## Testing an adapter

`aicodebox.adapters.base.reset_adapter_cache()` clears the cached instance, so a test can switch `AICODEBOX_ADAPTER` between cases. The base package's own tests use a stub adapter in [`aicodebox/tests/conftest.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/tests/conftest.py).
