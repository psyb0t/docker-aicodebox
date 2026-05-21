#!/bin/bash
# aicodebox-base entrypoint — agent-agnostic.
#
# Responsibilities:
#  1. UID/GID rematch against the mounted workspace
#  2. Docker socket GID fix
#  3. Run any first-run init scripts dropped by the child image
#     into /aicodebox-init.d/ (run once, marker in $HOME/.aicodebox/.init-done)
#  4. Load persisted auth env vars
#  5. Dispatch to a mode (api/telegram/cron) or fall through to the agent's
#     interactive/passthrough CLI via the configured adapter
#
# Child images may set:
#   AICODEBOX_ADAPTER=pkg.module:Class   # required for modes
#   AICODEBOX_AGENT_BINARY=pi            # the bin name for passthrough invocation
set -e

dbg() {
    if [ "${DEBUG:-}" = "true" ]; then
        echo "[entrypoint $(date +%H:%M:%S.%3N)] $*" >&2
    fi
}

usage() {
    cat <<EOF >&2
usage: <container> [args passed to the agent CLI]

env (mode flags — pick a foreground mode + optional MCP):
  AICODEBOX_API_MODE=1            run the FastAPI server (programmatic API)
  AICODEBOX_TELEGRAM_MODE=1       run the telegram bot
  AICODEBOX_CRON_MODE=1           run the cron scheduler
  AICODEBOX_MCP_MODE=1            expose the MCP server (coexists w/ any mode)

env (mode config):
  AICODEBOX_API_MODE_PORT=8080    port for API mode
  AICODEBOX_API_MODE_TOKEN=...    bearer token for API mode (empty = no auth)
  AICODEBOX_TELEGRAM_MODE_TOKEN   bot token from @BotFather
  AICODEBOX_TELEGRAM_MODE_CONFIG  path to telegram yaml
  AICODEBOX_CRON_MODE_FILE=path   yaml file for cron mode
  AICODEBOX_MCP_MODE_PORT=8081    port for standalone MCP server
  AICODEBOX_MCP_MODE_TOKEN=...    bearer token for MCP (no fallback)

env (adapter):
  AICODEBOX_ADAPTER               pkg.module:Class for the active agent
  AICODEBOX_AGENT_BINARY          fallback binary for passthrough (default: pi)

env (container):
  AICODEBOX_WORKSPACE             host workspace path (mounted at same path)
  AICODEBOX_CONTAINER_NAME        used for container-scoped state files
EOF
}

# ── 1. UID/GID match to workspace owner ───────────────────────────────────────
AICODE_WORKSPACE="${AICODEBOX_WORKSPACE:-${AICODE_WORKSPACE:-/workspace}}"
AICODE_CONTAINER_NAME="${AICODEBOX_CONTAINER_NAME:-${AICODE_CONTAINER_NAME:-aicodebox}}"

if [ -d "$AICODE_WORKSPACE" ]; then
    HOST_UID=$(stat -c '%u' "$AICODE_WORKSPACE")
    HOST_GID=$(stat -c '%g' "$AICODE_WORKSPACE")
    CURRENT_UID=$(id -u aicode)
    CURRENT_GID=$(id -g aicode)
    if [ "$HOST_UID" != "0" ] && [ "$HOST_GID" != "0" ]; then
        if [ "$HOST_GID" != "$CURRENT_GID" ]; then
            groupmod -g "$HOST_GID" aicode
        fi
        if [ "$HOST_UID" != "$CURRENT_UID" ]; then
            usermod -u "$HOST_UID" aicode
        fi
        find /home/aicode \( ! -user "$HOST_UID" -o ! -group "$HOST_GID" \) -print0 \
            | xargs -0 -r -P "$(( $(nproc) / 2 + 1 ))" chown aicode:aicode 2>/dev/null || true
    fi
fi

# ── 2. docker socket GID ──────────────────────────────────────────────────────
if [ -S /var/run/docker.sock ]; then
    SOCKET_GID=$(stat -c '%g' /var/run/docker.sock)
    CURRENT_DOCKER_GID=$(getent group docker | cut -d: -f3 || echo "")
    if [ -n "$SOCKET_GID" ] && [ -n "$CURRENT_DOCKER_GID" ] \
        && [ "$SOCKET_GID" != "$CURRENT_DOCKER_GID" ]; then
        groupmod -g "$SOCKET_GID" docker
    fi
fi

# ── 3. first-run init.d ───────────────────────────────────────────────────────
INIT_DIR="/aicodebox-init.d"
INIT_MARKER="/home/aicode/.aicodebox/.init-done"
if [ -d "$INIT_DIR" ] && [ ! -f "$INIT_MARKER" ]; then
    AICODE_STATE_DIR="$(dirname "$INIT_MARKER")"
    mkdir -p "$AICODE_STATE_DIR"
    chown aicode:aicode "$AICODE_STATE_DIR"
    for script in "$INIT_DIR"/*.sh; do
        [ -f "$script" ] || continue
        dbg "running init script: $script"
        sudo -E -u aicode -H bash "$script" || echo "[entrypoint] init script $script failed" >&2
    done
    touch "$INIT_MARKER"
    chown aicode:aicode "$INIT_MARKER"
fi

# ── 4. load persisted auth env ────────────────────────────────────────────────
AUTH_FILE="/home/aicode/.aicodebox/.${AICODE_CONTAINER_NAME}-auth"
if [ -f "$AUTH_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    . "$AUTH_FILE"
    set +a
fi

# ── 5. decide what to run ─────────────────────────────────────────────────────
#
# Foreground modes (mutually exclusive, priority order): API > Telegram > Cron.
# Telegram + Cron is the one allowed pair — cron runs in-thread inside telegram.
# MCP mode is independent — it can coexist with any foreground mode, served on
# its own port. In API mode the MCP surface is mounted at /mcp on the API port
# (no extra process). In other modes (telegram/cron/passthrough), MCP runs as
# a background uvicorn process spawned before the foreground exec.
_mode_module=""
if [ "${AICODEBOX_API_MODE:-}" = "1" ]; then
    _mode_module="aicodebox.modes.api"
elif [ "${AICODEBOX_TELEGRAM_MODE:-}" = "1" ] && [ "${AICODEBOX_CRON_MODE:-}" = "1" ]; then
    _mode_module="aicodebox.modes.telegram"
elif [ "${AICODEBOX_TELEGRAM_MODE:-}" = "1" ]; then
    _mode_module="aicodebox.modes.telegram"
elif [ "${AICODEBOX_CRON_MODE:-}" = "1" ]; then
    _mode_module="aicodebox.modes.cron"
fi

# MCP standalone runs as a sidecar process unless the foreground IS the API
# (then /mcp is mounted in-process).
_spawn_mcp_sidecar=0
if [ "${AICODEBOX_MCP_MODE:-}" = "1" ] && [ "$_mode_module" != "aicodebox.modes.api" ]; then
    _spawn_mcp_sidecar=1
fi

# ── 6. build env exports & dispatch ───────────────────────────────────────────
AICODE_UID=$(id -u aicode)
AICODE_GID=$(id -g aicode)

ENV_EXPORTS="export HOME=/home/aicode"
ENV_EXPORTS="$ENV_EXPORTS; export PATH=/home/aicode/.local/bin:/usr/local/bin:/usr/bin:/bin"

# Forward auth-relevant env vars verbatim (the adapter decides which it needs).
for var in ANTHROPIC_API_KEY ANTHROPIC_AUTH_TOKEN ANTHROPIC_OAUTH_TOKEN \
           ANTHROPIC_BASE_URL ANTHROPIC_MODEL CLAUDE_CODE_OAUTH_TOKEN \
           OPENAI_API_KEY OPENAI_BASE_URL OPENAI_ORG_ID \
           GEMINI_API_KEY OPENROUTER_API_KEY ZAI_API_KEY \
           GROQ_API_KEY DEEPSEEK_API_KEY XAI_API_KEY MISTRAL_API_KEY; do
    val="$(printenv "$var" 2>/dev/null || true)"
    if [ -n "$val" ]; then
        ENV_EXPORTS="$ENV_EXPORTS; export $var=$(printf '%q' "$val")"
    fi
done

# aicodebox-specific env (modes + adapter selection).
for var in AICODEBOX_ADAPTER AICODEBOX_AGENT_BINARY AICODEBOX_WORKSPACE \
           AICODEBOX_CONTAINER_NAME \
           AICODEBOX_AVAILABLE_MODELS AICODEBOX_AVAILABLE_EFFORTS \
           AICODEBOX_API_MODE AICODEBOX_API_MODE_PORT AICODEBOX_API_MODE_TOKEN \
           AICODEBOX_TELEGRAM_MODE AICODEBOX_TELEGRAM_MODE_TOKEN \
           AICODEBOX_TELEGRAM_MODE_CONFIG AICODEBOX_TELEGRAM_MODE_OVERRIDES \
           AICODEBOX_CRON_MODE AICODEBOX_CRON_MODE_FILE AICODEBOX_CRON_MODE_HISTORY_DIR \
           AICODEBOX_MCP_MODE AICODEBOX_MCP_MODE_PORT AICODEBOX_MCP_MODE_TOKEN \
           TELEGRAM_CHAT_ID DEBUG; do
    val="$(printenv "$var" 2>/dev/null || true)"
    if [ -n "$val" ]; then
        ENV_EXPORTS="$ENV_EXPORTS; export $var=$(printf '%q' "$val")"
    fi
done

if [ -n "$_mode_module" ]; then
    _FG_INVOKE="exec python3 -m $_mode_module"
else
    # Fall through to the agent's own CLI for interactive / passthrough use.
    AGENT_BIN="${AICODEBOX_AGENT_BINARY:-pi}"
    ESCAPED="$(printf '%q' "$AGENT_BIN")"
    for a in "$@"; do
        ESCAPED="$ESCAPED $(printf '%q' "$a")"
    done
    _FG_INVOKE="exec $ESCAPED"
fi

# MCP sidecar: spawn as a background subshell that traps its parent's exit so
# the python uvicorn dies with the container. The foreground exec then takes
# over PID 1 and dictates container lifetime.
if [ "$_spawn_mcp_sidecar" = "1" ]; then
    _MCP_INVOKE="python3 -m aicodebox.modes.mcp &"
    PY_INVOKE="$_MCP_INVOKE $_FG_INVOKE"
else
    PY_INVOKE="$_FG_INVOKE"
fi

cd "$AICODE_WORKSPACE" 2>/dev/null || cd /workspace

dbg "$PY_INVOKE"
exec setpriv --reuid="$AICODE_UID" --regid="$AICODE_GID" --init-groups \
    bash -c "$ENV_EXPORTS && cd $(printf '%q' "$AICODE_WORKSPACE") && $PY_INVOKE"
