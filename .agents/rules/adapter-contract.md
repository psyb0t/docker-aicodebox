# Adapter contract

Prefix: ADP. The contract in `aicodebox/adapters/base.py` is the public API every child image builds against.

- **ADP1:** `RunRequest`, `RunResult`, `StreamEvent`, `AgentAdapter` and the names exported from `aicodebox/adapters/__init__.py` are a compatibility promise to claudebox, codexbox and pibox. Add fields with defaults. Do not remove or rename a field, change a default, or make an optional method required without a breaking-change CHANGELOG entry.
- **ADP2:** Modes never call an agent directly. They build a `RunSpec` and go through `aicodebox/shared/runner.py` (`run`, `run_stream`, `run_with_json_retry`). A new `RunSpec` field also goes into `RunRequest` and `spec_to_request`.
- **ADP3:** Behavior that every agent needs lives in the runner, not in adapters. Native event retention is the example: `_attach_events` and `run_stream` keep every stdout line, and `AgentAdapter.parse_events` is a legacy hook the runner no longer calls.
- **ADP4:** The runner owns `raw_stdout`, `raw_stderr` and `exit_code`, and sets them after `parse_output`. Exit code 127 means the binary was not found, 124 a timeout. Keep those meanings.
- **ADP5:** Schema retries stop on a non-zero exit or a `provider_error`, and `usage` is summed over attempts (`_accumulate_usage`). The per-attempt list uses the keys `index`, `usage`, `exitCode`, `parseError`, which both `/run` and the OpenAI endpoint return.
- **ADP6:** Model and effort lists come only from `aicodebox/shared/choices.py`, so `/openai/v1/models` and the Telegram pickers agree.
- **ADP7:** A contract change updates `docs/adapter-contract.md` and the unit tests that cover it (`test_runner_stream.py`, `test_usage_accumulation.py`, `test_json_extraction.py`).
