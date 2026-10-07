#!/bin/bash
# Exercises the entrypoint's user hooks against a built image:
#  - $HOME/.aicodebox/bin is on PATH for the agent, for `docker exec` shells,
#    and for init scripts
#  - /aicodebox-init.d/*.sh runs before $HOME/.aicodebox/init.d/*.sh
#  - init runs once per container: not again on restart, again in a new
#    container that mounts the same state dir
#  - a failing init script is reported and does not stop the rest
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

readonly IMAGE="${IMAGE:-psyb0t/aicodebox:latest}"
readonly PREFIX="aicodebox-hooks-test-$$"
readonly STATE_VOLUME="${PREFIX}-state"
readonly IMAGE_INIT_VOLUME="${PREFIX}-image-init"
readonly STATE_DIR="/home/aicode/.aicodebox"
readonly PROBE="aicodebox-hook-probe"
readonly PROBE_OUTPUT="probe-ok"

failures=0

cleanup() {
	local name
	for name in "${PREFIX}-first" "${PREFIX}-second" "${PREFIX}-exec"; do
		docker rm -f "$name" >/dev/null 2>&1 || true # intentional: absent when its case failed early
	done
	docker volume rm -f "$STATE_VOLUME" "$IMAGE_INIT_VOLUME" >/dev/null 2>&1 || true # intentional: same
}
trap cleanup EXIT

expect_equal() {
	local description="$1"
	local expected="$2"
	local actual="$3"
	if [[ "$expected" == "$actual" ]]; then
		log INFO "pass: $description"
		return 0
	fi
	log ERROR "fail: $description expected=[$expected] actual=[$actual]"
	failures=$((failures + 1))
}

# Runs a shell command as root against the state and image-init volumes,
# bypassing the entrypoint, to seed or read them.
volume_shell() {
	docker run --rm \
		-v "${STATE_VOLUME}:/state" \
		-v "${IMAGE_INIT_VOLUME}:/image-init" \
		--entrypoint bash "$IMAGE" -c "$1"
}

run_agent() {
	local name="$1"
	shift
	docker run --name "$name" \
		-v "${STATE_VOLUME}:${STATE_DIR}" \
		-v "${IMAGE_INIT_VOLUME}:/aicodebox-init.d" \
		-e AICODEBOX_AGENT_BINARY=/bin/bash \
		"$IMAGE" "$@"
}

if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
	log ERROR "image not found: $IMAGE"
	exit 1
fi

log INFO "seeding state dir and image init dir"
volume_shell "
	set -euo pipefail
	mkdir -p /state/bin /state/init.d
	printf '#!/bin/sh\necho ${PROBE_OUTPUT}\n' > /state/bin/${PROBE}
	chmod +x /state/bin/${PROBE}
	printf 'echo image >> \"\$HOME/.aicodebox/init.log\"\n' > /image-init/10-image.sh
	printf 'exit 1\n' > /state/init.d/05-fails.sh
	printf 'echo \"user \$(id -un) \$(${PROBE})\" >> \"\$HOME/.aicodebox/init.log\"\n' > /state/init.d/10-user.sh
"

log INFO "case: the agent finds scripts in the state dir's bin"
# A missing probe exits 127; record it as a failed case so every case reports.
if ! first_output="$(run_agent "${PREFIX}-first" -c "${PROBE}" 2>"/tmp/${PREFIX}-first.err")"; then
	first_output=""
fi
expect_equal "agent PATH includes ${STATE_DIR}/bin" "$PROBE_OUTPUT" "$first_output"

log INFO "case: image init runs before user init, a failing script does not stop the rest"
expect_equal "init log after the first container" \
	"$(printf 'image\nuser aicode %s' "$PROBE_OUTPUT")" \
	"$(volume_shell 'cat /state/init.log')"
if grep -q "init script ${STATE_DIR}/init.d/05-fails.sh failed" "/tmp/${PREFIX}-first.err"; then
	log INFO "pass: failing init script is reported"
else
	log ERROR "fail: failing init script was not reported"
	failures=$((failures + 1))
fi
rm -f "/tmp/${PREFIX}-first.err"

log INFO "case: restarting the same container does not run init again"
docker start -a "${PREFIX}-first" >/dev/null 2>&1 || true # intentional: its exit code is the probe's, checked above
expect_equal "init log after a restart" "2" "$(volume_shell 'wc -l < /state/init.log')"

log INFO "case: a new container on the same state dir runs init again"
run_agent "${PREFIX}-second" -c true >/dev/null 2>&1
expect_equal "init log after a second container" "4" "$(volume_shell 'wc -l < /state/init.log')"

log INFO "case: docker exec shells find scripts in the state dir's bin"
docker run -d --name "${PREFIX}-exec" \
	-v "${STATE_VOLUME}:${STATE_DIR}" \
	-e AICODEBOX_AGENT_BINARY=sleep \
	"$IMAGE" infinity >/dev/null
exec_output="$(docker exec -u aicode "${PREFIX}-exec" "$PROBE" 2>/dev/null)" || exec_output="" # intentional: a missing probe is the failure under test
expect_equal "docker exec PATH includes ${STATE_DIR}/bin" "$PROBE_OUTPUT" "$exec_output"

if ((failures > 0)); then
	log ERROR "entrypoint hook contract failed failures=$failures"
	exit 1
fi
log INFO "entrypoint hook contract passed"
