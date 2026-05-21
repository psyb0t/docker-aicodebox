"""Single source of truth for model + effort lists.

Resolution order:
  1. env override (CSV) — AICODEBOX_AVAILABLE_MODELS / AICODEBOX_AVAILABLE_EFFORTS
  2. the configured adapter's class attribute
  3. empty list (adapter unconfigured)

Used by /v1/models (API mode), MCP, and the telegram /model + /effort pickers
so all three surfaces stay in agreement.
"""
from __future__ import annotations

import os

from aicodebox.adapters import get_adapter


def _csv(value: str) -> list[str]:
    return [m.strip() for m in value.split(",") if m.strip()]


def available_models() -> list[str]:
    env = os.environ.get("AICODEBOX_AVAILABLE_MODELS")
    if env:
        return _csv(env)
    try:
        return list(get_adapter().available_models)
    except RuntimeError:
        return []


def available_efforts() -> list[str]:
    env = os.environ.get("AICODEBOX_AVAILABLE_EFFORTS")
    if env:
        return _csv(env)
    try:
        return list(get_adapter().available_thinking_levels)
    except RuntimeError:
        return []
