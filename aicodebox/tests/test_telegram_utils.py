"""Markdown→Telegram-HTML conversion and long-message chunking."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from aicodebox.modes.telegram import utils

# ── md_to_tg_html ─────────────────────────────────────────────────────────────


def test_md_bold():
    assert utils.md_to_tg_html("**hi**") == "<b>hi</b>"


def test_md_italic_star():
    assert utils.md_to_tg_html("*hi*") == "<i>hi</i>"


def test_md_inline_code_escapes_special_chars():
    out = utils.md_to_tg_html("`x & y`")
    assert out == "<code>x &amp; y</code>"


def test_md_fenced_code_with_lang():
    out = utils.md_to_tg_html("```python\nprint(1)\n```")
    assert '<pre><code class="language-python">' in out
    assert "</code></pre>" in out


def test_md_fenced_code_no_lang():
    out = utils.md_to_tg_html("```\nx\n```")
    assert "<pre>x" in out
    assert "</pre>" in out


def test_md_heading_becomes_bold():
    assert "<b>Title</b>" in utils.md_to_tg_html("# Title")


def test_md_blockquote():
    out = utils.md_to_tg_html("> quoted")
    assert "<blockquote>quoted</blockquote>" in out


def test_md_list_bullet_normalized():
    out = utils.md_to_tg_html("- one\n- two")
    assert "• one" in out and "• two" in out


def test_md_link_escapes_url():
    out = utils.md_to_tg_html("[here](https://e?x=<bad>)")
    assert 'href="https://e?x=&lt;bad&gt;"' in out


def test_md_escapes_ampersand_outside_tags():
    out = utils.md_to_tg_html("a & b")
    assert "a &amp; b" in out


def test_md_supported_html_preserved():
    out = utils.md_to_tg_html("<b>bold</b>")
    assert out == "<b>bold</b>"


def test_md_empty_returns_empty():
    assert utils.md_to_tg_html("") == ""


# ── send_long ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_send_long_single_short_message():
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value="msg")
    sent = await utils.send_long(bot, 1, "hi", parse_mode="HTML")
    assert len(sent) == 1
    bot.send_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_send_long_splits_on_max_length(monkeypatch):
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value="msg")
    # 4096 is the Telegram limit. Make text larger so it definitely splits.
    big = "line\n" * 1000
    sent = await utils.send_long(bot, 1, big, parse_mode="HTML")
    assert len(sent) >= 2
    assert bot.send_message.await_count == len(sent)


@pytest.mark.asyncio
async def test_send_long_falls_back_to_plain_text_on_parse_error():
    bot = MagicMock()

    async def fail_then_succeed(**kwargs):
        if kwargs.get("parse_mode") == "HTML":
            raise RuntimeError("bad html")
        return "msg"

    bot.send_message = AsyncMock(side_effect=fail_then_succeed)
    sent = await utils.send_long(bot, 1, "<b>hi", parse_mode="HTML")
    assert sent == ["msg"]
    # called twice: once with HTML (failed), once plain
    assert bot.send_message.await_count == 2
