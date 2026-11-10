---
title: "Child images"
description: "Build an image for your coding agent on top of psyb0t/aicodebox: install the agent CLI, add an adapter package, set two environment variables, run it."
nav_order: 2
---

# Child images

A child image is `psyb0t/aicodebox` plus three things: your agent's CLI, a Python package with an [adapter class](adapter-contract.md), and two environment variables that point at them. Everything else (the modes, auth, workspaces, startup handling) comes from the base.

## Minimal recipe

This wires up an agent that ships as an npm package:

```dockerfile
FROM psyb0t/aicodebox:latest

# Your agent. Pin the version.
ARG AGENT_VERSION=1.2.3
RUN npm install -g @your-org/your-agent@${AGENT_VERSION}

# Your adapter package. It implements aicodebox.adapters.base.AgentAdapter.
COPY your_adapter /opt/your_adapter
RUN pip3 install --no-cache-dir --break-system-packages /opt/your_adapter

ENV AICODEBOX_ADAPTER=your_adapter.adapter:YourAdapter \
    AICODEBOX_AGENT_BINARY=your-agent
```

`AICODEBOX_ADAPTER` is `package.module:ClassName`. Every mode imports it on first use and reuses the instance for the life of the process. `AICODEBOX_AGENT_BINARY` is the command the container runs when no mode is enabled.

The base installs its own Python dependencies into the system Python with `uv pip install --system`, so a child image can add packages with `pip3 install --break-system-packages` or `uv pip install --system` without a virtual environment.

## Run it

API mode, with a token and a bind-mounted workspace:

```bash
docker run --rm -p 8080:8080 \
  -e AICODEBOX_API_MODE=1 \
  -e AICODEBOX_API_MODE_TOKEN="$(openssl rand -hex 16)" \
  -e AICODEBOX_AVAILABLE_MODELS=model-a,model-b \
  -v "$PWD/workspace:/workspace" \
  your/child-image:latest
```

API mode refuses to start without a model list, from `AICODEBOX_AVAILABLE_MODELS` or from the adapter's `available_models`. See [HTTP API](api.md).

With no mode flag, the container runs the agent binary directly and passes its arguments through, so the image also works as a plain CLI:

```bash
docker run --rm -it \
  -v "$PWD:/workspace" \
  your/child-image:latest --help
```

If `AICODEBOX_AGENT_BINARY` is unset, passthrough runs `pi`.

## What the child image can add

| Path or setting | Effect |
| --- | --- |
| `/aicodebox-init.d/*.sh` | Image init scripts. They run once per container, before the user's own init scripts. See [Container startup](container-startup.md#init-scripts). |
| `AICODEBOX_GIT_NAME`, `AICODEBOX_GIT_EMAIL` | Shared Git identity names. A child image can map its own prefixed variables onto these. |
| `/home/aicode/.aicodebox/.<container-name>-auth` | A shell file of `KEY=value` lines that the entrypoint loads into the environment on every start. Adapters that persist login tokens write it. |
| `AgentAdapter.translate_auth` | Rename provider credentials, such as `ANTHROPIC_API_KEY`, to whatever your agent reads. |

## Minimal or full parent

Pick one parent per variant:

```dockerfile
FROM psyb0t/aicodebox:latest-full
```

`latest-full` keeps the same entrypoint, workspace layout, Python package and modes as `latest`. The difference is the toolchain. A child image built on it should still install only its own agent CLI, adapter and configuration. See [Full image](full-image.md).

## Reference children

| Image | Agent |
| --- | --- |
| [claudebox](https://github.com/psyb0t/docker-claudebox) | Claude Code |
| [codexbox](https://github.com/psyb0t/docker-codexbox) | Codex CLI |
| [pibox](https://github.com/psyb0t/docker-pibox) | [pi-coding-agent](https://github.com/earendil-works/pi-coding-agent) |

Each child documents its own environment variable names, modes and differences. Its wrapper calls a sibling wrapper directly when one is installed beside it. A child must not rebuild a sibling's Docker command or mount another agent's state itself.
