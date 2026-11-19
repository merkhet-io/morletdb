#!/usr/bin/env bash
# Development check (not a benchmark): does PostgreSQL 19's REPACK (CONCURRENTLY) give back the space of removed
# recordings in a pg_morlet collection, while appends continue, without changing any stored chunk?
set -euo pipefail
B=${PG19_BIN:?set PG19_BIN to the bin directory of a PostgreSQL 19 installation with pg_morlet installed}
W=$(mktemp -d)
PORT=28839
cleanup() { [ -f "$W/data/postmaster.pid" ] && "$B/pg_ctl" -D "$W/data" -m fast stop >/dev/null; rm -rf "$W"; }
trap cleanup EXIT
"$B/initdb" -D "$W/data" -A trust -U postgres >/dev/null
"$B/pg_ctl" -D "$W/data" -o "-p $PORT -c max_wal_size=2GB" -l "$W/log" -w start >/dev/null
failures=0
check() { if [ "$2" = "$3" ]; then echo "PASS $1: $2"; else echo "FAIL $1: got $2, expected $3"; failures=$((failures + 1)); fi; }
q() { "$B/psql" -h localhost -p $PORT -U postgres -d postgres -v ON_ERROR_STOP=1 -qAtX "$@"; }
q -c "SHOW wal_level" -c "SHOW effective_wal_level" -c "SHOW max_repack_replication_slots" -c "SHOW default_toast_compression" | paste -sd' ' - | sed 's/^/wal_level effective max_repack_slots toast: /'
q -c "CREATE EXTENSION pg_morlet" -c "SELECT morlet_version()"
q -c "SELECT morlet_create_stream_collection('public','rp')" >/dev/null
# 40 recordings x 32 chunks of 4096 samples (noisy integers, so chunks are not trivially small)
q <<'SQL'
DO $$ BEGIN
  FOR r IN 0..39 LOOP
    PERFORM morlet_start_recording('rp','r'||lpad(r::text,2,'0'),4096,'x','counts');
    FOR c IN 0..31 LOOP
      PERFORM morlet_append_chunk('rp','r'||lpad(r::text,2,'0'),c*4096,
        ARRAY(SELECT round(800*sin((c*4096+i)/(7.+r))+((i*7919+r*104729+c*31)%257))::float8 FROM generate_series(0,4095) i));
    END LOOP;
  END LOOP; END $$;
SQL
size() { q -c "SELECT pg_total_relation_size('rp_chunks')"; }
kept="recording_id >= 'r20'"
sig() { q -c "SELECT md5(string_agg(recording_id||':'||start_sample||':'||md5(encoded),',' ORDER BY recording_id,start_sample)) FROM rp_chunks WHERE $kept AND start_sample < 32*4096"; }
s0=$(size); h0=$(sig)
echo "filled: rp_chunks $s0 bytes"
q -c "DELETE FROM rp WHERE recording_id < 'r20'"
q -c "VACUUM rp, rp_chunks"
s1=$(size); echo "after deleting half and VACUUM: $s1 bytes (files keep their size)"
# appends to the kept recordings while REPACK (CONCURRENTLY) runs
( for c in 32 33 34 35 36 37 38 39; do
    q -c "SELECT count(*) FROM (SELECT morlet_append_chunk('rp',recording_id,$c*4096,ARRAY(SELECT (i%97)::float8 FROM generate_series(0,4095) i)) FROM rp) x" >/dev/null
  done ) &
appends=$!
t0=$(date +%s)
q -c "REPACK (CONCURRENTLY, VERBOSE) rp_chunks" 2>&1 | tail -3
echo "REPACK took $(( $(date +%s) - t0 )) s"
if wait "$appends"; then a=ok; else a=failed; fi
s2=$(size); h2=$(sig)
echo "after REPACK (CONCURRENTLY): $s2 bytes"
check "concurrent appends" "$a" ok
check "kept chunks byte for byte unchanged" "$h2" "$h0"
check "chunks" "$(q -c "SELECT count(*) FROM rp_chunks")" $((20*40))
check "morlet_verify recordings with problems" "$(q -c "SELECT count(*) FROM morlet_verify('rp',NULL,100) WHERE cardinality(problems)>0")" 0
check "recordings whose last 32768 samples (incl. the concurrent appends) read" \
  "$(q -c "SELECT count(*) FROM rp r, LATERAL morlet_read_interval('rp',r.recording_id,40*4096-32768,40*4096) x WHERE cardinality(x.samples)=32768")" 20
check "space returned" "$([ "$s2" -lt "$s1" ] && echo yes || echo no)" yes
[ "$failures" -eq 0 ] || { echo "$failures check(s) failed"; exit 1; }
echo "all checks passed"
