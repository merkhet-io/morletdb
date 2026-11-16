#!/bin/sh
# Study B9 builds (PROTOCOL.md section 3): b9codec for each arm, every arm a `git archive` of its commit with the
# same harness source (evaluation/speed_b9/b9codec of HARNESS_COMMIT) copied in, built one after another in the
# same directory with `cargo build --release --locked --offline`, RUSTFLAGS unset, no target-cpu.
#
#   evaluation/speed_b9/build.sh OUT HARNESS_COMMIT arm=COMMIT...
#
# OUT/bin/<platform>/<arm>/b9codec for platform macos-arm64 (native), linux-arm64 and linux-amd64
# (rust:1.99.0-bookworm by digest), and linux-amd64-musl (static, x86_64-unknown-linux-musl via rust-lld, for the
# owner's native x86_64 host); OUT/build-env.txt records toolchains, image digests and the environment.
set -eu
OUT=$1; HC=$2; shift 2
REPO=$(git -C "$(dirname "$0")" rev-parse --show-toplevel)
IMAGE=rust@sha256:114c7a4425406451c2866b6aafe69fe29b1b298832db1277d411ac73c82d04d6   # rust:1.99.0-bookworm
export PATH="$HOME/.cargo/bin:$PATH"
unset RUSTFLAGS CARGO_BUILD_RUSTFLAGS CARGO_ENCODED_RUSTFLAGS CARGO_TARGET_DIR || true
mkdir -p "$OUT/bin"
ENV="$OUT/build-env.txt"
{
  echo "date: $(date -u +%FT%TZ)"; echo "harness commit: $(git -C "$REPO" rev-parse "$HC")"
  echo "macOS: $(sw_vers -productVersion) $(uname -m)"; echo "--- rustc -vV (macOS)"; (cd "$REPO/evaluation/speed_b9/b9codec" && rustc -vV)
  echo "--- cargo -V"; (cd "$REPO/evaluation/speed_b9/b9codec" && cargo -V)
  echo "--- build environment (Rust-related variables; none set means unset)"; env | grep -E '^(RUST|CARGO|MACOSX)' | sort || true
  echo "--- ~/.cargo/config.toml"; cat "$HOME/.cargo/config.toml" 2>/dev/null || echo "(none)"
  echo "--- .cargo/config.toml of each arm's tree (macOS link argument only, the same in every arm)"; git -C "$REPO" show "$HC:.cargo/config.toml"
  echo "--- image $IMAGE"
  for p in linux/arm64 linux/amd64; do
    echo "$p: $(docker run --rm --platform $p --pull never --network none $IMAGE sh -c 'uname -m; rustc -vV | tr "\n" " "')"
  done
} > "$ENV" 2>&1
SRC="$OUT/src"
for spec in "$@"; do
  arm=${spec%%=*}; c=${spec#*=}
  # fresh target directories per arm: git archive sets each commit's own mtimes, so cargo's freshness check
  # must never see another arm's outputs
  rm -rf "$SRC" "$OUT/target-mac" "$OUT/target-musl"; mkdir -p "$SRC"
  git -C "$REPO" archive "$c" | tar xf - -C "$SRC"
  rm -rf "$SRC/evaluation/speed_b9/b9codec"; mkdir -p "$SRC/evaluation/speed_b9"
  git -C "$REPO" archive "$HC" evaluation/speed_b9/b9codec | tar xf - -C "$SRC"
  echo "$arm $(git -C "$REPO" rev-parse "$c") grizzbit/src $(git -C "$REPO" rev-parse "$c:crates/grizzbit/src")" >> "$ENV"
  # macOS/arm64, native
  (cd "$SRC/evaluation/speed_b9/b9codec" && cargo build --release --locked --offline --target-dir "$OUT/target-mac" 2>&1 | tail -1)
  mkdir -p "$OUT/bin/macos-arm64/$arm"; cp "$OUT/target-mac/release/b9codec" "$OUT/bin/macos-arm64/$arm/"
  # static x86_64 musl (cross, rust-lld): the owner's native x86_64 host
  (cd "$SRC/evaluation/speed_b9/b9codec" && rustup target list --installed | grep -q x86_64-unknown-linux-musl &&
    CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER=rust-lld cargo build --release --locked --offline \
      --target x86_64-unknown-linux-musl --target-dir "$OUT/target-musl" 2>&1 | tail -1)
  mkdir -p "$OUT/bin/linux-amd64-musl/$arm"; cp "$OUT/target-musl/x86_64-unknown-linux-musl/release/b9codec" "$OUT/bin/linux-amd64-musl/$arm/"
  # Linux in Docker (glibc, bookworm): the same source at /src in every arm
  for p in arm64 amd64; do
    mkdir -p "$OUT/bin/linux-$p/$arm"
    docker run --rm --platform linux/$p --pull never --network none -v "$SRC:/src:ro" -v "$OUT/bin/linux-$p/$arm:/out" \
      $IMAGE sh -c 'cd /src/evaluation/speed_b9/b9codec && cargo build --release --locked --offline --target-dir /tmp/t 2>&1 | tail -1 && cp /tmp/t/release/b9codec /out/'
  done
done
rm -rf "$OUT/target-mac" "$OUT/target-musl"
(cd "$OUT/bin" && find . -name b9codec -type f | sort | xargs shasum -a 256) > "$OUT/binaries.sha256"
cat "$OUT/binaries.sha256"
