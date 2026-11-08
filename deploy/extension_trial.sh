#!/usr/bin/env bash
# Trial of the extension-only image (#315): mount its files into a clean official postgres image, as an image volume
# would, load pg_morlet through extension_control_path and dynamic_library_path, and check it before and after a
# restart. Used by the release workflow before the image is pushed.
#
#   deploy/extension_trial.sh IMAGE PG_MAJOR [PLATFORM]
#
# Fails if the postgres image already has pg_morlet, if CREATE EXTENSION does not install the version of the
# image's control file, or if an append, a read or interval statistics give a wrong answer.
set -euo pipefail
IMAGE=$1 PG_MAJOR=$2 PLATFORM=${3:-}
NAME=pg-morlet-extension-trial
WORK=$(mktemp -d)
cleanup() { docker rm -f "$NAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT
plat=(); [ -n "$PLATFORM" ] && plat=(--platform "$PLATFORM")

echo "== files of $IMAGE"
cid=$(docker create ${plat[@]+"${plat[@]}"} "$IMAGE" /none)
mkdir -p "$WORK/pg_morlet"
docker cp "$cid:/share" "$WORK/pg_morlet/"
docker cp "$cid:/lib" "$WORK/pg_morlet/"
docker rm "$cid" >/dev/null
(cd "$WORK/pg_morlet" && find . -type f | sort)
VERSION=$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$WORK/pg_morlet/share/extension/pg_morlet.control")
test -n "$VERSION"
test -n "$(find "$WORK/pg_morlet/lib" -name 'pg_morlet.*' -print -quit)"
grep -q "^module_pathname = 'pg_morlet'" "$WORK/pg_morlet/share/extension/pg_morlet.control" \
  || { echo "module_pathname still pins \$libdir: the library cannot load from the mounted directory" >&2; exit 1; }
chmod -R a+rX "$WORK"

echo "== postgres:$PG_MAJOR-bookworm with the files mounted at /extensions/pg_morlet"
docker run -d ${plat[@]+"${plat[@]}"} --name "$NAME" -e POSTGRES_PASSWORD=trial \
  -v "$WORK/pg_morlet:/extensions/pg_morlet:ro" "postgres:$PG_MAJOR-bookworm" \
  -c 'extension_control_path=$system:/extensions/pg_morlet/share' \
  -c 'dynamic_library_path=$libdir:/extensions/pg_morlet/lib' >/dev/null
ready() { for _ in $(seq 60); do docker exec "$NAME" pg_isready -U postgres -q && return 0; sleep 2; done; docker logs "$NAME"; return 1; }
ready; sleep 2; ready
q() { docker exec -i "$NAME" psql -U postgres -v ON_ERROR_STOP=1 -qAtX "$@"; }
docker exec "$NAME" sh -c "! ls /usr/share/postgresql/$PG_MAJOR/extension/pg_morlet* /usr/lib/postgresql/$PG_MAJOR/lib/pg_morlet* 2>/dev/null" \
  || { echo "the postgres image already has pg_morlet: the trial would not test the mounted files" >&2; exit 1; }

out=$(q <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet';
SELECT morlet_create_stream_collection('public','trial');
SELECT morlet_start_recording('trial','A',4096,'vibration','counts','{}');
SELECT morlet_append_chunk('trial','A',0,ARRAY(SELECT (i % 97) - 48 FROM generate_series(0,9999) i)::float8[]);
SELECT array_to_string((morlet_read_interval('trial','A',5000,5004)).samples, ',');
SELECT (morlet_interval_stats('trial','A',0,10000)).max;
SQL
)
expected=$(printf '%s\n' "$VERSION" trial "" 10000 "5,6,7,8" 48)
[ "$out" = "$expected" ] || { echo "unexpected results:"; echo "$out"; exit 1; }
echo "ok: CREATE EXTENSION installed $VERSION from the mounted files; append, read and statistics correct"

docker restart "$NAME" >/dev/null; ready; sleep 2; ready
after=$(q -c "SELECT array_to_string((morlet_read_interval('trial','A',5000,5004)).samples, ',')")
[ "$after" = "5,6,7,8" ] || { echo "after restart: $after"; exit 1; }
echo "ok: after a restart the library loads from the mounted files and reads are identical"
echo "extension trial: PASS ($IMAGE, PostgreSQL $PG_MAJOR)"
