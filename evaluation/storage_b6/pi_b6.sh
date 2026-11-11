#!/usr/bin/env bash
# B6 edge reference box (PROTOCOL.md section 4B): the Raspberry Pi 3B+ of X2 (aarch64) encodes and decodes the six
# waveforms with G21. Per input the SHA-256 of the concatenated chunk bytes (counts towards S5) and one pass of encode
# and decode speed (reported, no criterion). Runs over SSH, inside python:3.12.10-slim-bookworm on the Pi (the image the
# earlier Pi runs used; never pulled here: if it is not on the Pi, nothing runs), with the linux/arm64 b6codec that
# platforms_b6.sh built from this tree in rust:1.99.0-bookworm (bookworm's glibc, as the image's).
# Usage: B6_PI=user@host pi_b6.sh WORK OUTDIR
#   WORK: prepare_b6.py's directory; OUTDIR/pi-arm64.jsonl one line per waveform, OUTDIR/pi-arm64.status the outcome.
set -uo pipefail
PI=${B6_PI:?set B6_PI=user@host, the Raspberry Pi 3B+ of X2}
WORK="$(cd "$1" && pwd)"; OUT=$2
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
BIN="$(cd "$WORK/.." && pwd)/linux-arm64/b6codec"
IMG=python:3.12.10-slim-bookworm
DIR=b6-run
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 "$PI")
status() { echo "$1" > "$OUT/pi-arm64.status"; echo "$1"; }
[ -x "$BIN" ] || { status "not run: $BIN missing (run platforms_b6.sh linux-arm64 first)"; exit 1; }
names=$(python3 -c "import json,sys; print(' '.join(r['input'] for r in json.load(open(sys.argv[1]))['inputs'] if r['set']=='waveform'))" "$WORK/inputs.json")
"${SSH[@]}" true 2>/dev/null || { status "not run: $PI unreachable"; exit 1; }
img=$("${SSH[@]}" "docker image inspect $IMG --format '{{.Id}} {{.Os}}/{{.Architecture}}'" 2>/dev/null) ||
  { status "not run: $IMG is not on the Pi (it is never pulled by this script)"; exit 1; }
host=$("${SSH[@]}" "uname -srm; tr -d '\\0' < /proc/device-tree/model 2>/dev/null; echo; uptime" 2>/dev/null | tr '\n' ' ')
"${SSH[@]}" "rm -rf ~/$DIR && mkdir -p ~/$DIR/inputs" || { status "not run: cannot create ~/$DIR on the Pi"; exit 1; }
scp -q -o BatchMode=yes "$BIN" "$PI:$DIR/b6codec" || { status "not run: copy failed"; exit 1; }
for n in $names; do scp -q -o BatchMode=yes "$WORK/inputs/$n.bin" "$PI:$DIR/inputs/" || { status "not run: copy of $n failed"; exit 1; }; done
"${SSH[@]}" "docker run --rm --network none -v \$HOME/$DIR:/w $IMG sh -c '/w/b6codec selftest >/dev/null && for n in $names; do /w/b6codec hash /w/inputs/\$n.bin; done'" \
  > "$OUT/pi-arm64.jsonl" 2> "$OUT/pi-arm64.stderr"
rc=$?
"${SSH[@]}" "rm -rf ~/$DIR" 2>/dev/null || true
if [ $rc = 0 ]; then
  status "ok: $(wc -l < "$OUT/pi-arm64.jsonl" | tr -d ' ') waveforms, $IMG $img, b6codec $(shasum -a 256 "$BIN" | cut -d' ' -f1), $host"
else
  status "failed: exit $rc (see pi-arm64.stderr), $host"; exit 1
fi
