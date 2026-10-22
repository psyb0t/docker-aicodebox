#!/bin/bash
set -euo pipefail

log() {
	jq -cn --arg time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
		--arg level "$1" --arg file "${BASH_SOURCE[1]##*/}" \
		--argjson line "${BASH_LINENO[0]}" --arg func "${FUNCNAME[1]:-main}" \
		--arg msg "$2" '{time:$time,level:$level,file:$file,line:$line,func:$func,msg:$msg}' >&2
}
trap 'log ERROR "command failed exit=$?"' ERR
readonly IMAGE="${IMAGE:-psyb0t/aicodebox:latest}"
readonly PREFIX="aicodebox-identity-test-$$"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
readonly ENTRYPOINT="${ENTRYPOINT:-}"
readonly LOG_FILE="${TEST_LOG_FILE:-/tmp/${PREFIX}.log}"
exec > >(tee "$LOG_FILE") 2>&1

if (($#)); then
	printf 'Usage: IMAGE=image [ENTRYPOINT=absolute-path] %s\n' "$0" >&2
	exit 2
fi
docker image inspect "$IMAGE" >/dev/null
mounts=(--mount "type=bind,src=$SCRIPT_DIR/fixtures/entrypoint-identity.sh,dst=/identity-fixture.sh,readonly")
if [[ -n "$ENTRYPOINT" ]]; then
	[[ "$ENTRYPOINT" = /* && -f "$ENTRYPOINT" ]] || exit 2
	mounts+=(--mount "type=bind,src=$ENTRYPOINT,dst=/usr/local/bin/aicodebox-entrypoint,readonly")
fi

failures=0
cases=(unchanged uid-only gid-only both gid-collision nobody-gid uid-collision nobody-collision
	both-collisions root-owner root-uid root-gid socket-new socket-existing
	socket-root socket-primary socket-original-primary workspace-docker-gid
	repeat repeat-collisions regular-socket-path spaced-workspace missing-workspace)
for identity_case in "${cases[@]}"; do
	if docker run --rm --name "${PREFIX}-${identity_case}" --network none \
		--hostname identity-test --add-host identity-test:127.0.0.1 \
		"${mounts[@]}" --entrypoint bash "$IMAGE" \
		/identity-fixture.sh "$identity_case"; then
		log INFO "PASS $identity_case"
	else
		log ERROR "FAIL $identity_case"
		failures=$((failures + 1))
	fi
done
log INFO "Identity cases: ${#cases[@]} total, $failures failed"
((failures == 0))
