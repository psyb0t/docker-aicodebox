"""Agent adapter contract.

aicodebox modes (api / telegram / cron / mcp_server) are agent-agnostic. Each
deployed image picks one agent (claude-code, pi, codex, …) and provides a
concrete ``AgentAdapter`` subclass. The adapter is selected at runtime via
the ``AICODEBOX_ADAPTER`` env var (importable ``pkg.module:ClassName``).

An adapter translates a canonical ``RunRequest`` into the agent's native CLI
invocation, performs any environment aliasing required for auth, and parses
the resulting stdout into a normalized ``RunResult``.
"""

from __future__ import annotations

import importlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Callable, ClassVar, Optional

ProcHook = Optional[Callable[[subprocess.Popen], None]]


@dataclass
class RunRequest:
    prompt: str = ""
    workspace: str = ""
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    json_schema: dict | None = None
    output_format: str = "text"  # text | json
    no_continue: bool = False
    resume: str | None = None
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    timeout_seconds: int | None = None
    proc_hook: ProcHook = None
    thinking: str | None = None
    tools_allowlist: list[str] | None = None
    no_tools: bool = False


@dataclass
class RunResult:
    text: str
    raw_stdout: str
    raw_stderr: str
    exit_code: int
    session_id: str = ""
    parsed: Any = None
    parse_error: str | None = None
    usage: dict[str, Any] | None = None


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE)


def strip_json_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def parse_json_response(
    text: str, schema: dict | None = None,
) -> tuple[Any, str | None]:
    cleaned = strip_json_fences(text)
    try:
        value = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return None, f"response is not valid JSON: {exc}"
    if schema is not None:
        try:
            import jsonschema  # type: ignore[import-not-found]
        except ImportError:
            return value, None
        try:
            jsonschema.validate(value, schema)
        except jsonschema.ValidationError as exc:
            return None, f"response does not match schema: {exc.message}"
    return value, None


class AgentAdapter:
    """Each concrete agent implements this. One adapter per image."""

    name: ClassVar[str] = ""
    binary: ClassVar[str] = ""

    # Optional choice menus surfaced by the telegram bot's /model and /effort
    # commands. Empty means "free text only, no validation, no inline keyboard".
    available_models: ClassVar[list[str]] = []
    available_thinking_levels: ClassVar[list[str]] = []

    # ── canonical run path ───────────────────────────────────────────────────

    def validate(self, req: RunRequest) -> None:
        """Raise ValueError if the adapter cannot honour this request.

        Default impl accepts everything; override to reject combinations the
        agent does not support (e.g. an agent without native JSON schema must
        reject ``output_format=json`` combined with ``json_schema``)."""
        if req.output_format not in ("text", "json"):
            raise ValueError(
                f"output_format={req.output_format!r} invalid; choose text|json"
            )

    def build_argv(self, req: RunRequest) -> list[str]:
        del req
        raise NotImplementedError

    def build_env(self, req: RunRequest) -> dict[str, str]:
        env = dict(os.environ)
        env.update(self.translate_auth(env))
        env.update(req.env or {})
        return env

    def translate_auth(self, env: dict[str, str]) -> dict[str, str]:
        """Map canonical env vars (ANTHROPIC_API_KEY, etc.) into whatever
        names the agent's binary expects. Default: identity."""
        del env
        return {}

    def parse_output(self, stdout: str, req: RunRequest) -> RunResult:
        """Convert raw stdout into a normalized RunResult. Default: plain
        text mode — caller assigns exit_code/stderr separately."""
        del req
        return RunResult(
            text=stdout.strip(),
            raw_stdout=stdout,
            raw_stderr="",
            exit_code=0,
        )

    def post_validate_json(
        self, result: RunResult, req: RunRequest,
    ) -> None:
        """Parse stdout as JSON and (optionally) validate against
        req.json_schema. Adapters call this from parse_output when the agent
        does not natively enforce a schema."""
        if not req.json_schema or result.exit_code != 0:
            return
        value, err = parse_json_response(result.text or "", req.json_schema)
        result.parsed = value
        result.parse_error = err

    # ── interactive / passthrough ────────────────────────────────────────────

    def interactive_argv(self, workspace: str) -> list[str]:
        del workspace
        return [self.binary]

    def passthrough_argv(self, args: list[str]) -> list[str]:
        return [self.binary, *args]

    # ── auth persistence ─────────────────────────────────────────────────────

    def auth_paths(self) -> list[str]:
        """Paths inside the container that should be persisted across
        ``docker start`` so OAuth tokens survive container restarts."""
        return []


# ── adapter resolution ─────────────────────────────────────────────────────


_cached: AgentAdapter | None = None


def get_adapter() -> AgentAdapter:
    """Resolve the configured adapter, instantiate it, cache it.

    ``AICODEBOX_ADAPTER`` must be ``pkg.module:ClassName``."""
    global _cached
    if _cached is not None:
        return _cached
    ref = os.environ.get("AICODEBOX_ADAPTER")
    if not ref:
        raise RuntimeError(
            "AICODEBOX_ADAPTER not set; image must export it "
            "(e.g. 'pibox.adapter:PiAdapter')"
        )
    if ":" not in ref:
        raise RuntimeError(
            f"AICODEBOX_ADAPTER={ref!r} malformed; expected pkg.module:Class"
        )
    mod_path, cls_name = ref.split(":", 1)
    mod = importlib.import_module(mod_path)
    cls = getattr(mod, cls_name)
    inst = cls()
    if not isinstance(inst, AgentAdapter):
        raise RuntimeError(f"{ref} is not an AgentAdapter")
    _cached = inst
    return inst


def reset_adapter_cache() -> None:
    """For tests only."""
    global _cached
    _cached = None
