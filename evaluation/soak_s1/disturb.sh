#!/usr/bin/env bash
# Soak S1 planned disturbances (PROTOCOL.md, "Phases" and H3), run by hand once each, on the soak box:
#   SOAK_DIR=/srv/morlet-soak ./disturb.sh D1 <week start, epoch s>   # day 3: kill the collector (a crash)
#   SOAK_DIR=/srv/morlet-soak ./disturb.sh D2 <week start, epoch s>   # day 5: restart the database (orderly)
#   SOAK_DIR=/srv/morlet-soak ./disturb.sh D1r <week start, epoch s>  # amendment 1: D1 again as a real crash
# Refuses outside its day (D1: start+48 h .. start+72 h; D2: start+96 h .. start+120 h) and if it already ran.
# Appends one line to $SOAK_DIR/results/disturbances.jsonl, which analyze.py reads:
#   {"id", "cmd", "at", "done_at", "stored_before", "resumed_at", "resume_s", "stored_after"}
# Resumption (H3): the collector rewrites spool/status.json every minute; "stored" counts samples it stored since its
# process started. D1: resumed at the first status of the new process (started after the kill) with stored > 0.
# D2: resumed at the first status written after the restart with stored above its value when the restart returned.
# Polls every 10 s for up to 15 minutes; if storing has not resumed by then, resume_s is null (H3 fails).
set -euo pipefail
SOAK_DIR=${SOAK_DIR:?Set SOAK_DIR}
id=${1:?D1 or D2}; start=${2:?week start, epoch seconds}
OUT=$SOAK_DIR/results/disturbances.jsonl; ST=$SOAK_DIR/spool/status.json
case $id in
  D1) cmd="docker kill -s KILL soak-s1-collector-1"; lo=48; hi=72 ;;
  D2) cmd="docker restart soak-s1-db-1"; lo=96; hi=120 ;;
  D1r) # amendment 1: docker kill is a manual stop (no restart); SIGKILL to the process is a crash
    pid=$(docker inspect -f '{{.State.Pid}}' soak-s1-collector-1)
    [ "${pid:-0}" -gt 0 ] || { echo "collector not running" >&2; exit 2; }
    cmd="kill -s KILL $pid"; lo=48; hi=72
    { [ -f "$SOAK_DIR/results/disturbances.jsonl" ] && grep -q '"id": "D1"' "$SOAK_DIR/results/disturbances.jsonl"; } \
      || { echo "D1r only repeats a D1 that ran" >&2; exit 2; } ;;
  *) echo "D1, D2 or D1r" >&2; exit 2 ;;
esac
now=$(date +%s)
if [ "$now" -lt $((start + lo * 3600)) ] || [ "$now" -ge $((start + hi * 3600)) ]; then
  echo "$id belongs between $(date -ud @$((start + lo * 3600))) and $(date -ud @$((start + hi * 3600))); not now" >&2; exit 2
fi
if [ -f "$OUT" ] && grep -q "\"id\": \"$id\"" "$OUT"; then echo "$id already ran (see $OUT)" >&2; exit 2; fi
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2], ""))' "$ST" "$1"; }
before=$(field stored)
at=$(python3 -c 'import time; print(time.time())')
$cmd >/dev/null
done_at=$(python3 -c 'import time; print(time.time())')
base=$([ "$id" = D2 ] && field stored || echo 0)
resumed=""; after=""
for _ in $(seq 1 90); do
  sleep 10
  s_at=$(field at); s_started=$(field started); s_stored=$(field stored)
  if [ "$id" != D2 ]; then
    python3 -c "import sys; sys.exit(0 if float('$s_started') > $at and int('$s_stored') > 0 else 1)" 2>/dev/null && { resumed=$s_at; after=$s_stored; break; }
  else
    python3 -c "import sys; sys.exit(0 if float('$s_at') > $done_at and int('$s_stored') > int('$base') else 1)" 2>/dev/null && { resumed=$s_at; after=$s_stored; break; }
  fi
done
python3 - "$id" "$cmd" "$at" "$done_at" "$before" "$resumed" "$after" >> "$OUT" <<'EOF'
import json, sys
i, cmd, at, done, before, resumed, after = sys.argv[1:]
at = float(at)
print(json.dumps({"id": i, "cmd": cmd, "at": at, "done_at": float(done), "stored_before": int(before or 0),
                  "resumed_at": float(resumed) if resumed else None,
                  "resume_s": round(float(resumed) - at, 1) if resumed else None,
                  "stored_after": int(after) if after else None}))
EOF
tail -1 "$OUT"
