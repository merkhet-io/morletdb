#!/usr/bin/env bash
# Regression test for enabling probe search while envelopes are being built (two sessions).
#
#   tests/probe_enable_race.sh        (needs the installed extension and pgvector)
#
# Session 1 enables probe search and holds its transaction open; session 2 builds envelopes for new
# samples, which waits for session 1's lock on the envelope table. After session 1 commits and session 2
# finishes, every envelope must have its vector row (probe_backlog = 0). Before the fix, session 2 kept
# the "no probe table" it had looked up before waiting and wrote envelopes without vector rows.
# It drops and recreates the database morlet_probe_race.
set -euo pipefail
PG_CONFIG=${PG_CONFIG:-$HOME/.pgrx/17.11/pgrx-install/bin/pg_config}
BIN=$("$PG_CONFIG" --bindir)
PORT=${PGPORT:-28817}
DB=morlet_probe_race
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
psql -d "$DB" >/dev/null <<'SQL'
CREATE EXTENSION pg_morlet;
CREATE EXTENSION vector;
SELECT morlet_create_stream_collection('public','race');
SELECT morlet_enable_envelopes('race');
SELECT morlet_start_recording('race','r',100,'HHZ','counts','{}',512,256,'zscore','none');
SELECT morlet_append_chunk('race','r',0,ARRAY(SELECT round(100*sin(i/7.0))::float8 FROM generate_series(0,16383) i));
SELECT morlet_build_envelopes('race','r',2048,1024);
SELECT morlet_append_chunk('race','r',16384,ARRAY(SELECT round(100*sin(i/5.0))::float8 FROM generate_series(0,16383) i));
SQL

FIFO=$(mktemp -u); mkfifo "$FIFO"
trap 'rm -f "$FIFO"' EXIT
# Session 1: enable inside an open transaction; commit only when told to.
( { echo "BEGIN; SELECT morlet_enable_envelope_probes('race',2048);"; read -r _ < "$FIFO"; echo "COMMIT;"; } \
  | "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX -d "$DB" >/dev/null ) &
S1=$!
until [ "$(psql -d "$DB" -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$DB' AND state='idle in transaction'")" = 1 ]; do sleep 0.2; done
# Session 2: build envelopes for the new samples; it must wait for session 1's lock.
psql -d "$DB" -c "SELECT morlet_build_envelopes('race','r',2048,1024)" >/dev/null &
S2=$!
until [ "$(psql -d "$DB" -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$DB' AND wait_event_type='Lock'")" = 1 ]; do sleep 0.2; done
echo go > "$FIFO"
wait "$S1"; wait "$S2"

backlog=$(psql -d "$DB" -c "SELECT probe_backlog FROM morlet_collection_status('race') WHERE window_size=2048")
built=$(psql -d "$DB" -c "SELECT count(*) FROM race_envelopes WHERE window_size=2048 AND start_sample>=16384")
echo "envelopes built while waiting: $built, probe_backlog: $backlog"
[ "$built" -gt 0 ] || { echo "FAIL: session 2 built nothing"; exit 1; }
[ "$backlog" = 0 ] || { echo "FAIL: envelopes without vector rows"; exit 1; }
psql -d postgres -c "DROP DATABASE $DB"
echo "probe enable race: ok"
