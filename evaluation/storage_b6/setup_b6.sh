#!/usr/bin/env bash
# B6 programs (PROTOCOL.md sections 1 and 4; build record, section 9): fills evaluation/storage_b6/bin/ (not committed)
# and checks every program against the build record before any measurement.
#   bin/b6/libmorlet_ffi.dylib   the B6 decoder recorded in results/build.json (cargo build --locked --release
#                                -p morlet-ffi in a git archive of the build commit 50562e8). Rust embeds the build
#                                directory in the library, so a rebuild elsewhere has other bytes: the recorded file
#                                itself is installed (B6_DECODER=path, once) and its sha256 checked on every run
#   bin/b6/rawcodec_bench        the codec bench example, and bin/b6/b6codec, the harness: built from this tree, whose
#                                crates/ and Cargo.lock must equal the build commit's (checked here)
#   bin/release/libmorlet_ffi.dylib   the release decoder, from the 0.3.10-alpha.1 macOS wheel (wheel sha256 checked
#                                against the release's SHA256SUMS line, dylib sha256 checked)
# Usage: setup_b6.sh [WHEEL_DIR]   (WHEEL_DIR holds morletdb-0.1.0a3-py3-none-macosx_11_0_arm64.whl and SHA256SUMS)
set -euo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$H/../.." && pwd)"
BUILD=50562e8dcdd08fb0f3691660e521a20e72b233e3
B6_LIB_SHA=4cd33b93261f48b1edc5d49daf85d170b1458b8be0cadb6dd78023bbf49fc0ba
REL_LIB_SHA=343a8d9fd17d19c3c0ece5aab270d6ec9e7f3eb3591573cf321b49fd68aa42ff
REL_WHEEL=morletdb-0.1.0a3-py3-none-macosx_11_0_arm64.whl
REL_WHEEL_SHA=a08b6cb00f858ac9116998f0b9cd1f54dea93b44eb72f93c8750b2df1b6f165f
WHEELS=${1:-${B6_WHEEL_DIR:-}}
CARGO=${CARGO:-$HOME/.cargo/bin/cargo}
mkdir -p "$H/bin/b6" "$H/bin/release"
cd "$REPO"
# The harness programs are built from this tree: its codec and lockfile must be the build commit's.
git diff --quiet "$BUILD" -- crates Cargo.lock || { echo "REFUSED: crates/ or Cargo.lock differ from the build commit"; exit 1; }
git diff --quiet -- crates Cargo.lock || { echo "REFUSED: uncommitted changes in crates/ or Cargo.lock"; exit 1; }

sha() { shasum -a 256 "$1" | cut -d' ' -f1; }
if [ ! -f "$H/bin/b6/libmorlet_ffi.dylib" ] || [ "$(sha "$H/bin/b6/libmorlet_ffi.dylib")" != "$B6_LIB_SHA" ]; then
  [ -n "${B6_DECODER:-}" ] && [ -f "$B6_DECODER" ] || { echo "REFUSED: the recorded B6 decoder is not installed (B6_DECODER=path)"; exit 1; }
  cp "$B6_DECODER" "$H/bin/b6/libmorlet_ffi.dylib"
fi
[ "$(sha "$H/bin/b6/libmorlet_ffi.dylib")" = "$B6_LIB_SHA" ] || {
  echo "REFUSED: bin/b6/libmorlet_ffi.dylib has sha256 $(sha "$H/bin/b6/libmorlet_ffi.dylib"), the build record says $B6_LIB_SHA"; exit 1; }

"$CARGO" build --locked --release -p morlet-core --example rawcodec_bench > "$H/bin/build-bench.log" 2>&1
cp target/release/examples/rawcodec_bench "$H/bin/b6/"
(cd "$H/b6codec" && "$CARGO" build --locked --release) > "$H/bin/build-b6codec.log" 2>&1
cp "$H/b6codec/target/release/b6codec" "$H/bin/b6/"
"$H/bin/b6/b6codec" selftest > /dev/null

if [ ! -f "$H/bin/release/libmorlet_ffi.dylib" ] || [ "$(sha "$H/bin/release/libmorlet_ffi.dylib")" != "$REL_LIB_SHA" ]; then
  [ -n "$WHEELS" ] && [ -f "$WHEELS/$REL_WHEEL" ] || { echo "REFUSED: release wheel needed (setup_b6.sh WHEEL_DIR)"; exit 1; }
  [ "$(sha "$WHEELS/$REL_WHEEL")" = "$REL_WHEEL_SHA" ] || { echo "REFUSED: release wheel sha256"; exit 1; }
  grep -q "^$REL_WHEEL_SHA  *\(\./\)\?$REL_WHEEL\$" "$WHEELS/SHA256SUMS" || { echo "REFUSED: wheel not in SHA256SUMS"; exit 1; }
  t=$(mktemp -d); (cd "$t" && unzip -q "$WHEELS/$REL_WHEEL"); cp "$(find "$t" -name libmorlet_ffi.dylib | head -1)" "$H/bin/release/"; rm -rf "$t"
fi
[ "$(sha "$H/bin/release/libmorlet_ffi.dylib")" = "$REL_LIB_SHA" ] || { echo "REFUSED: release decoder sha256"; exit 1; }
{ echo "{"
  echo " \"build_commit\": \"$BUILD\", \"tree\": \"$(git rev-parse HEAD)\", \"written\": \"$(date +%FT%T)\","
  echo " \"b6_decoder\": \"$(sha "$H/bin/b6/libmorlet_ffi.dylib")\", \"release_decoder\": \"$(sha "$H/bin/release/libmorlet_ffi.dylib")\","
  echo " \"rawcodec_bench\": \"$(sha "$H/bin/b6/rawcodec_bench")\", \"b6codec\": \"$(sha "$H/bin/b6/b6codec")\""
  echo "}"; } > "$H/bin/programs.json"
echo "setup ok: $(tr -d '\n' < "$H/bin/programs.json")"
