#!/usr/bin/env bash
# B5 storage (PROTOCOL.md section 3) and codec speed (section 4A), benchmark set: B4's six waveforms, the 18 Elf
# benchmark series and the four development series (reported apart). B3.16's start condition (1-minute load < 3, no
# process except Docker's VM above half a core, AC power, lid open or not causing sleep) before the run and again
# before the speed measurement, recorded; machine snapshots before and after. Budget: 2 hours for both (section 7);
# nothing starts after 07:00.
#   exit 0 done; 3 storage or speed stopped on a failure (S1: a round trip that is not bit for bit exact, or the
#   harness); 4 stopped by the budget; 1 refused (no quiet machine in time)
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
cd "$H" || exit 1
V=/Users/zin/sophia/morletdb
export MORLET_DATA=${MORLET_DATA:-$V/data} B5_BENCH=$H/bin/b5/rawcodec_bench B5_CODEC=$H/b5codec/target/release/b5codec
PY=$V/.venv-b4/bin/python
R=$H/results
quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
if [ "$(date +%H%M)" -lt "${B5_LATEST:-0700}" ]; then DEADLINE=$(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B5_LATEST:-0700}" +%s)
else DEADLINE=$(( $(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B5_LATEST:-0700}" +%s) + 86400 )); fi
snap() { mkdir -p "$R/machine"; { echo "-- $2"; date +%FT%T; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true; } >> "$R/machine/$1.txt"; }
wait_gate() { # wait_gate <until epoch>
  while [ "$(date +%s)" -lt "$1" ]; do powered && quiet && return 0; sleep 15; done; return 1; }
echo "waiting for a quiet, powered machine until $(date -r "$DEADLINE" +%FT%H:%M) ($(date +%H:%M:%S))"
wait_gate "$DEADLINE" || { echo "REFUSED: no quiet, powered machine before $(date -r "$DEADLINE" +%H:%M) ($(uptime))"; exit 1; }
STOP=$(( $(date +%s) + 7200 )); [ "$STOP" -lt "$DEADLINE" ] || STOP=$DEADLINE
export B5_STOP_AT=$STOP
{ echo "start condition met $(date +%FT%T)"; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true
  echo "budget ends $(date -r "$STOP" +%FT%T)"; } > "$R/b5_storage.start"
snap storage "before storage"
echo "== storage $(date +%H:%M:%S)"
"$PY" -I measure_b5.py storage "$R/b5_storage.json" benchmark > "$R/b5_storage.log" 2>&1; rc=$?
snap storage "after storage (exit $rc)"
[ $rc = 4 ] && { echo "STOPPED: budget, during storage $(date +%H:%M:%S)"; exit 4; }
[ $rc = 0 ] || { echo "STOPPED: storage failed (exit $rc) $(date +%H:%M:%S); see b5_storage.log"; exit 3; }
echo "== speed: waiting for the start condition $(date +%H:%M:%S)"
wait_gate "$STOP" || { echo "STOPPED: no quiet, powered machine for the speed measurement before the budget ended"; exit 4; }
{ echo "start condition met $(date +%FT%T)"; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true; } > "$R/b5_speed.start"
snap speed "before speed"
echo "== speed $(date +%H:%M:%S)"
"$PY" -I measure_b5.py speed "$R/b5_speed.json" benchmark > "$R/b5_speed.log" 2>&1; rc=$?
snap speed "after speed (exit $rc)"
[ $rc = 4 ] && { echo "STOPPED: budget, during speed $(date +%H:%M:%S)"; exit 4; }
[ $rc = 0 ] || { echo "STOPPED: speed failed (exit $rc) $(date +%H:%M:%S); see b5_speed.log"; exit 3; }
echo "== storage and speed done $(date +%H:%M:%S)"
