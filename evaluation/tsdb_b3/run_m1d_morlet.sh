#!/usr/bin/env bash
# M1d deviation (PROTOCOL.md): MorletDB 0.3.12-alpha.1 repeated at 256 MB, then ascending until it passes.
set -euo pipefail
cd "$(dirname "$0")"
V=/Users/zin/sophia/morletdb
export B3_READS_FRESH_PROCESS=1 B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.12-alpha.1
for cap in 256m-repeat 512m 1g; do
  c=${cap%-repeat}; out="results/m1d/$cap"; mkdir -p "$out/machine"
  echo "== m1d morletdb $cap $(date +%H:%M:%S)"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "$out/machine/morletdb.txt"
  t0=$(date +%s); rc=0
  B3_MEMORY=$c B3_SET= B3_RUN="m1d/$cap" perl -e 'alarm shift; exec @ARGV' 3000 $V/.venv-b3/bin/python run.py load morletdb-i1-sum D2 1 > "$out/morletdb.log" 2>&1 || rc=$?
  st=$(docker inspect -f '{{.State.OOMKilled}} {{.State.Running}} {{.RestartCount}} {{.HostConfig.Memory}}' b3-morletdb 2>/dev/null || echo missing)
  printf "%s\t%s\t%s\t%s\t%s\n" morletdb "$cap" "$rc" "$(( $(date +%s) - t0 ))" "$st" >> results/m1d/outcomes.tsv
  docker rm -f b3-morletdb >/dev/null 2>&1 || true
  echo "   rc=$rc state=[$st]"
  if [ "$cap" != 256m-repeat ] && [ $rc = 0 ]; then break; fi
done
