---
title: "Container startup"
description: "What the aicodebox entrypoint does on every start: UID and GID remapping, Docker socket access, Git identity, once-per-container init scripts and the user bin directory."
nav_order: 6
---

# Container startup

The entrypoint, [`entrypoint.sh`](https://github.com/psyb0t/docker-aicodebox/blob/main/entrypoint.sh), runs as root on every container start, prepares the `aicode` user, then drops to that user and starts one [mode](modes.md). The steps run in this order.

## Match the workspace owner

The entrypoint reads the UID and GID of `AICODEBOX_WORKSPACE` (default `/workspace`). When both are non-zero, `aicode` takes them over, even when another account in the image already uses those IDs, and files under `/home/aicode` are handed to the new IDs. Files the agent writes into a bind-mounted workspace then belong to you on the host.

If either ID is 0, `aicode` keeps the image's UID 1000. It never becomes root.

`aicode` keeps its supplementary groups and passwordless sudo through the remap. Accounts that share a UID are the same Unix identity, so a lookup by number may show the other account's name. `HOME` stays `/home/aicode`.

## Docker socket access

When `/var/run/docker.sock` is mounted, the image's `docker` group takes the socket's GID, so `aicode` can use the host's Docker without sudo. The workspace primary group is kept even when it collides with Docker's old GID.

A mounted socket gives the agent control of the host's Docker. See [Security](security.md#docker-socket).

## Git identity

`AICODEBOX_GIT_NAME` and `AICODEBOX_GIT_EMAIL`, when non-empty, are written to the runtime user's global Git configuration on every start, before init scripts and the agent. Empty values change nothing. A value containing a newline or carriage return stops the container.

## Init scripts

Init scripts run once per container, on its first start:

1. `/aicodebox-init.d/*.sh`, baked in by the child image, run first.
2. `$HOME/.aicodebox/init.d/*.sh`, your own, run next. Bind-mount `$HOME/.aicodebox` to supply them.

Each set runs in file name order with `bash`, as `aicode` (passwordless sudo), with the container's environment and `$HOME/.aicodebox/bin` already on `PATH`. A script that exits non-zero is logged as `[entrypoint] init script <path> failed`, and the rest still run.

The record that init has run is `/var/lib/aicodebox/init-done`, inside the container's own filesystem. Restarting the container skips init. A new container runs it again, even when it mounts the same `$HOME/.aicodebox`. Put setup that must happen on every start in the child image's own entrypoint instead.

## User bin directory

Executables in `$HOME/.aicodebox/bin` come first on `PATH`: for the agent in every mode, for init scripts, and for `docker exec` shells. Drop a script there to give the agent a command.

```bash
mkdir -p aicodebox-state/bin
printf '#!/bin/sh\necho hello from my tool\n' > aicodebox-state/bin/my-tool
chmod +x aicodebox-state/bin/my-tool

docker run --rm \
  -v "$PWD/aicodebox-state:/home/aicode/.aicodebox" \
  your/child-image:latest
```

## Auth file

If `$HOME/.aicodebox/.<AICODEBOX_CONTAINER_NAME>-auth` exists, the entrypoint sources it with every variable exported, on every start. A child image's login flow can store tokens there so they survive a new container. The file is shell, so only write `KEY=value` lines you trust.

## Start the mode

The entrypoint switches to `aicode` with `setpriv`, keeps the container's environment, sets `HOME` and `PATH`, changes to the workspace and replaces itself with the chosen process. That process becomes the container's main process, and the container stops when it exits. See [Modes](modes.md) for which process that is.

## State directory

`$HOME/.aicodebox` (`/home/aicode/.aicodebox`) holds everything the base keeps between runs:

| Path | Written by |
| --- | --- |
| `bin/` | You. |
| `init.d/` | You. |
| `telegram.yml` | You. |
| `telegram_overrides.json` | The Telegram bot. |
| `cron/` | The cron scheduler: history, job summaries, the Telegram message map. |
| `.<container-name>-auth` | The child image's login flow, if it has one. |

The package itself stores nothing else. Bind-mount the directory to keep it.

## Logs

The entrypoint logs failures as one JSON object per line on stderr, with `time`, `level`, `file`, `line`, `func` and `msg`. `DEBUG=true` adds plain progress lines prefixed with `[entrypoint <time>]`.
