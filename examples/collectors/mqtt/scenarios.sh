#!/bin/sh
# Reruns the MQTT collector checks of README.md: duplicates, an altered duplicate, reordering, a dropped batch
# (gap), collector downtime with a persistent session, and a collector killed mid-stream. Needs a broker on
# 127.0.0.1:${MQTT_PORT:-1883} without authentication (a local test broker), DATABASE_URL (a role that may
# create tables) and Python with requirements.txt. Uses the collection public.mqtt_check.
set -eu
cd "$(dirname "$0")"
P=${MQTT_PORT:-1883}
C=public.mqtt_check
col() { python collector.py --port "$P" --collection "$C" --create --gap-timeout 3 --client-id "$1" --run-seconds "$2"; }
echo "== 1. duplicates 5,6; altered 7; batches 10/11 swapped; batch 20 dropped"
col check-1 14 & sleep 2; python publisher.py --port "$P" --acquisition S1 --batches 40 --duplicate 5,6 --alter 7 --swap 10 --drop 20; wait
echo "== 2. collector offline while batches 20-39 are published (persistent session)"
col check-2 6 & sleep 2; python publisher.py --port "$P" --acquisition S2 --batches 20; wait
python publisher.py --port "$P" --acquisition S2 --first-batch 20 --batches 20; col check-2 8
echo "== 3. collector killed (SIGKILL) mid-stream, then restarted"
col check-3 60 & pid=$!; sleep 2; python publisher.py --port "$P" --acquisition S3 --batches 120 --interval 0.05 & sleep 2
kill -9 "$pid"; wait; col check-3 10
echo "== check"; python check.py --collection "$C"
