---
title: "MCP server"
description: "The aicodebox MCP server: where it listens in each mode, its bearer token, host and origin allowlists, and the run_prompt and workspace file tools."
nav_order: 3
parent: "Modes"
---

# MCP server

`AICODEBOX_MCP_MODE=1` exposes the agent as a Model Context Protocol server over streamable HTTP, so an MCP client can hand it prompts and read or write workspace files. It runs beside any foreground mode. The code is [`aicodebox/modes/api/mcp_server.py`](https://github.com/psyb0t/docker-aicodebox/blob/main/aicodebox/modes/api/mcp_server.py).

## Where it listens

| Foreground mode | URL |
| --- | --- |
| API | `http://<host>:8080/mcp/` (`/mcp` works too), on the API port and in the API process. |
| Telegram, cron or passthrough | `http://<host>:8081/`, a separate process on `AICODEBOX_MCP_MODE_PORT`. |

## Authentication

Set `AICODEBOX_MCP_MODE_TOKEN` and send it as `Authorization: Bearer <token>`. Clients that cannot set headers can append `?apiToken=<token>` to the URL. A wrong or missing token gives `401 {"detail":"unauthorized"}`. With the variable empty or unset, MCP has no auth.

The MCP token is separate from `AICODEBOX_API_MODE_TOKEN`. There is no fallback between them: an API token does not open MCP, and an empty MCP token leaves MCP open even when the API has a token.

## Host and origin checks

MCP keeps the SDK's DNS rebinding protection on. By default it accepts only loopback `Host` values (`127.0.0.1`, `localhost`, `[::1]`, with any port) and loopback `http` origins. A request with another `Host` gets 421, one with another `Origin` gets 403.

Behind a reverse proxy, a tunnel or a public DNS name, list exactly what it forwards:

```bash
docker run --rm -p 8080:8080 \
  -e AICODEBOX_API_MODE=1 \
  -e AICODEBOX_MCP_MODE=1 \
  -e AICODEBOX_MCP_MODE_TOKEN="$MCP_TOKEN" \
  -e AICODEBOX_AVAILABLE_MODELS=model-a \
  -e AICODEBOX_MCP_MODE_ALLOWED_HOSTS=agent.example.com \
  -e AICODEBOX_MCP_MODE_ALLOWED_ORIGINS=https://agent.example.com \
  your/child-image:latest
```

Both variables are comma-separated and replace the defaults. A value may end in `:*` to allow any port. Setting either one to an empty string is an error, so leave it unset to keep the default. Do not turn the protection off or use broad wildcards on an endpoint that faces the internet.

## Tools

| Tool | Arguments | Returns |
| --- | --- | --- |
| `run_prompt` | `prompt`, and optional `workspace`, `model`, `system_prompt`, `append_system_prompt`, `no_continue` (default `true`), `resume`, `thinking`, `json_schema` | The agent's final text. |
| `list_files` | `path` (default root) | JSON `{"path", "entries": [{"name", "type"}]}`. |
| `read_file` | `path` | The file as text, with undecodable bytes replaced. |
| `write_file` | `path`, `content` | Overwrites or creates the file and its parents. JSON `{"status": "ok", "path", "size"}`, where `size` counts characters. |
| `delete_file` | `path` | JSON `{"status": "ok", "path"}`. Refuses directories. |

Paths are relative to `AICODEBOX_WORKSPACE`. A path that resolves outside it returns an error result instead of touching the file.

`run_prompt` runs the agent once. Unlike `/run` it does not retry on a schema failure: `json_schema` reaches the adapter, and the adapter decides what to do with it. `run_prompt` does not take the per-workspace lock that `/run` and the OpenAI endpoint share, so an MCP run can overlap an HTTP run in the same workspace.

## Client setup

Point the client at the URL from the table above, with the token in a header:

```json
{
  "mcpServers": {
    "aicodebox": {
      "type": "http",
      "url": "http://localhost:8080/mcp/",
      "headers": {
        "Authorization": "Bearer your-mcp-token"
      }
    }
  }
}
```

The exact file and keys depend on the client.
