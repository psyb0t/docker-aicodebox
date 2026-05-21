"""Telegram bot — drives the configured agent from chat messages.

Features
--------
- Text prompts → adapter run, output sent back chunked + Markdown→HTML converted.
- File uploads (document/photo/video/voice) → dropped into the chat's workspace.
- ``[SEND_FILE: relative/path]`` tags in agent output deliver workspace files
  back to the chat (photo / video / document based on MIME).
- Per-chat overrides for model / effort / system_prompt / append_system_prompt
  persisted to disk via ``aicodebox.modes.telegram.overrides``.
- Inline-keyboard pickers for /model and /effort populated from the adapter's
  ``available_models`` / ``available_thinking_levels`` class attrs.
- /cancel kills the in-flight subprocess for the chat.
- /reload re-reads the yaml config.
- /config dumps the merged chat config.
- /fetch <path> downloads a file from the workspace.
- /status lists busy chats.
- Replies to cron-triggered messages inject the job's instruction + result
  back into the prompt so follow-ups make sense; replies to ordinary
  messages inject the quoted text + kind so the agent has context.
"""

from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any, Callable

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
from telegram.constants import ChatAction
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from aicodebox.adapters import get_adapter
from aicodebox.modes.telegram import overrides as _overrides
from aicodebox.modes.telegram.config import TelegramConfigError, get_chat_config, is_allowed
from aicodebox.modes.telegram.config import load as load_config
from aicodebox.modes.telegram.cron_inbox import load_cron_message
from aicodebox.modes.telegram.utils import BOT_TOKEN, TELEGRAM_HTML_HINT, md_to_tg_html, send_long
from aicodebox.shared.logging import configure_logging
from aicodebox.shared.runner import RunSpec
from aicodebox.shared.runner import run as run_agent

log = logging.getLogger("telegram.bot")

ROOT_WORKSPACE = os.environ.get("AICODEBOX_WORKSPACE") or "/workspace"
MAX_FILE_BYTES = 50_000_000
_FILE_TAG_RE = re.compile(r"\[SEND_FILE:\s*(.+?)\]")

# Authoritative formatting + file-attachment hint appended to every prompt
# so the agent knows how to format for Telegram and how to ship files back.
TELEGRAM_SYSTEM_HINT = (
    TELEGRAM_HTML_HINT + "\n\nFile attachments: when the user asks you to send/share a file, "
    "image, or video, include [SEND_FILE: relative/path] anywhere in your "
    "response and it will be delivered as a Telegram attachment (image as "
    "photo, video as video, otherwise as document). Multiple tags are "
    "allowed. The tag itself is stripped from the visible message before "
    "delivery."
)


@dataclass
class _ChatState:
    """Per-chat run state — held in ``busy_chats`` while a run is in flight."""

    popen: subprocess.Popen | None = None
    cancelled: bool = False


config: dict[str, Any] = {}
chat_overrides: dict[int, dict[str, Any]] = {}
busy_chats: dict[int, _ChatState] = {}
_executor = ThreadPoolExecutor(max_workers=4)


# ── helpers ───────────────────────────────────────────────────────────────────


def _available_models() -> list[str]:
    env = os.environ.get("AICODEBOX_AVAILABLE_MODELS")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    try:
        return list(get_adapter().available_models)
    except RuntimeError:
        return []


def _available_efforts() -> list[str]:
    env = os.environ.get("AICODEBOX_AVAILABLE_EFFORTS")
    if env:
        return [m.strip() for m in env.split(",") if m.strip()]
    try:
        return list(get_adapter().available_thinking_levels)
    except RuntimeError:
        return []


def _merged_chat_config(chat_id: int) -> dict[str, Any]:
    overrides_for_chat = _overrides.get_chat_overrides(chat_overrides, chat_id)
    return get_chat_config(config, chat_id, overrides=overrides_for_chat)


def resolve_workspace(chat_cfg: dict[str, Any]) -> str:
    """Resolve and create the chat's workspace dir, refusing to escape ROOT."""
    sub = str(chat_cfg.get("workspace") or ".").lstrip("/")
    root = Path(ROOT_WORKSPACE).resolve()
    if sub in ("", "."):
        target = root
    else:
        target = (root / sub).resolve()
        if not (target == root or root in target.parents):
            raise ValueError(f"workspace path escapes root: {sub!r}")
    target.mkdir(parents=True, exist_ok=True)
    return str(target)


def _build_append_system_prompt(chat_cfg: dict[str, Any]) -> str:
    parts = [TELEGRAM_SYSTEM_HINT]
    user_append = chat_cfg.get("append_system_prompt")
    if user_append:
        parts.append(str(user_append))
    return "\n\n".join(parts)


def _is_image(path: str) -> bool:
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("image/")


def _is_video(path: str) -> bool:
    mime = mimetypes.guess_type(path)[0] or ""
    return mime.startswith("video/")


async def _typing_loop(bot, chat_id: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        except Exception:  # noqa: BLE001
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=4)
            return
        except asyncio.TimeoutError:
            pass


async def _send_file(bot, chat_id: int, path: str) -> None:
    """Send a single file to the chat as photo / video / document.

    Refuses missing, empty, or >50MB files. Logs and reports failures."""
    if not os.path.isfile(path):
        await bot.send_message(chat_id=chat_id, text=f"not found: {os.path.basename(path)}")
        return
    size = os.path.getsize(path)
    if size == 0:
        await bot.send_message(chat_id=chat_id, text=f"file is empty: {os.path.basename(path)}")
        return
    if size > MAX_FILE_BYTES:
        await bot.send_message(
            chat_id=chat_id,
            text=f"file too large ({size} bytes): {os.path.basename(path)}",
        )
        return
    name = os.path.basename(path)
    log.info("sending file %s (%d bytes) to chat %s", name, size, chat_id)
    try:
        with open(path, "rb") as f:
            inp = InputFile(f, filename=name)
            if _is_image(path):
                await bot.send_photo(chat_id=chat_id, photo=inp)
                return
            if _is_video(path):
                await bot.send_video(chat_id=chat_id, video=inp)
                return
            await bot.send_document(chat_id=chat_id, document=inp)
    except Exception as exc:  # noqa: BLE001
        log.exception("failed to send file %s", name)
        await bot.send_message(chat_id=chat_id, text=f"failed to send {name}: {exc}")


async def _extract_and_send_files(bot, chat_id: int, text: str, workspace: str) -> str:
    """Find [SEND_FILE: path] tags, send those files, strip tags from text.

    Paths are resolved relative to the chat's workspace and refused if they
    resolve outside the configured ``ROOT_WORKSPACE``."""
    root_real = os.path.realpath(ROOT_WORKSPACE)
    for rel_path in _FILE_TAG_RE.findall(text):
        full = os.path.realpath(os.path.join(workspace, rel_path.strip()))
        if not (full == root_real or full.startswith(root_real + os.sep)):
            log.warning("chat %s refused [SEND_FILE: %s] — outside root", chat_id, rel_path)
            continue
        await _send_file(bot, chat_id, full)
    return _FILE_TAG_RE.sub("", text).strip()


async def _post_response(bot, chat_id: int, text: str, workspace: str) -> None:
    """Strip SEND_FILE tags + send their attachments, then post the remaining text."""
    text = await _extract_and_send_files(bot, chat_id, text, workspace)
    if text:
        await send_long(bot, chat_id, md_to_tg_html(text), parse_mode="HTML")


# ── core run path ─────────────────────────────────────────────────────────────


async def _run_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prompt: str,
    no_continue: bool = False,
) -> None:
    chat = update.effective_chat
    msg = update.effective_message
    if not chat or not msg:
        return
    chat_id = chat.id

    if chat_id in busy_chats:
        await msg.reply_text("busy, try again later")
        return

    chat_cfg = _merged_chat_config(chat_id)
    try:
        workspace = resolve_workspace(chat_cfg)
    except ValueError as exc:
        await msg.reply_text(f"workspace error: {exc}")
        return

    state = _ChatState()
    busy_chats[chat_id] = state
    stop_typing = asyncio.Event()
    typing_task = asyncio.create_task(_typing_loop(context.bot, chat_id, stop_typing))

    try:
        try:
            adapter_name = get_adapter().name
        except RuntimeError:
            adapter_name = "?"
        log.info(
            "chat %s prompt (%d chars) workspace=%s adapter=%s",
            chat_id,
            len(prompt),
            workspace,
            adapter_name,
        )

        spec = RunSpec(
            prompt=prompt,
            workspace=workspace,
            model=chat_cfg.get("model"),
            system_prompt=chat_cfg.get("system_prompt"),
            append_system_prompt=_build_append_system_prompt(chat_cfg),
            no_continue=no_continue or not bool(chat_cfg.get("continue", True)),
            thinking=chat_cfg.get("thinking") or chat_cfg.get("effort"),
        )

        def _capture_proc(p: subprocess.Popen) -> None:
            state.popen = p

        loop = asyncio.get_running_loop()
        runner = partial(run_agent, spec, proc_hook=_capture_proc)
        result = await loop.run_in_executor(_executor, runner)

        if state.cancelled:
            log.info("chat %s cancelled, dropping output", chat_id)
            return

        output = (result.text or "").strip()
        if not output:
            output = f"(agent exited with code {result.exit_code} and no output)"
        log.info(
            "chat %s agent done exit=%s output=%d chars",
            chat_id,
            result.exit_code,
            len(output),
        )
        await _post_response(context.bot, chat_id, output, workspace)
    except Exception as exc:  # noqa: BLE001
        log.exception("chat %s agent error", chat_id)
        if not state.cancelled:
            await msg.reply_text(f"error: {exc}")
    finally:
        stop_typing.set()
        try:
            await typing_task
        except Exception:  # noqa: BLE001
            pass
        busy_chats.pop(chat_id, None)


# ── reply context builders ────────────────────────────────────────────────────


def _build_cron_reply_prompt(cron_entry: dict[str, Any], user_text: str) -> str:
    history_dir = cron_entry.get("history_dir")
    history_block = ""
    if history_dir:
        history_block = (
            f"Full run history is on disk at {history_dir!r}. That directory "
            f"contains meta.json (job metadata), stdout.log, stderr.log, "
            f"result.txt, and telegram.json (chat_id + message_ids of the "
            f"notification). Read those files if you need more context than "
            f"the truncated summary below.\n"
        )
    return (
        f"[Replying to cron job <b>{cron_entry['job_name']}</b> "
        f"that ran at {cron_entry['fired_at']}]\n"
        f"{history_block}"
        f"Job instruction (truncated): {cron_entry.get('instruction', '')}\n"
        f"Job result (truncated): {cron_entry.get('result', '')}\n\n"
        f"User follow-up: {user_text}"
    )


def _describe_reply_kind(replied) -> str:
    bits: list[str] = []
    if replied.photo:
        bits.append("photo")
    if replied.video:
        bits.append("video")
    if replied.document:
        bits.append(f"document ({replied.document.file_name or 'unnamed'})")
    if replied.sticker:
        bits.append(
            f"sticker ({replied.sticker.emoji or ''} from set {replied.sticker.set_name or '?'})"
        )
    if replied.voice:
        bits.append("voice message")
    if replied.audio:
        bits.append("audio")
    if replied.animation:
        bits.append("animation/gif")
    if bits:
        return ", ".join(bits)
    text = replied.text or replied.caption or ""
    return "text" if text else "non-text"


def _build_generic_reply_prompt(replied, user_text: str) -> str:
    quoted = replied.text or replied.caption or ""
    author = (
        "the bot (you)" if replied.from_user and replied.from_user.is_bot else "the user themselves"
    )
    kind = _describe_reply_kind(replied)
    quoted_block = (
        f"Quoted text:\n{quoted}\n" if quoted else "(no text content in the quoted message)\n"
    )
    return (
        f"[The user is replying to an earlier message (id={replied.message_id}) from {author}]\n"
        f"Quoted message kind: {kind}\n"
        f"{quoted_block}\n"
        f"User follow-up: {user_text}"
    )


# ── message handlers ──────────────────────────────────────────────────────────


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return
    text = msg.text
    if not text:
        return
    if not is_allowed(config, chat.id, user.id):
        log.info("ignoring message from chat=%s user=%s (not allowed)", chat.id, user.id)
        return

    prompt = text
    no_continue = False
    if msg.reply_to_message:
        replied = msg.reply_to_message
        cron_entry = load_cron_message(replied.message_id)
        if cron_entry:
            prompt = _build_cron_reply_prompt(cron_entry, text)
            no_continue = True
            log.info(
                "chat %s reply to cron job %s history_dir=%s (no-continue)",
                chat.id,
                cron_entry["job_name"],
                cron_entry.get("history_dir"),
            )
        else:
            prompt = _build_generic_reply_prompt(replied, text)
            log.info(
                "chat %s reply to message %s kind=%s",
                chat.id,
                replied.message_id,
                _describe_reply_kind(replied),
            )

    await _run_prompt(update, context, prompt, no_continue=no_continue)


async def _handle_file_upload(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    tg_file_obj,
    file_name: str,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg:
        return
    if not is_allowed(config, chat.id, user.id):
        return

    chat_cfg = _merged_chat_config(chat.id)
    try:
        workspace = resolve_workspace(chat_cfg)
    except ValueError as exc:
        await msg.reply_text(f"workspace error: {exc}")
        return

    tg_file = await tg_file_obj.get_file()
    safe_name = os.path.basename(file_name)
    dest = os.path.join(workspace, safe_name)
    await tg_file.download_to_drive(dest)
    log.info("chat %s uploaded %s to %s", chat.id, safe_name, dest)

    caption = (msg.caption or "").strip()
    if not caption:
        await msg.reply_text(f"saved {safe_name}")
        return

    prompt = f"I saved a file '{safe_name}' to the workspace. {caption}"
    await _run_prompt(update, context, prompt)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.document:
        return
    doc = msg.document
    file_name = doc.file_name or f"file_{doc.file_unique_id}"
    await _handle_file_upload(update, context, doc, file_name)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.photo:
        return
    photo = msg.photo[-1]  # largest size
    file_name = f"photo_{photo.file_unique_id}.jpg"
    await _handle_file_upload(update, context, photo, file_name)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.video:
        return
    video = msg.video
    file_name = video.file_name or f"video_{video.file_unique_id}.mp4"
    await _handle_file_upload(update, context, video, file_name)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg or not msg.voice:
        return
    voice = msg.voice
    file_name = f"voice_{voice.file_unique_id}.ogg"
    await _handle_file_upload(update, context, voice, file_name)


# ── command handlers ──────────────────────────────────────────────────────────


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    try:
        adapter_name = get_adapter().name
    except RuntimeError:
        adapter_name = "?"
    await msg.reply_text(f"aicodebox bot ready (agent: {adapter_name})")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = update.effective_message
    if not msg:
        return
    await msg.reply_text(
        "send a message and the bot will run it through the configured agent.\n"
        "commands: /model /effort /system_prompt /append_system_prompt /fetch "
        "/cancel /status /config /reload /start /help"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    if not busy_chats:
        await msg.reply_text("all clear")
        return
    lines = ["busy chats:"] + [f"  {cid}" for cid in busy_chats]
    await msg.reply_text("\n".join(lines))


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    state = busy_chats.get(chat.id)
    if not state:
        await msg.reply_text("nothing running")
        return
    state.cancelled = True
    if state.popen is not None and state.popen.poll() is None:
        try:
            state.popen.kill()
        except OSError as exc:
            log.warning("chat %s /cancel kill failed: %s", chat.id, exc)
    log.info("chat %s /cancel issued", chat.id)
    await msg.reply_text("cancelled")


async def cmd_reload(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    global config
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    try:
        config = load_config()
    except TelegramConfigError as exc:
        await msg.reply_text(f"reload failed: {exc}")
        return
    log.info("chat %s /reload config reloaded", chat.id)
    await msg.reply_text("config reloaded")


async def cmd_config(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    chat_cfg = _merged_chat_config(chat.id)
    lines = [f"chat {chat.id}:"]
    for k, v in sorted(chat_cfg.items()):
        lines.append(f"  {k}: {v}")
    await msg.reply_text("\n".join(lines))


def _resolved_value(chat_id: int, key: str, default: str = "default") -> str:
    val = _merged_chat_config(chat_id).get(key)
    return str(val) if val else default


async def _send_choice_keyboard(
    msg, chat_id: int, key: str, label: str, options: list[str]
) -> None:
    if not options:
        await msg.reply_text(
            f"no preset {label}s available — pass one explicitly with /{key} <value>"
        )
        return
    current = _resolved_value(chat_id, key)
    overridden_val = _overrides.get_chat_overrides(chat_overrides, chat_id).get(key)
    buttons = [
        [
            InlineKeyboardButton(
                ("✓ " if overridden_val == opt else "") + opt,
                callback_data=f"{key}:{opt}",
            )
        ]
        for opt in options
    ]
    buttons.append(
        [InlineKeyboardButton("reset to yaml default", callback_data=f"{key}:__reset__")]
    )
    suffix = " (overridden)" if overridden_val is not None else " (from yaml)"
    await msg.reply_text(
        f"current {label}: <b>{current}</b>{suffix}\nselect a new one:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="HTML",
    )


async def _cmd_choice(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    label: str,
    options: list[str],
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    if context.args:
        choice = context.args[0].strip().lower()
        try:
            _overrides.apply_choice(chat_overrides, chat.id, key, choice, options)
        except ValueError as exc:
            await msg.reply_text(str(exc))
            return
        await msg.reply_text(f"{label}: {_resolved_value(chat.id, key)}")
        return
    await _send_choice_keyboard(msg, chat.id, key, label, options)


async def cmd_model(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_choice(update, context, "model", "model", _available_models())


async def cmd_effort(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_choice(update, context, "effort", "effort", _available_efforts())


async def _cmd_text_override(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    key: str,
    label: str,
) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return

    text = (msg.text or "").partition(" ")[2].strip()
    if not text:
        current = _overrides.get_chat_overrides(chat_overrides, chat.id).get(key)
        yaml_val = _merged_chat_config(chat.id).get(key)
        if current is not None:
            await msg.reply_text(
                f"<b>{label}</b> override (chat {chat.id}):\n<pre>{current}</pre>\n"
                f"to clear: <code>/{key} reset</code>",
                parse_mode="HTML",
            )
            return
        if yaml_val:
            await msg.reply_text(
                f"<b>{label}</b> from yaml:\n<pre>{yaml_val}</pre>\n"
                f"override with: <code>/{key} &lt;text&gt;</code>",
                parse_mode="HTML",
            )
            return
        await msg.reply_text(
            f"no {label} set. usage: <code>/{key} &lt;text&gt;</code>",
            parse_mode="HTML",
        )
        return

    if text.lower() in _overrides.RESET_TOKENS:
        _overrides.clear_value(chat_overrides, chat.id, key)
        await msg.reply_text(f"{label} override cleared")
        return

    _overrides.set_value(chat_overrides, chat.id, key, text)
    await msg.reply_text(f"{label} override saved ({len(text)} chars)")


async def cmd_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_text_override(update, context, "system_prompt", "system_prompt")


async def cmd_append_system_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _cmd_text_override(update, context, "append_system_prompt", "append_system_prompt")


async def cmd_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat = update.effective_chat
    msg = update.effective_message
    if not user or not chat or not msg or not is_allowed(config, chat.id, user.id):
        return
    if not context.args:
        await msg.reply_text("usage: /fetch <path>")
        return
    rel_path = " ".join(context.args)
    log.info("chat %s /fetch %s", chat.id, rel_path)
    chat_cfg = _merged_chat_config(chat.id)
    try:
        workspace = resolve_workspace(chat_cfg)
    except ValueError as exc:
        await msg.reply_text(f"workspace error: {exc}")
        return
    full = os.path.realpath(os.path.join(workspace, rel_path))
    root_real = os.path.realpath(ROOT_WORKSPACE)
    if not (full == root_real or full.startswith(root_real + os.sep)):
        await msg.reply_text("path outside workspace")
        return
    if not os.path.isfile(full):
        await msg.reply_text(f"not found: {rel_path}")
        return
    await _send_file(context.bot, chat.id, full)


_BUTTON_HANDLERS: dict[str, tuple[str, Callable[[], list[str]]]] = {
    "model": ("model", _available_models),
    "effort": ("effort", _available_efforts),
}


async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or not is_allowed(config, chat.id, user.id):
        await query.answer("not allowed")
        return

    key, _, choice = query.data.partition(":")
    entry = _BUTTON_HANDLERS.get(key)
    if entry is None:
        await query.answer()
        return
    label, options_fn = entry
    options = options_fn()
    try:
        _overrides.apply_choice(chat_overrides, chat.id, key, choice, options)
    except ValueError as exc:
        await query.answer(str(exc))
        return
    new_val = _resolved_value(chat.id, key)
    await query.answer(f"{label}: {new_val}")
    try:
        await query.edit_message_text(
            f"{label} set: <b>{new_val}</b>",
            parse_mode="HTML",
        )
    except Exception:  # noqa: BLE001
        pass


# ── entry point ───────────────────────────────────────────────────────────────


def main() -> int:
    configure_logging()
    if not BOT_TOKEN:
        print("AICODEBOX_TELEGRAM_MODE_TOKEN not set", file=sys.stderr)
        return 1

    try:
        get_adapter()
    except RuntimeError as exc:
        log.error("%s", exc)
        return 1

    global config, chat_overrides
    try:
        config = load_config()
    except TelegramConfigError as exc:
        log.error("invalid telegram config: %s", exc)
        return 1
    chat_overrides = _overrides.load()
    log.info(
        "telegram bot starting (allowed_chats=%s overrides=%d chats)",
        config.get("allowed_chats"),
        len(chat_overrides),
    )

    if os.environ.get("AICODEBOX_CRON_MODE") == "1":
        cron_file = os.environ.get("AICODEBOX_CRON_MODE_FILE")
        if not cron_file:
            log.error("AICODEBOX_CRON_MODE=1 set but AICODEBOX_CRON_MODE_FILE missing")
            return 1
        from aicodebox.modes.cron.config import CronConfigError as _CronCfgErr
        from aicodebox.modes.cron.config import load as load_cron
        from aicodebox.modes.cron.scheduler import start_in_thread

        try:
            cron_cfg = load_cron(cron_file)
        except _CronCfgErr as exc:
            log.error("invalid cron file: %s", exc)
            return 1
        start_in_thread(cron_cfg)
        log.info("cron scheduler started in-thread (%d job(s))", len(cron_cfg.jobs))

    async def _post_init(app: Application) -> None:
        from telegram import BotCommand

        await app.bot.set_my_commands(
            [
                BotCommand("model", "Select agent model (overrides yaml)"),
                BotCommand("effort", "Select effort level (overrides yaml)"),
                BotCommand("system_prompt", "Set/show/reset system prompt override"),
                BotCommand("append_system_prompt", "Set/show/reset append-system-prompt override"),
                BotCommand("fetch", "Download a file from workspace"),
                BotCommand("cancel", "Kill running agent process"),
                BotCommand("status", "Show busy chats"),
                BotCommand("config", "Show chat config"),
                BotCommand("reload", "Reload YAML config"),
            ]
        )

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(True)
        .post_init(_post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(CommandHandler("reload", cmd_reload))
    app.add_handler(CommandHandler("config", cmd_config))
    app.add_handler(CommandHandler("model", cmd_model))
    app.add_handler(CommandHandler("effort", cmd_effort))
    app.add_handler(CommandHandler("system_prompt", cmd_system_prompt))
    app.add_handler(CommandHandler("append_system_prompt", cmd_append_system_prompt))
    app.add_handler(CommandHandler("fetch", cmd_fetch))
    app.add_handler(CallbackQueryHandler(on_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logging.getLogger("httpx").setLevel(logging.WARNING)
    app.run_polling(drop_pending_updates=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
