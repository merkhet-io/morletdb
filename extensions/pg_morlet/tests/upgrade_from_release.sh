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
PG_CONFIG=${PG_CONFIG:-$HOME/.pgrx/17.11/pgrx-install/bin/pg_config}
BIN=$("$PG_CONFIG" --bindir)
PORT=${PGPORT:-28817}
NEW_VERSION=$(sed -n "s/^default_version = '\(.*\)'/\1/p" "$EXT/pg_morlet.control")
WORK=$(mktemp -d)
trap 'git -C "$ROOT" worktree remove --force "$WORK/old" >/dev/null 2>&1 || true; rm -rf "$WORK"' EXIT
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }
install() { (cd "$1/extensions/pg_morlet" && CARGO_TARGET_DIR="$1/target" cargo pgrx install --release --pg-config "$PG_CONFIG" >/dev/null); }
restart() { (cd "$EXT" && cargo pgrx stop pg17 >/dev/null 2>&1 || true; cargo pgrx start pg17 >/dev/null); }

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
# then remove the blocker and update for real.
psql -d morlet_upgrade_rel_old -c "CREATE FUNCTION public.morlet_decode_interval(bytea[], bigint[], bigint, bigint)
  RETURNS double precision[] LANGUAGE sql AS 'SELECT NULL::double precision[]'"
if psql -d morlet_upgrade_rel_old -c "ALTER EXTENSION pg_morlet UPDATE" 2>/dev/null; then
  echo "FAIL: the blocked update succeeded"; exit 1
fi
test "$(psql -d morlet_upgrade_rel_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$OLD_VERSION"
snapshot morlet_upgrade_rel_old > "$WORK/failed.txt"
diff "$WORK/before.txt" "$WORK/failed.txt"
psql -d morlet_upgrade_rel_old -c "DROP FUNCTION public.morlet_decode_interval(bytea[], bigint[], bigint, bigint)"
echo "ok: a failed update rolls back; $OLD_VERSION stays installed and reads are identical"

psql -d morlet_upgrade_rel_old -c "ALTER EXTENSION pg_morlet UPDATE"
test "$(psql -d morlet_upgrade_rel_old -c "SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")" = "$NEW_VERSION"
snapshot morlet_upgrade_rel_old > "$WORK/after.txt"
diff "$WORK/before.txt" "$WORK/after.txt"
echo "ok: updated to $NEW_VERSION; reads, envelope search and comparisons are identical"

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
