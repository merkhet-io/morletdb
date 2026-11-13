#!/usr/bin/env bash
# B3.19 round 3 reproduction (RESULTS.md, "Diagnosis"): fresh CedarDB D2 loads of the full dataset with the same image,
# adapter and read-back check as B3.19; reps from 4 up (REPRO_FROM, REPRO_TO) so that round 3's preserved volume (b3-cedardb-d2-r1) is untouched.
# Correctness only; nothing here is a speed measurement. A failing load keeps its container and volume.
set -uo pipefail
cd "$(dirname "$0")"
export B3_RUN=b319-repro B3_READS_FRESH_PROCESS=1
mkdir -p "results/$B3_RUN"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -6; } >> "results/$B3_RUN/start.txt"
for r in $(seq ${REPRO_FROM:-4} ${REPRO_TO:-6}); do
  echo "== cedardb D2 r$r $(date +%H:%M:%S)"
  if /Users/zin/sophia/morletdb/.venv-b3/bin/python run_cedar.py load D2 $r > "results/$B3_RUN/r$r.log" 2>&1; then
    echo "   pass"
  else
    echo "   FAILED: $(grep -A6 '"mismatches"' results/$B3_RUN/r$r.log | head -8 | tr -s ' \n' ' ')"; break
  fi
done
echo "== done $(date +%H:%M:%S)"
