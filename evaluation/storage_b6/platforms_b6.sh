#!/usr/bin/env bash
# B6 platforms (PROTOCOL.md section 4B): G21 encodes every chunk of every input on each platform; per input the
# SHA-256 of its concatenated chunk bytes (b6codec hash, which also decodes every chunk and compares it by to_bits).
#   macos-arm64    native: bin/b6/b6codec
#   linux-arm64    Docker, rust:1.99.0-bookworm (linux/arm64), b6codec built inside from this tree, offline
#   linux-amd64    Docker, rust:1.99.0-bookworm (linux/amd64) under emulation, the same; only if that image is
#                  already local (this script never pulls an image): otherwise recorded as not available
# Usage: platforms_b6.sh WORK OUTDIR [platform ...]      (default: all three)
#   WORK: prepare_b6.py's directory; OUTDIR/<platform>.jsonl one line per input, OUTDIR/<platform>.status the outcome.
# The containers get B3's limits (--cpus 8 --memory 16g), the repository read-only and WORK read-only; the target
# directory is a volume b6-platform-<platform>. The linux-arm64 b6codec is also copied to WORK/../linux-arm64/ for the
# Raspberry Pi (pi_b6.sh).
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$H/../.." && pwd)"
WORK="$(cd "$1" && pwd)"; OUT=$2; shift 2
mkdir -p "$OUT"; OUT="$(cd "$OUT" && pwd)"
PLATFORMS=("$@"); [ ${#PLATFORMS[@]} = 0 ] && PLATFORMS=(macos-arm64 linux-arm64 linux-amd64)
IMG=rust:1.99.0-bookworm
names=$(python3 -c "import json,sys; print(' '.join(r['input'] for r in json.load(open(sys.argv[1]))['inputs']))" "$WORK/inputs.json")

native() {
  local f="$OUT/macos-arm64.jsonl"; : > "$f"
  for n in $names; do "$H/bin/b6/b6codec" hash "$WORK/inputs/$n.bin" >> "$f" || { echo "failed: $n" > "$OUT/macos-arm64.status"; return 1; }; done
  echo "ok: $(wc -l < "$f" | tr -d ' ') inputs, b6codec $(shasum -a 256 "$H/bin/b6/b6codec" | cut -d' ' -f1), $(uname -sm)" > "$OUT/macos-arm64.status"
}

linux() { # linux <arm64|amd64>
  local arch=$1 plat=linux-$1 f="$OUT/linux-$1.jsonl"
  local os; os=$(docker image inspect --platform "linux/$arch" "$IMG" --format '{{.Os}}/{{.Architecture}}' 2>/dev/null)
  if [ "$os" != "linux/$arch" ]; then
    echo "not available: $IMG for linux/$arch is not local (docker pull --platform linux/$arch $IMG needs the owner's OK)" > "$OUT/$plat.status"
    rm -f "$f"; return 0
  fi
  local bin; bin="$(cd "$WORK/.." && pwd)/$plat"; mkdir -p "$bin"
  docker run --rm --platform "linux/$arch" --cpus 8 --memory 16g --network none \
    -v "$REPO:/src:ro" -v "$WORK:/work:ro" -v "b6-platform-$plat:/target" -v "$bin:/out" \
    -e CARGO_TARGET_DIR=/target "$IMG" sh -c '
      set -e
      cd /src/evaluation/storage_b6/b6codec
      cargo build --release --locked --offline >/out/build.log 2>&1
      cp /target/release/b6codec /out/b6codec
      /out/b6codec selftest >/dev/null
      for n in '"$names"'; do /out/b6codec hash /work/inputs/$n.bin; done' > "$f" 2> "$OUT/$plat.stderr"
  local rc=$?
  if [ $rc = 0 ]; then
    echo "ok: $(wc -l < "$f" | tr -d ' ') inputs, $IMG $(docker image inspect --platform "linux/$arch" "$IMG" --format '{{.Id}}'), b6codec $(shasum -a 256 "$bin/b6codec" | cut -d' ' -f1)" > "$OUT/$plat.status"
  else
    echo "failed: exit $rc (see $plat.stderr)" > "$OUT/$plat.status"; return 1
  fi
}

rc=0
for p in "${PLATFORMS[@]}"; do
  echo "== $p $(date +%H:%M:%S)"
  case $p in
    macos-arm64) native || rc=1;;
    linux-arm64) linux arm64 || rc=1;;
    linux-amd64) linux amd64 || rc=1;;
    *) echo "unknown platform $p"; rc=2;;
  esac
  cat "$OUT/$p.status"
done
exit $rc
