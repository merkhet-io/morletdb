#!/usr/bin/env bash
# compose.yaml with plain docker, for a box whose Docker has no compose (the Pi's Docker 20.10). Same containers,
# same settings, all with --restart always so they come back after every reset or power cut.
#   set -a; . ~/pc-p1.env; set +a; evaluation/power_cut_p1/run_pi.sh
set -euo pipefail
: "${MORLET_IMAGE:?}" "${PC_DIR:?}" "${PC_ACK:?}" "${MORLET_DB_PASSWORD:?}" "${MORLET_STEWARD_PASSWORD:?}"
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
docker build -q -f "$ROOT/evaluation/power_cut_p1/Dockerfile" -t pc-p1-writer "$ROOT" >/dev/null
docker build -q -f "$ROOT/deploy/steward/Dockerfile" -t pc-p1-steward "$ROOT" >/dev/null
docker network inspect pc-p1 >/dev/null 2>&1 || docker network create pc-p1 >/dev/null
mkdir -p "$PC_DIR/pgdata"
docker rm -f pc-p1-db pc-p1-writer pc-p1-steward >/dev/null 2>&1 || true
docker run -d --name pc-p1-db --network pc-p1 --restart always \
  -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD="$MORLET_DB_PASSWORD" -e POSTGRES_DB=morlet \
  -e POSTGRES_INITDB_ARGS=--data-checksums -v "$PC_DIR/pgdata:/var/lib/postgresql/data" -p 0.0.0.0:54340:5432 \
  "$MORLET_IMAGE" postgres -c max_wal_size=256MB -c min_wal_size=64MB -c wal_compression=on >/dev/null
for _ in $(seq 90); do docker exec pc-p1-db pg_isready -U morlet -d morlet -q && break; sleep 2; done
sleep 3
DSN_ADMIN="postgresql://morlet:$MORLET_DB_PASSWORD@pc-p1-db:5432/morlet"
docker run -d --name pc-p1-writer --network pc-p1 --restart always -e DATABASE_URL="$DSN_ADMIN" \
  pc-p1-writer --ack "$PC_ACK" >/dev/null
for _ in $(seq 60); do   # the writer creates the collection; the steward is granted it once it exists
  if docker run --rm --network pc-p1 -e MORLET_STEWARD_DSN="$DSN_ADMIN" -e MORLET_STEWARD_PASSWORD="$MORLET_STEWARD_PASSWORD" \
       --entrypoint sh pc-p1-steward -c "python steward.py init >/dev/null && python steward.py grant" | grep -q pc_study; then
    break
  fi
  sleep 5
done
docker run -d --name pc-p1-steward --network pc-p1 --restart always -v "$PC_DIR:/data:ro" \
  -e MORLET_STEWARD_DSN="postgresql://morlet_steward:$MORLET_STEWARD_PASSWORD@pc-p1-db:5432/morlet" \
  pc-p1-steward run --verbose --data-path /data --load-per-cpu 1.0 >/dev/null
docker ps --filter name=pc-p1- --format '{{.Names}} {{.Status}}'
