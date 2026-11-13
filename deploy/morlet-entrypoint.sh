#!/bin/sh
# MorletDB image entrypoint: sizes shared_buffers to a small container memory limit, then hands over to the stock
# PostgreSQL entrypoint unchanged (#466).
#
# PostgreSQL's default shared_buffers (128 MB) is half of a 256 MB limit. Together with dirty page cache after a
# bulk load, a backend could then be killed by the kernel's out-of-memory killer (Study M1d; #466). When the
# container has a memory limit under 512 MB and nobody has chosen shared_buffers, the server starts with a quarter
# of the limit (at least 16 MB). At 512 MB and above, and without a limit, nothing changes.
#
# shared_buffers counts as chosen when it is passed on the command line, set with ALTER SYSTEM
# (postgresql.auto.conf), or set in postgresql.conf to anything but initdb's 128MB. MORLET_AUTO_SHARED_BUFFERS=off
# turns the sizing off.
set -eu

memory_limit() {
	if [ -r /sys/fs/cgroup/memory.max ]; then                     # cgroup v2: a number or "max"
		cat /sys/fs/cgroup/memory.max
	elif [ -r /sys/fs/cgroup/memory/memory.limit_in_bytes ]; then  # cgroup v1: a huge number when unlimited
		cat /sys/fs/cgroup/memory/memory.limit_in_bytes
	else
		echo max
	fi
}

chosen() {
	case " $* " in *shared_buffers*) return 0 ;; esac
	d=${PGDATA:-/var/lib/postgresql/data}
	if [ -f "$d/postgresql.auto.conf" ] && grep -Eq '^[[:space:]]*shared_buffers[[:space:]]*=' "$d/postgresql.auto.conf"; then
		return 0
	fi
	if [ -f "$d/postgresql.conf" ] && grep -E '^[[:space:]]*shared_buffers[[:space:]]*=' "$d/postgresql.conf" |
		grep -Evq "=[[:space:]]*'?128MB'?([[:space:]]|#|$)"; then
		return 0
	fi
	return 1
}

# Like the stock entrypoint: arguments that start with "-" are options for postgres.
case "${1:-}" in -*) set -- postgres "$@" ;; esac

if [ "${1:-}" = postgres ] && [ "${MORLET_AUTO_SHARED_BUFFERS:-on}" != off ] && ! chosen "$@"; then
	limit=$(memory_limit)
	case "$limit" in
	'' | *[!0-9]*) ;;   # "max" or unreadable: no limit
	*)
		if [ "$limit" -lt 536870912 ]; then
			mb=$((limit / 4 / 1048576))
			[ "$mb" -ge 16 ] || mb=16
			echo "morletdb: container memory limit $((limit / 1048576)) MB: starting with shared_buffers=${mb}MB" \
				"(set shared_buffers or MORLET_AUTO_SHARED_BUFFERS=off to choose yourself)" >&2
			set -- "$@" -c "shared_buffers=${mb}MB"
		fi
		;;
	esac
fi

exec docker-entrypoint.sh "$@"
