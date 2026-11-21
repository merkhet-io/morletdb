#!/usr/bin/env bash
# Soak S2 planned disturbances (PROTOCOL.md, "Phases" and H3), scheduled: nobody has to start them by hand.
#   SOAK_DIR=/srv/morlet-soak/s2 ./disturb.sh schedule <week start, epoch s>   # once, at the start of the test week
#   SOAK_DIR=/srv/morlet-soak/s2 ./disturb.sh due                              # cron, every 5 minutes
#   SOAK_DIR=/srv/morlet-soak/s2 ./disturb.sh schedule-test <start>            # shakedown: D1 at +1 h, D2 at +2 h
#   */5 * * * * SOAK_DIR=/srv/morlet-soak/s2 /path/to/evaluation/soak_s2/disturb.sh due
#
# schedule: draws each disturbance's time once, uniformly inside its window, from the week start and seed 529, and
#   writes $SOAK_DIR/results/schedule.json. Refuses a second schedule. Nobody chooses the times.
#     D1, a collector crash: SIGKILL to the collector's process on the host (S1's D1r: Docker sees an unexpected exit,
#         so the restart policy restarts it), between start + 48 h and start + 72 h;
#     D2, an orderly database restart (docker restart), between start + 96 h and start + 120 h.
# due: runs each scheduled disturbance whose time has come, once, and never after its window has closed (a late
#   one is logged as missed, not run). One run at a time (flock).
#
# Appends one line per disturbance to $SOAK_DIR/results/disturbances.jsonl, which analyze.py reads:
#   {"id", "cmd", "scheduled_at", "at", "done_at", "stored_before", "base", "resumed_at", "resume_s", "stored_after"}
# Resumption (H3) is measured from the database, not from the collector's per-process counter (S1 amendment 2):
# "stored" is the sum of the recordings' stored ends in public.live, read with psql in the database container.
# base is the stored count once the disturbance has returned (for D2, the first count the restarted database
# answers); storing has resumed at the first poll whose count is above base. Polls every 10 s for up to 15 minutes;
# if storing has not resumed by then, resume_s is null (H3 fails). One deadline of 15 minutes from the disturbance
# covers both waits (for the database to answer, then for a count above base), however long each psql call takes.
# Before its command runs, each attempt is appended to results/disturb.attempts.jsonl, so an interrupted run is
# logged as interrupted on the next tick and never repeated.
set -euo pipefail
SOAK_DIR=${SOAK_DIR:?Set SOAK_DIR}
RES=$SOAK_DIR/results; SCHED=$RES/schedule.json; OUT=$RES/disturbances.jsonl; ATT=$RES/disturb.attempts.jsonl
mkdir -p "$RES"
now() { python3 -c 'import time; print(time.time())'; }
stored() {   # the database's stored samples, or empty while it does not answer
  timeout 20 docker exec soak-s2-db-1 psql -U morlet -d morlet -XAtqc \
    "SELECT coalesce(sum((checkpoint->>'end')::bigint),0) FROM public.live" 2>/dev/null || true
}
log() { echo "$(date -u +%FT%TZ) $*" >> "$RES/disturb.log"; }

case ${1:?schedule, schedule-test or due} in
  schedule)
    start=${2:?week start, epoch seconds}
    [ -e "$SCHED" ] && { echo "already scheduled: $(cat "$SCHED")" >&2; exit 2; }
    python3 - "$start" > "$SCHED" <<'PY'
import json, random, sys
start = int(sys.argv[1])
windows = {"D1": (48, 72), "D2": (96, 120)}
out = {"start": start, "seed": 529, "disturbances": {}}
for i, (lo, hi) in windows.items():
    r = random.Random(f"S2-{i}-{start}-529")
    # at least 30 minutes inside the window on both sides, so a late cron tick still falls inside it
    at = start + lo * 3600 + 1800 + r.uniform(0, (hi - lo) * 3600 - 3600)
    out["disturbances"][i] = {"at": round(at), "window": [start + lo * 3600, start + hi * 3600]}
print(json.dumps(out))
PY
    cat "$SCHED"; exit 0 ;;
  schedule-test)   # shakedown only: D1 one hour and D2 two hours after the given start, in half-hour windows
    start=${2:?shakedown start, epoch seconds}
    [ -e "$SCHED" ] && { echo "already scheduled: $(cat "$SCHED")" >&2; exit 2; }
    python3 -c 'import json,sys; s=int(sys.argv[1]); print(json.dumps({"start": s, "test": True, "disturbances": {
      "D1": {"at": s + 3600, "window": [s + 1800, s + 5400]}, "D2": {"at": s + 7200, "window": [s + 5400, s + 9000]}}}))' \
      "$start" > "$SCHED"
    cat "$SCHED"; exit 0 ;;
  due) ;;
  *) echo "schedule, schedule-test or due" >&2; exit 2 ;;
esac

[ -e "$SCHED" ] || exit 0
exec 9>"$RES/.disturb.lock"
flock -n 9 || exit 0
t=$(now)
for id in D1 D2; do
  read -r at lo hi < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1]))["disturbances"][sys.argv[2]]; print(d["at"], *d["window"])' "$SCHED" "$id")
  [ -f "$OUT" ] && grep -q "\"id\": \"$id\"" "$OUT" && continue
  # once only: an attempt is recorded before its command runs. One that was started but never logged (the script was
  # interrupted) is logged as interrupted, with resume_s null (H3 fails), and never run again.
  if [ -f "$ATT" ] && grep -q "\"id\": \"$id\"" "$ATT"; then
    log "$id interrupted: attempted ($(grep "\"id\": \"$id\"" "$ATT" | tail -1)) but not logged"
    python3 -c 'import json,sys; a=[json.loads(l) for l in open(sys.argv[3]) if json.loads(l)["id"]==sys.argv[1]][-1]; print(json.dumps({"id": sys.argv[1], "cmd": a["cmd"], "scheduled_at": float(sys.argv[2]), "at": a["at"], "interrupted": True, "resume_s": None}))' "$id" "$at" "$ATT" >> "$OUT"
    continue
  fi
  python3 -c "import sys; sys.exit(0 if $t >= $at else 1)" || continue
  if python3 -c "import sys; sys.exit(0 if $t >= $hi else 1)"; then
    log "$id missed: window closed at $hi"
    python3 -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "cmd": None, "scheduled_at": float(sys.argv[2]), "at": float(sys.argv[2]), "missed": True}))' "$id" "$at" >> "$OUT"
    continue
  fi
  if [ "$id" = D1 ]; then
    pid=$(docker inspect -f '{{.State.Pid}}' soak-s2-collector-1 2>/dev/null || echo 0)
    if [ "${pid:-0}" -le 0 ]; then log "D1 deferred: collector not running"; continue; fi
    cmd="kill -s KILL $pid"
  else
    cmd="docker restart soak-s2-db-1"
  fi
  before=$(stored)
  t_at=$(now); log "$id start: $cmd"
  python3 -c 'import json,sys; print(json.dumps({"id": sys.argv[1], "cmd": sys.argv[2], "at": float(sys.argv[3])}))' \
    "$id" "$cmd" "$t_at" >> "$ATT"
  $cmd >/dev/null || log "$id: command returned $?"
  t_done=$(now)
  # one 15-minute deadline from the disturbance for the whole measurement; a poll starts every 10 s at most
  deadline=$(python3 -c "print($t_at + 900)")
  left() { python3 -c "import sys,time; sys.exit(0 if time.time() < $deadline else 1)"; }
  base=""
  while left; do base=$(stored); [ -n "$base" ] && break; sleep 10; done
  resumed=""; after=""
  if [ -n "$base" ]; then
    while left; do
      sleep 10
      s=$(stored)
      if [ -n "$s" ] && [ "$s" -gt "$base" ]; then resumed=$(now); after=$s; break; fi
    done
  fi
  python3 - "$id" "$cmd" "$at" "$t_at" "$t_done" "$before" "$base" "$resumed" "$after" >> "$OUT" <<'PY'
import json, sys
i, cmd, sched, at, done, before, base, resumed, after = sys.argv[1:]
at = float(at)
num = lambda s: int(s) if s else None
print(json.dumps({"id": i, "cmd": cmd, "scheduled_at": float(sched), "at": at, "done_at": float(done),
                  "stored_before": num(before), "base": num(base),
                  "resumed_at": float(resumed) if resumed else None,
                  "resume_s": round(float(resumed) - at, 1) if resumed else None, "stored_after": num(after)}))
PY
  log "$id done: $(tail -1 "$OUT")"
done
