#!/usr/bin/env bash
# B6 (PROTOCOL.md section 4C): B3's ingestion and reads in one session, as B5 section 4B and amendment B5.S6r,
# unchanged except for the arms:
#   g21  morletdb-i2 (Grizzbit 2.1): image morletdb:b6 (deploy/Dockerfile at the build commit 50562e8) with
#        morlet.chunk_format = grizzbit21 in its server configuration (b6_b3.py; checked after every start), decoder
#        bin/b6/libmorlet_ffi.dylib built from the same commit (sha256 checked; without it the arm fails, never raw reads)
#   q    QuestDB 10.1.0 with its B3 configuration (B3.16's adapter, unchanged)
#   rel  morletdb-i2 (release): ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1 (section 8, item 3), decoder from the
#        release's macOS wheel (bin/release, sha256 checked)
# Track S: r = 1, 2, 3; D2 then D1; order g21, q, rel; B3's full correctness after every load; on D2 r1 storage, reads
#   and statistics from a fresh client process (B3_READS_FRESH_PROCESS=1); MorletDB storage also per group by chunk
#   version and kind (b6_b3.py).
# Track P: 8 writers; r = 1, 2, 3; (D2) then (D1); order g21, q, rel; ingestion only (B3.17's morletdb-i1 write path).
# Before every load (B3.16 as amendment B5.S6r learned): 1-minute load average below 3, no process other than
# Docker's VM above half a core, AC power, lid open or closed without causing sleep, waited for up to 20 minutes; a
# load whose condition does not hold in that time is reported as not run. A failed load is rerun once at the end. No
# load starts after 07:00 or after the 3-hour session budget. Fresh volumes b6-* (B3_VOLUME_PREFIX); B3's own volumes
# are never touched. Machine snapshots before and after every load.
#
#   run_b6_b3.sh [OUT]          the session; results in OUT/b3-single/, OUT/b3-parallel/ (OUT: storage_b6/results)
#   run_b6_b3.sh smoke [OUT]    development set (B3_SET=dev), one D2 load per arm and track (section 4C, "Smoke
#                               check"); the start condition is recorded but not waited for; OUT: results/dev/b3-smoke
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
V=${B6_VENV_ROOT:-/Users/zin/sophia/morletdb}
PY=$V/.venv-b3/bin/python
REL=ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1
G21=morletdb:b6
REL_LIB=$H/bin/release/libmorlet_ffi.dylib
G21_LIB=$H/bin/b6/libmorlet_ffi.dylib
REL_LIB_SHA=343a8d9fd17d19c3c0ece5aab270d6ec9e7f3eb3591573cf321b49fd68aa42ff   # from the 0.3.10-alpha.1 macOS wheel
G21_LIB_SHA=4cd33b93261f48b1edc5d49daf85d170b1458b8be0cadb6dd78023bbf49fc0ba   # built from 50562e8 (results/build.json)
G21_IMG_ID=sha256:99ad124df31fef7dee84d041eba537a88e1321da660e9d9edfb3eb82ed0429eb
REL_IMG_ID=sha256:03ed3cc2abc82a27fa8456fa70a0cdd93cbf8b2d9f845d47b414024c38fa2c38
Q_IMG_ID=sha256:3b11dc5f77aa299c7c9191b10cf50124c9e97663ce93bc50df5040374e87efc9
MODE=session; [ "${1:-}" = smoke ] && { MODE=smoke; shift; }
export B3_READS_FRESH_PROCESS=1
if [ "$MODE" = smoke ]; then export B3_SET=dev; OUT=${1:-$H/results/dev/b3-smoke}; VP=b6-smoke
else OUT=${1:-$H/results}; VP=b6; fi
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
cd "$H/../tsdb_b3" || exit 1
export MORLET_DATA=${MORLET_DATA:-$V/data}
# B3's programs (and their fresh-process reads) read the Paderborn files from this checkout's data/ (gitignored): link it
# to MORLET_DATA when it is missing, as B5's checkout had it.
[ -e "$H/../../data" ] || ln -s "$MORLET_DATA" "$H/../../data"
[ -d "$H/../../data/paderborn/zenodo-15845309/extracted" ] || { echo "REFUSED: no Paderborn data under data/"; exit 1; }
notrun=()
TIMEOUT=$(command -v timeout || command -v gtimeout || ls /opt/homebrew/bin/timeout 2>/dev/null) || { echo "REFUSED: no timeout command"; exit 1; }

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
[ "$(sha "$REL_LIB")" = "$REL_LIB_SHA" ] || { echo "REFUSED: release decoder sha256"; exit 1; }
[ "$(sha "$G21_LIB")" = "$G21_LIB_SHA" ] || { echo "REFUSED: B6 decoder sha256"; exit 1; }
for x in "$G21 $G21_IMG_ID" "$REL $REL_IMG_ID" "questdb/questdb:10.1.0 $Q_IMG_ID"; do set -- $x
  [ "$(docker image inspect "$1" --format '{{.Id}}' 2>/dev/null)" = "$2" ] || { echo "REFUSED: image $1 is not $2"; exit 1; }; done

quiet() { awk -v l="$(sysctl -n vm.loadavg | awk '{print $2}')" 'BEGIN{exit !(l<3)}' &&
  [ -z "$(ps -Ao pcpu,comm -r | awk 'NR>1 && $1>50 && $0 !~ /Virtualization|com\.docker/')" ]; }
powered() { local b c; b=$(pmset -g batt 2>/dev/null || true); c=$(ioreg -r -k AppleClamshellState -d 1 2>/dev/null || true)
  [[ $b == *"AC Power"* ]] && [[ $c == *'"AppleClamshellState" = No'* || $c == *'"AppleClamshellCausesSleep" = No'* ]]; }
# the smoke check tests the harness only (no figure from it is used): the condition is recorded, not waited for
if [ "$MODE" = smoke ]; then quiet() { true; }; powered() { true; }; fi
# the next 07:00 (the owner's day): no load starts after it
if [ "$(date +%H%M)" -lt "${B6_LATEST:-0700}" ]; then DEADLINE=$(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B6_LATEST:-0700}" +%s)
else DEADLINE=$(( $(date -j -f "%Y%m%d%H%M" "$(date +%Y%m%d)${B6_LATEST:-0700}" +%s) + 86400 )); fi
docker info >/dev/null 2>&1 || { echo "REFUSED: Docker is not running"; exit 1; }
SESSION_END=$(( $(date +%s) + 3 * 3600 ))   # section 7: the B3 session takes at most 3 hours
mkdir -p "$OUT/b3-single" "$OUT/b3-parallel"
{ echo "mode $MODE"; date +%FT%T; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true
  docker ps --format '{{.Names}}'; docker system df
  for i in "$G21" "$REL" questdb/questdb:10.1.0; do echo "$i $(docker image inspect "$i" --format '{{.Id}}')"; done
  echo "decoder g21 $G21_LIB $G21_LIB_SHA"; echo "decoder rel $REL_LIB $REL_LIB_SHA"
  echo "session ends by $(date -r "$SESSION_END" +%H:%M:%S), no load after $(date -r "$DEADLINE" +%FT%H:%M)"; } > "$OUT/b3-single/b6_b3.start"

snap() { mkdir -p "$1/machine"; { echo "-- $3"; date +%FT%T; uptime; pmset -g batt | head -1; ps -Ao pcpu,comm -r | head -9 || true; } >> "$1/machine/$2.txt"; }
done_ok() { local f="$1/$2.json"; [ -f "$f" ] && grep -q '"correct": true' "$f" && { [ "$3" != D2r1 ] || grep -q '"analytics' "$f"; }; }
failed=()
stop_now() { local t; t=$(date +%s)
  if [ "$t" -ge "$DEADLINE" ]; then echo "STOPPED before $1: $(date +%H:%M) is the owner's day; loads done are reported as such"; return 0; fi
  if [ "$t" -ge "$SESSION_END" ]; then echo "STOPPED before $1: the 3-hour session budget ended; loads done are reported as such"; return 0; fi
  return 1; }
clean() { docker rm -f b3-questdb b3-morletdb >/dev/null 2>&1 || true; }
# arm <g21|q|rel> sets: SYS (S track system), IMG, LIB, FMT (chunk format asked for), PRE (volume prefix), SUB
arm() { case $1 in
  g21) SYS=morletdb-i2; IMG=$G21; LIB=$G21_LIB; FMT=grizzbit21; PRE=$VP-g21; SUB=grizzbit21;;
  q) SYS=questdb; IMG=$REL; LIB=$REL_LIB; FMT=; PRE=$VP-q; SUB=questdb;;
  rel) SYS=morletdb-i2; IMG=$REL; LIB=$REL_LIB; FMT=; PRE=$VP-rel; SUB=release;; esac; }
# one load: run <S|P> <arm> <D> <rep> [writers]
run() { local track=$1 a=$2 d=$3 r=$4 w=${5:-8} dir name sys tag
  arm "$a"
  if [ "$track" = S ]; then dir=$OUT/b3-single/$SUB; name="load_${SYS}_${d}_r$r"; sys=$SYS
  else dir=$OUT/b3-parallel/$SUB; sys=$([ "$a" = q ] && echo questdb || echo morletdb-i1); name="parallel_${sys}_${d}_w${w}_r$r"; fi
  tag=$([ "$track" = S ] && echo "${d}r$r" || echo P)   # D2r1: reads and analytics required
  if done_ok "$dir" "$name" "$tag"; then echo "skip $SUB $name"; return 0; fi
  stop_now "$SUB $name" && exit 0
  # an earlier failed attempt is kept beside the new one, never overwritten
  local s; s=failed-$(date +%H%M%S)
  for x in json log; do [ -f "$dir/$name.$x" ] && mv "$dir/$name.$x" "$dir/$name.$s.$x"; done
  # the start condition before every load, up to 20 minutes (amendment B5.S6r)
  local until=$(( $(date +%s) + 1200 )) ok=0 t0; t0=$(date +%s)
  [ "$until" -le "$DEADLINE" ] || until=$DEADLINE
  while [ "$(date +%s)" -lt "$until" ]; do docker info >/dev/null 2>&1 && powered && quiet && { ok=1; break; }; sleep 5; done
  if [ $ok != 1 ]; then echo "NOT RUN $track $SUB $name: start condition not met within 20 minutes or before 07:00 $(date +%H:%M:%S)"
    notrun+=("$track $a $d $r $w"); return 0; fi
  mkdir -p "$dir/machine"; echo "gate met $(date +%FT%T) after $(( $(date +%s) - t0 )) s, load $(sysctl -n vm.loadavg)" >> "$dir/machine/$name.txt"
  echo "== $track $SUB $name $(date +%H:%M:%S)"; snap "$dir" "$name" "before"
  # a load that does not finish within B6_LOAD_TIMEOUT (default 45 minutes; e.g. a writer process that died and left
  # its parent waiting) is stopped and counts as failed (rerun once at the end, as any failed load)
  if [ "$track" = S ]; then
    B3_RUN=$dir B3_MORLET_IMAGE=$IMG MORLETDB_NATIVE_LIB=$LIB B3_VOLUME_PREFIX=$PRE B6_CHUNK_FORMAT=$FMT \
      "$TIMEOUT" -k 30 "${B6_LOAD_TIMEOUT:-2700}" "$PY" "$H/b6_b3.py" load "$sys" "$d" "$r" > "$dir/$name.log" 2>&1
  else
    B3_RUN=$dir B3_MORLET_IMAGE=$IMG MORLETDB_NATIVE_LIB=$LIB B3_VOLUME_PREFIX=$PRE B6_CHUNK_FORMAT=$FMT \
      "$TIMEOUT" -k 30 "${B6_LOAD_TIMEOUT:-2700}" "$PY" "$H/b6_b3.py" parallel "$sys" "$d" "$w" "$r" > "$dir/$name.log" 2>&1
  fi
  local rc=$?
  [ $rc = 124 ] && echo "TIMED OUT $track $SUB $name after ${B6_LOAD_TIMEOUT:-2700} s" | tee -a "$dir/$name.log"
  snap "$dir" "$name" "after (exit $rc)"; clean
  if [ $rc != 0 ] || ! done_ok "$dir" "$name" "$tag"; then echo "FAILED $track $SUB $name (exit $rc) $(date +%H:%M:%S)"; failed+=("$track $a $d $r $w"); fi
  return 0; }
for t in b3-single b3-parallel; do for s in grizzbit21 questdb release; do mkdir -p "$OUT/$t/$s"; done; done
clean
if [ "$MODE" = smoke ]; then
  for a in g21 q rel; do run S "$a" D2 1; done
  for a in g21 q rel; do run P "$a" D2 1 8; done
else
  for r in 1 2 3; do for d in D2 D1; do for a in g21 q rel; do run S "$a" "$d" "$r"; done; done; done
  for r in 1 2 3; do for d in D2 D1; do for a in g21 q rel; do run P "$a" "$d" "$r" 8; done; done; done
  # a failed load is rerun once, at the end of the session
  again=("${failed[@]+"${failed[@]}"}"); failed=()
  for f in "${again[@]+"${again[@]}"}"; do set -- $f; echo "rerun once: $f"; run "$1" "$2" "$3" "$4" "$5"; done
fi
[ ${#failed[@]} = 0 ] || echo "FAILED: ${failed[*]}"
[ ${#notrun[@]} = 0 ] || echo "NOT RUN (start condition): ${notrun[*]}"
{ date +%FT%T; uptime; docker system df; } > "$OUT/b3-single/b6_b3.end"
echo "== B6 B3 $MODE done $(date +%H:%M:%S)"
[ ${#failed[@]} = 0 ]
