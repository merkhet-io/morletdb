#!/usr/bin/env bash
# B5 (PROTOCOL.md section 4B): B3's ingestion and reads in one session, three arms:
#   g2  morletdb-i2 (Grizzbit 2): image morletdb:b5 (deploy/Dockerfile at the build commit 5ce0328), decoder
#       libmorlet_ffi.dylib built from the same commit (sha256 checked below; without it the arm fails, never raw reads)
#   q   QuestDB 10.1.0 with its B3 configuration (B3.16's adapter, unchanged)
#   rel morletdb-i2 (release): ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1, decoder libmorlet_ffi.dylib from the
#       release's macOS wheel (wheel checked against the release's SHA256SUMS; dylib sha256 checked below)
# Track S (B3.16): r = 1, 2, 3; D2 then D1; order g2, q, rel; full correctness after every load; on D2 r1 storage,
#   reads and statistics from a fresh client process (B3_READS_FRESH_PROCESS=1); MorletDB storage also per group as
#   chunk bytes with chunk-kind counts (b3_load.py).
# Track P (B3.17): r = 1, 2, 3; (8 writers, D2) then (8 writers, D1); order g2, q, rel; ingestion only. B3.17's
#   MorletDB arm in parallel.py is `morletdb-i1`, B3.10's write path (256 chunks per transaction, one connection per
#   writer, batched raw appends, server defaults) - the same write path as morletdb-i2, whose difference is reads.
# A failed load is logged and rerun once at the end of the session. B3.16's start condition before the session
# (1-minute load < 3, no process except Docker's VM above half a core, AC power, lid open or not causing sleep);
# power and lid before every load. No load starts after 07:00 or after the 3-hour session budget; loads done are
# reported as such. Fresh volumes b5-* (B3_VOLUME_PREFIX); B3's own volumes are never touched.
#
#   run_b5_b3.sh            the session; results in evaluation/storage_b5/results/b3-single/, b3-parallel/
#   run_b5_b3.sh smoke      development set (B3_SET=dev), one D2 load per arm and track; results/dev/b3-smoke/
#   run_b5_b3.sh s6r        amendment B5.S6r: the session again, start condition before every load; results/s6r/
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
cd "$H/../tsdb_b3" || exit 1
V=/Users/zin/sophia/morletdb
PY=$V/.venv-b3/bin/python
REL=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1
G2=morletdb:b5
REL_LIB=$H/bin/release/libmorlet_ffi.dylib
G2_LIB=$H/bin/b5/libmorlet_ffi.dylib
REL_LIB_SHA=343a8d9fd17d19c3c0ece5aab270d6ec9e7f3eb3591573cf321b49fd68aa42ff   # from the 0.3.10-alpha.1 macOS wheel
G2_LIB_SHA=e55b6d38a65282d58c81ff93ec1e880f2f5c66078c5f5459d7991941c7fbcebd    # built from 5ce0328
MODE=${1:-session}
export B3_READS_FRESH_PROCESS=1
if [ "$MODE" = smoke ]; then export B3_SET=dev; OUT=$H/results/dev/b3-smoke
elif [ "$MODE" = s6r ]; then OUT=$H/results/s6r   # amendment B5.S6r: the session again, gate before every load
else OUT=$H/results; fi
VP=b5; [ "$MODE" = s6r ] && VP=b5-s6r
notrun=()

[ "$(shasum -a 256 "$REL_LIB" | cut -d' ' -f1)" = "$REL_LIB_SHA" ] || { echo "REFUSED: release decoder sha256"; exit 1; }
[ "$(shasum -a 256 "$G2_LIB" | cut -d' ' -f1)" = "$G2_LIB_SHA" ] || { echo "REFUSED: B5 decoder sha256"; exit 1; }

quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
# the smoke check tests the harness only (no figure from it is used): it does not wait for a quiet machine
[ "$MODE" = smoke ] && quiet() { true; }
# the next 07:00 (the owner's day): no load starts after it
if [ "$(date +%H%M)" -lt "${B5_LATEST:-0700}" ]; then DEADLINE=$(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B5_LATEST:-0700}" +%s)
else DEADLINE=$(( $(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B5_LATEST:-0700}" +%s) + 86400 )); fi
echo "waiting for a quiet, powered machine until $(date -r "$DEADLINE" +%FT%H:%M) ($(date +%H:%M:%S))"
ready=0; while [ "$(date +%s)" -lt "$DEADLINE" ]; do docker info >/dev/null 2>&1 && powered && quiet && { ready=1; break; }; sleep 15; done
[ "$ready" = 1 ] || { echo "REFUSED: no quiet, powered machine before $(date -r "$DEADLINE" +%H:%M) ($(uptime))"; exit 1; }
SESSION_END=$(( $(date +%s) + 3 * 3600 ))   # section 7: the B3 session takes at most 3 hours
mkdir -p "$OUT/b3-single" "$OUT/b3-parallel"
{ echo "mode $MODE"; date +%FT%T; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true
  docker ps --format '{{.Names}}'; docker system df
  for i in "$G2" "$REL" questdb/questdb:10.1.0; do echo "$i $(docker image inspect "$i" --format '{{.Id}}')"; done
  echo "decoder g2 $G2_LIB $G2_LIB_SHA"; echo "decoder rel $REL_LIB $REL_LIB_SHA"
  echo "session ends by $(date -r "$SESSION_END" +%H:%M:%S), no load after $(date -r "$DEADLINE" +%FT%H:%M)"; } > "$OUT/b3-single/b5_b3.start"

snap() { mkdir -p "$1/machine"; { date +%FT%T; uptime; ps -Ao pcpu,comm -r | head -9 || true; } >> "$1/machine/$2.txt"; }
done_ok() { local f="$1/$2.json"; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$3" != D2r1 ] || grep -q '"analytics' "$f"; }; }
failed=()
stop_now() { local t; t=$(date +%s)
  if [ "$t" -ge "$DEADLINE" ]; then echo "STOPPED before $1: $(date +%H:%M) is the owner's day; loads done are reported as such"; return 0; fi
  if [ "$t" -ge "$SESSION_END" ]; then echo "STOPPED before $1: the 3-hour session budget ended; loads done are reported as such"; return 0; fi
  return 1; }
clean() { docker rm -f b3-questdb b3-morletdb >/dev/null 2>&1 || true; }
# arm <g2|q|rel> sets: SYS (S track system), IMG, LIB, PRE (volume prefix), SUB (results subdirectory)
arm() { case $1 in
  g2) SYS=morletdb-i2; IMG=$G2; LIB=$G2_LIB; PRE=$VP-g2; SUB=grizzbit2;;
  q) SYS=questdb; IMG=$REL; LIB=$REL_LIB; PRE=$VP-q; SUB=questdb;;
  rel) SYS=morletdb-i2; IMG=$REL; LIB=$REL_LIB; PRE=$VP-rel; SUB=release;; esac; }
# one load: run <S|P> <arm> <D> <rep> [writers]
run() { local track=$1 a=$2 d=$3 r=$4 w=${5:-8} dir name sys tag
  arm "$a"
  if [ "$track" = S ]; then dir=$OUT/b3-single/$SUB; name="load_${SYS}_${d}_r$r"; sys=$SYS
  else dir=$OUT/b3-parallel/$SUB; sys=$([ "$a" = q ] && echo questdb || echo morletdb-i1); name="parallel_${sys}_${d}_w${w}_r$r"; fi
  tag=$([ "$track" = S ] && echo "$d$r" || echo P)
  if done_ok "$dir" "$name" "$tag"; then echo "skip $SUB $name"; return 0; fi
  stop_now "$SUB $name" && exit 0
  # an earlier failed attempt is kept beside the new one, never overwritten
  local s; s=failed-$(date +%H%M%S)
  for x in json log; do [ -f "$dir/$name.$x" ] && mv "$dir/$name.$x" "$dir/$name.$s.$x"; done
  powered || { echo "STOPPED: not on AC power with the lid open before $SUB $name $(date +%H:%M:%S)"; exit 1; }
  if [ "$MODE" = s6r ]; then   # amendment B5.S6r: the start condition before every load, up to 20 minutes
    local until=$(( $(date +%s) + 1200 )) ok=0 t0; t0=$(date +%s)
    [ "$until" -le "$DEADLINE" ] || until=$DEADLINE
    while [ "$(date +%s)" -lt "$until" ]; do docker info >/dev/null 2>&1 && powered && quiet && { ok=1; break; }; sleep 5; done
    if [ $ok != 1 ]; then echo "NOT RUN $track $SUB $name: start condition not met within 20 minutes or before 07:00 $(date +%H:%M:%S)"
      notrun+=("$track $a $d $r $w"); return 0; fi
    mkdir -p "$dir/machine"; echo "gate met $(date +%FT%T) after $(( $(date +%s) - t0 )) s, load $(sysctl -n vm.loadavg)" >> "$dir/machine/$name.txt"
  fi
  echo "== $track $SUB $name $(date +%H:%M:%S)"; snap "$dir" "$name"
  if [ "$track" = S ]; then
    B3_RUN=$dir B3_MORLET_IMAGE=$IMG MORLETDB_NATIVE_LIB=$LIB B3_VOLUME_PREFIX=$PRE \
      "$PY" "$H/b3_load.py" load "$sys" "$d" "$r" > "$dir/$name.log" 2>&1
  else
    B3_RUN=$dir B3_MORLET_IMAGE=$IMG MORLETDB_NATIVE_LIB=$LIB B3_VOLUME_PREFIX=$PRE \
      "$PY" parallel.py "$sys" "$d" "$w" "$r" > "$dir/$name.log" 2>&1
  fi
  local rc=$?
  snap "$dir" "$name"; clean
  if [ $rc != 0 ] || ! done_ok "$dir" "$name" "$tag"; then echo "FAILED $track $SUB $name (exit $rc) $(date +%H:%M:%S)"; failed+=("$track $a $d $r $w"); fi
  return 0; }
mkdir -p "$OUT/b3-single/grizzbit2" "$OUT/b3-single/questdb" "$OUT/b3-single/release" \
  "$OUT/b3-parallel/grizzbit2" "$OUT/b3-parallel/questdb" "$OUT/b3-parallel/release"
clean
if [ "$MODE" = smoke ]; then
  for a in g2 q rel; do run S "$a" D2 1; done
  for a in g2 q rel; do run P "$a" D2 1 8; done
else
  for r in 1 2 3; do for d in D2 D1; do for a in g2 q rel; do run S "$a" "$d" "$r"; done; done; done
  for r in 1 2 3; do for d in D2 D1; do for a in g2 q rel; do run P "$a" "$d" "$r" 8; done; done; done
  # a failed load is rerun once, at the end of the session (section 4B)
  again=("${failed[@]+"${failed[@]}"}"); failed=()
  for f in "${again[@]+"${again[@]}"}"; do set -- $f; echo "rerun once: $f"; run "$1" "$2" "$3" "$4" "$5"; done
  [ ${#failed[@]} = 0 ] || echo "FAILED after rerun: ${failed[*]}"
fi
[ ${#notrun[@]} = 0 ] || echo "NOT RUN (start condition): ${notrun[*]}"
{ date +%FT%T; uptime; docker system df; } > "$OUT/b3-single/b5_b3.end"
echo "== B5 B3 $MODE done $(date +%H:%M:%S)"
