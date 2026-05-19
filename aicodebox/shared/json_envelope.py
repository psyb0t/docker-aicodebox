"""Decode the JSON output emitted by claude-code, pi, and opencode.

These three harnesses can be configured to produce structured output:
  - claude-code: `--output-format json` → single JSON object.
  - pi: `--mode json` → single JSON object.
  - opencode: `--format json` → NDJSON event stream.

The shapes differ but all carry the same pieces of information the OpenAI
adapter needs: the final assistant text, token usage, and a stop reason.
This module exposes one tolerant parser that handles every shape so callers
don't need per-harness branches.
"""

from __future__ import annotations

import json
from typing import Any


def extract_text(value: Any) -> str:
    """Pull text out of common envelope shapes: string, list of content
    blocks, or nested {text/content/result/message} dicts."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for block in value:
            if isinstance(block, dict):
                parts.append(block.get("text") or block.get("content") or "")
            elif isinstance(block, str):
                parts.append(block)
        return "".join(parts)
    if isinstance(value, dict):
        return extract_text(
            value.get("text") or value.get("content") or value.get("result") or "",
        )
    return ""


def parse_envelope(raw: str) -> tuple[str, dict, str | None]:
    """Decode harness JSON output.

    Returns (text, usage_dict, stop_reason). Both single-JSON-object envelopes
    (claude-code, pi) and NDJSON event streams (opencode) are handled. On
    parse failure all fields are returned empty/None rather than raising —
    the caller can fall back to plain text output.
    """
    raw = (raw or "").strip()
    if not raw:
        return "", {}, None

    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            text = extract_text(
                obj.get("result")
                or obj.get("text")
                or obj.get("content")
                or obj.get("message"),
            )
            usage = obj.get("usage") or obj.get("tokens") or {}
            stop = obj.get("stop_reason") or obj.get("finish_reason")
            return text, usage if isinstance(usage, dict) else {}, stop
    except json.JSONDecodeError:
        pass

    text_parts: list[str] = []
    usage: dict = {}
    stop: str | None = None
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        chunk = extract_text(
            ev.get("delta")
            or ev.get("text")
            or ev.get("content")
            or ev.get("message"),
        )
        if chunk:
            text_parts.append(chunk)
        if isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        if ev.get("stop_reason"):
            stop = ev["stop_reason"]
        elif ev.get("finish_reason") and not stop:
            stop = ev["finish_reason"]
    return "".join(text_parts), usage, stop
