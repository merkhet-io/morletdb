#!/bin/sh
# Study B9 H3(a) and gate 7: the four configurations' chunk-bytes hashes (`b9codec hash`) per arm and input.
#
#   hashes.sh OUT.jsonl PLATFORM BINDIR INPUT_DIR NAME...
#
# PLATFORM: macos-arm64 (native) | linux-arm64 | linux-amd64 (Docker, rust:1.99.0-bookworm by digest, emulated on
# the Mac for amd64) | native (run BINDIR/<arm>/b9codec directly, e.g. on a Linux host). BINDIR holds
# <arm>/b9codec for each arm; every arm hashes every NAME (INPUT_DIR/NAME.bin). One JSON line per arm and input.
set -eu
OUT=$1; PLAT=$2; BIN=$3; IN=$4; shift 4
IMAGE=rust@sha256:114c7a4425406451c2866b6aafe69fe29b1b298832db1277d411ac73c82d04d6
for arm in $(ls "$BIN"); do
  for name in "$@"; do
    case "$PLAT" in
      macos-arm64|native) line=$("$BIN/$arm/b9codec" hash "$IN/$name.bin") ;;
      linux-arm64|linux-amd64)
        line=$(docker run --rm --platform "linux/${PLAT#linux-}" --pull never --network none \
          -v "$BIN/$arm:/b:ro" -v "$IN:/in:ro" $IMAGE /b/b9codec hash "/in/$name.bin") ;;
      *) echo "unknown platform $PLAT" >&2; exit 2 ;;
    esac
    printf '{"platform":"%s","arm":"%s","input":"%s","binary_sha256":"%s","at":"%s","hash":%s}\n' "$PLAT" "$arm" \
      "$name" "$(shasum -a 256 "$BIN/$arm/b9codec" 2>/dev/null | cut -c1-64 || sha256sum "$BIN/$arm/b9codec" | cut -c1-64)" \
      "$(date -u +%FT%TZ)" "$line" >> "$OUT"
  done
done
