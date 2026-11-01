#!/usr/bin/env bash
# Build one evaluation database for E1/D1/D2 from fetched CX data (artifact step 5).
#
#   evaluation/consolidated/prepare_db.sh dev     morlet_r2   data/cx-scale
#   evaluation/consolidated/prepare_db.sh heldout morlet_bl1c data/cx-confirm-2021
#
# Creates DB (fails if it exists), loads recordings, raw chunks and 4096/2048 envelopes with the study's load
# script (which also builds that study's own side table and index), then enables product probe search
# (`morlet_enable_envelope_probes`, HNSW m 16, ef_construction 64). The probe tables of the original
# databases were created with the same call. Needs PSQL (default: pgrx PostgreSQL 17 on port 28817) and
# .venv-seismic. About 15 minutes per database on an M5 Max.
set -euo pipefail
ROLE=$1; DB=$2; DATA=$3
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PG_CONFIG=${PG_CONFIG:-$HOME/.pgrx/17.11/pgrx-install/bin/pg_config}
PSQL="$("$PG_CONFIG" --bindir)/psql -h localhost -p ${PGPORT:-28817} -v ON_ERROR_STOP=1 -qAtX"
PY=${PY:-$ROOT/.venv-seismic/bin/python}
case $ROLE in
  dev) LOAD=evaluation/retrieval_scaling/load.py; SUMS=evaluation/retrieval_scaling/results/SHA256SUMS.mseed ;;
  heldout) LOAD=evaluation/bounded_lag/confirm/load.py; SUMS=evaluation/bounded_lag/confirm/results/SHA256SUMS.mseed ;;
  *) echo "role must be dev or heldout"; exit 2 ;;
esac
(cd "$DATA" && shasum -a 256 -c "$ROOT/$SUMS" >/dev/null) && echo "data: checksums OK"
$PSQL -d postgres -c "CREATE DATABASE $DB"
OUT=$(mktemp -d)
MORLET_DSN="host=localhost port=${PGPORT:-28817} dbname=$DB" "$PY" -I "$ROOT/$LOAD" "$DATA" "$OUT" >/dev/null
$PSQL -d "$DB" -c "SET maintenance_work_mem = '4GB'" -c "SELECT morlet_enable_envelope_probes('public.r2', 4096)" >/dev/null
$PSQL -d "$DB" -c "SELECT window_size, envelopes, probe_rows, probe_backlog FROM morlet_collection_status('public.r2')"
echo "load timings and sizes: $OUT/load.json"
