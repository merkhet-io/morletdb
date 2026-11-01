#!/usr/bin/env bash
# Upgrade test against a real database that ran the previous extension release.
#
#   tests/upgrade.sh [OLD_REF]   (default v0.1.0-alpha.3)
#
# Installs OLD_REF into the pgrx-managed PostgreSQL 17, populates a database
# through the old API, installs this checkout, then checks that:
#  1. the old SQL keeps working with the new shared library (before UPDATE);
#  2. ALTER EXTENSION ... UPDATE succeeds and existing data reads identically;
#  3. legacy collections keep accepting appends, and the documented data
#     transfer copies a recording into an encoded collection bitwise;
#  4. the upgraded catalog (0.1.0 -> ... -> the control file's default version, through
#     every chained update script) equals a fresh installation of that version;
#  5. the update refuses 0.1.0 objects older than alpha.3.
# It drops and recreates the databases morlet_upgrade_{old,fresh,guard}.
set -euo pipefail
OLD_REF=${1:-v0.1.0-alpha.3}
EXT=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(cd "$EXT/../.." && pwd)
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
NEW_VERSION=$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$EXT/pg_morlet.control")
WORK=$(mktemp -d)
trap 'git -C "$ROOT" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }
install() { (cd "$1/extensions/pg_morlet" && CARGO_TARGET_DIR="$1/target" cargo pgrx install --release --pg-config "$PG_CONFIG" >/dev/null); }
restart() { (cd "$EXT" && cargo pgrx stop "pg$PG_MAJOR" >/dev/null 2>&1 || true; cargo pgrx start "pg$PG_MAJOR" >/dev/null); }

# Deterministic fingerprints of everything a user can read back.
snapshot() {
  psql -d "$1" <<'SQL'
SELECT 'status', recording_id, s.* FROM plant r, morlet_stream_status('plant', r.recording_id) s ORDER BY 2;
SELECT 'chunks', recording_id, count(*), md5(string_agg(samples::text, '|' ORDER BY start_sample)) FROM plant_chunks GROUP BY 2 ORDER BY 2;
SELECT 'segments', recording_id, count(*), md5(string_agg(features::text, '|' ORDER BY start_sample)) FROM plant_segments GROUP BY 2 ORDER BY 2;
SELECT 'read', md5(samples::text) FROM morlet_read_interval('plant','A',3000,9000);
SELECT 'search', recording_id, start_sample, distance FROM morlet_search_streams('plant',
  ARRAY(SELECT round(800*sin(i/11.))::float8 FROM generate_series(0,511) i),4096,'vibration','counts','{}','zscore',5);
SELECT 'interval', recording_id, start_sample, distance FROM morlet_search_interval('plant','A',4096,4608,'{}',5);
SELECT 'bounded', recording_id, start_sample, distance FROM morlet_search('machines',
  ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),4096,'x','g','{}','zscore',256,3);
SQL
}

catalog() {
  psql -d "$1" <<'SQL'
SELECT pg_describe_object(d.classid, d.objid, 0),
  CASE WHEN d.classid = 'pg_proc'::regclass THEN md5(pg_get_functiondef(d.objid)) END
FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
WHERE e.extname = 'pg_morlet' AND d.deptype = 'e' ORDER BY 1;
SQL
}

echo "== installing $OLD_REF"
git -C "$ROOT" worktree add -q --detach "$WORK/old" "$OLD_REF"
install "$WORK/old"
restart
for db in morlet_upgrade_old morlet_upgrade_fresh morlet_upgrade_guard; do
  psql -d postgres -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"
done
psql -d morlet_upgrade_old <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public','plant');
SELECT morlet_start_recording('plant','A',4096,'vibration','counts','{"asset":"P1"}');
SELECT morlet_start_recording('plant','B',100,'HHZ','counts','{"asset":"S1"}',2048,512,'zscore','morlet-energy-lowrate-v1');
SELECT morlet_append_chunk('plant','A',0,ARRAY(SELECT round(1000*sin(i/11.))+(i*7919)%31 FROM generate_series(0,4999) i)::float8[]);
SELECT morlet_append_chunks('plant','A',ARRAY[
  ROW(5000,ARRAY(SELECT round(1000*sin(i/11.))+(i*7919)%31 FROM generate_series(5000,8999) i)::float8[])::morlet_input_chunk,
  ROW(9000,ARRAY(SELECT round(1000*sin(i/11.))+(i*7919)%31 FROM generate_series(9000,12287) i)::float8[])::morlet_input_chunk]);
SELECT morlet_append_chunk('plant','B',0,ARRAY(SELECT sin(i/5.)*exp(-power((i-3000)/300.,2)) FROM generate_series(0,8191) i)::float8[]);
SELECT morlet_create_collection('public','machines');
SELECT morlet_enable_typed_segments('machines');
INSERT INTO machines(sample_rate_hz,channel,unit,samples)
  SELECT 4096,'x','g',ARRAY(SELECT sin(i::float8/(8+r)) FROM generate_series(0,1023) i) FROM generate_series(0,3) r;
SQL
psql -d morlet_upgrade_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'"
snapshot morlet_upgrade_old > "$WORK/before.txt"
test "$(wc -l < "$WORK/before.txt")" -ge 20  # every query must contribute rows
echo "snapshot: $(wc -l < "$WORK/before.txt") rows"

echo "== installing this checkout and restarting"
install "$ROOT"
restart
snapshot morlet_upgrade_old > "$WORK/newlib.txt"
diff "$WORK/before.txt" "$WORK/newlib.txt"
echo "ok: old SQL with the new library reads identically"

psql -d morlet_upgrade_old -c "ALTER EXTENSION pg_morlet UPDATE"
test "$(psql -d morlet_upgrade_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$NEW_VERSION"
snapshot morlet_upgrade_old > "$WORK/after.txt"
diff "$WORK/before.txt" "$WORK/after.txt"
echo "ok: updated to $NEW_VERSION, existing data reads identically"

psql -d morlet_upgrade_old <<'SQL'
-- Legacy collections keep float8[] chunks and accept appends and identical replays.
SELECT morlet_append_chunk('plant','A',12288,ARRAY(SELECT round(1000*sin(i/11.))+(i*7919)%31 FROM generate_series(12288,16383) i)::float8[]);
SELECT morlet_append_chunk('plant','A',0,ARRAY(SELECT round(1000*sin(i/11.))+(i*7919)%31 FROM generate_series(0,4999) i)::float8[]);
DO $$ BEGIN
  IF (SELECT received_samples FROM morlet_stream_status('plant','A'))<>16384
    OR (SELECT encoded FROM morlet_stream_relations('plant')) THEN RAISE EXCEPTION 'legacy append failed'; END IF;
  IF (SELECT grid_chunks FROM morlet_recording_storage('plant','A')) IS NOT NULL THEN RAISE EXCEPTION 'legacy report'; END IF;
END $$;
-- Documented data transfer into a new encoded collection.
SELECT morlet_create_stream_collection('public','plant_v2');
DO $$
DECLARE r record; c record;
BEGIN
  FOR r IN SELECT * FROM plant ORDER BY recording_id LOOP
    PERFORM morlet_start_recording('plant_v2',r.recording_id,r.sample_rate_hz,r.channel,r.unit,r.metadata,
      (r.checkpoint->>'window')::integer,(r.checkpoint->>'stride')::integer,r.checkpoint->>'normalization',
      r.checkpoint->>'extractor_version',1);
    FOR c IN SELECT start_sample,samples FROM plant_chunks WHERE recording_id=r.recording_id ORDER BY start_sample LOOP
      PERFORM morlet_append_chunk('plant_v2',r.recording_id,c.start_sample,c.samples);
    END LOOP;
  END LOOP;
END $$;
DO $$ BEGIN
  IF (SELECT array_agg(float8send(v)) FROM unnest((SELECT samples FROM morlet_read_interval('plant','A',0,16384))) v)
     IS DISTINCT FROM (SELECT array_agg(float8send(v)) FROM unnest((SELECT samples FROM morlet_read_interval('plant_v2','A',0,16384))) v)
    OR (SELECT array_agg(features ORDER BY recording_id,start_sample) FROM plant_segments)
     IS DISTINCT FROM (SELECT array_agg(features ORDER BY recording_id,start_sample) FROM plant_v2_segments)
  THEN RAISE EXCEPTION 'transfer mismatch'; END IF;
END $$;
-- Materialized envelopes also work on an upgraded legacy (float8[]) collection.
SELECT morlet_enable_envelopes('plant');
DO $$
DECLARE first bigint; second bigint;
BEGIN
  first:=morlet_build_envelopes('plant','A',4096,4096);
  second:=morlet_build_envelopes('plant','A',4096,4096);
  IF first<>4 OR second<>0
    OR (SELECT count(*) FROM morlet_search_envelope_interval('plant','A',0,4096,'{}',5))<>3
    OR (SELECT envelope_table_bytes FROM morlet_collection_storage('plant'))=0
  THEN RAISE EXCEPTION 'envelopes on a legacy collection failed (built %, then %)',first,second; END IF;
END $$;
-- Context model on the same upgraded legacy collection.
SELECT morlet_enable_context('plant');
SELECT morlet_add_context('plant','u1','[{"recording_id":"A","key":"block","value":"N1","start_sample":0,"end_sample":8192},
  {"recording_id":"A","key":"block","value":"N1","start_sample":8192,"end_sample":16384},
  {"recording_id":"A","key":"part","value":1,"start_sample":0,"end_sample":8192},
  {"recording_id":"A","key":"part","value":2,"start_sample":8192,"end_sample":16384}]');
DO $$ BEGIN
  IF morlet_build_context_segments('plant','A',ARRAY['block'],ARRAY['part'])<>2 OR morlet_build_context_values('plant','A')<>2
    OR (SELECT hist_n FROM morlet_compare_to_history('plant',(SELECT max(segment_id) FROM plant_ctx_segments),min_history=>1) LIMIT 1)<>1
  THEN RAISE EXCEPTION 'context on a legacy collection failed'; END IF;
END $$;
SQL
echo "ok: legacy appends continue; transfer into an encoded collection is bitwise identical; envelopes and context work on legacy collections"

psql -d morlet_upgrade_fresh -c "CREATE EXTENSION pg_morlet"
catalog morlet_upgrade_old > "$WORK/upgraded.cat"
catalog morlet_upgrade_fresh > "$WORK/fresh.cat"
diff "$WORK/fresh.cat" "$WORK/upgraded.cat"
echo "ok: upgraded catalog equals a fresh $NEW_VERSION install ($(wc -l < "$WORK/fresh.cat") objects)"

psql -d morlet_upgrade_guard <<'SQL'
CREATE EXTENSION pg_morlet VERSION '0.1.0';
-- Simulate alpha.1, which predates batched appends.
ALTER EXTENSION pg_morlet DROP FUNCTION morlet_append_chunks(regclass,text,morlet_input_chunk[]);
DROP FUNCTION morlet_append_chunks(regclass,text,morlet_input_chunk[]);
SQL
if psql -d morlet_upgrade_guard -c "ALTER EXTENSION pg_morlet UPDATE TO '0.2.0'" 2>"$WORK/guard.err"; then
  echo "guard did not fire" >&2; exit 1
fi
grep -q "supports alpha.3 only" "$WORK/guard.err"
echo "ok: update refuses pre-alpha.3 objects"
echo "PASS"
