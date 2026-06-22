"""Tests for usage-token accumulation + per-attempt breakdown across
JSON retry attempts.

Every retry attempt is its own paid LLM call. ``run_with_json_retry``
must surface BOTH:

  1. The SUM of tokens across all attempts as ``result.usage`` — so
     callers see the real billable cost without knowing retries
     happened.
  2. The per-attempt breakdown as ``result.attempts`` — so callers
     can render "retry 2/3 cost X tokens" or debug which attempt
     failed which way.
"""

from __future__ import annotations

import sys

import pytest

from aicodebox.adapters import base as adapter_base
from aicodebox.adapters.base import RunResult
from aicodebox.shared import runner as runner_mod
from aicodebox.shared.runner import (
    JSON_RETRY_MAX,
    RunSpec,
    _accumulate_usage,
    run_with_json_retry,
)


class _UsageAdapter(adapter_base.AgentAdapter):
    name = "usage-test"
    binary = "/bin/true"

    def build_argv(self, req):
        del req
        return [self.binary]


@pytest.fixture
def usage_adapter(monkeypatch):
    monkeypatch.setitem(
        sys.modules, "aicodebox.tests._usage", sys.modules[__name__],
    )
    monkeypatch.setenv(
        "AICODEBOX_ADAPTER",
        "aicodebox.tests.test_usage_accumulation:_UsageAdapter",
    )
    adapter_base.reset_adapter_cache()
    yield _UsageAdapter
    adapter_base.reset_adapter_cache()


def _patch_runner(monkeypatch, results: list[RunResult]):
    calls = {"n": 0}

    def fake(spec, proc_hook=None):
        del spec, proc_hook
        i = calls["n"]
        calls["n"] += 1
        return results[min(i, len(results) - 1)]

    monkeypatch.setattr(runner_mod, "run", fake)
    return calls


# ── _accumulate_usage unit tests ────────────────────────────────────────────


def test_accumulate_input_output_total_keys_sum():
    """Sum the standard OAI keys plus the alias variants adapters use."""
    target: dict = {}
    _accumulate_usage(target, {
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
    })
    _accumulate_usage(target, {
        "input_tokens": 110, "output_tokens": 25, "total_tokens": 135,
    })
    assert target == {
        "input_tokens": 210, "output_tokens": 45, "total_tokens": 255,
    }


def test_accumulate_cache_tokens_sum():
    """Anthropic-style cache fields sum just like any other numeric key
    — caller pays for cache reads + writes per attempt."""
    target: dict = {}
    _accumulate_usage(target, {
        "input_tokens": 100, "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 0,
    })
    _accumulate_usage(target, {
        "input_tokens": 110, "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 50,
    })
    assert target == {
        "input_tokens": 210,
        "cache_creation_input_tokens": 50,
        "cache_read_input_tokens": 50,
    }


def test_accumulate_handles_none():
    target = {"input_tokens": 10}
    _accumulate_usage(target, None)
    assert target == {"input_tokens": 10}


def test_accumulate_handles_empty_dict():
    target = {"input_tokens": 10}
    _accumulate_usage(target, {})
    assert target == {"input_tokens": 10}


def test_accumulate_new_keys_added():
    """A later attempt may surface a key the first didn't (e.g.
    cache_creation_tokens on the second turn). New keys land in target."""
    target = {"input_tokens": 100}
    _accumulate_usage(target, {"input_tokens": 50, "cache_tokens": 200})
    assert target == {"input_tokens": 150, "cache_tokens": 200}


def test_accumulate_non_numeric_keeps_first():
    """Non-numeric fields (model id, request id, etc.) keep the first
    occurrence — summing would be meaningless."""
    target = {"model": "claude-opus-4-7"}
    _accumulate_usage(target, {"model": "claude-opus-4-7-different"})
    assert target == {"model": "claude-opus-4-7"}


def test_accumulate_bool_treated_as_non_numeric():
    """bool is a subclass of int in Python — without explicit handling,
    True + True would be 2. Bool fields keep the first occurrence."""
    target = {"cached": True}
    _accumulate_usage(target, {"cached": True})
    assert target == {"cached": True}


def test_accumulate_floats_sum():
    target = {"cost_usd": 0.0042}
    _accumulate_usage(target, {"cost_usd": 0.0018})
    assert target["cost_usd"] == pytest.approx(0.0060)


def test_accumulate_type_mismatch_keeps_existing():
    """If existing value is non-numeric, an incoming numeric value for
    the same key must not corrupt the type — keep the existing one."""
    target = {"input_tokens": "unknown"}
    _accumulate_usage(target, {"input_tokens": 50})
    assert target == {"input_tokens": "unknown"}


# ── run_with_json_retry: sum + per-attempt array ────────────────────────────


def _spec(workspace="/tmp", schema=None):
    return RunSpec(
        prompt="x",
        workspace=workspace,
        json_schema=schema or {"type": "object", "required": ["n"]},
        output_format="json-verbose",
    )


def test_usage_summed_and_attempts_array_on_success(
    usage_adapter, monkeypatch,
):
    """3 attempts (initial + 2 retries). Final result.usage carries the
    SUM. result.attempts carries the per-attempt breakdown."""
    del usage_adapter
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    _patch_runner(monkeypatch, [
        RunResult(
            text='{"n": "bad"}', raw_stdout='{"n": "bad"}', raw_stderr="",
            exit_code=0, usage={
                "input_tokens": 100, "output_tokens": 20,
                "cache_read_input_tokens": 0,
            },
        ),
        RunResult(
            text='{"n": "still bad"}', raw_stdout='{"n": "still bad"}',
            raw_stderr="", exit_code=0, usage={
                "input_tokens": 110, "output_tokens": 25,
                "cache_read_input_tokens": 50,
            },
        ),
        RunResult(
            text='{"n": 7}', raw_stdout='{"n": 7}', raw_stderr="",
            exit_code=0, usage={
                "input_tokens": 120, "output_tokens": 30,
                "cache_read_input_tokens": 60,
            },
        ),
    ])

    result, parsed, parse_error, retries = run_with_json_retry(
        _spec(schema=schema),
    )
    assert parse_error is None
    assert parsed == {"n": 7}
    assert retries == 2

    # SUM
    assert result.usage == {
        "input_tokens": 330,
        "output_tokens": 75,
        "cache_read_input_tokens": 110,
    }

    # Per-attempt breakdown
    assert result.attempts is not None
    assert len(result.attempts) == 3
    assert result.attempts[0]["index"] == 0
    assert result.attempts[0]["usage"] == {
        "input_tokens": 100, "output_tokens": 20,
        "cache_read_input_tokens": 0,
    }
    assert result.attempts[0]["exitCode"] == 0
    assert "schema" in (result.attempts[0]["parseError"] or "")

    assert result.attempts[1]["index"] == 1
    assert result.attempts[1]["usage"] == {
        "input_tokens": 110, "output_tokens": 25,
        "cache_read_input_tokens": 50,
    }
    assert result.attempts[1]["parseError"] is not None

    assert result.attempts[2]["index"] == 2
    assert result.attempts[2]["usage"] == {
        "input_tokens": 120, "output_tokens": 30,
        "cache_read_input_tokens": 60,
    }
    assert result.attempts[2]["parseError"] is None  # success on attempt 3


def test_attempts_array_on_exhaustion(usage_adapter, monkeypatch):
    """4 attempts (initial + JSON_RETRY_MAX retries) all fail. Sum
    reflects 4× cost. Per-attempt array has 4 entries each with
    parseError set."""
    del usage_adapter
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    bad = RunResult(
        text='{"n": "bad"}', raw_stdout='{"n": "bad"}', raw_stderr="",
        exit_code=0, usage={"input_tokens": 100, "output_tokens": 10},
    )
    _patch_runner(monkeypatch, [bad] * 5)

    result, parsed, parse_error, retries = run_with_json_retry(
        _spec(schema=schema),
    )
    assert parsed is None
    assert parse_error is not None
    assert retries == JSON_RETRY_MAX

    assert result.usage == {
        "input_tokens": 4 * 100,
        "output_tokens": 4 * 10,
    }
    assert result.attempts is not None
    assert len(result.attempts) == 4
    for i, attempt in enumerate(result.attempts):
        assert attempt["index"] == i
        assert attempt["exitCode"] == 0
        assert attempt["parseError"] is not None


def test_attempts_array_captures_crash(usage_adapter, monkeypatch):
    """If a retry attempt exits non-zero, the loop aborts but that
    attempt still appears in the array with its exit code."""
    del usage_adapter
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    _patch_runner(monkeypatch, [
        RunResult(
            text='{"n": "bad"}', raw_stdout='{"n": "bad"}', raw_stderr="",
            exit_code=0,
            usage={"input_tokens": 100, "output_tokens": 20},
        ),
        RunResult(
            text="", raw_stdout="",
            raw_stderr="connection refused", exit_code=2,
            usage=None,
        ),
    ])

    result, parsed, _parse_error, retries = run_with_json_retry(
        _spec(schema=schema),
    )
    assert parsed is None
    assert retries == 1
    assert result.exit_code == 2
    assert result.attempts is not None
    assert len(result.attempts) == 2
    assert result.attempts[0]["exitCode"] == 0
    assert result.attempts[1]["exitCode"] == 2
    assert result.attempts[1]["usage"] is None


def test_attempts_array_single_entry_when_no_retries(
    usage_adapter, monkeypatch,
):
    """Initial attempt succeeds → attempts is a single-entry array
    [initial]. Caller can still iterate uniformly without checking
    'did retries happen'."""
    del usage_adapter
    schema = {"type": "object"}
    _patch_runner(monkeypatch, [
        RunResult(
            text='{"ok": true}', raw_stdout='{"ok": true}', raw_stderr="",
            exit_code=0,
            usage={"input_tokens": 50, "output_tokens": 10},
        ),
    ])

    result, _, _, retries = run_with_json_retry(_spec(schema=schema))
    assert retries == 0
    assert result.usage == {"input_tokens": 50, "output_tokens": 10}
    assert result.attempts is not None
    assert len(result.attempts) == 1
    assert result.attempts[0] == {
        "index": 0,
        "usage": {"input_tokens": 50, "output_tokens": 10},
        "exitCode": 0,
        "parseError": None,
    }


def test_retry_prompt_includes_original_task(usage_adapter, monkeypatch):
    """Regression: the retry prompt MUST include the original task so
    the LLM (running in a fresh no_continue session) has the context
    needed to make an informed correction. Without it, errors that
    require task context to fix (e.g. picking the right enum value
    from a large allowed-values list) make the retry re-pick blindly.

    Before this fix the retry prompt only had: bad output + parse error
    + schema. Now it ALSO carries the original spec.prompt verbatim.
    """
    del usage_adapter
    schema = {
        "type": "object",
        "properties": {
            "currency": {"enum": ["EUR", "GBP", "JPY", "USD"]},
        },
        "required": ["currency"],
    }
    original_task = (
        "Write a brief about the Sri Lanka rate hike on 2026-06-19. "
        "Pick the currency that's relevant to the story."
    )

    captured_prompts: list[str] = []

    def fake(spec, proc_hook=None):
        del proc_hook
        captured_prompts.append(spec.prompt)
        idx = len(captured_prompts)
        # First attempt: invalid currency. Second: valid.
        if idx == 1:
            return RunResult(
                text='{"currency": "LKR"}',
                raw_stdout='{"currency": "LKR"}', raw_stderr="",
                exit_code=0,
                usage={"input_tokens": 100, "output_tokens": 5},
            )
        return RunResult(
            text='{"currency": "USD"}',
            raw_stdout='{"currency": "USD"}', raw_stderr="",
            exit_code=0,
            usage={"input_tokens": 200, "output_tokens": 5},
        )

    monkeypatch.setattr(runner_mod, "run", fake)

    spec = RunSpec(
        prompt=original_task,
        workspace="/tmp",
        json_schema=schema,
        output_format="json-verbose",
    )
    _, parsed, parse_error, retries = run_with_json_retry(spec)

    assert parse_error is None
    assert parsed == {"currency": "USD"}
    assert retries == 1
    assert len(captured_prompts) == 2

    # First attempt: original task verbatim.
    assert captured_prompts[0] == original_task

    # Second attempt (the retry): must contain BOTH the original task
    # AND the bad-output context + error + schema. Without the original
    # task the model can't correct an enum mismatch sensibly.
    retry_prompt = captured_prompts[1]
    assert original_task in retry_prompt, (
        "retry prompt missing original task — the model has no idea "
        "what task it's correcting"
    )
    assert '{"currency": "LKR"}' in retry_prompt  # the bad output
    assert "LKR" in retry_prompt  # the validation error mentions LKR
    assert "EUR" in retry_prompt  # the schema is re-stated


def test_usage_handles_missing_usage_on_some_attempts(
    usage_adapter, monkeypatch,
):
    """Adapter may omit usage on a failed attempt (e.g. timeout before
    the response completed). Accumulator skips those gracefully and
    the attempt entry still records usage=None."""
    del usage_adapter
    schema = {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"],
    }
    _patch_runner(monkeypatch, [
        RunResult(
            text='{"n": "bad"}', raw_stdout='{"n": "bad"}', raw_stderr="",
            exit_code=0, usage=None,
        ),
        RunResult(
            text='{"n": 7}', raw_stdout='{"n": 7}', raw_stderr="",
            exit_code=0,
            usage={"input_tokens": 120, "output_tokens": 30},
        ),
    ])

    result, _, parse_error, retries = run_with_json_retry(
        _spec(schema=schema),
    )
    assert parse_error is None
    assert retries == 1
    assert result.usage == {"input_tokens": 120, "output_tokens": 30}
    assert result.attempts is not None
    assert result.attempts[0]["usage"] is None
    assert result.attempts[1]["usage"] == {
        "input_tokens": 120, "output_tokens": 30,
    }
