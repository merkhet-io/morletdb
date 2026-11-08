#!/usr/bin/env bash
# Extensions on demand (#315): pg_morlet loads from a directory outside the server's installation, as from an image
# volume, through extension_control_path (control and SQL files) and dynamic_library_path (the library). PostgreSQL
# 18 and later.
#
#   tests/on_demand.sh        (needs cargo-pgrx and the server of pg_env.sh running)
#
# Packages the extension into a temporary <dir>/share/extension and <dir>/lib, moves the server's own pg_morlet files
# aside for the test (restored on exit), then checks that CREATE EXTENSION fails without the paths and works with
# them: the extension version, an append, a read, interval statistics, and that no C function names $libdir.
# It drops and recreates the database morlet_on_demand.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
[ "$PG_MAJOR" -ge 18 ] || { echo "skip: extension_control_path needs PostgreSQL 18 or later (this is $PG_MAJOR)"; exit 0; }
EXT=$(cd "$(dirname "$0")/.." && pwd)
DB=morlet_on_demand
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }
SHARE=$("$PG_CONFIG" --sharedir)/extension
LIB=$("$PG_CONFIG" --pkglibdir)
VERSION=$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$EXT/pg_morlet.control")

WORK=$(mktemp -d)
restore() {
  if [ -d "$WORK/aside" ]; then
    find "$WORK/aside" -maxdepth 1 -name 'pg_morlet*.sql' -exec mv {} "$SHARE/" \;
    [ -f "$WORK/aside/pg_morlet.control" ] && mv "$WORK/aside/pg_morlet.control" "$SHARE/"
    find "$WORK/aside" -maxdepth 1 -name 'pg_morlet.*' ! -name '*.sql' ! -name '*.control' -exec mv {} "$LIB/" \;
  fi
  rm -rf "$WORK"
}
trap restore EXIT

echo "== packaging pg_morlet $VERSION into a separate directory"
(cd "$EXT" && cargo pgrx package --pg-config "$PG_CONFIG" --out-dir "$WORK/pkg" >/dev/null)
MNT=$WORK/mnt
mkdir -p "$MNT/share/extension" "$MNT/lib" "$WORK/aside"
cp "$WORK/pkg$SHARE"/pg_morlet* "$MNT/share/extension/"
cp "$WORK/pkg$LIB"/pg_morlet.* "$MNT/lib/"
# The server's own copy goes aside, so only the separate directory can provide the extension.
mv "$SHARE"/pg_morlet* "$LIB"/pg_morlet.* "$WORK/aside/"

fail=0
expect() {  # expect NAME GOT WANTED
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$3', got '$2'"; fail=1; fi
}

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
if psql -d "$DB" -c "CREATE EXTENSION pg_morlet" 2>/dev/null; then
  echo "FAIL without paths: CREATE EXTENSION found pg_morlet in the server's own directories"; fail=1
else
  echo "ok   without paths: pg_morlet is not available"
fi

out=$(psql -d "$DB" <<SQL
SET extension_control_path = '\$system:$MNT/share';
SET dynamic_library_path = '\$libdir:$MNT/lib';
CREATE EXTENSION pg_morlet;
SELECT extversion FROM pg_extension WHERE extname = 'pg_morlet';
SELECT morlet_create_stream_collection('public','od');
SELECT morlet_start_recording('od','A',4096,'vibration','counts','{}');
SELECT morlet_append_chunk('od','A',0,ARRAY(SELECT (i % 97) - 48 FROM generate_series(0,9999) i)::float8[]);
SELECT array_to_string((morlet_read_interval('od','A',5000,5004)).samples, ',');
SELECT (morlet_interval_stats('od','A',0,10000)).max;
SELECT count(*) FROM pg_proc WHERE probin LIKE '%\$libdir%morlet%';
SELECT count(*) FROM pg_proc WHERE probin = 'pg_morlet';
SQL
)
r=(); while IFS= read -r line; do r+=("$line"); done <<<"$out"
expect "with paths: CREATE EXTENSION installs $VERSION" "${r[0]}" "$VERSION"
expect "append stores 10,000 samples" "${r[3]}" "10000"
expect "read returns the stored samples" "${r[4]}" "5,6,7,8"
expect "interval statistics" "${r[5]}" "48"
expect "no C function names \$libdir" "${r[6]}" "0"
if [ "${r[7]}" -gt 0 ]; then
  echo "ok   ${r[7]} C functions load pg_morlet through dynamic_library_path"
else
  echo "FAIL no C function names pg_morlet"; fail=1
fi

psql -d postgres -c "DROP DATABASE $DB"
[ "$fail" -eq 0 ] || exit 1
echo "on demand: PASS"
