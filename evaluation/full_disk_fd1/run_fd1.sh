#!/usr/bin/env bash
# FD1 (PROTOCOL.md): three repeats of each arm. Build the image first (see Dockerfile).
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=${FD1_PYTHON:-python3}
for r in 1 2 3; do for arm in bare bare-wal steward; do
  [ -f "evaluation/full_disk_fd1/results/${arm}_r${r}.json" ] && { echo "skip $arm r$r"; continue; }
  echo "== $arm r$r $(date +%H:%M:%S)"
  $PY -I evaluation/full_disk_fd1/fd1.py "$arm" "$r" || echo "FAILED $arm r$r"
done; done
echo "== FD1 done $(date +%H:%M:%S)"
