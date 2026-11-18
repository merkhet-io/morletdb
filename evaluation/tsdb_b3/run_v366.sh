#!/usr/bin/env bash
# #366 follow-up (PR #368): what the write-time decode check costs on each append path. Development set only
# (B3_SET=dev), one session, alternating; D2 reps 2-4 (no rep-1 storage/read stages). Not a benchmark statement.
#   images: the release 0.3.10-alpha.1; main at 8ecb7ac (#367: single-chunk path decodes twice); this branch (#368)
#   paths:  "morletdb" (B3's base client: one morlet_append_chunk call per chunk, the single-chunk path) and
#           "morletdb-i1-sum" (B3's current setup: morlet_append_chunks_raw, the batch path)
set -uo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_SET=dev B3_READS_FRESH_PROCESS=1
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
late() { [ "$(date +%H%M)" -ge 0700 ] && [ "$(date +%H%M)" -lt 1800 ]; }
until docker image inspect morletdb:dev-main-8ecb7ac morletdb:dev-366-skip >/dev/null 2>&1; do late && { echo "images not built before 07:00"; exit 1; }; sleep 30; done
ready=0; for _ in $(seq 1 $(( 600 * 4 ))); do powered && quiet && { ready=1; break; }; late && break; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine in time ($(uptime))"; exit 1; }
IMAGES=("ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1" "morletdb:dev-main-8ecb7ac" "morletdb:dev-366-skip")
for rep in 2 3 4; do
  for sys in morletdb morletdb-i1-sum; do
    for img in "${IMAGES[@]}"; do
      late && { echo "STOPPED at $(date +%H:%M): owner's day"; exit 1; }
      tag=$(echo "$img" | sed 's#.*:##')
      export B3_MORLET_IMAGE=$img B3_RUN=v366/$tag
      mkdir -p "results/dev/$B3_RUN/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -6; } >> "results/dev/$B3_RUN/machine/$sys.txt"
      echo "== $tag $sys r$rep $(date +%H:%M:%S)"
      "$V/.venv-b3/bin/python" run.py load "$sys" D2 $rep > "results/dev/$B3_RUN/${sys}_r$rep.log" 2>&1 || echo "   FAILED"
      docker rm -f b3-morletdb >/dev/null 2>&1
    done
  done
done
echo "== v366 done $(date +%H:%M:%S)"
