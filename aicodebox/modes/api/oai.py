"""OpenAI Chat Completions adapter.

Translates ``POST /openai/v1/chat/completions`` into a prompt run via the
configured agent adapter, then wraps the agent output in OpenAI's response
envelope. Supports:

- single-turn and multi-turn conversations (flattened to a tagged transcript,
  or written to a JSON file under ``/workspace/_oai_uploads`` when multimodal)
- multimodal ``image_url`` content (data URLs decoded; http(s) URLs fetched
  through an SSRF guard) — saved under ``_oai_uploads`` and referenced by
  absolute path in the prompt
- streaming (``stream=true``): runs the adapter via
  ``runner.run_stream`` and forwards each ``StreamEvent`` of type ``delta``
  as its own ``chat.completion.chunk``. Adapters that don't override
  ``parse_stream_event`` get the default line-per-delta behaviour.
- reject ``tools`` / ``tool_choice`` / ``response_format=json_object`` with
  400 so clients fall back to ``/run`` rather than silently losing capability
"""
from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import logging
import mimetypes
import os
import socket
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Union

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict

from aicodebox.adapters import get_adapter
from aicodebox.modes.api.auth import check_bearer
from aicodebox.modes.api.runs import REGISTRY as RUNS
from aicodebox.modes.api.workspace import (
    ROOT_WORKSPACE,
    WorkspaceError,
    resolve as resolve_workspace,
)
from aicodebox.shared.runner import RunSpec, run as run_agent, run_stream

log = logging.getLogger("api.oai")

UPLOAD_DIR = Path(ROOT_WORKSPACE) / "_oai_uploads"
UPLOAD_TTL_SECONDS = 24 * 3600
REMOTE_IMAGE_TIMEOUT = 30
REMOTE_IMAGE_MAX_BYTES = 50 * 1024 * 1024

router = APIRouter(dependencies=[Depends(check_bearer)])


class _OAIMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    role: str
    content: Union[str, list[Any]]


class _OAIRequest(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    model: str = ""
    messages: list[_OAIMessage]
    stream: bool = False
    tools: Any = None
    tool_choice: Any = None
    response_format: dict | None = None
    reasoning_effort: str | None = None


# ── image / SSRF helpers ─────────────────────────────────────────────────────


def _is_safe_remote_url(url: str) -> bool:
    """SSRF guard: reject URLs that resolve to private/loopback/link-local IPs."""
    try:
        parsed = urllib.parse.urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    if not parsed.hostname:
        return False
    try:
        infos = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_multicast or ip.is_reserved or ip.is_unspecified):
            return False
    return True


def _write_upload(raw: bytes, ext: str) -> str:
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"upload_{uuid.uuid4().hex[:12]}{ext}"
    fpath = UPLOAD_DIR / fname
    fpath.write_bytes(raw)
    log.info("saved oai upload: %s (%d bytes)", fname, len(raw))
    return str(fpath)


def _save_data_uri(url: str) -> str | None:
    header, _, b64 = url.partition(",")
    mime = header.split(";")[0].replace("data:", "")
    try:
        raw = base64.b64decode(b64)
    except (ValueError, TypeError):
        log.warning("failed to decode data: URL")
        return None
    ext = mimetypes.guess_extension(mime) or ".bin"
    return _write_upload(raw, ext)


def _fetch_remote_sync(url: str) -> tuple[bytes, str] | None:
    if not _is_safe_remote_url(url):
        log.warning("refusing to fetch image from unsafe URL: %s", url[:200])
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "aicodebox-api"})
        with urllib.request.urlopen(req, timeout=REMOTE_IMAGE_TIMEOUT) as resp:  # noqa: S310
            content_type = resp.headers.get("Content-Type", "application/octet-stream")
            raw = resp.read(REMOTE_IMAGE_MAX_BYTES)
    except Exception:  # noqa: BLE001
        log.warning("failed to download image from %s", url[:200])
        return None
    return raw, content_type.split(";")[0].strip()


async def _save_image(url: str) -> str | None:
    if url.startswith("data:"):
        return _save_data_uri(url)
    if url.startswith(("http://", "https://")):
        result = await asyncio.get_event_loop().run_in_executor(
            None, _fetch_remote_sync, url,
        )
        if not result:
            return None
        raw, mime = result
        ext = (mimetypes.guess_extension(mime)
               or os.path.splitext(urllib.parse.urlparse(url).path)[1]
               or ".bin")
        return _write_upload(raw, ext)
    log.warning("unsupported image URL scheme")
    return None


async def _resolve_content(content: Union[str, list[Any]]) -> Union[str, list[Any]]:
    if isinstance(content, str):
        return content
    resolved: list[Any] = []
    for block in content:
        if isinstance(block, str):
            resolved.append(block)
            continue
        if not isinstance(block, dict):
            resolved.append(block)
            continue
        if block.get("type") == "image_url":
            url = block.get("image_url", {}).get("url", "")
            if not url:
                continue
            saved = await _save_image(url)
            if saved:
                resolved.append({"type": "text", "text": f"[See image: {saved}]"})
            continue
        resolved.append(block)
    return resolved


def _content_text_only(content: Union[str, list[Any]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


def _resolved_to_text(content: Union[str, list[Any]]) -> str:
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
            continue
        if isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))
    return "\n".join(p for p in parts if p)


async def _messages_to_prompt(
    messages: list[_OAIMessage],
) -> tuple[str, str | None]:
    system_parts: list[str] = []
    conv: list[dict[str, Any]] = []
    has_multimodal = False
    for msg in messages:
        if msg.role == "system":
            system_parts.append(_content_text_only(msg.content))
            continue
        resolved = await _resolve_content(msg.content)
        if isinstance(msg.content, list) and any(
            isinstance(b, dict) and b.get("type") == "image_url"
            for b in msg.content
        ):
            has_multimodal = True
        conv.append({"role": msg.role, "content": resolved})

    system_prompt = "\n\n".join(p for p in system_parts if p) or None

    if not conv:
        return "", system_prompt

    if not has_multimodal and len(conv) == 1 and conv[0]["role"] == "user":
        text = _resolved_to_text(conv[0]["content"]).strip()
        if text:
            return text, system_prompt

    if not has_multimodal:
        lines: list[str] = [
            "Continue this conversation. Respond to the last user message."
        ]
        for entry in conv:
            tag = str(entry["role"]).upper()
            text = _resolved_to_text(entry["content"]).strip()
            if not text:
                continue
            lines.append(f"\n[{tag}]\n{text}")
        return "\n".join(lines), system_prompt

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    conv_path = UPLOAD_DIR / f"conv_{uuid.uuid4().hex[:12]}.json"
    conv_path.write_text(json.dumps(conv, indent=2))
    log.info("oai: multi-turn/multimodal (%d msgs), wrote conv to %s",
             len(conv), conv_path)
    prompt = (
        f"Read the conversation in {conv_path}. "
        "It contains a JSON array of messages with roles (user/assistant). "
        "Any file paths in [See image: ...] blocks are absolute paths to "
        "files on disk — read them. "
        "Respond to the last user message in the conversation."
    )
    return prompt, system_prompt


def _strip_provider_prefix(model: str) -> str:
    if "/" in model:
        return model.split("/", 1)[-1]
    return model


def _usage_tokens(usage: dict[str, Any] | None, *keys: str) -> int:
    """Read the first present + truthy key from usage as an int.

    Different adapters surface token counts under different conventions
    (``input_tokens`` / ``inputTokens`` / ``input``). Try each in order;
    return 0 if none match."""
    if not usage:
        return 0
    for key in keys:
        value = usage.get(key)
        if value:
            try:
                return int(value)
            except (TypeError, ValueError):
                continue
    return 0


@router.get("/openai/v1/models")
def models() -> dict[str, Any]:
    from aicodebox.shared.choices import available_models

    # Empty list is rejected at API mode boot — see server.main(). Reaching
    # this code path with an empty list means env got mutated post-boot;
    # surface that as a 503 rather than handing the OAI client a bogus
    # "pi"/adapter-name model that isn't a real model.
    ids = available_models()
    if not ids:
        raise HTTPException(
            status_code=503,
            detail="no models configured; set AICODEBOX_AVAILABLE_MODELS",
        )
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": 0,
                "owned_by": "aicodebox",
            }
            for mid in ids
        ],
    }


@router.post("/openai/v1/chat/completions")
async def chat_completions(
    req: _OAIRequest,
    x_workspace: str | None = Header(default=None, alias="x-aicodebox-workspace"),
    x_continue: str | None = Header(default=None, alias="x-aicodebox-continue"),
    x_append_system_prompt: str | None = Header(
        default=None, alias="x-aicodebox-append-system-prompt",
    ),
    x_claude_workspace: str | None = Header(default=None, alias="x-claude-workspace"),
    x_claude_continue: str | None = Header(default=None, alias="x-claude-continue"),
    x_claude_append_system_prompt: str | None = Header(
        default=None, alias="x-claude-append-system-prompt",
    ),
) -> Any:
    x_workspace = x_workspace or x_claude_workspace
    x_continue = x_continue or x_claude_continue
    x_append_system_prompt = x_append_system_prompt or x_claude_append_system_prompt
    if req.tools or req.tool_choice:
        raise HTTPException(
            status_code=400,
            detail="tools/tool_choice not supported — agent runs its own tools",
        )
    if req.response_format and req.response_format.get("type") == "json_object":
        raise HTTPException(
            status_code=400,
            detail="response_format=json_object not supported — use /run with jsonSchema",
        )

    prompt, system_prompt = await _messages_to_prompt(req.messages)
    if not prompt:
        raise HTTPException(status_code=400, detail="no user message provided")

    try:
        workspace = resolve_workspace(x_workspace)
    except WorkspaceError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    no_continue = x_continue is None or x_continue.lower() not in ("1", "true", "yes")
    requested_model = _strip_provider_prefix(req.model or "")
    # "pi" (or whatever the adapter's synthetic id is) is a placeholder, not a
    # real upstream model — drop it so the adapter uses its configured default.
    if requested_model == get_adapter().name:
        requested_model = ""

    if req.stream:
        return await _stream_response(
            prompt=prompt,
            system_prompt=system_prompt,
            append_system_prompt=x_append_system_prompt,
            workspace=workspace,
            model=requested_model or None,
            no_continue=no_continue,
            thinking=req.reasoning_effort,
            req_model=req.model or get_adapter().name,
        )

    spec = RunSpec(
        prompt=prompt,
        workspace=workspace,
        model=requested_model or None,
        system_prompt=system_prompt,
        append_system_prompt=x_append_system_prompt,
        no_continue=no_continue,
        thinking=req.reasoning_effort,
        output_format="json",
    )

    if not RUNS.acquire_workspace(workspace):
        raise HTTPException(status_code=409, detail="workspace busy, retry later")

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def _do(rid: str) -> tuple[str, dict[str, Any], str | None]:
        def hook(proc: Any) -> None:
            RUNS.register_proc(rid, proc)
        result = run_agent(spec, proc_hook=hook)
        if result.exit_code != 0:
            log.warning(
                "oai chat: agent rc=%s stderr=%r",
                result.exit_code, result.raw_stderr[:200],
            )
        return result.text or "", result.usage or {}, None

    try:
        _, ret, err = await asyncio.get_event_loop().run_in_executor(
            None, lambda: RUNS.run_sync(workspace, _do),
        )
    finally:
        RUNS.release_workspace(workspace)
    if err is not None:
        raise HTTPException(status_code=500, detail=err)

    text, usage, _stop = ret if isinstance(ret, tuple) else (ret, {}, None)
    in_tok = _usage_tokens(usage, "input_tokens", "inputTokens", "input")
    out_tok = _usage_tokens(usage, "output_tokens", "outputTokens", "output")

    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": req.model or get_adapter().name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text or ""},
                "finish_reason": "stop",
            },
        ],
        "usage": {
            "prompt_tokens": in_tok,
            "completion_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        },
    }


# ── streaming (one SSE chunk per adapter StreamEvent) ────────────────────────


def _sse_chunk(
    cid: str, created: int, model: str,
    delta: dict, finish: str | None = None,
) -> str:
    obj = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(obj)}\n\n"


async def _stream_response(
    *,
    prompt: str,
    system_prompt: str | None,
    append_system_prompt: str | None,
    workspace: str,
    model: str | None,
    no_continue: bool,
    thinking: str | None,
    req_model: str,
) -> StreamingResponse:
    if not RUNS.acquire_workspace(workspace):
        raise HTTPException(status_code=409, detail="workspace busy, retry later")

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async def gen() -> AsyncIterator[str]:
        spec = RunSpec(
            prompt=prompt,
            workspace=workspace,
            model=model,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            no_continue=no_continue,
            thinking=thinking,
        )
        finish: str = "stop"
        try:
            yield _sse_chunk(
                cid, created, req_model,
                {"role": "assistant", "content": ""},
            )
            async for event in run_stream(spec):
                if event.type == "delta" and event.text:
                    yield _sse_chunk(
                        cid, created, req_model, {"content": event.text},
                    )
                    continue
                if event.type == "error":
                    finish = "error"
                    log.warning(
                        "oai stream: adapter error: %s", event.text[:200],
                    )
                    yield _sse_chunk(
                        cid, created, req_model,
                        {"content": f"[error: {event.text}]"},
                    )
                    continue
                if event.type == "stop" and event.data:
                    finish = str(event.data.get("reason") or finish)
                    continue
            yield _sse_chunk(cid, created, req_model, {}, finish)
            yield "data: [DONE]\n\n"
        finally:
            RUNS.release_workspace(workspace)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ── housekeeping ─────────────────────────────────────────────────────────────


def purge_stale_uploads(now: float | None = None) -> int:
    if not UPLOAD_DIR.exists():
        return 0
    cutoff = (now or time.time()) - UPLOAD_TTL_SECONDS
    purged = 0
    for entry in UPLOAD_DIR.iterdir():
        try:
            if entry.is_file() and entry.stat().st_mtime < cutoff:
                entry.unlink()
                purged += 1
        except OSError:
            continue
    return purged


__all__ = ["router", "purge_stale_uploads", "UPLOAD_DIR"]
