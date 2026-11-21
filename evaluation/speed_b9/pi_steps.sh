#!/bin/sh
# Study B9 step 5 on the Raspberry Pi 3B+ host, in ~/b9 (PROTOCOL.md sections 2 H3 and 6.B): the pinned image,
# --network none --cpus 1, never pulled.
#   pi_steps.sh hashes OUT.jsonl NAME...   the four configurations' chunk-bytes hashes per arm and input
#   pi_steps.sh tests OUT.log              H3(b): codec_golden, encoder_equivalence, float32_equivalence, the corpus
#                                          byte test and the LPC kernel unit tests (linux/arm64 glibc test binaries
#                                          of the candidate, built in rust:1.99.0-bookworm with source at /src)
set -eu
cd ~/b9
IMAGE=sha256:06001cf262e0684d505218be89ef045e23cc3270af43a127d4ab94a4a78e1344
what=$1; out=$2; shift 2
case "$what" in
  hashes)
    for arm in baseline candidate; do
      for n in "$@"; do
        line=$(docker run --rm --network none --cpus 1 -v "$HOME/b9:/w:ro" -w /w $IMAGE ./bin/$arm/b9codec hash inputs/$n.bin)
        printf '{"platform":"pi-linux-arm64","arm":"%s","input":"%s","binary_sha256":"%s","at":"%s","hash":%s}\n' \
          "$arm" "$n" "$(sha256sum bin/$arm/b9codec | cut -c1-64)" "$(date -u +%FT%TZ)" "$line" >> "$out"
      done
    done ;;
  tests)
    { uname -a; sha256sum pitest/bins/*; } > "$out"
    docker run --rm --network none --cpus 1 -v "$HOME/b9/pitest/src:/src:ro" -v "$HOME/b9/pitest/bins:/t:ro" \
      -w /src/crates/grizzbit $IMAGE sh -c '
        rc=0
        for t in codec_golden encoder_equivalence float32_equivalence corpus_bytes; do
          echo "== $t"; /t/$t-* --nocapture 2>&1 || rc=1
        done
        echo "== grizzbit lib lpc_kernel::tests"; /t/grizzbit-* lpc_kernel::tests --nocapture 2>&1 || rc=1
        echo "overall exit $rc"; exit $rc' >> "$out" 2>&1 ;;
  *) echo "usage: pi_steps.sh hashes OUT NAME... | tests OUT" >&2; exit 2 ;;
esac
