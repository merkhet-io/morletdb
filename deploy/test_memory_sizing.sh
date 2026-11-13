#!/usr/bin/env bash
# Checks the image's shared_buffers sizing (deploy/morlet-entrypoint.sh, #466) for every case it promises:
# limits below, at and above 512 MB, no limit, a value chosen on the command line, with ALTER SYSTEM (kept across a
# restart), and the off switch. Usage: MORLET_IMAGE=<image> deploy/test_memory_sizing.sh
set -euo pipefail
IMAGE=${MORLET_IMAGE:?Set MORLET_IMAGE to the image under test}
N=morlet-memtest-$$
fail=0
cleanup() { docker rm -f "$N" >/dev/null 2>&1 || true; docker volume rm -f "$N" >/dev/null 2>&1 || true; }
trap cleanup EXIT

wait_ready() {
	for _ in $(seq 1 120); do
		if docker exec "$N" pg_isready -U postgres -h 127.0.0.1 -q 2>/dev/null &&
			docker exec "$N" psql -U postgres -h 127.0.0.1 -Atqc 'SELECT 1' >/dev/null 2>&1; then
			return 0
		fi
		sleep 0.5
	done
	docker logs "$N" | tail -20
	return 1
}

value() { docker exec "$N" psql -U postgres -h 127.0.0.1 -Atqc 'SHOW shared_buffers'; }

check() {   # check <name> <expected> <docker run options...> -- <command...>
	local name=$1 want=$2; shift 2
	local opts=() cmd=()
	while [ $# -gt 0 ] && [ "$1" != -- ]; do opts+=("$1"); shift; done
	[ $# -gt 0 ] && shift
	cmd=("$@")
	cleanup
	docker run -d --name "$N" -e POSTGRES_PASSWORD=test -v "$N:/var/lib/postgresql/data" \
		${opts[@]+"${opts[@]}"} "$IMAGE" ${cmd[@]+"${cmd[@]}"} >/dev/null
	wait_ready
	report "$name" "$want" "$(value)"
}

report() {
	if [ "$3" = "$2" ]; then echo "ok   $1: $3"; else echo "FAIL $1: $3, expected $2"; fail=1; fi
}

check "256 MB limit"            64MB  --memory 256m --memory-swap 256m --
check "128 MB limit"            32MB  --memory 128m --memory-swap 128m --
check "96 MB limit"             24MB  --memory 96m --memory-swap 96m --
check "511 MB limit"            127MB --memory 511m --memory-swap 511m --
check "512 MB limit"            128MB --memory 512m --memory-swap 512m --
check "no limit"                128MB --
check "command line wins"       32MB  --memory 256m --memory-swap 256m -- postgres -c shared_buffers=32MB
check "options only"            64MB  --memory 256m --memory-swap 256m -- -c work_mem=8MB
check "off switch"              128MB --memory 256m --memory-swap 256m -e MORLET_AUTO_SHARED_BUFFERS=off --

# ALTER SYSTEM is kept across a restart under a small limit.
check "before ALTER SYSTEM"     64MB  --memory 256m --memory-swap 256m --
docker exec "$N" psql -U postgres -h 127.0.0.1 -qc "ALTER SYSTEM SET shared_buffers = '48MB'"
docker restart "$N" >/dev/null
wait_ready
report "ALTER SYSTEM kept" 48MB "$(value)"

# The extension still loads under the sizing.
if docker exec "$N" psql -U postgres -h 127.0.0.1 -Atqc 'CREATE EXTENSION pg_morlet; SELECT 1' >/dev/null; then
	echo "ok   CREATE EXTENSION pg_morlet"
else
	echo "FAIL CREATE EXTENSION pg_morlet"; fail=1
fi

exit $fail
