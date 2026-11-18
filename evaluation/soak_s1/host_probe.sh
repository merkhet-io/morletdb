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
state=$(for c in $containers; do docker inspect -f '{{json .Name}}: {"status": {{json .State.Status}}, "health": {{if .State.Health}}{{json .State.Health.Status}}{{else}}null{{end}}, "restarts": {{.RestartCount}}, "started": {{json .State.StartedAt}}, "oom": {{.State.OOMKilled}}}' "$c"; done | paste -sd, -)
# the steward's own status (NORMAL / ATTENTION / ACTION REQUIRED, with its findings and any ingest hold): H2
steward=$(timeout 60 docker exec soak-s1-steward-1 python steward.py status 2>&1 | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))' || echo null)
other=$(docker ps --filter label=com.docker.compose.project --format '{{.Names}} {{.Status}}' | grep -v soak-s1 | python3 -c '
import json, sys
print(json.dumps({l.split()[0]: " ".join(l.split()[1:]) for l in sys.stdin if l.strip()}))')
# shakedown change 7 (H4): process memory from cgroup v2 memory.stat (anon, shmem), page cache (file) reported apart
memstat=$(for c in $containers; do id=$(docker inspect -f '{{if .State.Running}}{{.Id}}{{end}}' "$c" 2>/dev/null) || continue
  f=/sys/fs/cgroup/system.slice/docker-$id.scope/memory.stat
  [ -n "$id" ] && [ -r "$f" ] || continue
  awk -v n="$c" '$1=="anon"||$1=="shmem"||$1=="file"{v[$1]=$2} END{printf "\"%s\": {\"anon\": %d, \"shmem\": %d, \"file\": %d}\n", n, v["anon"], v["shmem"], v["file"]}' "$f"
done | paste -sd, - || true)
temp=$(cat /sys/class/thermal/thermal_zone*/temp 2>/dev/null | sort -n | tail -1)
status=$(cat "$SOAK_DIR/spool/status.json" 2>/dev/null || echo null)
python3 - "$stats" "{${state}}" "$other" "$temp" "$status" "$SOAK_DIR" "$steward" "{${memstat}}" >> "$OUT" <<'EOF'
import json, os, shutil, sys, time
stats, state, other, temp, status, soak, steward, memstat = sys.argv[1:]
du = shutil.disk_usage(soak)
root = shutil.disk_usage("/")
print(json.dumps({"at": time.time(), "load": os.getloadavg(), "temp_c": int(temp) / 1000 if temp else None,
                  "soak_fs": {"total": du.total, "used": du.used, "free": du.free},
                  "root_free": root.free, "containers": json.loads(stats), "state": json.loads(state),
                  "other_services": json.loads(other), "collector": json.loads(status),
                  "steward": json.loads(steward), "memstat": json.loads(memstat)}))
EOF
