"""Cron scheduler — runs jobs on schedule, dispatches to the configured agent.

History layout
--------------
Each run gets a dedicated directory:

    ~/.aicodebox/cron/history/<workspace_slug>/<YYYYmmdd-HHMMSS>-<job>/
        meta.json       — job metadata + exit code + timings
        stdout.log      — raw agent stdout
        stderr.log      — raw agent stderr
        result.txt      — final parsed result text
        telegram.json   — chat_id + message_ids of the notification (if sent)

A per-job summary jsonl (``~/.aicodebox/cron/<name>.jsonl``) is also appended
for back-compat with existing tooling.

Telegram tracking
-----------------
When a job posts to telegram, the message_id → run-metadata mapping is
stored in ``~/.aicodebox/cron/telegram_messages.json`` so the telegram bot
can detect cron-reply context and inject the run's history_dir into the
follow-up prompt. Capped at the 200 most recent messages.
"""

from __future__ import annotations

import json
import logging
import os
import re
import signal
import threading
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from croniter import croniter

from aicodebox.adapters import get_adapter
from aicodebox.modes.cron.config import CronConfig, CronConfigError, CronJob, load
from aicodebox.shared.logging import configure_logging
from aicodebox.shared.runner import RunSpec
from aicodebox.shared.runner import run as run_agent

log = logging.getLogger("cron")

_HOME = Path(os.environ.get("HOME", "/home/aicode"))
HISTORY_ROOT = _HOME / ".aicodebox" / "cron"
HISTORY_RUNS_ROOT = HISTORY_ROOT / "history"
TELEGRAM_MESSAGES_FILE = HISTORY_ROOT / "telegram_messages.json"
TELEGRAM_MESSAGES_CAP = 200

_shutdown = threading.Event()
_running: dict[str, threading.Thread] = {}
_running_lock = threading.Lock()
_tg_lock = threading.Lock()


def _slugify(path: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "_", path).strip("_")
    return s or "workspace"


def _post_to_telegram(chat_id: int, html: str) -> int | None:
    token = os.environ.get("AICODEBOX_TELEGRAM_BOT_TOKEN")
    if not token:
        log.warning("telegram_chat_id set but AICODEBOX_TELEGRAM_BOT_TOKEN missing")
        return None
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": html,
            "parse_mode": "HTML",
        }
    ).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=30) as resp:
            payload = json.loads(resp.read().decode())
    except Exception as exc:  # noqa: BLE001
        log.warning("telegram sendMessage failed: %s; retrying without HTML", exc)
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": html}).encode()
        try:
            with urllib.request.urlopen(url, data=data, timeout=30) as resp:
                payload = json.loads(resp.read().decode())
        except Exception as exc2:  # noqa: BLE001
            log.error("telegram sendMessage plain retry failed: %s", exc2)
            return None
    if not payload.get("ok"):
        log.error("telegram sendMessage not ok: %s", payload)
        return None
    return int(payload["result"]["message_id"])


def _record_telegram_message(message_id: int, entry: dict[str, Any]) -> None:
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    with _tg_lock:
        data: dict[str, Any] = {}
        if TELEGRAM_MESSAGES_FILE.is_file():
            try:
                data = json.loads(TELEGRAM_MESSAGES_FILE.read_text()) or {}
            except Exception:  # noqa: BLE001
                data = {}
        data[str(message_id)] = entry
        if len(data) > TELEGRAM_MESSAGES_CAP:
            for k in list(data.keys())[:-TELEGRAM_MESSAGES_CAP]:
                del data[k]
        tmp = TELEGRAM_MESSAGES_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.rename(TELEGRAM_MESSAGES_FILE)


def _expand(text: str | None, fired_at: datetime, job_name: str) -> str | None:
    if text is None:
        return None
    return text.replace("{system_datetime}", fired_at.strftime("%Y-%m-%d %H:%M:%S UTC")).replace(
        "{job_name}", job_name
    )


def _resolve_workspace(subpath: str) -> str:
    root = Path(os.environ.get("AICODEBOX_WORKSPACE") or "/workspace")
    if subpath in ("", "."):
        target = root
    else:
        p = Path(subpath)
        if p.is_absolute() or ".." in p.parts:
            raise ValueError(f"invalid workspace subpath: {subpath!r}")
        target = (root / p).resolve()
        if not str(target).startswith(str(root.resolve())):
            raise ValueError(f"workspace escapes root: {subpath!r}")
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _previous_run_dir(workspace_slug: str, name: str, current_dir: Path) -> Path | None:
    """Most recent prior run dir for this job in this workspace, or None."""
    parent = HISTORY_RUNS_ROOT / workspace_slug
    if not parent.is_dir():
        return None
    candidates = [p for p in parent.glob(f"*-{name}") if p.is_dir() and p != current_dir]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def _build_history_hint(
    workspace_slug: str,
    name: str,
    current_dir: Path,
) -> str | None:
    prev = _previous_run_dir(workspace_slug, name, current_dir)
    if not prev:
        return None
    pattern = str(HISTORY_RUNS_ROOT / workspace_slug / f"*-{name}")
    return (
        f"Prior runs of this same cron job ({name!r}) are on disk. "
        f"Most recent prior run: {str(prev)!r} (contains meta.json with the "
        f"job parameters + exit_code, stdout.log, stderr.log, result.txt with "
        f"the parsed agent output, and telegram.json if a notification was "
        f"sent). Older runs match the glob {pattern!r} — timestamp-prefixed "
        f"so lexicographic sort = chronological. Read those files if "
        f"comparing against past output is useful for this run; otherwise "
        f"ignore."
    )


def _record_summary(job: CronJob, fired_at: datetime, result: dict[str, Any]) -> Path:
    """Append a one-line summary to ``<job-name>.jsonl`` for cumulative inspection."""
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    record_path = HISTORY_ROOT / f"{job.name}.jsonl"
    entry = {
        "job": job.name,
        "fired_at": fired_at.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "schedule": job.schedule,
        **result,
    }
    with record_path.open("a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return record_path


def _write_meta(meta_path: Path, meta: dict[str, Any]) -> None:
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))


def _notify_telegram(
    job: CronJob,
    fired_at: datetime,
    result_text: str,
    exit_code: int,
    job_dir: Path,
) -> None:
    if not job.telegram_chat_id:
        return
    if not os.environ.get("AICODEBOX_TELEGRAM_BOT_TOKEN"):
        log.warning(
            "[%s] telegram_chat_id set but AICODEBOX_TELEGRAM_BOT_TOKEN missing",
            job.name,
        )
        return

    try:
        from aicodebox.modes.telegram.utils import md_to_tg_html

        body_html = md_to_tg_html(result_text)
    except Exception:  # noqa: BLE001
        body_html = result_text

    header = f"<b>[{job.name}]</b>"
    if result_text.strip():
        html = f"{header}\n{body_html}"
    elif exit_code != 0:
        html = f"{header} job failed (rc={exit_code})"
    else:
        html = f"{header} finished (no output)"

    msg_id = _post_to_telegram(job.telegram_chat_id, html)
    if not msg_id:
        return

    tg_record = {
        "chat_id": job.telegram_chat_id,
        "message_id": msg_id,
    }
    try:
        (job_dir / "telegram.json").write_text(json.dumps(tg_record, indent=2, ensure_ascii=False))
    except OSError as exc:
        log.warning("[%s] failed to write telegram.json: %s", job.name, exc)

    _record_telegram_message(
        msg_id,
        {
            "job_name": job.name,
            "fired_at": fired_at.isoformat(),
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "instruction": (job.instruction or "")[:500],
            "result": (result_text or "")[:2000],
            "chat_id": job.telegram_chat_id,
            "history_dir": str(job_dir),
        },
    )
    log.info(
        "[%s] posted to telegram chat=%s msg=%s",
        job.name,
        job.telegram_chat_id,
        msg_id,
    )


def _run_job(job: CronJob, fired_at: datetime) -> None:
    try:
        workspace = _resolve_workspace(job.workspace)
    except ValueError as exc:
        log.error("[%s] workspace error: %s", job.name, exc)
        _record_summary(job, fired_at, {"error": str(exc), "exit_code": -1})
        return

    workspace_slug = _slugify(workspace)
    ts = fired_at.strftime("%Y%m%d-%H%M%S")
    job_dir = HISTORY_RUNS_ROOT / workspace_slug / f"{ts}-{job.name}"
    try:
        job_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("[%s] failed to create history dir %s: %s", job.name, job_dir, exc)
        _record_summary(job, fired_at, {"error": str(exc), "exit_code": -1})
        return

    instruction = _expand(job.instruction, fired_at, job.name) or ""
    system_prompt = _expand(job.system_prompt, fired_at, job.name)
    append_system_prompt = _expand(job.append_system_prompt, fired_at, job.name)

    history_hint = _build_history_hint(workspace_slug, job.name, job_dir)
    if history_hint:
        append_system_prompt = (
            (append_system_prompt + "\n\n") if append_system_prompt else ""
        ) + history_hint

    started_at = datetime.now(timezone.utc).isoformat()
    meta_path = job_dir / "meta.json"
    meta: dict[str, Any] = {
        "name": job.name,
        "schedule": job.schedule,
        "model": job.model,
        "effort": job.effort,
        "thinking": job.thinking,
        "instruction": instruction,
        "system_prompt": system_prompt,
        "append_system_prompt": append_system_prompt,
        "workspace": workspace,
        "workspace_slug": workspace_slug,
        "started_at": started_at,
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "telegram_chat_id": job.telegram_chat_id,
    }
    _write_meta(meta_path, meta)

    log.info("[%s] firing (workspace=%s history=%s)", job.name, workspace, job_dir)

    spec = RunSpec(
        prompt=instruction,
        workspace=workspace,
        model=job.model,
        system_prompt=system_prompt,
        append_system_prompt=append_system_prompt,
        no_continue=job.no_continue,
        thinking=job.thinking or job.effort,
    )

    error: str | None = None
    result_text = ""
    exit_code = -1
    stderr_text = ""
    stdout_text = ""
    try:
        result = run_agent(spec)
        result_text = (result.text or "").strip()
        exit_code = result.exit_code
        stderr_text = result.raw_stderr or ""
        stdout_text = result.raw_stdout or ""
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        log.exception("[%s] crashed", job.name)

    finished_at = datetime.now(timezone.utc).isoformat()
    meta["finished_at"] = finished_at
    meta["exit_code"] = exit_code
    meta["error"] = error
    _write_meta(meta_path, meta)

    try:
        (job_dir / "stdout.log").write_text(stdout_text)
        (job_dir / "stderr.log").write_text(stderr_text)
        (job_dir / "result.txt").write_text(result_text)
    except OSError as exc:
        log.warning("[%s] failed to write run artefacts: %s", job.name, exc)

    summary: dict[str, Any] = {
        "exit_code": exit_code,
        "text": result_text,
        "stderr": stderr_text,
        "history_dir": str(job_dir),
    }
    if error:
        summary["error"] = error
    _record_summary(job, fired_at, summary)

    if exit_code == 0 and not error:
        log.info("[%s] finished ok", job.name)
    else:
        log.warning(
            "[%s] finished rc=%s err=%s stderr=%r",
            job.name,
            exit_code,
            error,
            stderr_text[:200],
        )

    if result_text or exit_code != 0:
        _notify_telegram(job, fired_at, result_text, exit_code, job_dir)


def _spawn(job: CronJob, fired_at: datetime) -> None:
    with _running_lock:
        existing = _running.get(job.name)
        if existing and existing.is_alive():
            log.warning("[%s] previous run still active — skipping tick", job.name)
            return

        def target() -> None:
            try:
                _run_job(job, fired_at)
            finally:
                with _running_lock:
                    if _running.get(job.name) is threading.current_thread():
                        _running.pop(job.name, None)

        t = threading.Thread(target=target, name=f"job-{job.name}", daemon=True)
        _running[job.name] = t
        t.start()


def _signal(signum: int, _frame: Any) -> None:
    log.info("received signal %d, shutting down", signum)
    _shutdown.set()


def run_loop(cfg: CronConfig) -> None:
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        adapter_name = get_adapter().name
    except RuntimeError:
        adapter_name = "?"
    log.info(
        "cron loop starting: %d job(s), adapter=%s, history=%s",
        len(cfg.jobs),
        adapter_name,
        HISTORY_RUNS_ROOT,
    )
    for j in cfg.jobs:
        log.info("  - %s [%s] workspace=%s", j.name, j.schedule, j.workspace)

    now = datetime.now()
    iters = {j.name: croniter(j.schedule, now, second_at_beginning=True) for j in cfg.jobs}
    next_at = {n: it.get_next(datetime) for n, it in iters.items()}

    while not _shutdown.is_set():
        now = datetime.now()
        for j in cfg.jobs:
            if next_at[j.name] <= now:
                fired_at = next_at[j.name]
                _spawn(j, fired_at)
                next_at[j.name] = iters[j.name].get_next(datetime)
        soonest = min(next_at.values())
        delta = max(0.2, min(2.0, (soonest - datetime.now()).total_seconds()))
        _shutdown.wait(timeout=delta)

    log.info("waiting for in-flight jobs...")
    with _running_lock:
        threads = list(_running.values())
    for t in threads:
        t.join(timeout=30)
    log.info("bye")


def start_in_thread(cfg: CronConfig) -> threading.Thread:
    """Start the cron loop in a daemon thread (used in combined telegram+cron mode)."""
    HISTORY_ROOT.mkdir(parents=True, exist_ok=True)
    HISTORY_RUNS_ROOT.mkdir(parents=True, exist_ok=True)
    t = threading.Thread(target=run_loop, args=(cfg,), name="cron-loop", daemon=True)
    t.start()
    return t


def request_shutdown() -> None:
    _shutdown.set()


def main() -> int:
    configure_logging()
    cron_file = os.environ.get("AICODEBOX_MODE_CRON_FILE")
    if not cron_file:
        log.error("AICODEBOX_MODE_CRON_FILE not set")
        return 1
    try:
        cfg = load(cron_file)
    except CronConfigError as exc:
        log.error("invalid cron file: %s", exc)
        return 1

    try:
        get_adapter()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    signal.signal(signal.SIGTERM, _signal)
    signal.signal(signal.SIGINT, _signal)

    run_loop(cfg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
