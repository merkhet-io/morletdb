#!/usr/bin/env bash
# Commit notifications (#205), with a real listener session: exactly one notification per committed transaction that
# stored chunks, none for rolled-back transactions, rolled-back savepoints, identical replays or a disabled collection;
# the payload carries the stored sample ranges, or only a count with detail => false.
#
#   tests/notifications.sh        (needs the installed extension)
# It drops and recreates the database morlet_notify.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
DB=morlet_notify
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
psql -d "$DB" >/dev/null <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public','n');
SELECT morlet_start_recording('n',r,1000,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['r1','r2']) r;
SELECT morlet_enable_notifications('n');
SQL

WORK=$(mktemp -d); trap 'rm -rf "$WORK"' EXIT
fail=0
expect() {  # expect NAME CONDITION-OUTPUT EXPECTED
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$3', got '$2'"; fail=1; fi
}
chunk() { echo "SELECT morlet_append_chunk('n','$1',$2,ARRAY(SELECT sin(i/7.0) FROM generate_series(0,$(($3 - 1))) i))"; }

# The listener holds a pg_sleep while the writer works; notifications queued meanwhile are printed when it returns.
psql -d "$DB" -c "LISTEN morlet_n" -c "SELECT pg_sleep(8)" -c "SELECT 1" >"$WORK/listener" 2>&1 &
listener=$!
for _ in $(seq 100); do
  [ "$(psql -d "$DB" -c "SELECT count(*) FROM pg_stat_activity WHERE datname='$DB' AND query='SELECT pg_sleep(8)' AND state='active'")" = 1 ] && break
  sleep 0.05
done

psql -d "$DB" >/dev/null <<SQL
-- 1: two recordings in one transaction: one notification listing both
BEGIN; $(chunk r1 0 4096); $(chunk r1 4096 4096); $(chunk r2 0 1000); COMMIT;
-- 2: rolled back: nothing
BEGIN; $(chunk r1 8192 4096); ROLLBACK;
-- 3: a rolled-back savepoint leaves only the rest
BEGIN; SAVEPOINT s; $(chunk r2 1000 1000); ROLLBACK TO SAVEPOINT s; $(chunk r1 8192 2048); COMMIT;
-- 4: an identical replay stores nothing: nothing
BEGIN; $(chunk r1 0 4096); COMMIT;
-- 5: a transaction without chunks: nothing
BEGIN; SELECT 1; COMMIT;
-- 6: detail => false: only the count
SELECT morlet_enable_notifications('n', detail => false);
$(chunk r2 1000 500);
-- 7: disabled: nothing
SELECT morlet_disable_notifications('n');
$(chunk r2 1500 500);
SQL
wait "$listener"

payloads=$(sed -n 's/^Asynchronous notification "morlet_n" with payload "\(.*\)" received from server process with PID [0-9]*\.$/\1/p' "$WORK/listener")
expect "three notifications for the three committed transactions with new chunks" "$(grep -c . <<<"$payloads" || true)" 3
first=$(sed -n 1p <<<"$payloads"); third=$(sed -n 3p <<<"$payloads")
expect "one notification lists both recordings and their ranges" "$(psql -d "$DB" -c "SELECT string_agg(r->>'recording_id'||':'||(r->>'first_sample')||'-'||(r->>'end_sample')||'@'||(r->>'end_s'),' ' ORDER BY r->>'recording_id') FROM jsonb_array_elements('$first'::jsonb->'recordings') r")" "r1:0-8192@8.192 r2:0-1000@1"
expect "payload names the collection and version" "$(psql -d "$DB" -c "SELECT ('$first'::jsonb->>'v')||' '||('$first'::jsonb->>'collection')")" "1 public.n"
expect "the rolled-back savepoint is not reported" "$(psql -d "$DB" -c "SELECT string_agg((r->>'recording_id')||':'||(r->>'first_sample')||'-'||(r->>'end_sample'),' ') FROM jsonb_array_elements('$(sed -n 2p <<<"$payloads")'::jsonb->'recordings') r")" "r1:8192-10240"
expect "detail => false carries only the count" "$third" '{"v": 1, "collection": "public.n", "recording_count": 1}'
expect "disabled: no channel" "$(psql -d "$DB" -c "SELECT count(*) FROM morlet_notification_channel('n')")" 0

psql -d postgres -c "DROP DATABASE $DB"
[ "$fail" = 0 ] || exit 1
echo "notifications: PASS"
