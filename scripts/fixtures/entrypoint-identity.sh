#!/bin/bash
set -euo pipefail

log() {
	jq -cn --arg time "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" \
		--arg level "$1" --arg file "${BASH_SOURCE[1]##*/}" \
		--argjson line "${BASH_LINENO[0]}" --arg func "${FUNCNAME[1]:-main}" \
		--arg msg "$2" '{time:$time,level:$level,file:$file,line:$line,func:$func,msg:$msg}' >&2
}
trap 'log ERROR "fixture failed case=$identity_case exit=$?"' ERR
readonly identity_case="${1:?case required}"
original_uid="$(id -u aicode)"
original_gid="$(id -g aicode)"
original_docker_gid="$(getent group docker | cut -d: -f3)"
readonly original_uid original_gid original_docker_gid
workspace_uid="$original_uid"
workspace_gid="$original_gid"
socket_gid=""
export AICODEBOX_WORKSPACE=/identity-workspace
export AICODEBOX_AGENT_BINARY=/bin/bash
case "$identity_case" in
unchanged | repeat | regular-socket-path) ;;
uid-only) workspace_uid=31003 ;;
gid-only) workspace_gid=31004 ;;
both | spaced-workspace)
	workspace_uid=31003
	workspace_gid=31004
	;;
gid-collision) workspace_gid=100 ;;
nobody-gid) workspace_gid=65534 ;;
uid-collision) workspace_uid=1 ;;
nobody-collision) workspace_uid=65534 ;;
both-collisions | repeat-collisions)
	workspace_uid=65534
	workspace_gid=100
	;;
root-owner)
	workspace_uid=0
	workspace_gid=0
	;;
root-uid)
	workspace_uid=0
	workspace_gid=31004
	;;
root-gid)
	workspace_uid=31003
	workspace_gid=0
	;;
socket-new) socket_gid=31004 ;;
socket-existing) socket_gid=100 ;;
socket-root) socket_gid=0 ;;
socket-primary)
	workspace_uid=31003
	workspace_gid=31004
	socket_gid=31004
	;;
socket-original-primary) socket_gid="$original_gid" ;;
workspace-docker-gid)
	workspace_gid="$original_docker_gid"
	socket_gid=31004
	;;
missing-workspace) ;;
*)
	log ERROR "Unknown case: $identity_case"
	exit 2
	;;
esac
if [[ "$identity_case" == spaced-workspace ]]; then
	export AICODEBOX_WORKSPACE='/identity workspace'
fi
export EXPECTED_UID="$workspace_uid" EXPECTED_GID="$workspace_gid"
if [[ "$workspace_uid" == 0 || "$workspace_gid" == 0 ]]; then
	export EXPECTED_UID="$original_uid" EXPECTED_GID="$original_gid"
fi
groupadd -g 31005 identity-extra
usermod -aG identity-extra aicode
if [[ "$identity_case" != missing-workspace ]]; then
	install -d -m 0777 -o "$workspace_uid" -g "$workspace_gid" "$AICODEBOX_WORKSPACE"
	if [[ "$workspace_uid" != 0 && "$workspace_gid" != 0 ]]; then
		chmod 0700 "$AICODEBOX_WORKSPACE"
	fi
fi
install -d /aicodebox-init.d
if [[ -n "$socket_gid" ]]; then
	export SOCKET_GID="$socket_gid"
	python3 -c 'import os, socket; s=socket.socket(socket.AF_UNIX); s.bind("/var/run/docker.sock"); os.chown("/var/run/docker.sock", 0, int(os.environ["SOCKET_GID"])); os.chmod("/var/run/docker.sock", 0o660)'
elif [[ "$identity_case" == regular-socket-path ]]; then
	touch /var/run/docker.sock
fi

# Use one probe through both sudo in init.d and setpriv in the final agent.
# shellcheck disable=SC2016 # Expand the probe variables in the runtime user shell.
probe='set -eu
test "$(id -u)" = "$EXPECTED_UID"
test "$(id -g)" = "$EXPECTED_GID"
test "$(id -u)" != 0
test "$HOME" = /home/aicode
for group in sudo docker identity-extra; do
    gid=$(getent group "$group" | cut -d: -f3)
    case " $(id -G) " in *" $gid "*) ;; *) printf "missing group %s\n" "$group" >&2; exit 1;; esac
done
test -w "$HOME"
test -w "$AICODEBOX_WORKSPACE"
touch "$AICODEBOX_WORKSPACE/agent-file" "$HOME/identity-file"
test "$(stat -c %u:%g "$AICODEBOX_WORKSPACE/agent-file")" = "$EXPECTED_UID:$EXPECTED_GID"
if test -S /var/run/docker.sock; then test -w /var/run/docker.sock; fi
test "$(sudo -n id -u)" = 0
printf "identity-probe-ok\n"'
printf '%s\n' '#!/bin/bash' "$probe" >/identity-probe.sh
chmod 0755 /identity-probe.sh
# shellcheck disable=SC2016 # The init hook must resolve the runtime workspace.
printf 'bash /identity-probe.sh > "$AICODEBOX_WORKSPACE/init-result"\n' >/aicodebox-init.d/00-identity.sh

if [[ "$identity_case" == missing-workspace ]]; then
	if bash /usr/local/bin/aicodebox-entrypoint /identity-probe.sh; then
		log ERROR 'Missing workspace unexpectedly succeeded'
		exit 1
	fi
	exit 0
fi
bash /usr/local/bin/aicodebox-entrypoint /identity-probe.sh
test "$(cat "$AICODEBOX_WORKSPACE/init-result")" = identity-probe-ok
test "$(stat -c %u:%g "$AICODEBOX_WORKSPACE/init-result")" = "$EXPECTED_UID:$EXPECTED_GID"
test "$(stat -c %u:%g /home/aicode/identity-file)" = "$EXPECTED_UID:$EXPECTED_GID"
if [[ "$identity_case" == repeat || "$identity_case" == repeat-collisions ]]; then
	bash /usr/local/bin/aicodebox-entrypoint /identity-probe.sh
fi
