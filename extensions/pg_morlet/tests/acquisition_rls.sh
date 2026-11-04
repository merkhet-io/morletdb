#!/usr/bin/env bash
# Regression test: under morlet_enable_row_security, a role cannot attach a recording it sees to an
# acquisition it does not see (by naming its ID), through morlet_add_acquisition_channel or by a direct
# INSERT or UPDATE of the channel table; legitimate tenant and owner flows keep working. Each statement
# runs in its own transaction, because an acquisition is visible to the transaction that created it.
#
#   tests/acquisition_rls.sh        (needs the installed extension)
#
# It drops and recreates the database morlet_acq_rls.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
DB=morlet_acq_rls
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "CREATE DATABASE $DB"
psql -d "$DB" >/dev/null <<'SQL'
SET client_min_messages=warning;
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public','sig');
SELECT morlet_start_recording('sig',r,1000,'x','g',jsonb_build_object('tenant',t),512,256,'zscore','none')
  FROM (VALUES ('v1','v'),('v2','v'),('v3','v'),('a1','a'),('a2','a'),('a3','a'),('a4','a'),('a5','a')) x(r,t);
SELECT morlet_enable_acquisitions('sig');
SELECT morlet_create_acquisition('sig','victim','M',NULL,'{"private":"v"}');
SELECT morlet_add_acquisition_channel('sig','victim','v1','vib');
SELECT morlet_create_acquisition('sig','empty','M');
ALTER TABLE sig ENABLE ROW LEVEL SECURITY;
DROP ROLE IF EXISTS morlet_rls_a; DROP ROLE IF EXISTS morlet_rls_v;
CREATE ROLE morlet_rls_a; CREATE ROLE morlet_rls_v;
CREATE POLICY tenant_a ON sig TO morlet_rls_a USING (metadata->>'tenant'='a');
CREATE POLICY tenant_v ON sig TO morlet_rls_v USING (metadata->>'tenant'='v');
SELECT morlet_enable_row_security('sig');
GRANT USAGE ON SCHEMA public TO morlet_rls_a, morlet_rls_v;
GRANT SELECT,INSERT,UPDATE ON sig, sig_acq, sig_acq_channels TO morlet_rls_a, morlet_rls_v;
SQL

fail=0
as() { local role=$1; shift; psql -d "$DB" -c "SET ROLE $role" -c "$1" 2>&1 || true; }
expect() {  # expect NAME PATTERN OUTPUT
  if grep -q -- "$2" <<<"$3"; then echo "ok   $1"; else echo "FAIL $1: expected /$2/, got: $3"; fail=1; fi
}

expect "hidden acquisition not readable" '^0$' "$(as morlet_rls_a "SELECT count(*) FROM sig_acq WHERE acquisition_id='victim'")"
expect "attach to hidden acquisition (function)" 'not visible to this role' \
  "$(as morlet_rls_a "SELECT morlet_add_acquisition_channel('sig','victim','a1','x')")"
expect "attach to hidden acquisition (INSERT)" 'violates row-level security policy for table "sig_acq_channels"' \
  "$(as morlet_rls_a "INSERT INTO sig_acq_channels VALUES ('victim','a1','x',0,'daq',NULL)")"
expect "attach to an empty acquisition created earlier" 'not visible to this role' \
  "$(as morlet_rls_a "SELECT morlet_add_acquisition_channel('sig','empty','a1','x')")"
expect "attach to an empty acquisition created earlier (INSERT)" 'violates row-level security policy' \
  "$(as morlet_rls_a "INSERT INTO sig_acq_channels VALUES ('empty','a1','x',0,'daq',NULL)")"
expect "still hidden after the attempts" '^0$' "$(as morlet_rls_a "SELECT count(*) FROM sig_acq WHERE acquisition_id IN ('victim','empty')")"

# A tenant creates an acquisition and adds its first channel in one transaction, more channels later.
expect "create and first channel in one transaction" '^t$' "$(psql -d "$DB" -c "BEGIN" -c "SET LOCAL ROLE morlet_rls_a" \
  -c "SELECT morlet_create_acquisition('sig','own','M') AND morlet_add_acquisition_channel('sig','own','a1','x')" -c "COMMIT" 2>&1)"
expect "own acquisition visible" '^own$' "$(as morlet_rls_a "SELECT acquisition_id FROM sig_acq")"
expect "second channel later" '^t$' "$(as morlet_rls_a "SELECT morlet_add_acquisition_channel('sig','own','a2','y')")"
expect "identical retry" '^f$' "$(as morlet_rls_a "SELECT morlet_add_acquisition_channel('sig','own','a2','y')")"
expect "other tenant cannot join it" 'not visible to this role' \
  "$(as morlet_rls_v "SELECT morlet_add_acquisition_channel('sig','own','v2','z')")"
expect "moving a channel into a hidden acquisition (UPDATE)" 'violates row-level security policy' \
  "$(as morlet_rls_a "UPDATE sig_acq_channels SET acquisition_id='victim' WHERE recording_id='a2'")"
expect "first channel inside a savepoint" '^t$' "$(psql -d "$DB" -c "BEGIN" -c "SET LOCAL ROLE morlet_rls_v" -c "SAVEPOINT s" \
  -c "SELECT morlet_create_acquisition('sig','sp','M')" -c "RELEASE s" -c "SAVEPOINT t" \
  -c "SELECT morlet_add_acquisition_channel('sig','sp','v3','r')" -c "COMMIT" 2>&1 | tail -1)"
psql -d "$DB" -c "SET ROLE morlet_rls_a" -c "SELECT morlet_create_acquisition('sig','late','M')" >/dev/null
expect "first channel in a later transaction" 'not visible to this role' \
  "$(as morlet_rls_a "SELECT morlet_add_acquisition_channel('sig','late','a3','q')")"
expect "owner unaffected" '^t$' "$(psql -d "$DB" -c "SELECT morlet_add_acquisition_channel('sig','empty','a4','w')" 2>&1)"
expect "memberships" '^own:a1,own:a2,sp:v3,empty:a4,victim:v1$' "$(psql -d "$DB" -c "SELECT string_agg(acquisition_id||':'||recording_id,',' ORDER BY acquisition_id='victim',acquisition_id='empty',acquisition_id,recording_id) FROM sig_acq_channels")"

psql -d postgres -c "DROP DATABASE $DB" -c "DROP ROLE morlet_rls_a" -c "DROP ROLE morlet_rls_v" >/dev/null
exit $fail
