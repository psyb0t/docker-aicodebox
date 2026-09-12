#!/bin/bash
set -euo pipefail
trap 'log ERROR "command failed exit=$?"' ERR

SCRIPT_NAME="$(basename "$0")"
readonly SCRIPT_NAME
readonly LOG_FILE="${TEST_LOG_FILE:-/tmp/${SCRIPT_NAME%.sh}.log}"
exec > >(tee -a "$LOG_FILE") 2>&1

log() {
	local level="$1"
	shift
	local timestamp
	timestamp="$(date -u '+%Y-%m-%dT%H:%M:%S.%3NZ')"
	printf '{"time":"%s","level":"%s","file":"%s","line":%d,"func":"%s","msg":"%s"}\n' \
		"$timestamp" "$level" "$SCRIPT_NAME" "${BASH_LINENO[0]}" "${FUNCNAME[1]:-main}" "$*" >&2
}

readonly IMAGE="${IMAGE:-psyb0t/aicodebox:latest-full}"

if [[ "${DEBUG:-}" == "true" ]]; then
	log DEBUG "checking full image"
fi

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
	log ERROR "image not found"
	exit 1
fi

log INFO "starting full image command contract"
docker run --rm -e AICODEBOX_AGENT_BINARY=/bin/bash "$IMAGE" -lc '
    set -euo pipefail

    [[ "${AICODEBOX_IMAGE_VARIANT:-}" == "full" ]]
    [[ "$(id -un)" == "aicode" ]]
    [[ "$(go version)" == *"go1.26.7"* ]]
    [[ "$(python --version 2>&1)" == "Python 3.14.7" ]]
    python -c "import aicodebox"

    tools=(
        go gofmt golangci-lint gopls dlv staticcheck gomodifytags impl gotests gofumpt
        python pip pytest black flake8 isort autoflake pyright mypy vulture pipenv poetry
        node npm eslint prettier tsc ts-node yarn pnpm nodemon pm2 create-react-app vue ng express newman http-server serve lighthouse storybook
        gh terraform kubectl helm
        make cmake nano vim htop tmux zip unzip
        ping dig tree fdfind rg batcat eza ag shellcheck shfmt http
        clang-format valgrind gdb strace ltrace
        sqlite3 psql mysql redis-cli
    )

    for tool in "${tools[@]}"; do
        command -v "$tool" >/dev/null || {
            printf "missing full-image tool: %s\n" "$tool" >&2
            exit 1
        }
    done

    python -c "import pytest_cov"
    pytest --help | grep -F -- "--cov" >/dev/null
'
log INFO "full image command contract passed"
