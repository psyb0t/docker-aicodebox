"""In-memory async run registry.

Tracks runs by uuid: pending → running → completed | failed | cancelled.
Completed/failed/cancelled results are purged on first read. Stale entries
(any status, older than RESULT_TTL_SECONDS) are purged by a background task.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

RESULT_TTL_SECONDS = 6 * 3600  # 6 hours
MAX_WORKERS = 8

log = logging.getLogger("runs")


@dataclass
class RunEntry:
    run_id: str
    workspace: str
    status: str = "running"  # running | completed | failed | cancelled
    result: Any = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None


class RunRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, RunEntry] = {}
        self._busy: set[str] = set()  # workspace paths in-flight
        self._procs: dict[str, subprocess.Popen] = {}
        self._executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

    # ── proc tracking for cancellation ───────────────────────────────────
    def register_proc(self, run_id: str, proc: subprocess.Popen) -> None:
        with self._lock:
            entry = self._runs.get(run_id)
            if entry and entry.status == "cancelled":
                # cancelled before subprocess actually launched — kill immediately
                proc.kill()
                return
            self._procs[run_id] = proc

    def unregister_proc(self, run_id: str) -> None:
        with self._lock:
            self._procs.pop(run_id, None)

    def cancel(self, run_id: str) -> bool:
        """Mark a run as cancelled and kill its subprocess if running. Returns False on unknown id."""
        proc: subprocess.Popen | None = None
        with self._lock:
            entry = self._runs.get(run_id)
            if not entry:
                return False
            if entry.status != "running":
                # already finalized — nothing to kill, but report success
                return True
            entry.status = "cancelled"
            entry.finished_at = time.time()
            proc = self._procs.pop(run_id, None)
        if proc and proc.poll() is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        return True

    # ── busy-workspace guard ─────────────────────────────────────────────
    def acquire_workspace(self, workspace: str) -> bool:
        with self._lock:
            if workspace in self._busy:
                return False
            self._busy.add(workspace)
            return True

    def release_workspace(self, workspace: str) -> None:
        with self._lock:
            self._busy.discard(workspace)

    # ── synchronous run ──────────────────────────────────────────────────
    def run_sync(
        self, workspace: str, fn: Callable[[str], Any]
    ) -> tuple[str, Any, str | None]:
        run_id = uuid.uuid4().hex
        entry = RunEntry(run_id=run_id, workspace=workspace)
        with self._lock:
            self._runs[run_id] = entry
        err: str | None = None
        result: Any = None
        try:
            result = fn(run_id)
            if entry.status != "cancelled":
                entry.status = "completed"
                entry.result = result
        except Exception as exc:  # noqa: BLE001
            if entry.status != "cancelled":
                entry.status = "failed"
                entry.error = str(exc)
            err = str(exc)
            log.exception("run %s failed", run_id)
        finally:
            entry.finished_at = time.time()
            self.unregister_proc(run_id)
        return run_id, result, err

    # ── async run ────────────────────────────────────────────────────────
    def submit_async(self, workspace: str, fn: Callable[[str], Any]) -> str:
        run_id = uuid.uuid4().hex
        entry = RunEntry(run_id=run_id, workspace=workspace)
        with self._lock:
            self._runs[run_id] = entry

        def _wrap() -> None:
            try:
                result = fn(run_id)
                if entry.status != "cancelled":
                    entry.result = result
                    entry.status = "completed"
            except Exception as exc:  # noqa: BLE001
                if entry.status != "cancelled":
                    entry.status = "failed"
                    entry.error = str(exc)
                log.exception("async run %s failed", run_id)
            finally:
                entry.finished_at = time.time()
                self.unregister_proc(run_id)
                self.release_workspace(workspace)

        self._executor.submit(_wrap)
        return run_id

    # ── poll / read once ─────────────────────────────────────────────────
    def get(self, run_id: str) -> RunEntry | None:
        with self._lock:
            entry = self._runs.get(run_id)
            if not entry:
                return None
            # If running, return without purging. If finalized, purge on read.
            if entry.status != "running":
                self._runs.pop(run_id, None)
            return entry

    # ── introspection (used by /status) ───────────────────────────────────
    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            runs = [
                {"runId": e.run_id, "workspace": e.workspace, "status": e.status}
                for e in self._runs.values()
            ]
            return {"busyWorkspaces": sorted(self._busy), "runs": runs}

    # ── housekeeping ─────────────────────────────────────────────────────
    def purge_stale(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - RESULT_TTL_SECONDS
        purged = 0
        with self._lock:
            for rid, entry in list(self._runs.items()):
                ts = entry.finished_at or entry.created_at
                if ts < cutoff:
                    self._runs.pop(rid, None)
                    purged += 1
        return purged


# module-level singleton
REGISTRY = RunRegistry()
