---
title: "Home"
description: "docker-aicodebox is a base image: write one adapter for a terminal coding agent and get an HTTP API, OpenAI endpoint, MCP server, Telegram bot and cron."
nav_order: 1
permalink: /
seo:
  type: WebSite
---

# aicodebox

`psyb0t/aicodebox` is the agent-agnostic base image under [claudebox](https://github.com/psyb0t/docker-claudebox), [codexbox](https://github.com/psyb0t/docker-codexbox) and [pibox](https://github.com/psyb0t/docker-pibox). You install a terminal coding agent on top of it, write one Python adapter class that turns "run this prompt" into that agent's command line, and the base gives you an HTTP API, an OpenAI-compatible chat completions endpoint, an MCP server, a Telegram bot and a cron scheduler. One container, same interfaces, swap the agent.

You don't fork this. You `FROM` it.

```dockerfile
FROM psyb0t/aicodebox:latest

RUN npm install -g @your-org/your-agent@1.2.3
COPY your_adapter /opt/your_adapter
RUN pip3 install --no-cache-dir --break-system-packages /opt/your_adapter

ENV AICODEBOX_ADAPTER=your_adapter.adapter:YourAdapter \
    AICODEBOX_AGENT_BINARY=your-agent
```

Then start it in API mode:

```bash
docker run --rm -p 8080:8080 \
  -e AICODEBOX_API_MODE=1 \
  -e AICODEBOX_API_MODE_TOKEN="$(openssl rand -hex 16)" \
  -e AICODEBOX_AVAILABLE_MODELS=model-a,model-b \
  -v "$PWD/workspace:/workspace" \
  your/child-image:latest
```

`aicodebox` is a base image, not a host launcher. For local agent work, use a child wrapper such as claudebox, codexbox or pibox. Those wrappers mount the requested workspace and the matching agent state.

## What's in the box

| Layer | Contents |
| --- | --- |
| OS | Ubuntu 24.04, an `aicode` user (UID 1000) with passwordless sudo and membership in the `docker` group. |
| Runtimes | Node.js 24.21.0, Python 3.14.8, uv 0.13.0, Docker CE with buildx and compose. |
| Package | The `aicodebox` Python package: the adapter contract and four mode dispatchers (API, Telegram, cron, MCP). Nothing runs until a mode starts. |
| Modes | All opt-in through environment variables. One foreground mode per container, except Telegram and cron, which share a process. MCP runs beside any of them. |
| Auth | `AICODEBOX_API_MODE_TOKEN` guards the HTTP API, `AICODEBOX_MCP_MODE_TOKEN` guards MCP. Empty means no auth. Telegram has a chat and user allowlist. |
| State | Telegram overrides, cron history, your init scripts and your `bin/` directory live under `$HOME/.aicodebox/`. Bind-mount it to keep them. |

The `latest-full` tag adds a general development toolchain on top: Go and its tools, pinned Node and Python developer tools, editors, database clients, `gh`, Terraform, kubectl and Helm. See [Full image](full-image.md).

## Where to go next

| You want to | Read |
| --- | --- |
| Build an image for your agent | [Child images](child-images.md) |
| Write the adapter class | [Adapter contract](adapter-contract.md) |
| Pick and combine modes | [Modes](modes.md) |
| Call the agent over HTTP | [HTTP API](api.md) |
| Point an OpenAI client at it | [OpenAI-compatible endpoint](openai.md) |
| Connect an MCP client | [MCP server](mcp.md) |
| Talk to the agent from a chat | [Telegram bot](telegram.md) |
| Run the agent on a schedule | [Cron scheduler](cron.md) |
| Look up an environment variable | [Configuration](configuration.md) |
| Understand UID remapping, init scripts and `bin/` | [Container startup](container-startup.md) |
| Expose it beyond localhost | [Security](security.md) |
| Fix a container that will not start | [Troubleshooting](troubleshooting.md) |
| Change the code or run the tests | [Development](development.md) |
