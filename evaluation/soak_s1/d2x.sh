#!/usr/bin/env bash
# Soak S1 amendment 2 (PROTOCOL.md): D2x, an orderly database restart after the test week, exploratory.
#   SOAK_DIR=/srv/morlet-soak ./d2x.sh
# Runs once. Measures resumption like disturb.sh does for D2 and logs one line to $SOAK_DIR/amendment2/disturbances.jsonl
# (not read by the week's analysis).
set -euo pipefail
SOAK_DIR=${SOAK_DIR:?Set SOAK_DIR}
mkdir -p "$SOAK_DIR/amendment2"
OUT=$SOAK_DIR/amendment2/disturbances.jsonl; ST=$SOAK_DIR/spool/status.json
cmd="docker restart soak-s1-db-1"
if [ -f "$OUT" ] && grep -q '"id": "D2x"' "$OUT"; then echo "D2x already ran (see $OUT)" >&2; exit 2; fi
field() { python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get(sys.argv[2], ""))' "$ST" "$1"; }
before=$(field stored)
at=$(python3 -c 'import time; print(time.time())')
$cmd >/dev/null
done_at=$(python3 -c 'import time; print(time.time())')
base=$(field stored)
resumed=""; after=""
for _ in $(seq 1 90); do
  sleep 10
  s_at=$(field at); s_stored=$(field stored)
  python3 -c "import sys; sys.exit(0 if float('$s_at') > $done_at and int('$s_stored') > int('$base') else 1)" 2>/dev/null \
    && { resumed=$s_at; after=$s_stored; break; }
done
python3 - "D2x" "$cmd" "$at" "$done_at" "$before" "$resumed" "$after" >> "$OUT" <<'PY'
import json, sys
i, cmd, at, done, before, resumed, after = sys.argv[1:]
at = float(at)
print(json.dumps({"id": i, "cmd": cmd, "at": at, "done_at": float(done), "stored_before": int(before or 0),
                  "resumed_at": float(resumed) if resumed else None,
                  "resume_s": round(float(resumed) - at, 1) if resumed else None,
                  "stored_after": int(after) if after else None}))
PY
tail -1 "$OUT"
