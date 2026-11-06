#!/usr/bin/env bash
# Study M1 (PROTOCOL.md, "Study M1: memory caps"): the smallest memory cap at which each system completes B3's D2
# load with every read exact. Caps 256m, 512m, 1g, 2g, 4g (swap disabled), ascending per system until the first pass,
# plus one uncapped reference load (16g, B3's limit) per system in the same session. Keep the Mac awake on AC power.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
: "${MORLETDB_NATIVE_LIB:?set MORLETDB_NATIVE_LIB to libmorlet_ffi.dylib from the 0.3.7-alpha.1 macOS wheel}"
export B3_READS_FRESH_PROCESS=1 MORLETDB_NATIVE_LIB B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.7-alpha.1
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${M1_WAIT_MIN:-60} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine with Docker in time ($(uptime))"; exit 1; }
mkdir -p results/m1; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } > results/m1/m1.start
clean() { docker rm -f b3-questdb b3-tdengine b3-timescale b3-iotdb b3-crate b3-morletdb >/dev/null 2>&1 || true; }
cont() { case $1 in crate) echo b3-crate ;; *) echo "b3-$1" ;; esac; }
cmd() { case $1 in
  morletdb)  echo "$V/.venv-b3/bin/python run.py load morletdb-i2 D2 1" ;;
  questdb)   echo "$V/.venv-b3/bin/python run.py load questdb D2 1" ;;
  timescale) echo "$V/.venv-b3/bin/python run_timescale.py load D2 1" ;;
  tdengine)  echo "$V/.venv-b3t/bin/python run_tdengine.py load default D2 1" ;;
  iotdb)     echo "$V/.venv-b3i/bin/python run_iotdb.py load table D2 1" ;;
  crate)     echo "$V/.venv-b3c/bin/python run_crate.py load array D2 1" ;;
esac; }
heap() { case $1 in 256m) echo 128m ;; 512m) echo 256m ;; 1g) echo 512m ;; 2g) echo 1g ;; 4g) echo 2g ;; 16g) echo 8g ;; esac; }
# load <system> <cap>: one D2 load under the cap; outcome line in results/m1/outcomes.tsv
load() { local s=$1 cap=$2 out="results/m1/$2"; mkdir -p "$out/machine"
  powered || { echo "STOPPED: not on AC power before $s $cap"; exit 1; }
  echo "== m1 $s $cap $(date +%H:%M:%S)"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "$out/machine/$s.txt"
  local t0 rc=0; t0=$(date +%s)
  if [ "$cap" = 16g ]; then unset B3_MEMORY; else export B3_MEMORY=$cap; fi
  export B3_CRATE_HEAP; B3_CRATE_HEAP=$(heap "$cap")
  B3_SET= B3_RUN="m1/$cap" perl -e 'alarm shift; exec @ARGV' 1200 $(cmd "$s") > "$out/$s.log" 2>&1 || rc=$?
  local st; st=$(docker inspect -f '{{.State.OOMKilled}} {{.State.Running}} {{.RestartCount}} {{.HostConfig.Memory}}' "$(cont "$s")" 2>/dev/null || echo "missing")
  { date +%FT%T; uptime; } >> "$out/machine/$s.txt"
  printf "%s\t%s\t%s\t%s\t%s\n" "$s" "$cap" "$rc" "$(( $(date +%s) - t0 ))" "$st" >> results/m1/outcomes.tsv
  clean; unset B3_MEMORY
  echo "   rc=$rc state=[$st] $(( $(date +%s) - t0 ))s"; }
# pass: the runner exited 0 within the time limit and wrote a result with every count and read correct (an
# out-of-memory kill stops the database, so the load cannot complete correctly); container state is recorded only
passed() { local f="results/m1/$2/load_$(pref "$1")_D2_r1.json"; [ -f "$f" ] && grep -q '"correct": true' "$f" &&
  awk -F'\t' -v s="$1" -v c="$2" '$1==s && $2==c && $3==0{ok=1} END{exit !ok}' results/m1/outcomes.tsv; }
pref() { case $1 in morletdb) echo morletdb-i2 ;; tdengine) echo tdengine-default ;; iotdb) echo iotdb-table ;; crate) echo crate-array ;; *) echo "$1" ;; esac; }
SYSTEMS="morletdb questdb timescale tdengine iotdb crate"
clean
for s in $SYSTEMS; do load "$s" 16g; done
for cap in 256m 512m 1g 2g 4g; do
  for s in $SYSTEMS; do
    [ -f "results/m1/min_$s" ] && continue
    load "$s" "$cap"
    if passed "$s" "$cap"; then echo "$cap" > "results/m1/min_$s"; echo "   PASS $s at $cap"; else echo "   no pass $s at $cap"; fi
  done
done
echo "== M1 done $(date +%H:%M:%S)"; for s in $SYSTEMS; do echo "$s min $(cat results/m1/min_$s 2>/dev/null || echo '>4g')"; done
