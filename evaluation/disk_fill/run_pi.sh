#!/usr/bin/env bash
# Fill a real disk on purpose with the steward on (#287), inside a 1.2 GB loop-mounted ext4 file so nothing else on
# the machine can fill. Needs sudo (loop mount), Docker, the images morletdb:main and morlet-steward:fill, and a
# Python with numpy and psycopg 3. Writes result.json, db.log and steward.log here; cleans up after itself.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
PY=${PY:-python3}
IMG=/srv/morlet-filltest.img MNT=/mnt/morlet-filltest PORT=28910
DSN="host=127.0.0.1 port=$PORT user=morlet password=fill dbname=morlet"
cleanup() {
  docker rm -fv fillsteward filldb >/dev/null 2>&1 || true
  if mountpoint -q "$MNT"; then sudo umount "$MNT"; fi
  sudo rm -f "$IMG"
}
trap cleanup EXIT
cleanup
sudo fallocate -l 1200M "$IMG" && sudo mkfs.ext4 -q "$IMG"
sudo mkdir -p "$MNT" && sudo mount -o loop "$IMG" "$MNT"
sudo mkdir "$MNT/pg" && sudo chmod 777 "$MNT"
sudo fallocate -l 200M "$MNT/ballast" && sudo chown "$(id -u)" "$MNT/ballast"
docker run -d --name filldb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=fill -e POSTGRES_DB=morlet \
  -e POSTGRES_INITDB_ARGS=--data-checksums -p 127.0.0.1:$PORT:5432 -v "$MNT/pg:/var/lib/postgresql/data" \
  morletdb:main postgres -c max_wal_size=64MB -c min_wal_size=32MB >/dev/null
for _ in $(seq 60); do docker exec filldb pg_isready -U morlet -q && break; sleep 2; done; sleep 3
"$PY" "$HERE/fill.py" setup --dsn "$DSN"
docker run --rm --network host -e MORLET_STEWARD_DSN="postgresql://morlet:fill@127.0.0.1:$PORT/morlet" \
  -e MORLET_STEWARD_PASSWORD=fillsteward morlet-steward:fill init
docker run --rm --network host -e MORLET_STEWARD_DSN="postgresql://morlet:fill@127.0.0.1:$PORT/morlet" \
  morlet-steward:fill grant
docker run -d --name fillsteward --network host -v "$MNT:/data:ro" \
  -e MORLET_STEWARD_DSN="postgresql://morlet_steward:fillsteward@127.0.0.1:$PORT/morlet" morlet-steward:fill \
  run --verbose --data-path /data --tick 3 --reserve-bytes 300000000 --reserve-fraction 0 >/dev/null
df -h "$MNT" | tail -1
"$PY" "$HERE/fill.py" fill --dsn "$DSN" --mount "$MNT" --ballast "$MNT/ballast" --out "$HERE/result.json"
df -h "$MNT" | tail -1
docker logs filldb > "$HERE/db.log" 2>&1
docker logs fillsteward > "$HERE/steward.log" 2>&1
echo "== database log: PANIC / FATAL / write failures"
grep -E "PANIC|FATAL|could not (extend|write)|No space left" "$HERE/db.log" || echo "none"
