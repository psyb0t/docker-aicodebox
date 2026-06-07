# Changelog

All notable changes per release. Versions follow [semver](https://semver.org)
pre-1.0 conventions: minor bumps may include breaking REST changes (called
out explicitly), patch bumps are docs / build / fixes only.

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
