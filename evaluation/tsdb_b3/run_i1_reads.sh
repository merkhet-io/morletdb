#!/usr/bin/env bash
# I1 read investigation (issue #233; exploratory attribution after B3.11, not a stage). Three arms in rotation,
# three fresh D2 loads each, the read benchmark three times on every load:
#   rel-033   old write path, released image 0.3.5-alpha.1   (B3.10/B3.11's baseline)
#   b310-033  old write path, local image morletdb:b310       (separates the build)
#   b310-i1   I1 write path,  local image morletdb:b310       (B3.10/B3.11's new arm)
set -euo pipefail
cd "$(dirname "$0")"
PY=${PY:-/Users/zin/sophia/morletdb/.venv-b3/bin/python}
export B3_READS_EVERY_REP=1 B3_READ_REPEATS=3
snap() { { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -8 || true; } >> "results/$B3_RUN/machine/$1_r$2.txt"; }
arm() {   # run-name system image rep
  export B3_RUN=$1; mkdir -p "results/$1/machine"
  if [ -f "results/$1/load_$2_D2_r$4.json" ] && grep -q '"correct": true' "results/$1/load_$2_D2_r$4.json"; then echo "skip $*"; return; fi
  echo "== $* $(date +%H:%M:%S)"; snap "$2" "$4"
  B3_MORLET_IMAGE=$3 "$PY" run.py load "$2" D2 "$4"; snap "$2" "$4"
}
for r in 1 2 3; do
  arm i1r-rel-033 morletdb-033 ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1 "$r"
  arm i1r-b310-033 morletdb-033 morletdb:b310 "$r"
  arm i1r-b310-i1 morletdb-i1 morletdb:b310 "$r"
done
echo "== done $(date +%H:%M:%S)"
