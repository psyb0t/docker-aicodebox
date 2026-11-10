---
title: "Telegram bot"
description: "Telegram mode: telegram.yml chat allowlists and per-chat settings, bot commands, file uploads, SEND_FILE attachments, overrides and replies to cron messages."
nav_order: 4
parent: "Modes"
---

# Telegram bot

`AICODEBOX_TELEGRAM_MODE=1` with `AICODEBOX_TELEGRAM_MODE_TOKEN=<token from @BotFather>` runs a Telegram bot that sends each message to the agent and posts the answer back. It polls Telegram, so it needs outbound HTTPS and no published port. The code is in [`aicodebox/modes/telegram/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/modes/telegram).

```bash
docker run -d --name agent-bot \
  -e AICODEBOX_TELEGRAM_MODE=1 \
  -e AICODEBOX_TELEGRAM_MODE_TOKEN="$BOT_TOKEN" \
  -v "$PWD/workspace:/workspace" \
  -v "$PWD/aicodebox-state:/home/aicode/.aicodebox" \
  your/child-image:latest
```

## Configuration file

The bot reads `$HOME/.aicodebox/telegram.yml`, or the path in `AICODEBOX_TELEGRAM_MODE_CONFIG`:

```yaml
allowed_chats: [-100123, 42]
default:
  model: model-a
  workspace: shared
chats:
  -100123:
    workspace: alpha
    model: model-b
    allowed_users: [10, 20]
```

| Key | Where | Meaning |
| --- | --- | --- |
| `allowed_chats` | top level | Chat IDs the bot answers. Empty or missing means every chat. |
| `default` | top level | Settings for every chat. |
| `chats.<id>` | top level | Settings for one chat, over `default`. |
| `allowed_users` | `chats.<id>` | User IDs allowed in that chat. Empty or missing means everyone in the chat. |
| `workspace` | `default` or chat | Subdirectory of the workspace root. Without one, each chat gets `chat_<id>`, using the absolute value of the chat ID. |
| `model` | `default` or chat | Model for the agent. |
| `effort`, `thinking` | `default` or chat | Reasoning level. `thinking` wins when both are set. |
| `system_prompt` | `default` or chat | Replace the agent's system prompt. |
| `append_system_prompt` | `default` or chat | Appended after the bot's own formatting hint. |
| `continue` | `default` or chat | `false` starts a fresh session for every message. Default `true`. |

When the file does not exist, the bot falls back to `TELEGRAM_CHAT_ID`: that one chat is allowed and nothing else is configured. With neither the file nor that variable, the bot answers every chat that finds it. Always set an allowlist; see [Security](security.md#telegram).

Settings merge in this order, later wins: `default`, `chats.<id>`, then the chat's runtime overrides. `/reload` reads the file again without a restart.

## Messages

- A text message runs the agent in the chat's workspace. The answer is converted from Markdown to the HTML subset Telegram accepts and split into messages of at most 4096 characters. If Telegram rejects the HTML, the chunk is sent again as plain text.
- The bot adds a hint to the system prompt that tells the agent to write Markdown and how to send files back.
- A chat runs one prompt at a time. A message that arrives while one is running gets `busy, try again later`. Up to four chats run at once.
- Replying to an earlier message adds that message's text and kind (photo, document, voice and so on) to the prompt.
- Replying to a message that a [cron job](cron.md#telegram-notifications) posted adds the job's instruction, its result and the path of its run history, and starts a fresh session.
- An answer with no text is reported as `(agent exited with code <n> and no output)`.

## Files

Documents, photos, videos and voice messages are saved into the chat's workspace under their own file name (photos as `photo_<id>.jpg`, voice as `voice_<id>.ogg`). Without a caption the bot replies `saved <name>`. With a caption it runs the agent with "I saved a file '<name>' to the workspace." followed by the caption.

To send a file back, the agent writes `[SEND_FILE: relative/path]` anywhere in its answer. The bot strips the tag and sends the file: images as photos, videos as videos, anything else as a document. The path is relative to the chat's workspace and must resolve inside the workspace root. Empty files and files over 50 MB are refused with a message.

## Commands

| Command | Effect |
| --- | --- |
| `/model [name]` | Pick the model from a keyboard of `AICODEBOX_AVAILABLE_MODELS` or the adapter's list, or set it directly. The argument is lowercased before it is matched. |
| `/effort [level]` | The same for the reasoning level, from `AICODEBOX_AVAILABLE_EFFORTS` or the adapter's list. |
| `/system_prompt [text]` | Show or set this chat's system prompt override. |
| `/append_system_prompt [text]` | Show or set this chat's appended prompt override. |
| `/fetch <path>` | Send a file from the chat's workspace. |
| `/cancel` | Kill the running agent for this chat and drop its output. |
| `/status` | List chats with a run in progress. |
| `/config` | Show this chat's merged settings. |
| `/reload` | Read `telegram.yml` again. |
| `/start`, `/help` | Short greeting and command list. |

`reset`, `default`, `clear`, `none` or `__reset__` as the value clears an override. `/effort` stores the `effort` setting, so it has no effect on a chat whose configuration sets `thinking`. With no model or effort list configured, `/model` and `/effort` answer with the variable to set.

Overrides are stored in `$HOME/.aicodebox/telegram_overrides.json` (or `AICODEBOX_TELEGRAM_MODE_OVERRIDES`) and survive restarts when that directory is mounted.

## Telegram with cron

Set `AICODEBOX_CRON_MODE=1` and `AICODEBOX_CRON_MODE_FILE` next to the Telegram variables and the bot runs the [cron scheduler](cron.md) in a thread of the same process. An invalid cron file stops the bot at startup.

The bot skips updates that queued up while it was down.
