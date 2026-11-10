---
title: "Cron scheduler"
description: "Cron mode: the jobs YAML file, five and six field schedules, placeholders, per-run history directories, overlap handling and Telegram notifications."
nav_order: 5
parent: "Modes"
---

# Cron scheduler

`AICODEBOX_CRON_MODE=1` with `AICODEBOX_CRON_MODE_FILE=/path/to/cron.yaml` runs the agent on a schedule. It runs as the foreground process, or inside the [Telegram bot](telegram.md#telegram-with-cron) when Telegram mode is on too. The code is in [`aicodebox/modes/cron/`](https://github.com/psyb0t/docker-aicodebox/tree/main/aicodebox/modes/cron).

```bash
docker run -d --name agent-cron \
  -e AICODEBOX_CRON_MODE=1 \
  -e AICODEBOX_CRON_MODE_FILE=/home/aicode/.aicodebox/cron.yaml \
  -v "$PWD/workspace:/workspace" \
  -v "$PWD/aicodebox-state:/home/aicode/.aicodebox" \
  your/child-image:latest
```

## The jobs file

```yaml
model: model-a
jobs:
  - name: morning-report
    schedule: "0 0 9 * * *"
    instruction: |
      Summarize yesterday's git activity in this repository.
      The time is {system_datetime}.
    workspace: shared
    telegram_chat_id: -100123
    model: model-b
```

Top-level keys are defaults for every job: `model`, `system_prompt`, `append_system_prompt`, `telegram_chat_id`, `effort` and `thinking`. Each job takes:

| Key | Required | Default | Meaning |
| --- | --- | --- | --- |
| `name` | yes | | Unique, letters, digits, `_` and `-` only. Used in history paths. |
| `schedule` | yes | | Cron expression, see below. |
| `instruction` | yes | | The prompt. Must not be blank. |
| `workspace` | no | `.` | Subdirectory of the workspace root, created on first run. An absolute path or `..` fails each run with an error in `<job>.jsonl`. |
| `model`, `system_prompt`, `append_system_prompt`, `effort`, `thinking` | no | top-level value | Per-job overrides. `thinking` wins over `effort`. |
| `no_continue` | no | `false` | `true` starts a fresh agent session on every run. Must be a boolean. |
| `telegram_chat_id` | no | top-level value | Post the result to this chat. |

The file is checked at startup. A missing file, invalid YAML, an empty `jobs` list, a duplicate or badly formed name, a bad schedule, a blank instruction or a value of the wrong type stops the mode with `invalid cron file: <reason>`.

## Schedules

| Fields | Resolution | Example |
| --- | --- | --- |
| 5 | minute | `0 9 * * *`, 09:00 every day |
| 6 | second, seconds first | `0 0 9 * * *`, 09:00:00 every day, or `*/30 * * * * *`, every 30 seconds |

Schedules use the container's local time, which is UTC unless the image or the run sets `TZ`.

If a job is still running when its next time comes, that tick is skipped and logged. Different jobs run in parallel.

## Placeholders

`instruction`, `system_prompt` and `append_system_prompt` may contain:

| Placeholder | Replaced with |
| --- | --- |
| `{system_datetime}` | The scheduled time in UTC, as `YYYY-MM-DD HH:MM:SS UTC`, also when `TZ` is set. |
| `{job_name}` | The job's name. |

Nothing else is replaced.

## Run history

Each run gets a directory under the cron state root, `$HOME/.aicodebox/cron/` by default or `AICODEBOX_CRON_MODE_HISTORY_DIR`:

```text
history/<workspace-slug>/<YYYYMMDD-HHMMSS>-<job>/
  meta.json       job settings, start and finish times, exit code, error
  stdout.log      raw agent stdout
  stderr.log      raw agent stderr
  result.txt      the agent's final text
  telegram.json   chat_id and message_id, when a notification was sent
```

The scheduler also appends one JSON line per run to `<job>.jsonl` in the state root. When a job has run before, the scheduler appends a note to the system prompt with the path of its most recent run and a glob for older ones, so the agent can compare against past output.

Nothing deletes old runs. Prune the history directory yourself.

## Telegram notifications

With `telegram_chat_id` set and `AICODEBOX_TELEGRAM_MODE_TOKEN` available, each run posts `[<job>]` and its result to that chat, converted to Telegram HTML, or plain text if Telegram rejects the HTML. An empty result posts `finished (no output)` or `job failed (rc=<n>)`. This works in cron mode alone: the bot does not need to be running.

The scheduler records each posted message in `telegram_messages.json` in the state root, keeping the latest 200. When someone replies to one in a chat the bot serves, the bot adds the job's context to the prompt.

## Shutdown

On SIGTERM or SIGINT the scheduler stops starting jobs and waits up to 30 seconds for each running one.
