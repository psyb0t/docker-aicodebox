# Changelog

All notable changes per release. Versions follow [semver](https://semver.org)
pre-1.0 conventions: minor bumps may include breaking REST changes (called
out explicitly), patch bumps are docs / build / fixes only.

## v0.8.3 — 2026-06-19

Fix the version-reporting drift v0.8.2 (and every prior release)
shipped with: `aicodebox.__version__` reported `0.1.0` regardless of
the actual release tag, and the docker image only ever tagged
`:latest`. One canonical version source now; everything derives.

### What was wrong

Up to v0.8.2 inclusive:

- `pyproject.toml` `[project] version` was stuck at `0.1.0` (never
  bumped).
- `aicodebox/__init__.py` had `__version__ = "0.1.0"` hardcoded
  (also never bumped).
- `Makefile` tagged every build `psyb0t/aicodebox:latest` — no
  `vX.Y.Z` tag.

So a customer running v0.8.2 from docker saw
`aicodebox.__version__ == "0.1.0"` even though the released tag was
`v0.8.2`. The OAI `info.version` in any generated spec, the
`/healthz` payload, and any `--version` output would have lied
about what was actually running.

### The fix — single source of truth

- **`pyproject.toml` `[project] version`** is now THE canonical
  string. Bump that one line, everything else follows.
- **`aicodebox/__init__.py`** reads it at import time via
  `importlib.metadata.version("aicodebox")`. Falls back to
  `"0.0.0+source"` only if the package isn't installed (source
  checkout without `uv sync` — that fallback exists so the bug
  is OBVIOUS rather than silently reporting a stale number).
- **`Makefile`** derives the docker tag from `pyproject.toml` via
  `awk` and tags BOTH `psyb0t/aicodebox:vX.Y.Z` AND
  `psyb0t/aicodebox:latest` on every `make build`. New `make
  version` target prints what would be tagged. Override at build
  time via `VERSION=… make build` for one-offs.

### Release flow going forward

```bash
$EDITOR pyproject.toml      # bump version = "0.X.Y"
uv lock                     # refresh uv.lock with new project version
make version                # confirm derived tag matches
make build                  # tags :vX.Y.Z + :latest
docker run --rm --entrypoint python3 psyb0t/aicodebox:vX.Y.Z \
    -c "import aicodebox; print(aicodebox.__version__)"
                            # confirms runtime matches
```

Then the usual git commit + tag + push via `git-update.sh`.

### Migration

None. Anyone scripting against `aicodebox.__version__` was
already getting `"0.1.0"` regardless of what they pulled —
they'll now get the real version starting with v0.8.3. Strict
improvement.

## v0.8.2 — 2026-06-19

Backfill proper logging across the schema-mode code path so a future
operator can reconstruct exactly what happened from logs alone. No
behavior change.

### Logging additions

- **`adapters.base.parse_json_response`** — was completely silent.
  Now: DEBUG per candidate (which extraction strategy, length,
  sample, parse outcome, schema outcome), DEBUG on winning candidate
  with its length, INFO summary on "all candidates tried, none
  matched" with the first error. When a schema is provided but the
  `jsonschema` lib isn't importable, WARN that validation was
  skipped. Module gains its own logger (`adapters.base`).
- **`shared.runner.run_with_json_retry`** — was emitting only the
  per-retry INFO line. Now: INFO on entry (max retries, schema
  keys), DEBUG on each attempt outcome (exit code, parse error,
  usage keys), INFO/WARN per retry transition, WARN on
  attempt-crashed-mid-retry abort, INFO terminal summary
  (outcome=success|exhausted|crashed, attempts, retries,
  total usage).
- **`modes.api.oai.chat_completions`** — INFO entry log (model,
  stream flag, message count, schema/resume/no-tools presence
  flags). On schema-mode success: INFO with retries, attempts
  count, total usage, content length. On non-schema success: INFO
  with text length + usage. Header parse helpers
  (`_parse_int_header` / `_parse_dict_header` / `_parse_list_header`)
  WARN with the rejected value truncated before raising 400.

### Format

Uses the existing project logger (`shared.logging._JsonFormatter`).
JSON shape when `DEBUG=1` includes `ts/level/logger/func/line/file/
msg`. Plain `LEVEL logger: msg` text otherwise. No secrets, tokens,
full request bodies, or env dumps in any added call. Header values
truncated to ≤80 chars in the rejection warnings; raw stderr
truncated to 200 chars in the crash warnings (consistent with
existing log calls in the same files).

### Tests

All 151 existing tests still pass. No new test files — added
logging is best-effort observability, not functional contract.
Manually verified the JSON shape end-to-end with `DEBUG=1` against
`parse_json_response` (fenced-prose input) and confirmed `ts /
level / logger / func / line / file / msg` all present.

### Migration

None — internal observability improvement only. No public surface
changed.

## v0.8.1 — 2026-06-18

Three correctness fixes on the v0.8.0 schema-mode path: distinguish
agent crashes from validation failures (500 vs 422), sum token usage
across retry attempts so the bill is honest, and surface per-attempt
breakdown so callers can debug + bill per attempt. Plus regression
tests for streaming RunSpec plumbing that v0.7.0 shipped untested.

### Bug fix — agent crash in schema mode returns 500, not 422

In v0.8.0 a schema-mode request whose agent process exited non-zero
(crash, timeout, missing binary) returned **422** — the same code used
for "agent ran fine but the JSON it produced still doesn't match your
schema after 3 retries". That conflated two very different failure
sources: caller-side schema problems vs server-side process problems.
A client retrying on 422 (assuming "my schema is too strict") would
loop indefinitely on a real server crash.

Now:
- **422** — retries exhausted, agent's output still doesn't validate.
  Caller's schema or prompt is the root cause.
- **500** — agent process exited non-zero. Server-side problem.
  Detail includes the exit code + the first 200 chars of stderr.

The non-schema path is unchanged (it never inspected `exit_code` and
still returns 200 with whatever text the agent produced, matching
prior behavior).

### Bug fix — usage is summed across retries

In v0.8.0 the schema-mode response surfaced `result.usage` from the
FINAL attempt only. Every retry is its own paid LLM call; reporting
only the last attempt's tokens under-counted the actual provider bill.

`run_with_json_retry` now sums every numeric usage field — including
`input_tokens` / `output_tokens` / `total_tokens` / `cache_creation_*`
/ `cache_read_*` / anything else the adapter surfaces — across all
attempts. The summed total is written back to `result.usage` so
downstream payloads (`/run` response, OAI envelope) report the real
billable cost without needing to know retries happened. Non-numeric
fields (model id, request id, etc.) keep the first occurrence —
summing strings is meaningless.

### New — per-attempt breakdown

The retry helper now also populates `result.attempts`, a list of
per-attempt records:

```json
[
  {"index": 0, "usage": {"input_tokens": 100, "output_tokens": 20},
   "exitCode": 0, "parseError": "schema mismatch: n is string"},
  {"index": 1, "usage": {"input_tokens": 110, "output_tokens": 25},
   "exitCode": 0, "parseError": "schema mismatch: n is string"},
  {"index": 2, "usage": {"input_tokens": 120, "output_tokens": 30},
   "exitCode": 0, "parseError": null}
]
```

Surfaced as `attempts` on the `/run` response and as
`aicodebox_attempts` on the OAI envelope (vendor extension key — OAI
clients ignore unknown fields). Callers can render "retry 2/3 cost X
tokens", debug which retry failed which way, or bill per attempt.
Always present in schema mode; absent in non-schema mode.

### Tests

- `tests/test_usage_accumulation.py` (NEW) — 14 cases covering the
  `_accumulate_usage` helper (numeric sum, cache-token fields, none /
  empty handling, new-key inclusion, non-numeric first-wins, bool
  edge case, float sum, type-mismatch preservation) and
  `run_with_json_retry` end-to-end (success path with summed usage +
  3-entry attempts array, exhaustion with 4× sum + 4-entry array,
  crash mid-retry, single-entry array on no-retries-needed, missing
  per-attempt usage).
- `tests/test_oai_schema.py::test_schema_retry_succeeds` — extended
  to assert summed `usage` (210 / 50 / 260) and the
  `aicodebox_attempts` array in the OAI envelope.
- `tests/test_oai_schema.py::test_schema_agent_crash_returns_500` —
  asserts the new 500 with the exit code + stderr in `detail`.
- `tests/test_oai_schema.py::test_schema_failure_after_retries_returns_422`
  — extended to assert the actual retry count (initial + 3 retries =
  4 attempts, matching `JSON_RETRY_MAX = 3`).
- `tests/test_oai_schema.py::test_stream_plumbs_runspec_headers` —
  new regression catch: verifies the streaming path's `_stream_response`
  threads `extra_args` / `no_tools` / `tools_allowlist` /
  `timeout_seconds` / `resume` from headers all the way into the
  `RunSpec` built inside the async generator. v0.7.0 added these
  kwargs but never tested them end-to-end.

### Migration

None — strict improvement. Clients that read `usage` get the real
total now (previously under-counted). Clients that need per-attempt
detail read `attempts` (`/run`) or `aicodebox_attempts` (OAI). Clients
catching 5xx for "server problem, maybe retry" already do the right
thing. Clients with a specific 422 handler should narrow it to
"schema validation failed" and add a 500 handler for "agent crashed".

## v0.8.0 — 2026-06-18

Wire the schema validation + self-correction path into
`/openai/v1/chat/completions` and make `parse_json_response` tolerant of
LLMs that wrap JSON in fences mid-prose. v0.7.0 plumbed
`x-aicodebox-json-schema` through to the adapter but ignored
`result.parsed` / `result.parse_error` — the route returned the raw text
into `message.content` with no validation. This release closes that gap
and reserves the retry budget for actual structural failures.

### Schema validation on `/openai/v1/chat/completions`

When `x-aicodebox-json-schema` is set, the route now runs the same
self-correction path `/run` uses (up to 3 re-prompts on parse /
validation failure). On success `message.content` carries the canonical
re-serialized JSON — no fences, no surrounding prose, regardless of how
the LLM originally formatted it. On exhaustion the route returns
**422** with the validation error in `detail`. Setting `stream=true`
together with the schema header returns **400** — schema validation
requires the complete response and there's no clean way to recover from
a mid-stream parse failure over the SSE wire.

### Smarter JSON extraction in `adapters.base.parse_json_response`

`parse_json_response` now tries multiple extraction strategies in order
before declaring a response invalid:

1. Clean JSON — caller already returned just JSON.
2. Edge-fences stripped — `` ```json\n{...}\n``` `` at the boundaries.
3. Mid-prose ``` fenced blocks — LAST block first (LLMs that emit
   prose-then-answer put the canonical value in the trailing fence).
4. Balanced-brace extraction — first complete `{...}` or `[...]`
   substring, with proper string-literal + escape handling so braces
   inside JSON strings don't throw off the depth counter.

When a schema is provided, the loop prefers candidates that BOTH parse
AND schema-validate. The retry budget is therefore reserved for the
"agent emitted JSON with the wrong structure" case — pure fencing /
chatter issues are absorbed here without a round trip.

Benefits `/run` callers too (same shared helper). New extractor in
`adapters/base.py`; the public surface (`parse_json_response`,
`strip_json_fences`) is unchanged. Private helpers `_json_candidates`
and `_balanced_extract` exposed for tests.

### Refactor

- Move `_retry_prompt` + `_run_json_with_retry` out of
  `modes/api/server.py` into `shared/runner.py` as `_json_retry_prompt`
  + `run_with_json_retry`. Both `/run` and `/openai/v1/chat/completions`
  call it now. `JSON_RETRY_MAX = 3` lives next to it.

### Tests

- `tests/test_json_extraction.py` — 15 cases covering clean JSON, edge
  fences, mid-prose fenced blocks (single, multiple, last-wins), brace
  extraction with strings + escapes + nesting, schema-aware candidate
  selection.
- `tests/test_oai_schema.py` — 7 TestClient-driven end-to-end cases:
  schema success (canonical content), retry success, exhaustion → 422,
  `stream=true` + schema → 400, other-header plumbing,
  malformed-header 400.
- `tests/test_api_run_response.py` — patcher updated to also intercept
  `shared.runner.run` (call site moved during the refactor).

### Migration

None — additive. v0.7.0 callers that set `x-aicodebox-json-schema` and
got back unvalidated text in `message.content` now get either canonical
JSON (success) or a 422 (validation failure after retries). Clients
that were silently accepting malformed JSON should add a 422 handler.

## v0.7.0 — 2026-06-07

Expose the remaining RunSpec knobs on `/openai/v1/chat/completions` so OAI
clients no longer have to drop down to `/run` for schema-validated output,
session resumes, or tool-allowlist control.

- New request headers on `/openai/v1/chat/completions`:
  - `x-aicodebox-json-schema` — JSON object; schema-validates the agent's
    final assistant turn and flips the run to `output_format=json-verbose`
    so the adapter event stream is available end-to-end (mirrors `/run`'s
    `_derive_output_format`).
  - `x-aicodebox-resume` — string; resumes a specific adapter session id.
  - `x-aicodebox-extra-args` — JSON array OR comma-separated string;
    appended to the adapter's CLI invocation.
  - `x-aicodebox-timeout-seconds` — integer; per-run timeout.
  - `x-aicodebox-tools-allowlist` — JSON array OR comma-separated string;
    restricts which adapter tools the run may use.
  - `x-aicodebox-no-tools` — `1` / `true` / `yes`; disables the adapter's
    tool surface entirely.
- Malformed header values surface as 400 with the offending header name
  in the detail; parsing helpers (`_parse_bool_header`, `_parse_int_header`,
  `_parse_dict_header`, `_parse_list_header`) live in `aicodebox/modes/api/oai.py`.
- Body-level 400s on `tools` / `tool_choice` / `response_format=json_object`
  are unchanged — those are distinct OAI-protocol concerns from the new
  `tools_allowlist` / `no_tools` / `json_schema` RunSpec knobs.
- Repo: add `.github/FUNDING.yml` for GitHub Sponsors / Monero links.

No breaking changes — all new headers default to off, existing requests
keep their prior shape.

## v0.6.0 — 2026-05-23

Collapse v0.5.0's two-flag matrix on `/run`. `jsonSchema` is now the only
dial that decides response richness.

- **Breaking.** `/run` request model no longer accepts `verbose`. Pydantic
  silently drops it (`extra=ignore`), so stale `verbose=true` callers get
  the lean text response without a 422.
- `/run` response shape is fully determined by `jsonSchema`:
  - no `jsonSchema` → `{runId, workspace, exitCode, text}` (lean)
  - `jsonSchema` set → `{runId, workspace, exitCode, text, json, events,
    sessionId, usage}` (full); on schema-validation failure after 3
    retries, `json` is replaced by `parseError` + `jsonRetries`.
- Want events without strict validation? Pass `"jsonSchema": {"type":
  "object"}` — permissive, just forces JSON output.

Migration:
- drop `verbose=true`; if you wanted events / sessionId / usage, set
  `jsonSchema` (use `{"type":"object"}` for permissive).
- schema-set callers now also receive `text` + `events` + `sessionId` +
  `usage` alongside `json`.
- SDK regen recommended.

## v0.5.0 — 2026-05-23

Split LLM output style and response richness into two orthogonal request
flags on `/run`.

- **Breaking.** Replace v0.4.0's `outputFormat` enum with two booleans:
  - `jsonSchema` (dict | null) — agent runs in JSON mode, response carries
    `json` field. 3-retry self-correction on parse / schema failure.
  - `verbose` (bool) — response includes `events` + `sessionId` + `usage`
    alongside `text`.
- `jsonSchema` + `verbose=true` composes — verbose surface plus `json`
  (or `parseError` + `jsonRetries` on failure).
- **Breaking.** Field rename: `parsed` → `json`. SDK regen recommended.

## v0.4.0 — 2026-05-23

Restructure `/run`'s response payload around an explicit `outputFormat`
dial and add JSON self-correction.

- **Breaking.** `/run` no longer ships `raw_stdout` / `raw_stderr` by
  default. Payload shape is now strictly determined by `outputFormat`:
  - `text` → `{..., text}`
  - `json` → `{..., parsed}` on success; `{..., text, parseError,
    jsonRetries}` on retry exhaustion.
  - `json-verbose` → `{..., events}`.
- Always-when-populated: `sessionId`, `usage`. Opt-in via
  `includeRaw=true`: `stdout` + `stderr`. Auto-included on
  `exitCode != 0`: `stderr`.
- JSON mode now self-corrects: failed JSON decode / schema validation
  triggers up to 3 retries where the agent is re-prompted with its prior
  bad output and the specific error. `jsonRetries` reports the count.
- `AgentAdapter` gains `parse_events(stdout, req)` — invoked only in
  `json-verbose` mode. Default returns `[]`; adapters emitting structured
  streams override to JSON-decode each line.

Migration: callers reading `result.stdout` / `result.stderr` must opt
into `includeRaw` or migrate to `result.text` / `result.parsed` /
`result.events`. JSON callers reading raw text on success must move to
`parsed`. SDK regen recommended.

## v0.3.0 — 2026-05-23

Real OAI streaming on `/openai/v1/chat/completions` — replaces the
previous single-chunk fake-stream.

- New `StreamEvent` + `AgentAdapter.parse_stream_event` — typed per-line
  adapter hook for structured streaming.
- `shared.runner.run_stream` — async generator over the agent's stdout.
  Default behaviour: one text delta per line; adapters override
  `parse_stream_event` to decode their native event stream (json-verbose,
  etc.).
- `oai.py _stream_response` emits one `chat.completion.chunk` per delta
  event in real time. Subprocess is killed on client disconnect.
- `oai.py` reads usage token counts through an alias-aware helper
  (`input_tokens` / `inputTokens` / `input` — same for output) so
  adapters don't need to dual-write the key shape.

No breaking changes — adapters without a custom `parse_stream_event` get
the default line-per-delta behaviour automatically.

## v0.2.1 — 2026-05-21

Require an explicit model list for API mode; remove the silent
adapter-name fallback.

- **Breaking.** API mode refuses to boot if neither
  `AICODEBOX_AVAILABLE_MODELS` nor `adapter.available_models` declares
  anything. Previously the harness fell back to `[adapter.name]` (e.g.
  `["pi"]`), which is a binary name and not a valid model id.
- `/v1/models` reflects the configured list verbatim — no adapter-name
  fallback.
- Telegram `/model` and `/effort` pickers degrade gracefully on empty
  list (reply with a "set this env var" message; bot keeps running).
- Cron / MCP / passthrough are unaffected — they forward the caller's
  model string to the harness, which errors directly on bad model ids.
- `shared/choices.py` is the single source of truth for the list.

Migration: API-mode containers that didn't set
`AICODEBOX_AVAILABLE_MODELS` used to boot and serve `/v1/models` with the
bogus fallback. Now they refuse to start. Set the env var
(comma-separated) with the model ids your provider actually serves.

## v0.2.0 — 2026-05-21

Env-var rename to the `<MODE>_MODE` convention + standalone MCP mode.

- **Breaking.** Every mode flag and knob follows
  `<MODE>_MODE` / `<MODE>_MODE_<KNOB>` now. Every consumer setting
  `AICODEBOX_MODE_API`, `AICODEBOX_TELEGRAM_BOT_TOKEN`,
  `AICODEBOX_MODE_CRON_FILE`, etc., must rename to the new shape. No
  backwards-compat shim.
- MCP is independent of API / Telegram / Cron and can coexist (sidecar
  in non-API modes, `/mcp` mount inside API).
- Separate bearer token per surface (`API_MODE_TOKEN` vs
  `MCP_MODE_TOKEN`), no fallback between them.

## v0.1.3 — 2026-05-20

Documentation fix.

- Correct mode-combination claims in `README.md` (some combinations
  previously listed as supported were not).

## v0.1.2 — 2026-05-20

Build fixes.

- Fix arm64 build path.
- Use `uv`'s system install flow inside the image.

## v0.1.1 — 2026-05-19

Tooling + supply-chain hardening.

- Switch Python dependency management from pip / requirements files to
  `uv` with a lockfile.
- Pin every base / tool image by `@sha256:` digest.

## v0.1.0 — 2026-05-19

Initial release — agent-agnostic foundation image. Provides the
`AgentAdapter` contract, the `/run` synchronous + asynchronous run path,
the `/openai/v1` chat-completions translator, Telegram bot mode, cron
mode, and MCP mode, all selected at runtime via env-var flags. Concrete
adapters (claude-code, pi, codex, …) ship as downstream images that
subclass `AgentAdapter` and set `AICODEBOX_ADAPTER`.
