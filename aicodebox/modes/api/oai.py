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
from aicodebox.shared.runner import (
    RunSpec,
    run as run_agent,
    run_stream,
    run_with_json_retry,
)

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


def _parse_bool_header(value: str | None) -> bool:
    return value is not None and value.strip().lower() in ("1", "true", "yes")


def _parse_int_header(value: str | None, name: str) -> int | None:
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError as exc:
        log.warning(
            "oai: header %s rejected — not an integer: %r", name, value[:80],
        )
        raise HTTPException(
            status_code=400,
            detail=f"{name}: must be an integer, got {value!r}",
        ) from exc


def _parse_dict_header(value: str | None, name: str) -> dict | None:
    if value is None:
        return None
    try:
        obj = json.loads(value)
    except json.JSONDecodeError as exc:
        log.warning(
            "oai: header %s rejected — invalid JSON (len=%d): %s",
            name, len(value), exc,
        )
        raise HTTPException(
            status_code=400,
            detail=f"{name}: invalid JSON: {exc}",
        ) from exc
    if not isinstance(obj, dict):
        log.warning(
            "oai: header %s rejected — expected JSON object, got %s",
            name, type(obj).__name__,
        )
        raise HTTPException(
            status_code=400,
            detail=f"{name}: must be a JSON object",
        )
    return obj


def _schema_from_response_format(rf: dict | None) -> dict | None:
    """Extract a JSON schema from the OpenAI ``response_format`` body field.

    The OpenAI Chat Completions API accepts three response_format shapes:

      {"type": "text"}                    → plain prose (no schema)
      {"type": "json_object"}             → force JSON output (any shape)
      {"type": "json_schema",
       "json_schema": {
           "name": "<label>",
           "schema": {...},
           "strict": <bool>
       }}                                 → schema-constrained JSON

    Returns:
      - None when no schema constraint applies (type=text or rf is None)
      - {"type": "object"} for json_object (permissive — "must be JSON")
      - the inner schema dict for json_schema

    Raises 400 on malformed shapes so the caller can correct.
    """
    if not rf:
        return None
    rf_type = rf.get("type", "text")
    if rf_type == "text":
        return None
    if rf_type == "json_object":
        # OpenAI's "force JSON output" mode — no structural constraint,
        # just "the model must emit parseable JSON". Permissive schema
        # forces the retry helper to validate parseability without
        # rejecting any particular shape.
        return {"type": "object"}
    if rf_type == "json_schema":
        wrapper = rf.get("json_schema")
        if not isinstance(wrapper, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "response_format.json_schema must be an object with "
                    "{name, schema, strict?}"
                ),
            )
        inner = wrapper.get("schema")
        if not isinstance(inner, dict):
            raise HTTPException(
                status_code=400,
                detail=(
                    "response_format.json_schema.schema must be a JSON "
                    "object describing the expected output"
                ),
            )
        return inner
    raise HTTPException(
        status_code=400,
        detail=(
            f"response_format.type={rf_type!r} not recognized; expected "
            "text | json_object | json_schema"
        ),
    )


def _schema_source(rf: dict | None, header_value: str | None) -> str:
    """Label for the entry-log: where the schema constraint (if any)
    came from. ``response_format`` (body) > header > ``none``."""
    if rf:
        rf_type = rf.get("type")
        if rf_type in ("json_object", "json_schema"):
            return f"response_format.{rf_type}"
    if header_value is not None:
        return "x-aicodebox-json-schema"
    return "none"


def _parse_list_header(value: str | None, name: str) -> list[str] | None:
    """Accept either a JSON array or a comma-separated string."""
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    if stripped.startswith("["):
        try:
            arr = json.loads(stripped)
        except json.JSONDecodeError as exc:
            log.warning(
                "oai: header %s rejected — invalid JSON array (len=%d): %s",
                name, len(stripped), exc,
            )
            raise HTTPException(
                status_code=400,
                detail=f"{name}: invalid JSON array: {exc}",
            ) from exc
        if not isinstance(arr, list):
            log.warning(
                "oai: header %s rejected — expected JSON array, got %s",
                name, type(arr).__name__,
            )
            raise HTTPException(
                status_code=400,
                detail=f"{name}: JSON value must be an array",
            )
        return [str(x) for x in arr]
    return [s.strip() for s in stripped.split(",") if s.strip()]


@router.post("/openai/v1/chat/completions")
async def chat_completions(
    req: _OAIRequest,
    x_workspace: str | None = Header(default=None, alias="x-aicodebox-workspace"),
    x_continue: str | None = Header(default=None, alias="x-aicodebox-continue"),
    x_append_system_prompt: str | None = Header(
        default=None, alias="x-aicodebox-append-system-prompt",
    ),
    x_json_schema: str | None = Header(
        default=None, alias="x-aicodebox-json-schema",
    ),
    x_resume: str | None = Header(default=None, alias="x-aicodebox-resume"),
    x_extra_args: str | None = Header(
        default=None, alias="x-aicodebox-extra-args",
    ),
    x_timeout_seconds: str | None = Header(
        default=None, alias="x-aicodebox-timeout-seconds",
    ),
    x_tools_allowlist: str | None = Header(
        default=None, alias="x-aicodebox-tools-allowlist",
    ),
    x_no_tools: str | None = Header(default=None, alias="x-aicodebox-no-tools"),
    x_claude_workspace: str | None = Header(default=None, alias="x-claude-workspace"),
    x_claude_continue: str | None = Header(default=None, alias="x-claude-continue"),
    x_claude_append_system_prompt: str | None = Header(
        default=None, alias="x-claude-append-system-prompt",
    ),
) -> Any:
    x_workspace = x_workspace or x_claude_workspace
    x_continue = x_continue or x_claude_continue
    x_append_system_prompt = x_append_system_prompt or x_claude_append_system_prompt
    log.info(
        "oai chat: request model=%r stream=%s messages=%d "
        "schema_via=%s has_resume=%s no_tools=%s",
        req.model, req.stream, len(req.messages),
        _schema_source(req.response_format, x_json_schema),
        x_resume is not None,
        x_no_tools is not None,
    )
    if req.tools or req.tool_choice:
        raise HTTPException(
            status_code=400,
            detail="tools/tool_choice not supported — agent runs its own tools",
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

    header_schema = _parse_dict_header(
        x_json_schema, "x-aicodebox-json-schema",
    )
    body_schema = _schema_from_response_format(req.response_format)
    extra_args = _parse_list_header(x_extra_args, "x-aicodebox-extra-args") or []
    timeout_seconds = _parse_int_header(
        x_timeout_seconds, "x-aicodebox-timeout-seconds",
    )
    tools_allowlist = _parse_list_header(
        x_tools_allowlist, "x-aicodebox-tools-allowlist",
    )
    no_tools = _parse_bool_header(x_no_tools)

    # Body's response_format is the OpenAI standard — it wins over the
    # x-aicodebox-json-schema header (the header was our pre-standard
    # ergonomic alternative; standard SDKs ship the body field). The
    # header stays as a fallback for clients that can't set the body
    # field cleanly.
    if body_schema is not None and header_schema is not None:
        log.info(
            "oai chat: both response_format body field and "
            "x-aicodebox-json-schema header set — body wins (OAI standard)",
        )
    json_schema = body_schema if body_schema is not None else header_schema

    # Schema-driven runs need the adapter's verbose event stream so the final
    # assistant turn can be schema-validated (mirrors /run's _derive_output_format).
    schema_output_format = "json-verbose" if json_schema is not None else None

    # Streaming + schema is incompatible: schema validation requires the
    # complete response. We can't validate while still emitting deltas, and
    # mid-stream parse failure has no clean way to recover via the SSE wire.
    if req.stream and json_schema is not None:
        raise HTTPException(
            status_code=400,
            detail=(
                "schema-validated responses (x-aicodebox-json-schema header "
                "or response_format=json_schema / json_object) are "
                "incompatible with stream=true; set stream=false"
            ),
        )

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
            json_schema=json_schema,
            resume=x_resume,
            extra_args=extra_args,
            timeout_seconds=timeout_seconds,
            tools_allowlist=tools_allowlist,
            no_tools=no_tools,
            output_format=schema_output_format,
        )

    spec = RunSpec(
        prompt=prompt,
        workspace=workspace,
        model=requested_model or None,
        system_prompt=system_prompt,
        append_system_prompt=x_append_system_prompt,
        no_continue=no_continue,
        thinking=req.reasoning_effort,
        output_format=schema_output_format or "json",
        json_schema=json_schema,
        resume=x_resume,
        extra_args=extra_args,
        timeout_seconds=timeout_seconds,
        tools_allowlist=tools_allowlist,
        no_tools=no_tools,
    )

    if not RUNS.acquire_workspace(workspace):
        raise HTTPException(status_code=409, detail="workspace busy, retry later")

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    def _do(
        rid: str,
    ) -> tuple[
        str,
        dict[str, Any],
        list[dict[str, Any]] | None,
        int | None,
        str | None,
    ]:
        """Returns
        ``(content, usage, attempts, error_status, error_detail)``.

        ``attempts`` is the per-attempt breakdown from
        ``run_with_json_retry`` (None outside schema mode). ``usage`` is
        the SUM across all attempts.

        ``error_status`` is the HTTP code the route should raise (``None``
        on success). Splitting "agent crashed" from "schema validation
        failed" lets the route surface them as 500 vs 422 respectively —
        same wire shape, different semantics.
        """
        def hook(proc: Any) -> None:
            RUNS.register_proc(rid, proc)
        # Schema runs go through the retry helper — same self-correction
        # path /run uses (up to 3 re-prompts on parse / validation failure).
        # On success the OAI envelope's ``content`` carries the canonical
        # JSON re-serialized (no fences, no surrounding prose) so the
        # caller sees structurally clean output regardless of how the LLM
        # originally formatted it.
        if json_schema is not None:
            result, parsed, parse_error, retries = run_with_json_retry(
                spec, proc_hook=hook,
            )
            if result.exit_code != 0:
                log.warning(
                    "oai chat (schema): agent rc=%s stderr=%r",
                    result.exit_code, result.raw_stderr[:200],
                )
                return (
                    result.text or "",
                    result.usage or {},
                    result.attempts,
                    500,
                    f"agent exited with code {result.exit_code}: "
                    f"{result.raw_stderr[:200]}",
                )
            if parse_error is not None:
                log.warning(
                    "oai chat (schema): %d retries exhausted, error=%s",
                    retries, parse_error,
                )
                return (
                    result.text or "",
                    result.usage or {},
                    result.attempts,
                    422,
                    f"json_schema validation failed after {retries} "
                    f"retries: {parse_error}",
                )
            content = json.dumps(parsed)
            log.info(
                "oai chat (schema): success retries=%d attempts=%d "
                "total_usage=%s content_len=%d",
                retries,
                len(result.attempts) if result.attempts else 1,
                result.usage or None,
                len(content),
            )
            return content, result.usage or {}, result.attempts, None, None

        result = run_agent(spec, proc_hook=hook)
        if result.exit_code != 0:
            log.warning(
                "oai chat: agent rc=%s stderr=%r",
                result.exit_code, result.raw_stderr[:200],
            )
        else:
            log.info(
                "oai chat: success text_len=%d usage=%s",
                len(result.text or ""), result.usage or None,
            )
        return result.text or "", result.usage or {}, None, None, None

    try:
        _, ret, err = await asyncio.get_event_loop().run_in_executor(
            None, lambda: RUNS.run_sync(workspace, _do),
        )
    finally:
        RUNS.release_workspace(workspace)
    if err is not None:
        raise HTTPException(status_code=500, detail=err)

    text, usage, attempts, error_status, error_detail = ret
    if error_status is not None:
        raise HTTPException(status_code=error_status, detail=error_detail)
    in_tok = _usage_tokens(usage, "input_tokens", "inputTokens", "input")
    out_tok = _usage_tokens(usage, "output_tokens", "outputTokens", "output")

    envelope: dict[str, Any] = {
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
    # Per-attempt breakdown — only present when schema mode actually
    # ran the retry helper. Holds an array of {index, usage, exitCode,
    # parseError} entries so clients can bill per-attempt and debug
    # which retry failed which way. Lives under an ``aicodebox_*`` key
    # because the OAI schema has no slot for vendor extension data;
    # OAI-only clients ignore unknown fields cleanly.
    if attempts:
        envelope["aicodebox_attempts"] = attempts
    return envelope


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
    json_schema: dict | None = None,
    resume: str | None = None,
    extra_args: list[str] | None = None,
    timeout_seconds: int | None = None,
    tools_allowlist: list[str] | None = None,
    no_tools: bool = False,
    output_format: str | None = None,
) -> StreamingResponse:
    if not RUNS.acquire_workspace(workspace):
        raise HTTPException(status_code=409, detail="workspace busy, retry later")

    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    created = int(time.time())

    async def gen() -> AsyncIterator[str]:
        spec_kwargs: dict[str, Any] = dict(
            prompt=prompt,
            workspace=workspace,
            model=model,
            system_prompt=system_prompt,
            append_system_prompt=append_system_prompt,
            no_continue=no_continue,
            thinking=thinking,
            json_schema=json_schema,
            resume=resume,
            extra_args=list(extra_args or []),
            timeout_seconds=timeout_seconds,
            tools_allowlist=tools_allowlist,
            no_tools=no_tools,
        )
        if output_format is not None:
            spec_kwargs["output_format"] = output_format
        spec = RunSpec(**spec_kwargs)
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
