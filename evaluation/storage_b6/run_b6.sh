#!/usr/bin/env bash
# B6's run (PROTOCOL.md sections 3, 4 and 7), in this order: programs checked (setup), inputs built (prepare), storage,
# codec speed, the B3 session, platform hashes, the Raspberry Pi; then the summary.
#
#   nohup caffeinate -ims evaluation/storage_b6/run_b6.sh > evaluation/storage_b6/results/b6_run.log 2>&1 &
#   evaluation/storage_b6/run_b6.sh dev        the harness test: development inputs only (gate 7), results in
#                                               results/dev/dryrun/; start conditions recorded, not waited for; B3 as
#                                               the smoke check (B3's development set); no Pi
#
# Resumable: a step that completed leaves OUT/steps/<step>.done and is skipped when the runner starts again; a step that
# stopped or failed is run again from its start. Every step is logged with machine snapshots (OUT/machine/run.txt:
# load, power, busiest processes) before and after.
# Stops (as B5): a failure in storage or speed (S1, or the harness: exit 3) stops B6 before the B3 session; a budget stop
# there (exit 4) does not. No step starts after 07:00 (B6_LATEST) and nothing runs into the owner's day.
# Budgets (section 7): storage and codec speed 2 hours together (B6_STOP_AT, from the first storage start); the B3
# session 3 hours (run_b6_b3.sh); platform hashes 3 hours.
# Environment: MORLET_DATA (default /Users/zin/sophia/morletdb/data), B6_PI=user@host for the Pi step (without it the
# step is left for a later start), B6_WHEEL_DIR for setup when bin/release is not installed yet.
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
V=${B6_VENV_ROOT:-/Users/zin/sophia/morletdb}
PY=$V/.venv-b3/bin/python
export MORLET_DATA=${MORLET_DATA:-$V/data}
MODE=${1:-benchmark}
case $MODE in
  benchmark) SET=benchmark; OUT=$H/results; WORK=$H/work/benchmark;;
  dev) SET=dev; OUT=$H/results/dev/dryrun; WORK=$H/work/dev; export B6_DRYRUN=1;;
  *) echo "usage: run_b6.sh [benchmark|dev]"; exit 2;;
esac
mkdir -p "$OUT/steps" "$OUT/machine" "$WORK"
if [ "$(date +%H%M)" -lt "${B6_LATEST:-0700}" ]; then DEADLINE=$(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B6_LATEST:-0700}" +%s)
else DEADLINE=$(( $(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B6_LATEST:-0700}" +%s) + 86400 )); fi
[ "$MODE" = dev ] && DEADLINE=$(( $(date +%s) + 86400 ))

snap() { { echo "-- $1"; date +%FT%T; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true; } >> "$OUT/machine/run.txt"; }
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
wait_gate() { # wait for B3.16's start condition until epoch $1 (the harness test records it, does not wait)
  [ "$MODE" = dev ] && return 0
  while [ "$(date +%s)" -lt "$1" ]; do powered && quiet && return 0; sleep 15; done; return 1; }
step() { # step <name> <command...>: skipped when done; logged with snapshots; marked done on exit 0
  local s=$1; shift
  if [ -f "$OUT/steps/$s.done" ]; then echo "== $s: done earlier ($(cat "$OUT/steps/$s.done")), skipped"; return 0; fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then echo "== $s: NOT STARTED, $(date +%H:%M) is the owner's day"; return 5; fi
  echo "== $s $(date +%FT%T) load $(sysctl -n vm.loadavg)"; snap "before $s"
  "$@"; local rc=$?
  snap "after $s (exit $rc)"; echo "== $s exit $rc $(date +%FT%T)"
  [ $rc = 0 ] && date +%FT%T > "$OUT/steps/$s.done"
  return $rc; }

do_setup() { "$H/setup_b6.sh" ${B6_WHEEL_DIR:+"$B6_WHEEL_DIR"} && cp "$H/bin/programs.json" "$OUT/b6_programs.json"; }
do_prepare() { "$PY" -I "$H/prepare_b6.py" "$SET" "$WORK" > "$OUT/prepare.log" 2>&1 && cp "$WORK/inputs.json" "$OUT/b6_inputs.json"; }
budget() { # the 2-hour storage and speed budget, from the first storage start (kept across restarts)
  [ -f "$OUT/steps/budget" ] || echo $(( $(date +%s) + 7200 )) > "$OUT/steps/budget"
  local stop; stop=$(cat "$OUT/steps/budget"); [ "$stop" -lt "$DEADLINE" ] || stop=$DEADLINE
  export B6_STOP_AT=$stop; }
do_storage() {
  wait_gate "$DEADLINE" || { echo "REFUSED: no quiet, powered machine before 07:00"; return 4; }
  budget
  "$PY" -I "$H/measure_b6.py" storage "$WORK" "$OUT/b6_storage.json" > "$OUT/b6_storage.log" 2>&1; }
do_speed() { budget; "$PY" -I "$H/measure_b6.py" speed "$WORK" "$OUT/b6_speed.json" > "$OUT/b6_speed.log" 2>&1; }
do_b3() { if [ "$MODE" = dev ]; then "$H/run_b6_b3.sh" smoke "$OUT"; else "$H/run_b6_b3.sh" "$OUT"; fi; }
do_platforms() {
  "$H/platforms_b6.sh" "$WORK" "$OUT/platforms"; local rc=$?
  "$PY" -I "$H/measure_b6.py" platforms "$WORK" "$OUT/platforms" "$OUT/b6_platforms.json" || rc=3
  return $rc; }
do_pi() {
  if [ "$MODE" = dev ]; then echo "skipped: no Pi in the harness test"; return 0; fi
  if [ -z "${B6_PI:-}" ]; then echo "left for later: B6_PI is not set (run_b6.sh again with B6_PI=user@host)"; return 6; fi
  "$H/pi_b6.sh" "$WORK" "$OUT/platforms" && "$PY" -I "$H/measure_b6.py" platforms "$WORK" "$OUT/platforms" "$OUT/b6_platforms.json"; }

echo "== B6 run ($MODE) $(date +%FT%T) at $(git -C "$H" rev-parse --short HEAD); no step after $(date -r "$DEADLINE" +%FT%H:%M)"
step setup do_setup || { echo "B6 STOPPED: programs do not match the build record"; exit 1; }
step prepare do_prepare || { echo "B6 STOPPED: inputs could not be built (see prepare.log)"; exit 1; }
step storage do_storage; rc=$?
case $rc in 0|4|5) ;; *) echo "B6 STOPPED after storage (exit $rc): see b6_storage.log"; exit $rc;; esac
step speed do_speed; rc=$?
case $rc in 0|4|5) ;; *) echo "B6 STOPPED after speed (exit $rc): see b6_speed.log"; exit $rc;; esac
step b3 do_b3 || echo "B3 session exit $? (failed or not-run loads are reported; the run continues)"
step platforms do_platforms || echo "platforms exit $? (see platforms/*.status)"
step pi do_pi || echo "Pi step not done (exit $?)"
"$PY" -I "$H/summarize_b6.py" "$OUT" > "$OUT/b6_summary.json" 2> "$OUT/summary.err"
"$PY" -I "$H/summarize_b6.py" "$OUT" --md > "$OUT/b6_summary.md" 2>> "$OUT/summary.err"
echo "== B6 run ($MODE) finished $(date +%FT%T): $(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['verdict'])" "$OUT/b6_summary.json" 2>/dev/null)"
