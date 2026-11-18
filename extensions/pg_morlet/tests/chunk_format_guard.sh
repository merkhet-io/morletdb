#!/usr/bin/env bash
# morlet.chunk_format and morlet.verify_appends with real sessions: a misspelt value never falls back quietly.
#
#   tests/chunk_format_guard.sh        (needs the installed extension)
#
# PostgreSQL keeps a value set before pg_morlet's library loads as a placeholder: `SET` early in a session,
# `ALTER DATABASE/ROLE ... SET`, `-c` (here through PGOPTIONS). Each new psql session below starts without the
# library, so the setting arrives that way. An invalid value must make the first append fail with a message naming
# it and nothing may be stored; once the library is loaded, `SET` refuses an invalid value at once. Valid values
# work through every route, and unset means grizzbit1. The same holds for morlet.float32_chunks.
# It drops and recreates the database morlet_chunk_format and the role morlet_cf_role.
set -euo pipefail
. "$(dirname "$0")/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN
DB=morlet_chunk_format
psql() { "$BIN/psql" -h localhost -p "$PORT" -v ON_ERROR_STOP=1 -qAtX "$@"; }

psql -d postgres -c "DROP DATABASE IF EXISTS $DB" -c "DROP ROLE IF EXISTS morlet_cf_role" \
  -c "CREATE DATABASE $DB" -c "CREATE ROLE morlet_cf_role LOGIN SUPERUSER"
psql -d "$DB" >/dev/null <<'SQL'
CREATE EXTENSION pg_morlet;
SELECT morlet_create_stream_collection('public','cf');
SELECT morlet_start_recording('cf','r',4096,'x','g');
SQL
# The samples are integers, so float32 values: with morlet.float32_chunks on (the default) a version-6 chunk would
# replace version 2 wherever it is smaller. This test is about morlet.chunk_format, so it pins float32 codings off.
psql -d postgres -c "ALTER DATABASE $DB SET morlet.float32_chunks = off"

fail=0
expect() {  # expect NAME GOT EXPECTED
  if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: expected '$3', got '$2'"; fail=1; fi
}
# append [psql options...]: one chunk of gridded samples after the last stored one, in a new session; prints the
# chunk's version byte, or the error
append() {
  local out next
  next=$(psql -d "$DB" -c "SELECT coalesce(max(start_sample+sample_count),0) FROM cf_chunks")
  if out=$(psql -d "$DB" "$@" -c "SELECT morlet_append_chunk('cf','r',$next,
             ARRAY(SELECT round(3000*sin(i/7.3))::float8 FROM generate_series($next,$next+4095) i))" \
             -c "SELECT get_byte(encoded,0) FROM cf_chunks WHERE start_sample=$next" 2>&1); then
    echo "$out" | tail -1
  else
    echo "$out" | sed -n 's/^.*ERROR: *//p' | head -1
  fi
}
BAD='invalid value for parameter "morlet.chunk_format": "grizbit21"; available values: grizzbit1, grizzbit21'
stored() { psql -d "$DB" -c "SELECT count(*) FROM cf_chunks"; }

expect "unset is grizzbit1" "$(append)" 2
expect "SET before the library loads, valid" "$(append -c "SET morlet.chunk_format = grizzbit21")" 5
expect "SET before the library loads, misspelt: the append fails" \
  "$(append -c "SET morlet.chunk_format = grizbit21")" "$BAD"
expect "-c, valid" "$(PGOPTIONS='-c morlet.chunk_format=grizzbit21' append)" 5
expect "-c, misspelt: the append fails" "$(PGOPTIONS='-c morlet.chunk_format=grizbit21' append)" "$BAD"

# ALTER DATABASE/ROLE in a session without the library: PostgreSQL stores the value unchecked.
psql -d postgres -c "ALTER DATABASE $DB SET morlet.chunk_format = 'grizbit21'"
expect "ALTER DATABASE, misspelt: the append fails" "$(append)" "$BAD"
psql -d postgres -c "ALTER DATABASE $DB SET morlet.chunk_format = 'grizzbit21'"
expect "ALTER DATABASE, valid" "$(append)" 5
psql -d postgres -c "ALTER DATABASE $DB RESET morlet.chunk_format"
psql -d postgres -c "ALTER ROLE morlet_cf_role SET morlet.chunk_format = 'grizbit21'"
expect "ALTER ROLE, misspelt: the append fails" "$(append -U morlet_cf_role)" "$BAD"
psql -d postgres -c "ALTER ROLE morlet_cf_role RESET morlet.chunk_format"
expect "after RESET, grizzbit1 again" "$(append)" 2

# The batch path fails the same way.
next=$(psql -d "$DB" -c "SELECT max(start_sample+sample_count) FROM cf_chunks")
got=$(PGOPTIONS='-c morlet.chunk_format=grizbit21' psql -d "$DB" -c "SELECT morlet_append_chunks_raw('cf','r',$next,
        morlet_float8_le_bytes(ARRAY(SELECT round(3000*sin(i/7.3))::float8 FROM generate_series(0,8191) i)),4096)" 2>&1 \
      | sed -n 's/^.*ERROR: *//p' | head -1) || true
expect "batch append, misspelt: fails" "$got" "$BAD"

# morlet.verify_appends is never registered, so it stays the text it was set to; an invalid value set before the
# library loads makes the append fail as well, instead of verifying (or not) by default.
VBAD='invalid value for parameter "morlet.verify_appends": "yes please"; available values: on, off'
expect "verify_appends, SET before the library loads, invalid: the append fails" \
  "$(append -c "SET morlet.verify_appends = 'yes please'")" "$VBAD"
expect "verify_appends, -c, invalid: the append fails" "$(PGOPTIONS='-c morlet.verify_appends=yes\ please' append)" "$VBAD"
psql -d postgres -c "ALTER DATABASE $DB SET morlet.verify_appends = 'yes please'"
expect "verify_appends, ALTER DATABASE, invalid: the append fails" "$(append)" "$VBAD"
psql -d postgres -c "ALTER DATABASE $DB RESET morlet.verify_appends"
# morlet.float32_chunks is not registered either: the same rule.
FBAD='invalid value for parameter "morlet.float32_chunks": "of"; available values: on, off'
expect "float32_chunks, SET before the library loads, invalid: the append fails" \
  "$(append -c "SET morlet.float32_chunks = 'of'")" "$FBAD"
expect "float32_chunks, -c, invalid: the append fails" "$(PGOPTIONS='-c morlet.float32_chunks=of' append)" "$FBAD"
psql -d postgres -c "ALTER DATABASE $DB SET morlet.float32_chunks = 'of'"
expect "float32_chunks, ALTER DATABASE, invalid: the append fails" "$(append)" "$FBAD"
psql -d postgres -c "ALTER DATABASE $DB SET morlet.float32_chunks = off"
# Before the first morlet_* call, current_setting shows only what was set (NULL when unset, which means the
# default); SHOW after a morlet_* call shows morlet.chunk_format as pg_morlet sees it.
expect "current_setting before loading, unset" \
  "$(psql -d "$DB" -c "SELECT coalesce(current_setting('morlet.chunk_format', true), '<null>')")" "<null>"
expect "SHOW after a morlet_* call" "$(psql -d "$DB" -c "SELECT morlet_version()" -c "SHOW morlet.chunk_format" | tail -1)" grizzbit1

# Every failed append stored nothing: one chunk for each of the five successful appends above.
expect "failed appends stored nothing" "$(stored)" 5

# With the library loaded, the setting refuses a misspelt value at once.
got=$(psql -d "$DB" -c "SELECT morlet_version()" -c "SET morlet.chunk_format = grizbit21" 2>&1 | sed -n 's/^.*ERROR: *//p') || true
expect "SET after the library loaded is refused" "$got" 'invalid value for parameter "morlet.chunk_format": "grizbit21"'

psql -d postgres -c "DROP DATABASE $DB" -c "DROP ROLE morlet_cf_role"
[ "$fail" -eq 0 ] && echo "PASS" || { echo "FAIL"; exit 1; }
