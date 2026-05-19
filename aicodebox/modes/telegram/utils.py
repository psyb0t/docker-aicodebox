"""Shared telegram helpers: markdown→HTML conversion and chunked sending."""
from __future__ import annotations

import logging
import os
import re

from telegram import Bot
from telegram.constants import MessageLimit

logger = logging.getLogger("telegram.utils")

BOT_TOKEN = os.environ.get("AICODEBOX_TELEGRAM_BOT_TOKEN", "")

_HTML_TAG_RE = re.compile(r"<[^>]+>")


# Authoritative formatting hint for the harness when output is going to Telegram.
TELEGRAM_HTML_HINT = (
    "Your output will be posted to a Telegram chat. Format using STANDARD "
    "MARKDOWN — the host will convert your output to Telegram-compatible HTML "
    "automatically. Use:\n"
    "  **bold**, *italic* (or _italic_), ~~strikethrough~~\n"
    "  `inline code`, ```language\\nfenced code block\\n```\n"
    "  # / ## / ### headings (rendered as bold — Telegram has no h1..h6)\n"
    "  > blockquoted line\n"
    "  - bulleted list item (rendered as • since Telegram has no <ul>/<li>)\n"
    "  [link text](https://example.com)\n"
    "Do NOT write raw HTML tags yourself — write Markdown and let the converter "
    "produce the HTML. Do NOT pre-escape characters with &amp;/&lt;/&gt; in "
    "regular prose; the converter escapes literals safely. Keep responses "
    "concise but readable."
)


def _strip_html(text: str) -> str:
    return _HTML_TAG_RE.sub("", text)


_PLACEHOLDER = "CB{}"


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


_SUPPORTED_HTML_RE = re.compile(
    r"</?(?:b|strong|i|em|u|ins|s|strike|del|tg-spoiler|code|pre|blockquote)>"
    r"|<blockquote\s+expandable>"
    r'|<span\s+class="tg-spoiler">|</span>'
    r'|<a\s+href="[^"<>]*">|</a>'
    r'|<pre><code\s+class="language-[A-Za-z0-9_+-]+">|</code></pre>',
    re.IGNORECASE,
)


def md_to_tg_html(text: str) -> str:
    """Convert Markdown-ish text to the HTML subset Telegram accepts."""
    if not text:
        return ""

    placeholders: list[str] = []

    def stash(html: str) -> str:
        placeholders.append(html)
        return _PLACEHOLDER.format(len(placeholders) - 1)

    text = _SUPPORTED_HTML_RE.sub(lambda m: stash(m.group(0)), text)

    def _fence(m: re.Match) -> str:
        lang = (m.group(1) or "").strip()
        body = _esc(m.group(2))
        if lang:
            return stash(f'<pre><code class="language-{_esc(lang)}">{body}</code></pre>')
        return stash(f"<pre>{body}</pre>")

    text = re.sub(r"```([^\n`]*)\n(.*?)```", _fence, text, flags=re.DOTALL)
    text = re.sub(r"`([^`\n]+)`", lambda m: stash(f"<code>{_esc(m.group(1))}</code>"), text)

    def _link(m: re.Match) -> str:
        label = m.group(1)
        url = m.group(2).strip()
        return stash(f'<a href="{_esc(url)}">{_esc(label)}</a>')

    text = re.sub(r"\[([^\]\n]+)\]\(([^)\n]+)\)", _link, text)
    text = re.sub(r"\*\*([^\*\n]+?)\*\*", lambda m: stash(f"<b>{_esc(m.group(1))}</b>"), text)
    text = re.sub(r"(?<!_)__([^_\n]+?)__(?!_)", lambda m: stash(f"<b>{_esc(m.group(1))}</b>"), text)
    text = re.sub(r"(?<![\*\w])\*([^\*\n]+?)\*(?!\*)", lambda m: stash(f"<i>{_esc(m.group(1))}</i>"), text)
    text = re.sub(r"(?<![_\w])_([^_\n]+?)_(?!_)", lambda m: stash(f"<i>{_esc(m.group(1))}</i>"), text)
    text = re.sub(r"~~([^~\n]+?)~~", lambda m: stash(f"<s>{_esc(m.group(1))}</s>"), text)
    text = re.sub(
        r"(?m)^[ \t]{0,3}(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$",
        lambda m: stash(f"<b>{_esc(m.group(2))}</b>"),
        text,
    )

    def _blockquote(m: re.Match) -> str:
        lines = [re.sub(r"^[ \t]{0,3}>[ \t]?", "", ln) for ln in m.group(0).splitlines()]
        body = _esc("\n".join(lines))
        return stash(f"<blockquote>{body}</blockquote>")

    text = re.sub(r"(?m)(^[ \t]{0,3}>[^\n]*(?:\n[ \t]{0,3}>[^\n]*)*)", _blockquote, text)
    text = re.sub(r"(?m)^([ \t]*)[-*+][ \t]+", lambda m: f"{m.group(1)}• ", text)
    text = _esc(text)

    def _restore(m: re.Match) -> str:
        return placeholders[int(m.group(1))]

    sentinel_re = re.compile(r"CB(\d+)")
    for _ in range(len(placeholders) + 1):
        new_text = sentinel_re.sub(_restore, text)
        if new_text == text:
            break
        text = new_text

    return text


def make_bot(token: str = "") -> Bot:
    return Bot(token=token or BOT_TOKEN)


async def send_long(bot: Bot, chat_id: int, text: str, parse_mode: str = "HTML") -> list:
    """Send text to a chat, splitting at newline boundaries to respect Telegram limits."""
    sent = []
    max_len = MessageLimit.MAX_TEXT_LENGTH
    while text:
        chunk = text[:max_len] if len(text) > max_len else text
        if len(text) > max_len:
            split_at = text.rfind("\n", 0, max_len)
            if split_at != -1:
                chunk = text[:split_at]
        try:
            msg = await bot.send_message(chat_id=chat_id, text=chunk, parse_mode=parse_mode)
        except Exception as e:
            logger.warning("send_message with parse_mode=%s failed (%s), retrying as plain text", parse_mode, e)
            plain = _strip_html(chunk) if parse_mode == "HTML" else chunk
            msg = await bot.send_message(chat_id=chat_id, text=plain)
        sent.append(msg)
        text = text[len(chunk):].lstrip("\n")
        if not text:
            break
    return sent
