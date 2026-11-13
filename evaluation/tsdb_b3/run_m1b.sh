#!/usr/bin/env bash
# Study M1b (PROTOCOL.md): CedarDB's smallest memory cap, MorletDB 0.3.9 at 256 MB and 16 GB, M1's rules.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_READS_FRESH_PROCESS=1 B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
ready=0; for _ in $(seq 1 $(( ${M1_WAIT_MIN:-60} * 4 ))); do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine with Docker in time ($(uptime))"; exit 1; }
mkdir -p results/m1b; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> results/m1b/m1b.start
clean() { docker rm -f b3-cedardb b3-morletdb >/dev/null 2>&1 || true; }
cmd() { case $1 in
  morletdb) echo "$V/.venv-b3/bin/python run.py load morletdb-i1-sum D2 1" ;;
  cedardb)  echo "$V/.venv-b3/bin/python run_cedar.py load D2 1" ;;
esac; }
pref() { case $1 in morletdb) echo morletdb-i1-sum ;; *) echo "$1" ;; esac; }
passed() { local f="results/m1b/$2/load_$(pref "$1")_D2_r1.json"; [ -f "$f" ] && grep -q '"correct": true' "$f" &&
  awk -F'\t' -v s="$1" -v c="$2" '$1==s && $2==c && $3==0{ok=1} END{exit !ok}' results/m1b/outcomes.tsv 2>/dev/null; }
tried() { awk -F'\t' -v s="$1" -v c="$2" '$1==s && $2==c{t=1} END{exit !t}' results/m1b/outcomes.tsv 2>/dev/null; }
load() { local s=$1 cap=$2 out="results/m1b/$2"; mkdir -p "$out/machine"
  if tried "$s" "$cap"; then echo "skip $s $cap"; return; fi
  powered || { echo "STOPPED: not on AC power before $s $cap"; exit 1; }
  echo "== m1b $s $cap $(date +%H:%M:%S)"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "$out/machine/$s.txt"
  local t0 rc=0; t0=$(date +%s)
  if [ "$cap" = 16g ]; then unset B3_MEMORY; else export B3_MEMORY=$cap; fi
  B3_SET= B3_RUN="m1b/$cap" perl -e 'alarm shift; exec @ARGV' 1200 $(cmd "$s") > "$out/$s.log" 2>&1 || rc=$?
  local st; st=$(docker inspect -f '{{.State.OOMKilled}} {{.State.Running}} {{.RestartCount}} {{.HostConfig.Memory}}' "b3-$s" 2>/dev/null || echo "missing")
  { date +%FT%T; uptime; } >> "$out/machine/$s.txt"
  printf "%s\t%s\t%s\t%s\t%s\n" "$s" "$cap" "$rc" "$(( $(date +%s) - t0 ))" "$st" >> results/m1b/outcomes.tsv
  clean; unset B3_MEMORY
  echo "   rc=$rc state=[$st] $(( $(date +%s) - t0 ))s"; }
clean
load cedardb 16g; load morletdb 16g
for cap in 256m 512m 1g 2g 4g; do
  if passed cedardb "$cap"; then break; fi
  load cedardb "$cap"
  if passed cedardb "$cap"; then break; fi
done
load morletdb 256m
echo "== M1b done $(date +%H:%M:%S)"
