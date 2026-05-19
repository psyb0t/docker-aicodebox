"""Workspace path resolution for API mode.

Requests pass a `workspace` field that is a subpath under ROOT_WORKSPACE.
Empty / unset → ROOT_WORKSPACE itself. Path traversal (`..`) is rejected.
"""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_ROOT = os.environ.get("AICODEBOX_WORKSPACE") or os.environ.get("AICODE_WORKSPACE") or "/workspace"
ROOT_WORKSPACE = _DEFAULT_ROOT


class WorkspaceError(ValueError):
    pass


def resolve(subpath: str | None) -> str:
    """Return absolute path under ROOT_WORKSPACE. Creates the dir if missing."""
    root = Path(ROOT_WORKSPACE)
    root.mkdir(parents=True, exist_ok=True)
    if not subpath:
        return str(root)
    # reject absolute paths and traversal
    p = Path(subpath)
    if p.is_absolute() or ".." in p.parts:
        raise WorkspaceError(f"invalid workspace subpath: {subpath!r}")
    target = (root / p).resolve()
    if not str(target).startswith(str(root.resolve())):
        raise WorkspaceError(f"workspace escapes root: {subpath!r}")
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


__all__ = ["ROOT_WORKSPACE", "WorkspaceError", "resolve"]
# os imported for future use (mode bits, symlink checks)
_ = os
