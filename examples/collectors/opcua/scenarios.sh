#!/bin/sh
# Reruns the collector checks of README.md against a scratch database: normal run, restart inside the ring buffer,
# server restart, gap, duplicate delivery. Needs DATABASE_URL (a role that may create tables) and Python with
# requirements.txt. Uses port 4841 and the collection public.machine_check.
set -eu
cd "$(dirname "$0")"
EP=opc.tcp://127.0.0.1:4841/morletdb/
C=public.machine_check
server() { python server.py --port 4841 >/tmp/morlet-opcua-server.log 2>&1 & echo $! >/tmp/morlet-opcua-server.pid; sleep 3; }
stop_server() { kill "$(cat /tmp/morlet-opcua-server.pid)" 2>/dev/null || true; sleep 1; }
collect() { python collector.py "$EP" --collection "$C" --create --sample-quantum 0.001 --run-seconds "$1" | grep -v context_not_stored; }
server
echo "== 1. normal run (15 s)";                 collect 15
echo "== 2. restart inside the ring (4 s off)"; sleep 4; collect 8
echo "== 3. gap (16 s off, ring holds 12.8 s)"; sleep 16; collect 8
echo "== 4. server restart while collecting"
collect 20 & pid=$!; sleep 6; stop_server; sleep 3; server; wait $pid
echo "== 5. duplicate delivery"
python - <<'PY'
import os, psycopg, signal_model as sm
with psycopg.connect(os.environ["DATABASE_URL"]) as c:
    rid, first, end = c.execute("SELECT recording_id,(metadata->>'first_block')::bigint,(checkpoint->>'end')::bigint "
                                "FROM public.machine_check ORDER BY 2 DESC LIMIT 1").fetchone()
    r = c.execute("SELECT morlet_append_chunk('public.machine_check'::regclass,%s,%s,%s)",
                  (rid, 3 * sm.BLOCK, sm.block(first + 3).tolist())).fetchone()[0]; c.commit()
    print("identical duplicate accepted, end unchanged:", r == end)
    bad = sm.block(first + 3); bad[0] += 0.001
    try:
        c.execute("SELECT morlet_append_chunk('public.machine_check'::regclass,%s,%s,%s)", (rid, 3 * sm.BLOCK, bad.tolist()))
        print("tampered duplicate ACCEPTED"); raise SystemExit(1)
    except psycopg.Error as e:
        c.rollback(); print("tampered duplicate refused:", e.diag.message_primary)
PY
stop_server
echo "== check"; python check.py --collection "$C"
