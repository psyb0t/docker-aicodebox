---
title: "Security"
description: "What an aicodebox container can reach, which tokens guard the API, MCP and Telegram, and what to set before exposing any of them beyond localhost."
nav_order: 8
---

# Security

An aicodebox container runs a coding agent that can execute commands, edit files and, with the Docker socket mounted, control the host's Docker. Anyone who can send it a prompt gets all of that. Treat every mode as remote code execution for whoever can reach it.

## What the agent can reach

| Resource | Access |
| --- | --- |
| Commands | The agent runs as `aicode`, which has passwordless sudo. Root inside the container is one command away. |
| Files | The mounted workspace, `$HOME/.aicodebox` and anything else mounted. |
| Docker | With `/var/run/docker.sock` mounted, full control of the host's Docker, which is as good as root on the host. |
| Network | Whatever the container's network allows. The base adds no egress filter. |
| Credentials | Every environment variable in the container, and the auth file startup loads. |

## Before exposing a port

- Set `AICODEBOX_API_MODE_TOKEN` and `AICODEBOX_MCP_MODE_TOKEN` to long random values, for example `openssl rand -hex 32`. Both default to no auth. The two are separate: setting the API token does not protect MCP.
- Publish on loopback (`-p 127.0.0.1:8080:8080`) and put a TLS reverse proxy in front for anything remote. The servers speak plain HTTP and bind every interface inside the container.
- Behind a proxy, set `AICODEBOX_MCP_MODE_ALLOWED_HOSTS` and `AICODEBOX_MCP_MODE_ALLOWED_ORIGINS` to the exact names it forwards. Do not turn the check off or widen it with wildcards. See [MCP server](mcp.md#host-and-origin-checks).
- Mount only the workspace the agent needs, and mount the Docker socket only when the agent must run containers.

## Tokens

The API and MCP tokens are single values compared as plain strings. There is no rotation list, rate limit or lockout. MCP also accepts the token in the `apiToken` query parameter for clients that cannot set headers; a token in a URL can end up in proxy and client logs, so prefer the header.

`/healthz` is always open. It reports whether an adapter is configured and the adapter's name, nothing else.

## Telegram

The bot answers every chat that is allowed by `telegram.yml`. Two cases are open to anyone who finds the bot:

- `allowed_chats` is empty or missing in `telegram.yml`.
- `telegram.yml` does not exist and `TELEGRAM_CHAT_ID` is unset.

Always list your chat IDs in `allowed_chats`, and add `allowed_users` per chat for group chats. Messages from other chats and users are ignored.

## Workspace paths

Request paths for workspaces, files, MCP tools, Telegram `/fetch` and `[SEND_FILE: ...]` are resolved under `AICODEBOX_WORKSPACE`, and paths that leave it are refused. This keeps a caller's paths inside the root. It does not restrict the agent, which can read and write anywhere its user can.

## Remote images in OpenAI requests

The OpenAI endpoint downloads `http` and `https` image URLs from requests. Before the download it resolves the host and refuses private, loopback, link-local, multicast and reserved addresses. The check covers the first URL only: an HTTP redirect is followed without a second check, and the address is resolved again for the actual download. Do not rely on it to keep a caller away from internal services on a network that has them.

## Docker socket

Mounting `/var/run/docker.sock` lets the agent start containers, mount any host path into them and run them privileged. Startup gives `aicode` the socket's group so this works without sudo. Mount it only when the agent needs it, and only on a host where that agent may act as root.

## Full image advisories

The full image ships Node developer tools with known advisories in their dependency trees. The modes do not load them, but the agent can run them. See [Optional Node tool advisories](dependency-audit.md).

## Supply chain

Third-party base images are pinned by digest. Python, Node, Go and the release binaries are downloaded at fixed versions and checked against SHA-256 hashes in the Dockerfiles. Python dependencies come from the frozen [`uv.lock`](https://github.com/psyb0t/docker-aicodebox/blob/main/uv.lock), resolved with a fixed release-date cutoff in [`pyproject.toml`](https://github.com/psyb0t/docker-aicodebox/blob/main/pyproject.toml). The CI image workflow scans every build with a `high` severity threshold.
