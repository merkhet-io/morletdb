#!/usr/bin/env bash
# Regression test for concurrent context writes (two sessions, READ COMMITTED).
#
#   tests/context_concurrency.sh        (needs the installed extension)
#
# Session 1 adds context and holds its transaction open; session 2 adds context to the same recording.
# Overlapping intervals of one key must not both commit, and an identical batch retried concurrently must be
# stored once. Before the fix, both sessions' checks ran before either committed and both succeeded.
# It drops and recreates the database morlet_ctx_race.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
DB=morlet_ctx_race
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
psql -d "$DB" >/dev/null <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public','sig');
SELECT morlet_enable_context('sig');
SELECT morlet_start_recording('sig','r',1000,'x','g');
SQL

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
fail=0
# race SQL1 SQL2: session 1 runs SQL1 and holds its transaction for 3 s; session 2 starts SQL2 after 1 s.
race() {
  psql -d "$DB" -c "BEGIN" -c "$1" -c "SELECT pg_sleep(3)" -c "COMMIT" >"$WORK/a" 2>&1 &
  sleep 1
  psql -d "$DB" -c "$2" >"$WORK/b" 2>&1 || true
  wait
}
expect() {  # expect NAME PATTERN OUTPUT
  if grep -q -- "$2" <<<"$3"; then echo "ok   $1"; else echo "FAIL $1: expected /$2/, got: $3"; fail=1; fi
}
row() { echo "SELECT morlet_add_context('sig','$1','[{\"recording_id\":\"r\",\"key\":\"$2\",\"value\":1,\"start_sample\":$3${4:-}}]')"; }

race "$(row A mode 0 ',"end_sample":100')" "$(row B mode 50 ',"end_sample":150')"
expect "first overlapping interval commits" '^1$' "$(cat "$WORK/a")"
expect "second overlapping interval is rejected" 'overlapping context intervals for key mode' "$(cat "$WORK/b")"
expect "one interval stored" '^1$' "$(psql -d "$DB" -c "SELECT count(*) FROM sig_ctx WHERE key='mode'")"

race "$(row dup ev 10)" "$(row dup ev 10)"
expect "identical concurrent retry returns 0" '^0$' "$(cat "$WORK/b")"
expect "batch stored once" '^1$' "$(psql -d "$DB" -c "SELECT count(*) FROM sig_ctx WHERE batch_id='dup'")"

race "$(row conf ev 20)" "$(row conf ev 21)"
expect "conflicting concurrent retry is an error" 'already exists with different rows' "$(cat "$WORK/b")"

race "SELECT morlet_revise_context('sig',(SELECT ctx_id FROM sig_ctx WHERE batch_id='A'),'{\"end_sample\":300}')" \
  "$(row C mode 200 ',"end_sample":250')"
expect "revision and addition do not both commit an overlap" 'overlapping context intervals' "$(cat "$WORK/b")"

psql -d postgres -c "DROP DATABASE $DB" >/dev/null
exit $fail
