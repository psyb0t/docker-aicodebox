"""Cron mode config parsing.

YAML schema:
    model: <default-model>            # optional
    system_prompt: <text>             # optional
    append_system_prompt: <text>      # optional
    telegram_chat_id: <chat-id>       # optional (phase 4)
    jobs:
      - name: <slug>
        schedule: "*/30 * * * * *"    # croniter, 6-field (second-resolution)
        instruction: <prompt>
        workspace: subpath            # optional, default '.'
        model: <override>             # optional
        system_prompt: <text>         # optional override
        append_system_prompt: <text>  # optional override
        no_continue: false            # optional
        telegram_chat_id: <chat-id>   # optional (phase 4)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from croniter import croniter

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class CronConfigError(ValueError):
    pass


@dataclass
class CronJob:
    name: str
    schedule: str
    instruction: str
    workspace: str = "."
    model: str | None = None
    system_prompt: str | None = None
    append_system_prompt: str | None = None
    no_continue: bool = False
    telegram_chat_id: int | None = None
    effort: str | None = None
    thinking: str | None = None


@dataclass
class CronConfig:
    jobs: list[CronJob] = field(default_factory=list)
    defaults: dict[str, Any] = field(default_factory=dict)


def _str_or_none(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CronConfigError(f"{label!r} must be a string")
    return value


def _int_or_none(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise CronConfigError(f"{label!r} must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value)
    raise CronConfigError(f"{label!r} must be an integer")


def load(path: str | Path) -> CronConfig:
    p = Path(path)
    if not p.is_file():
        raise CronConfigError(f"cron file not found: {path}")
    try:
        data = yaml.safe_load(p.read_text()) or {}
    except yaml.YAMLError as exc:
        raise CronConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CronConfigError("cron file must be a mapping at top level")

    defaults: dict[str, Any] = {
        "model": _str_or_none(data.get("model"), "model"),
        "system_prompt": _str_or_none(data.get("system_prompt"), "system_prompt"),
        "append_system_prompt": _str_or_none(
            data.get("append_system_prompt"), "append_system_prompt"
        ),
        "telegram_chat_id": _int_or_none(data.get("telegram_chat_id"), "telegram_chat_id"),
        "effort": _str_or_none(data.get("effort"), "effort"),
        "thinking": _str_or_none(data.get("thinking"), "thinking"),
    }

    raw_jobs = data.get("jobs")
    if not isinstance(raw_jobs, list) or not raw_jobs:
        raise CronConfigError("'jobs' must be a non-empty list")

    seen: set[str] = set()
    jobs: list[CronJob] = []
    for i, j in enumerate(raw_jobs):
        if not isinstance(j, dict):
            raise CronConfigError(f"job #{i}: must be a mapping")
        name = j.get("name")
        if not name or not isinstance(name, str):
            raise CronConfigError(f"job #{i}: 'name' is required (string)")
        if not _NAME_RE.match(name):
            raise CronConfigError(f"job '{name}': name must match [A-Za-z0-9_-]+")
        if name in seen:
            raise CronConfigError(f"duplicate job name: {name}")
        seen.add(name)
        schedule = j.get("schedule")
        if not schedule or not isinstance(schedule, str):
            raise CronConfigError(f"job '{name}': 'schedule' is required (string)")
        if not croniter.is_valid(schedule, second_at_beginning=True):
            raise CronConfigError(f"job '{name}': invalid cron expression: {schedule!r}")
        instruction = j.get("instruction")
        if not instruction or not isinstance(instruction, str) or not instruction.strip():
            raise CronConfigError(f"job '{name}': 'instruction' is required (non-empty string)")
        workspace = j.get("workspace", ".")
        if not isinstance(workspace, str):
            raise CronConfigError(f"job '{name}': 'workspace' must be a string")
        no_continue = j.get("no_continue", False)
        if not isinstance(no_continue, bool):
            raise CronConfigError(f"job '{name}': 'no_continue' must be a bool")
        job = CronJob(
            name=name,
            schedule=schedule,
            instruction=instruction,
            workspace=workspace,
            model=_str_or_none(j.get("model"), f"job '{name}': model") or defaults["model"],
            system_prompt=_str_or_none(j.get("system_prompt"), f"job '{name}': system_prompt")
            or defaults["system_prompt"],
            append_system_prompt=_str_or_none(
                j.get("append_system_prompt"), f"job '{name}': append_system_prompt"
            )
            or defaults["append_system_prompt"],
            no_continue=no_continue,
            telegram_chat_id=_int_or_none(
                j.get("telegram_chat_id"), f"job '{name}': telegram_chat_id"
            )
            or defaults["telegram_chat_id"],
            effort=_str_or_none(j.get("effort"), f"job '{name}': effort")
            or defaults["effort"],
            thinking=_str_or_none(j.get("thinking"), f"job '{name}': thinking")
            or defaults["thinking"],
        )
        jobs.append(job)

    return CronConfig(jobs=jobs, defaults=defaults)
