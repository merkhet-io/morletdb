#!/usr/bin/env bash
# Regression tests for the SQL 0.3.4 fixes (two sessions where a race is involved, READ COMMITTED):
#   #227  concurrent identical acquisition creation and channel membership succeed idempotently;
#         conflicting attributes are still refused
#   #225  concurrent envelope builders with different strides cannot both commit; identical ones leave one grid
#   #226  morlet_search_similar keeps the caller's max_candidates on the probe route (small-gallery fallback,
#         invalid values, and the approximate route's own candidate count)
#
#   tests/sql034_fixes.sh        (needs the installed extension and pgvector)
# It drops and recreates the database morlet_sql034.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
DB=morlet_sql034
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
psql -d "$DB" >/dev/null <<'SQL'
CREATE EXTENSION pg_morlet;
CREATE EXTENSION vector;
SELECT morlet_create_stream_collection('public','s');
SELECT morlet_enable_acquisitions('s');
SELECT morlet_enable_envelopes('s');
SELECT morlet_start_recording('s',r,100,'HHZ','counts','{}',512,256,'zscore','none') FROM unnest(ARRAY['r1','r2','r3']) r;
SELECT morlet_append_chunk('s',r,0,ARRAY(SELECT round(100*sin(i/7.0+k))::float8 FROM generate_series(0,8191) i))
  FROM unnest(ARRAY['r1','r2','r3']) WITH ORDINALITY u(r,k);
SQL

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
fail=0
# race SQL1 SQL2: session 1 runs SQL1 and holds its transaction for 2 s; session 2 starts SQL2 after 0.7 s.
race() {
  psql -d "$DB" -c "BEGIN" -c "$1" -c "SELECT pg_sleep(2)" -c "COMMIT" >"$WORK/a" 2>&1 &
  sleep 0.7
  psql -d "$DB" -c "$2" >"$WORK/b" 2>&1 || true
  wait
}
expect() {  # expect NAME PATTERN OUTPUT
  if grep -q -- "$2" <<<"$3"; then echo "ok   $1"; else echo "FAIL $1: expected /$2/, got: $3"; fail=1; fi
}

# --- #227 acquisitions
acq="SELECT morlet_create_acquisition('s','a1','M1')"
race "$acq" "$acq"
expect "first creation returns true" '^t$' "$(cat "$WORK/a")"
expect "concurrent identical creation returns false" '^f$' "$(cat "$WORK/b")"
race "SELECT morlet_create_acquisition('s','a2','M1')" "SELECT morlet_create_acquisition('s','a2','M2')"
expect "concurrent conflicting creation is refused" 'already exists with different attributes' "$(cat "$WORK/b")"
ch="SELECT morlet_add_acquisition_channel('s','a1','r1','vib')"
race "$ch" "$ch"
expect "concurrent identical membership returns false" '^f$' "$(cat "$WORK/b")"
race "SELECT morlet_add_acquisition_channel('s','a1','r2','cur')" "SELECT morlet_add_acquisition_channel('s','a1','r3','cur')"
expect "role taken concurrently is refused" 'already taken' "$(cat "$WORK/b")"
expect "one channel per role" '^1$' "$(psql -d "$DB" -c "SELECT count(*) FROM s_acq_channels WHERE acquisition_id='a1' AND role='cur'")"

# --- #225 envelope builders
race "SELECT morlet_build_envelopes('s','r1',512,512)" "SELECT morlet_build_envelopes('s','r1',512,300)"
expect "incompatible concurrent stride is refused" 'were built with stride 512, not 300' "$(cat "$WORK/b")"
expect "grid has one stride" '^0$' "$(psql -d "$DB" -c "SELECT count(*) FROM s_envelopes WHERE recording_id='r1' AND window_size=512 AND start_sample%512<>0")"
race "SELECT morlet_build_envelopes('s','r2',512,256)" "SELECT morlet_build_envelopes('s','r2',512,256)"
expect "identical concurrent build: second has nothing left" '^0$' "$(cat "$WORK/b")"
expect "identical concurrent build: one complete grid" '^31$' "$(psql -d "$DB" -c "SELECT count(*) FROM s_envelopes WHERE recording_id='r2' AND window_size=512")"

# --- #226 budgets through the probe route
psql -d "$DB" >/dev/null -c "SELECT morlet_build_envelopes('s','r3',512,256)" \
  -c "SELECT morlet_enable_envelope_probes('s',512)"
q() { psql -d "$DB" -c "$1" 2>&1 || true; }
expect "exact route keeps the budget" 'more than max_candidates' \
  "$(q "SELECT count(*) FROM morlet_search_envelope_interval('s','r2',0,512,max_candidates=>1)")"
expect "probe route keeps the budget (small-gallery fallback)" 'more than max_candidates' \
  "$(q "SELECT count(*) FROM morlet_search_similar('s','r2',0,512,max_candidates=>1)")"
expect "probe route rejects max_candidates 0" 'max_candidates must be at least 1' \
  "$(q "SELECT count(*) FROM morlet_search_similar('s','r2',0,512,max_candidates=>0)")"
expect "probe route rejects a negative max_candidates" 'max_candidates must be at least 1' \
  "$(q "SELECT count(*) FROM morlet_search_similar('s','r2',0,512,max_candidates=>-1)")"
expect "approximate route checks its candidate count" 'probe search scores up to' \
  "$(q "SELECT count(*) FROM morlet_search_envelope_probes('s','r2',0,512,exact_threshold=>0,max_candidates=>10)")"
expect "a sufficient budget still searches" '^[1-9]' \
  "$(q "SELECT count(*) FROM morlet_search_similar('s','r2',0,512,max_candidates=>1000)")"

psql -d postgres -c "DROP DATABASE $DB" >/dev/null
exit $fail
