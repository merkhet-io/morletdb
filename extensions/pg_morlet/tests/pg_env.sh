# Sourced by the scripts in this directory. Resolves the pgrx-managed PostgreSQL they use:
#   PG_CONFIG  as given, else the pg$PG_MAJOR entry (default pg17) of $PGRX_HOME/config.toml
#              (PGRX_HOME defaults to ~/.pgrx, as in cargo-pgrx)
#   PG_MAJOR   the server major version reported by that pg_config
#   PORT       $PGPORT, else cargo-pgrx's default for that major (28800 + major)
#   BIN        its bindir
# shellcheck shell=bash disable=SC2034
if [ -z "${PG_CONFIG:-}" ]; then
  _pgrx_config="${PGRX_HOME:-$HOME/.pgrx}/config.toml"
  PG_CONFIG=$(sed -n "s/^pg${PG_MAJOR:-17} *= *\"\(.*\)\"/\1/p" "$_pgrx_config" 2>/dev/null || true)
  if [ -z "$PG_CONFIG" ]; then
    echo "no pg${PG_MAJOR:-17} entry in $_pgrx_config: run cargo pgrx init, or set PG_CONFIG" >&2
    exit 1
  fi
fi
PG_MAJOR=$("$PG_CONFIG" --version | sed -n 's/^PostgreSQL \([0-9]*\).*/\1/p')
PORT=${PGPORT:-$((28800 + PG_MAJOR))}
BIN=$("$PG_CONFIG" --bindir)
