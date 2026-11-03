#!/bin/sh
# S1 (PROTOCOL.md): load, gate, then the timed steps, each once the 5-minute load is below 5.
set -u
cd "$(dirname "$0")/../.."
R=evaluation/chunk_summaries/results
export B3_SET=dev
wait_load() {
  while :; do
    l=$(sysctl -n vm.loadavg | awk '{print $3}')
    awk -v l="$l" 'BEGIN{exit !(l<5)}' && break
    echo "waiting: 5-min load $l"; sleep 60
  done
}
for step in load gate time ingest finish; do
  case $step in time|ingest) wait_load ;; esac
  echo "== $step $(date +%T)"
  .venv-b3/bin/python -I evaluation/chunk_summaries/s1.py $step > "$R/$step.log" 2>&1 || { echo "$step failed"; exit 1; }
done
echo done
