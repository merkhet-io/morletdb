#!/usr/bin/env bash
# Upgrade test from a 0.2.0 or later release against a real database.
#
#   tests/upgrade_from_release.sh [OLD_REF [OLD_VERSION]]   (default v0.2.0-alpha.1 0.2.0)
#
# Installs OLD_REF into the pgrx-managed PostgreSQL 17, populates a database through the
# 0.2.0 API (encoded chunks, envelopes, context, segments, values, labels), installs this
# checkout, then checks that:
#  1. the old SQL keeps working with the new shared library (before UPDATE);
#  2. ALTER EXTENSION ... UPDATE reaches the control file's default version and every
#     read, search and comparison returns identical results;
#  3. the new acquisition API works on the upgraded data (before_only included);
#  4. the upgraded catalog equals a fresh installation.
# It drops and recreates the databases morlet_upgrade_rel_{old,fresh}.
set -euo pipefail
OLD_REF=${1:-v0.2.0-alpha.1}
OLD_VERSION=${2:-0.2.0}
EXT=$(cd "$(dirname "$0")/.." && pwd)
ROOT=$(cd "$EXT/../.." && pwd)
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
NEW_VERSION=$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$EXT/pg_morlet.control")
WORK=$(mktemp -d)
trap 'git -C "$ROOT" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }
# A ref builds with the cargo-pgrx of its own Cargo.lock. Released refs use pgrx 0.16.1; when this checkout uses
# another version, MORLET_RELEASE_CARGO_PGRX names a cargo-pgrx binary of the released version.
pgrx_for() {
  local want have
  want=$(awk '$0=="name = \"pgrx\"" {getline; gsub(/version = |"/,""); print}' "$1/Cargo.lock")
  have=$(cargo pgrx --version | awk '{print $2}')
  if [ "$want" = "$have" ]; then echo "cargo pgrx"; return; fi
  if [ -z "${MORLET_RELEASE_CARGO_PGRX:-}" ] || [ "$("$MORLET_RELEASE_CARGO_PGRX" pgrx --version | awk '{print $2}')" != "$want" ]; then
    echo "$1 needs cargo-pgrx $want (this checkout: $have): set MORLET_RELEASE_CARGO_PGRX to one" >&2; return 1
  fi
  echo "$MORLET_RELEASE_CARGO_PGRX pgrx"
}
install() {
  local pgrx; pgrx=$(pgrx_for "$1")
  (cd "$1/extensions/pg_morlet" && CARGO_TARGET_DIR="$1/target" $pgrx install --release --pg-config "$PG_CONFIG" >/dev/null)
}
# A released ref never changes: with MORLET_UPGRADE_CACHE set, its packaged files (library, control and SQL,
# at their absolute install paths) are built once, kept there and copied into place on later runs.
install_release() {
  local cache=${MORLET_UPGRADE_CACHE:-} key
  if [ -z "$cache" ]; then install "$1"; return; fi
  key="$cache/$OLD_REF-pg$PG_MAJOR"
  if [ ! -d "$key" ]; then
    rm -rf "$key.tmp"
    local pgrx; pgrx=$(pgrx_for "$1")
    (cd "$1/extensions/pg_morlet" && CARGO_TARGET_DIR="$1/target" $pgrx package --pg-config "$PG_CONFIG" --out-dir "$key.tmp" >/dev/null)
    mv "$key.tmp" "$key"
  else
    echo "$OLD_REF: packaged build from $cache"
  fi
  cp -R "$key/." /
}
restart() { (cd "$EXT" && cargo pgrx stop "pg$PG_MAJOR" >/dev/null 2>&1 || true; cargo pgrx start "pg$PG_MAJOR" >/dev/null); }

snapshot() {
  psql -d "$1" <<'SQL'
SELECT 'status', recording_id, s.* FROM line r, morlet_stream_status('line', r.recording_id) s ORDER BY 2;
SELECT 'read', r.recording_id, md5(i.samples::text) FROM line r, morlet_read_interval('line', r.recording_id, 1000, 7000) i ORDER BY 2;
SELECT 'storage', recordings, samples FROM morlet_collection_storage('line');
SELECT 'envelopes', recording_id, start_sample, round(distance::numeric, 12) FROM morlet_search_envelope_interval('line','p1',4096,8192,'{}',5);
SELECT 'segments', recording_id, start_sample, end_sample, context, occurrence FROM line_ctx_segments ORDER BY 2, 3;
SELECT 'compare', value_name, round(relative_deviation::numeric, 12), hist_n, status
  FROM morlet_compare_to_history('line', (SELECT max(segment_id) FROM line_ctx_segments), min_history=>1) ORDER BY 2;
SELECT 'summary', round(score::numeric, 12), round(score_quantile::numeric, 12), top_value, hist_n, status
  FROM morlet_compare_summary('line', (SELECT max(segment_id) FROM line_ctx_segments), min_history=>1);
SELECT 'labels', recording_id, label, start_sample, end_sample FROM line_labels ORDER BY 2;
SQL
  # Derived data of later releases (#195): chunk summaries (0.3.3), the integrity check (0.3.5), roll-ups and
  # coverage (0.3.6), written by the old release and read back after the update.
  if ver_ge "$OLD_VERSION" 0.3.3; then psql -d "$1" <<'SQL'
SELECT 'stats', r.recording_id, iv.a, s.sample_count, s.min, s.max, round(s.mean::numeric, 12), round(s.rms::numeric, 12)
  FROM line r, (VALUES (0::bigint, 16000::bigint), (100, 15900), (4096, 8192)) iv(a, b),
       morlet_interval_stats('line', r.recording_id, iv.a, iv.b) s ORDER BY 2, 3;
SQL
  fi
  if ver_ge "$OLD_VERSION" 0.3.5; then
    psql -d "$1" -c "SELECT 'verify', recording_id, chunks, samples, problems FROM morlet_verify('line') ORDER BY 2"
  fi
  if ver_ge "$OLD_VERSION" 0.3.6; then psql -d "$1" <<'SQL'
SELECT 'rollups', level, block, start_sample, end_sample, md5(summary::text) FROM roll_rollups ORDER BY 2, 3;
SELECT 'rollup-stats', iv.a, s.sample_count, s.min, s.max, round(s.mean::numeric, 9), round(s.rms::numeric, 9)
  FROM (VALUES (0::bigint, 1310720::bigint), (0, 1048576), (12345, 1300000)) iv(a, b),
       morlet_interval_stats('roll', 'r1', iv.a, iv.b) s ORDER BY 2, 3;
SELECT 'coverage', recording_id, start_sample, end_sample, state, windows FROM morlet_coverage('line') ORDER BY 2, 3;
SELECT 'verify-roll', recording_id, chunks, samples, problems FROM morlet_verify('roll') ORDER BY 2;
SQL
  fi
}
ver_ge() { [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$2" ]; }

catalog() {
  psql -d "$1" <<'SQL'
SELECT pg_describe_object(d.classid, d.objid, 0),
  CASE WHEN d.classid = 'pg_proc'::regclass THEN CASE
    WHEN (SELECT prokind FROM pg_proc WHERE oid = d.objid) = 'a'
      THEN md5((SELECT row(a.aggtransfn, a.aggfinalfn, a.aggcombinefn, a.aggserialfn, a.aggdeserialfn,
                           a.aggfinalmodify, a.aggtranstype, p.proparallel, pg_get_function_arguments(p.oid))::text
                FROM pg_aggregate a JOIN pg_proc p ON p.oid = a.aggfnoid WHERE a.aggfnoid = d.objid))
    ELSE md5(pg_get_functiondef(d.objid)) END END
FROM pg_depend d JOIN pg_extension e ON e.oid = d.refobjid
WHERE e.extname = 'pg_morlet' AND d.deptype = 'e' ORDER BY 1;
SQL
}

echo "== installing $OLD_REF"
git -C "$ROOT" worktree add -q --detach "$WORK/old" "$OLD_REF"
install_release "$WORK/old"
restart
for db in morlet_upgrade_rel_old morlet_upgrade_rel_fresh; do
  psql -d postgres -c "DROP DATABASE IF EXISTS $db" -c "CREATE DATABASE $db"
done
psql -d morlet_upgrade_rel_old <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public', 'line');
SELECT morlet_enable_envelopes('line');
SELECT morlet_enable_context('line');
SELECT morlet_start_recording('line', p, 1000, 'vib', 'g', '{"machine":"M1"}', 512, 256, 'zscore', 'none', 0.001)
  FROM unnest(ARRAY['p1','p2','p3']) p;
SELECT morlet_append_chunk('line', p, 0, ARRAY(SELECT round((k*sin(i/3.)+0.2*sin(i/17.))::numeric, 3)::float8
  FROM generate_series(0, 15999) i)) FROM (VALUES ('p1',1.0),('p2',1.05),('p3',1.6)) v(p,k);
SELECT morlet_build_envelopes('line', p, 4096, 2048) FROM unnest(ARRAY['p1','p2','p3']) p;
SELECT morlet_add_context('line', 'c-'||p, jsonb_build_array(
  jsonb_build_object('recording_id',p,'key','block','value','N1','start_sample',0,'end_sample',8000),
  jsonb_build_object('recording_id',p,'key','block','value','N2','start_sample',8000,'end_sample',16000),
  jsonb_build_object('recording_id',p,'key','part','value',p,'start_sample',0,'end_sample',16000)))
  FROM unnest(ARRAY['p1','p2','p3']) p;
SELECT morlet_build_context_segments('line', p, ARRAY['block'], ARRAY['part']) FROM unnest(ARRAY['p1','p2','p3']) p;
SELECT morlet_build_context_values('line', p) FROM unnest(ARRAY['p1','p2','p3']) p;
SELECT morlet_add_label('line', 'p2', 8000, 16000, 'checked', 'upgrade test');
SQL
if ver_ge "$OLD_VERSION" 0.3.3; then psql -d morlet_upgrade_rel_old <<'SQL'
SELECT morlet_enable_chunk_summaries('line');
SELECT morlet_build_chunk_summaries('line', p, 1000) FROM unnest(ARRAY['p1','p2','p3']) p;
SQL
fi
if ver_ge "$OLD_VERSION" 0.3.6; then psql -d morlet_upgrade_rel_old <<'SQL'
SELECT morlet_create_stream_collection('public', 'roll');
SELECT morlet_enable_chunk_summaries('roll');
SELECT morlet_enable_rollups('roll');
SELECT morlet_start_recording('roll', 'r1', 1000, 'vib', 'g', '{}', 512, 256, 'zscore', 'none', 0.001);
SELECT morlet_append_chunk('roll', 'r1', c*32768, ARRAY(SELECT round(sin(i/7.)::numeric + (i % 1000) / 1000., 3)::float8
  FROM generate_series(c*32768, c*32768+32767) i)) FROM generate_series(0, 39) c;
SELECT morlet_build_chunk_summaries('roll', 'r1', 1000);
SELECT morlet_build_rollups('roll', 'r1');
DO $$ BEGIN IF (SELECT count(*) FROM roll_rollups) = 0 THEN RAISE EXCEPTION 'no roll-ups built'; END IF; END $$;
SQL
fi
test "$(psql -d morlet_upgrade_rel_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$OLD_VERSION"
snapshot morlet_upgrade_rel_old > "$WORK/before.txt"
test "$(wc -l < "$WORK/before.txt")" -gt 20

echo "== installing this checkout ($NEW_VERSION)"
install "$ROOT"
restart
snapshot morlet_upgrade_rel_old > "$WORK/library.txt"
diff "$WORK/before.txt" "$WORK/library.txt"
echo "ok: $OLD_VERSION SQL with the new library returns identical results"

# A failed update must roll back completely (pilot Stage 2, B7): block it with an object that
# already has the signature of a function the update creates, check that nothing changed,
# then remove the blocker and update for real. The blocker is a function new in the first
# update step after OLD_VERSION.
case "$OLD_VERSION" in
  0.3.1) BLOCKER="public.morlet_search_similar(regclass, text, bigint, bigint, jsonb, integer, double precision, boolean, boolean, integer)" ;;
  0.3.2) BLOCKER="public.morlet_decode_interval_raw(bytea[], bigint[], bigint, bigint)" ;;
  0.3.4) BLOCKER="public.morlet_verify(regclass, text, integer)" ;;
  0.3.5) BLOCKER="public.morlet_coverage(regclass, text[], integer)" ;;
  0.3.6) BLOCKER="" ;;   # 0.3.6 -> 0.3.7 only redefines C functions with a new library path: nothing new to block
  0.3.7) BLOCKER="public.morlet_samples_to_ns(numeric, double precision)" ;;
  0.3.8) BLOCKER="" ;;   # 0.3.8 -> 0.3.9 only changes the volatility of two functions: nothing new to block
  0.3.9) BLOCKER="" ;;   # 0.3.9 -> 0.3.10 only replaces existing functions: nothing new to block
  0.3.3) BLOCKER="public.morlet_search_envelope_probes(regclass, text, bigint, bigint, jsonb, integer, double precision, boolean, boolean, integer, integer, integer, timestamp with time zone, timestamp with time zone, boolean, integer)" ;;
  *) BLOCKER="public.morlet_decode_interval(bytea[], bigint[], bigint, bigint)" ;;
esac
if [ -z "$BLOCKER" ]; then
  echo "skip: the update from $OLD_VERSION creates no function, so a failed update cannot be provoked by a blocker"
else
psql -d morlet_upgrade_rel_old -c "CREATE FUNCTION $BLOCKER RETURNS void LANGUAGE sql AS ''"
if psql -d morlet_upgrade_rel_old -c "ALTER EXTENSION pg_morlet UPDATE" 2>/dev/null; then
  echo "FAIL: the blocked update succeeded"; exit 1
fi
test "$(psql -d morlet_upgrade_rel_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$OLD_VERSION"
snapshot morlet_upgrade_rel_old > "$WORK/failed.txt"
diff "$WORK/before.txt" "$WORK/failed.txt"
psql -d morlet_upgrade_rel_old -c "DROP FUNCTION $BLOCKER"
echo "ok: a failed update rolls back; $OLD_VERSION stays installed and reads are identical"
fi

psql -d morlet_upgrade_rel_old -c "ALTER EXTENSION pg_morlet UPDATE"
test "$(psql -d morlet_upgrade_rel_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$NEW_VERSION"
snapshot morlet_upgrade_rel_old > "$WORK/after.txt"
diff "$WORK/before.txt" "$WORK/after.txt"
echo "ok: updated to $NEW_VERSION; reads, envelope search and comparisons are identical"
# The update pins TOAST compression of every existing collection's bulk columns to pglz (#280).
pins=$(psql -d morlet_upgrade_rel_old -c "SELECT count(*) FILTER (WHERE a.attcompression='p')||' '||count(*)
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
  WHERE c.relnamespace='public'::regnamespace AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped
    AND a.atttypid IN ('bytea'::regtype,'float8[]'::regtype)")
read -r pinned total <<<"$pins"
[ "$total" -gt 0 ] && [ "$pinned" = "$total" ] || { echo "FAIL: $pinned of $total bulk columns pinned to pglz"; exit 1; }
echo "ok: all $total bulk columns of the existing collections are pinned to pglz"

psql -d morlet_upgrade_rel_old <<'SQL'
SELECT morlet_enable_acquisitions('line');
SELECT morlet_create_acquisition('line', 'a-'||p, 'M1', ('2026-03-0'||n||' 08:00Z')::timestamptz)
  FROM (VALUES ('p1',1),('p2',2),('p3',3)) v(p,n);
SELECT morlet_add_acquisition_channel('line', 'a-'||p, p, 'vib') FROM unnest(ARRAY['p1','p2','p3']) p;
DO $$ BEGIN
  IF (SELECT hist_n FROM morlet_compare_summary('line',
        (SELECT segment_id FROM line_ctx_segments WHERE recording_id='p3' AND context->>'block'='N1'),
        min_history=>1, before_only=>true)) <> 2
    OR (SELECT hist_n FROM morlet_compare_summary('line',
        (SELECT segment_id FROM line_ctx_segments WHERE recording_id='p1' AND context->>'block'='N1'),
        min_history=>1, before_only=>true)) <> 0
    OR (SELECT count(*) FROM morlet_read_acquisition('line', 'a-p1', 0, 1000000000)) <> 1
  THEN RAISE EXCEPTION 'acquisitions on upgraded data failed'; END IF;
END $$;
SQL
echo "ok: acquisitions, aligned reads and before_only work on upgraded data"

psql -d morlet_upgrade_rel_fresh -c "CREATE EXTENSION pg_morlet"
catalog morlet_upgrade_rel_old > "$WORK/upgraded.cat"
catalog morlet_upgrade_rel_fresh > "$WORK/fresh.cat"
diff "$WORK/fresh.cat" "$WORK/upgraded.cat"
echo "ok: upgraded catalog equals a fresh $NEW_VERSION install ($(wc -l < "$WORK/fresh.cat") objects)"
echo "PASS"
