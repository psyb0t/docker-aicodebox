"""File operations scoped to the API workspace root.

  GET    /files            → list workspace root
  GET    /files/{path}     → list dir or stream file
  PUT    /files/{path}     → write raw body to file (creates parents)
  DELETE /files/{path}     → remove file (refuses directories)
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Request
from fastapi.responses import FileResponse

from aicodebox.modes.api.auth import check_bearer
from aicodebox.modes.api.workspace import ROOT_WORKSPACE

log = logging.getLogger("api.files")

router = APIRouter(dependencies=[Depends(check_bearer)])


def _resolve(path: str) -> str:
    """Resolve a user-supplied subpath against ROOT_WORKSPACE. Rejects traversal."""
    root = Path(ROOT_WORKSPACE).resolve()
    root.mkdir(parents=True, exist_ok=True)
    cleaned = (path or "").lstrip("/")
    candidate = (root / cleaned).resolve() if cleaned else root
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="path escapes workspace root") from exc
    return str(candidate)


def _listdir_payload(full: str, requested: str) -> dict[str, Any]:
    entries = []
    for name in sorted(os.listdir(full)):
        entry_path = os.path.join(full, name)
        is_dir = os.path.isdir(entry_path)
        entry: dict[str, Any] = {
            "name": name,
            "type": "dir" if is_dir else "file",
        }
        if not is_dir:
            try:
                entry["size"] = os.path.getsize(entry_path)
            except OSError:
                entry["size"] = 0
        entries.append(entry)
    return {"path": requested or "/", "entries": entries}


@router.get("/files")
def list_root() -> dict[str, Any]:
    full = _resolve("")
    return _listdir_payload(full, "")


@router.get("/files/{path:path}")
def get_files(path: str = PathParam(...)) -> Any:
    full = _resolve(path)
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail=f"not found: {path}")
    if os.path.isdir(full):
        return _listdir_payload(full, path)
    return FileResponse(full)


@router.put("/files/{path:path}")
async def put_files(request: Request, path: str = PathParam(...)) -> dict[str, Any]:
    if not path:
        raise HTTPException(status_code=400, detail="path required")
    full = _resolve(path)
    if os.path.isdir(full):
        raise HTTPException(status_code=400, detail="path is a directory")
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    body = await request.body()
    with open(full, "wb") as f:
        f.write(body)
    log.info("put_files path=%s bytes=%d", path, len(body))
    return {"status": "ok", "path": path, "size": len(body)}


@router.delete("/files/{path:path}")
def delete_files(path: str = PathParam(...)) -> dict[str, Any]:
    full = _resolve(path)
    if not os.path.exists(full):
        raise HTTPException(status_code=404, detail=f"not found: {path}")
    if os.path.isdir(full):
        raise HTTPException(status_code=400, detail="cannot delete directories")
    os.remove(full)
    log.info("delete_files path=%s", path)
    return {"status": "ok", "path": path}
