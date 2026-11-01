#!/usr/bin/env bash
# The PostgreSQL compatibility checks that CI runs, for one PostgreSQL major version and one mode.
#
#   PGRX_HOME=<pgrx home for these tests> tests/ci_local.sh <17|18> <without-pgvector|with-pgvector|upgrade>
#
# Prerequisites (docs/compatibility.md): cargo-pgrx matching Cargo.lock, a PostgreSQL registered
# in $PGRX_HOME (`cargo pgrx init --pgNN download`, or `--pgNN /path/to/pg_config`), and for
# with-pgvector, pgvector built into that PostgreSQL.
#
#  without-pgvector  requires that pgvector is NOT installed, then runs every #[pg_test]. The probe
#                    tests return early; probe_search_requires_the_vector_extension checks the error.
#  with-pgvector     requires that pgvector IS installed, so the probe tests cannot skip, runs every
#                    #[pg_test], then the two-session probe-enable race test (probe_enable_race.sh).
#  upgrade           PostgreSQL 17 only: upgrades populated databases from v0.1.0-alpha.3,
#                    v0.2.0-alpha.1 and v0.3.0-alpha.1 (tags must be fetched); those releases were
#                    built for PostgreSQL 17 only.
#
# #[pg_test]s run in pgrx's own throwaway cluster (target/pgrx-test-data-NN). The race and upgrade
# tests start PGRX_HOME's server (data-NN, port 28800+NN) and create and drop their own databases
# there (morlet_probe_race, morlet_upgrade_*). The script requires an explicit PGRX_HOME and refuses
# to start a server when another one already listens on that port.
set -euo pipefail
usage() { echo "usage: PGRX_HOME=... $0 <17|18> <without-pgvector|with-pgvector|upgrade>" >&2; exit 2; }
[ $# -eq 2 ] || usage
MAJOR=$1 MODE=$2
case "$MAJOR" in 17|18) ;; *) usage ;; esac
case "$MODE" in without-pgvector|with-pgvector|upgrade) ;; *) usage ;; esac
: "${PGRX_HOME:?set PGRX_HOME to the pgrx home whose PostgreSQL these tests may install into and start (see docs/compatibility.md)}"
export PGRX_HOME
TESTS=$(cd "$(dirname "$0")" && pwd)
EXT=$(cd "$TESTS/.." && pwd)
unset PG_CONFIG PGPORT
PG_MAJOR=$MAJOR
. "$TESTS/pg_env.sh"  # PG_CONFIG, PG_MAJOR, PORT, BIN from $PGRX_HOME/config.toml
export PG_CONFIG
[ "$PG_MAJOR" = "$MAJOR" ] || { echo "pg$MAJOR in $PGRX_HOME/config.toml is PostgreSQL $PG_MAJOR" >&2; exit 1; }
echo "== $("$PG_CONFIG" --version) ($PG_CONFIG), mode $MODE"

vector_control="$("$PG_CONFIG" --sharedir)/extension/vector.control"
case "$MODE" in
  with-pgvector)
    [ -f "$vector_control" ] || { echo "FAIL: pgvector is not installed in this PostgreSQL ($vector_control); the probe tests would skip" >&2; exit 1; }
    echo "pgvector $(sed -n "s/^default_version = '\(.*\)'/\1/p" "$vector_control") installed" ;;
  without-pgvector)
    [ ! -f "$vector_control" ] || { echo "FAIL: pgvector is installed ($vector_control); use a PostgreSQL without it for this mode" >&2; exit 1; } ;;
  upgrade)
    [ "$MAJOR" = 17 ] || { echo "upgrade tests start from releases built for PostgreSQL 17 only" >&2; exit 2; } ;;
esac

# Use PGRX_HOME's own server, never another one that already listens on the port; stop it on exit.
own_server() {
  if "$BIN/pg_isready" -q -h localhost -p "$PORT" && [ ! -f "$PGRX_HOME/data-$MAJOR/postmaster.pid" ]; then
    echo "FAIL: another PostgreSQL listens on port $PORT; stop it or use a different PGRX_HOME" >&2; exit 1
  fi
  trap '(cd "$EXT" && cargo pgrx stop "pg$MAJOR")' EXIT
}

cd "$EXT"
case "$MODE" in
  without-pgvector|with-pgvector)
    log=$(mktemp)
    status=0
    cargo pgrx test "pg$MAJOR" 2>&1 | tee "$log" || status=$?
    echo "== test summary"; grep -E "^test result:" "$log" || true
    rm -f "$log"
    [ "$status" -eq 0 ] || exit "$status"
    if [ "$MODE" = with-pgvector ]; then
      cargo pgrx install --pg-config "$PG_CONFIG"
      own_server
      cargo pgrx start "pg$MAJOR"
      "$TESTS/probe_enable_race.sh"
    fi ;;
  upgrade)
    own_server  # the upgrade scripts restart it after each install
    "$TESTS/upgrade.sh" v0.1.0-alpha.3
    "$TESTS/upgrade_from_release.sh" v0.2.0-alpha.1 0.2.0
    "$TESTS/upgrade_from_release.sh" v0.3.0-alpha.1 0.3.0 ;;
esac
echo "== pg$MAJOR $MODE: PASS"
