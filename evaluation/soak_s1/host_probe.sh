#!/usr/bin/env bash
# Soak S1 host probe (cron, every 15 minutes): one JSON line per run in $SOAK_DIR/results/host.jsonl with the soak
# containers' CPU, memory and restarts, the soak filesystem, load, temperature, the collector's status, and the health
# of the host's other services (stop rule: if they suffer, the soak stops).
#   */15 * * * * SOAK_DIR=/srv/morlet-soak /path/to/evaluation/soak_s1/host_probe.sh
set -euo pipefail
SOAK_DIR=${SOAK_DIR:?Set SOAK_DIR}
OUT=$SOAK_DIR/results/host.jsonl
mkdir -p "$SOAK_DIR/results"
containers=$(docker ps -a --filter label=com.docker.compose.project=soak-s1 --format '{{.Names}}' | sort | tr '\n' ' ')
stats=$( { if [ -n "$containers" ]; then docker stats --no-stream --format '{{json .}}' $containers 2>/dev/null; fi; } | python3 -c '
import json, sys
print(json.dumps({d["Name"]: {"cpu": d["CPUPerc"], "mem": d["MemUsage"]} for d in map(json.loads, sys.stdin)}))')
state=$(for c in $containers; do docker inspect -f '{{json .Name}}: {"status": {{json .State.Status}}, "restarts": {{.RestartCount}}, "started": {{json .State.StartedAt}}, "oom": {{.State.OOMKilled}}}' "$c"; done | paste -sd, -)
other=$(docker ps --filter label=com.docker.compose.project --format '{{.Names}} {{.Status}}' | grep -v soak-s1 | python3 -c '
import json, sys
print(json.dumps({l.split()[0]: " ".join(l.split()[1:]) for l in sys.stdin if l.strip()}))')
temp=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)
status=$(cat "$SOAK_DIR/spool/status.json" 2>/dev/null || echo null)
python3 - "$stats" "{${state}}" "$other" "$temp" "$status" "$SOAK_DIR" >> "$OUT" <<'EOF'
import json, os, shutil, sys, time
stats, state, other, temp, status, soak = sys.argv[1:]
du = shutil.disk_usage(soak)
root = shutil.disk_usage("/")
print(json.dumps({"at": time.time(), "load": os.getloadavg(), "temp_c": int(temp) / 1000 if temp else None,
                  "soak_fs": {"total": du.total, "used": du.used, "free": du.free},
                  "root_free": root.free, "containers": json.loads(stats), "state": json.loads(state),
                  "other_services": json.loads(other), "collector": json.loads(status)}))
EOF
