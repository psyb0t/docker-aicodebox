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


@dataclass
class StreamEvent:
    """One unit of output from a streaming agent invocation.

    Emitted by ``runner.run_stream`` after passing a raw stdout line through
    ``AgentAdapter.parse_stream_event``. The mode layer (OAI streaming,
    telegram live-typing, etc.) consumes these and renders them in its own
    transport.

    Types:
      ``delta``    — incremental assistant text. ``text`` carries the chunk.
      ``session``  — session/conversation id. ``data["id"]``.
      ``usage``    — token counts. ``data`` mirrors RunResult.usage shape.
      ``stop``     — terminal event. ``data["reason"]`` is the finish reason.
      ``error``    — non-fatal parse / runtime warning. ``text`` carries msg.
      ``raw``      — unparsed line (debug). Modes typically ignore.
    """

    type: str
    text: str = ""
    data: dict | None = None


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*\n?|\n?```\s*$", re.IGNORECASE)
_FENCE_BLOCK_RE = re.compile(
    r"```(?:json|JSON)?\s*\n?(.*?)\n?\s*```",
    re.DOTALL,
)


def strip_json_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _balanced_extract(text: str, open_ch: str, close_ch: str) -> str | None:
    """Find the first balanced ``open_ch``-to-``close_ch`` substring.

    Walks the text once tracking string literals + escapes so braces
    inside JSON strings don't throw off the depth counter. Returns the
    matched substring (inclusive) or ``None`` if no balanced block exists.
    """
    start = text.find(open_ch)
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
                continue
            if ch == "\\":
                escape = True
                continue
            if ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
            continue
        if ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _json_candidates(text: str) -> list[str]:
    """Best-effort candidate strings to try parsing as JSON, in order.

    LLMs that ignore the "no prose, no fences" instruction commonly emit
    one of these shapes:

      - clean JSON only                    → candidate 0 wins
      - JSON wrapped in ``` fences          → candidate 1 wins (edge-strip)
      - prose + ```json{...}``` block       → candidate 2/3 wins (fenced block)
      - prose + bare {...} object           → candidate 4 wins (brace-balance)

    Caller iterates and returns the first that parses + schema-validates.
    """
    candidates: list[str] = [text]

    edge_stripped = strip_json_fences(text)
    if edge_stripped and edge_stripped != text:
        candidates.append(edge_stripped)

    fenced_blocks = _FENCE_BLOCK_RE.findall(text)
    # Iterate fenced blocks LAST-first — LLMs that emit prose then a fenced
    # answer at the end put the canonical value in the trailing block.
    for block in reversed(fenced_blocks):
        block_stripped = block.strip()
        if block_stripped and block_stripped not in candidates:
            candidates.append(block_stripped)

    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        extracted = _balanced_extract(text, open_ch, close_ch)
        if extracted and extracted not in candidates:
            candidates.append(extracted)

    return candidates


def parse_json_response(
    text: str, schema: dict | None = None,
) -> tuple[Any, str | None]:
    """Decode an LLM response as JSON, tolerating fences + surrounding prose.

    Tries multiple extraction strategies (see ``_json_candidates``) and
    returns the first candidate that both parses AND, if ``schema`` is
    provided, schema-validates. The retry path on the caller side is
    therefore reserved for "the model produced JSON but with the wrong
    structure" — pure fencing/chatter issues are absorbed here.
    """
    first_parse_error: str | None = None
    first_parsed: Any = None
    first_schema_error: str | None = None

    try:
        import jsonschema  # type: ignore[import-not-found]
        have_jsonschema = True
    except ImportError:
        jsonschema = None  # type: ignore[assignment]
        have_jsonschema = False

    for candidate in _json_candidates(text):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError as exc:
            if first_parse_error is None:
                first_parse_error = f"response is not valid JSON: {exc}"
            continue

        if schema is None or not have_jsonschema:
            return value, None

        try:
            jsonschema.validate(value, schema)  # type: ignore[union-attr]
            return value, None
        except jsonschema.ValidationError as exc:  # type: ignore[union-attr]
            if first_parsed is None:
                first_parsed = value
                first_schema_error = (
                    f"response does not match schema: {exc.message}"
                )
            continue

    if first_schema_error is not None:
        return None, first_schema_error
    return None, first_parse_error or "response is not valid JSON"


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

        Default impl accepts everything; override to reject combinations
        the agent does not support. ``json_schema`` is valid with both
        ``output_format=json`` (clean JSON-only output) and
        ``output_format=json-verbose`` (event stream where the final
        assistant turn carries the schema-validated JSON the caller asked
        for) — the wrapper validates ``result.text`` either way and
        retries on parse / schema failure."""
        if req.output_format not in ("text", "json", "json-verbose"):
            raise ValueError(
                f"output_format={req.output_format!r} invalid; "
                "choose text | json | json-verbose"
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

    # ── event log (post-hoc parse of completed stdout) ───────────────────────

    def parse_events(
        self, stdout: str, req: RunRequest,
    ) -> list[dict[str, Any]]:
        """Extract structured events from a completed run's stdout.

        Returned by ``/run`` as the ``events`` field whenever the list is
        non-empty — gives clients structured access to tool calls / thinking
        blocks / per-turn metadata without forcing them to opt into raw
        stdout. Default: empty list (plain-text adapters have no events).

        Adapters whose binaries emit a structured stream (pi's
        ``--output-format=json-verbose`` for example) override this to JSON-
        decode each line and return the parsed objects. Lines that fail to
        decode are dropped silently — events is a best-effort surface, not
        the canonical transcript (use ``includeRaw`` if you need the bytes).
        """
        del stdout, req
        return []

    # ── streaming ────────────────────────────────────────────────────────────

    def parse_stream_event(
        self, line: str, req: RunRequest,
    ) -> StreamEvent | None:
        """Parse one raw stdout line from a streaming run.

        Called once per line by ``runner.run_stream``. Return ``None`` to
        skip the line (e.g. structured-stream adapters that filter heartbeat
        or non-content events).

        Default behaviour: every non-empty line becomes a text delta with a
        trailing newline restored. Adapters whose binaries emit a structured
        stream (e.g. pi's ``--output-format=json-verbose``) override this to
        decode JSON events and emit typed StreamEvents (``delta``, ``usage``,
        ``session``, ``stop``).
        """
        del req
        if not line:
            return None
        return StreamEvent(type="delta", text=f"{line}\n")

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
