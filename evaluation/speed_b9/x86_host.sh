#!/bin/sh
# Study B9 H3(a) on the owner's native x86_64 Linux host (owner's decision 2026-11-16; run by the owner, from the
# Mac, when that host is free). Copies the static x86_64 musl b9codec of both arms and the 12 inputs to HOST:~/b9,
# checks every SHA-256 there, computes the four configurations' chunk-bytes hashes per arm and input under
# `nice -n 19` (no Docker; files under ~/b9 only), fetches the result and deletes the inputs from the host.
#
#   evaluation/speed_b9/x86_host.sh HOST BIN_DIR WORK OUT.jsonl
#
# BIN_DIR: build.sh's OUT/bin/linux-amd64-musl (holding baseline/ and candidate/); WORK: the b9-work directory of
# prepare_b6.py (WORK/inputs/<name>.bin). The expected SHA-256 are the build record's (results/build.json) and B6's
# (storage_b6/results/b6_inputs.json); a mismatch stops before anything is hashed.
set -eu
HOST=$1; BIN=$2; WORK=$3; OUT=$4
HERE=$(cd "$(dirname "$0")" && pwd)
INPUTS="cx ims sleep ecg pad-c-b6 pad-p-b6 b3-vibration_1 b3-phase_current_1 b3-phase_current_2 b3-force b3-speed b3-torque"
python3 -I - "$HERE" "$INPUTS" > "$HERE/results/x86_host_expected.sha256" <<'EOF'
import json, sys
here = sys.argv[1]
b = json.load(open(here + "/results/build.json"))
for arm in ("baseline", "candidate"):
    print(b["binaries"]["linux-amd64-musl"][arm]["sha256"] + "  bin/%s/b9codec" % arm)
inp = {r["input"]: r for r in json.load(open(here + "/../storage_b6/results/b6_inputs.json"))["inputs"]}
for n in sys.argv[2].split():
    print(inp[n]["chunk_file_sha256"] + "  inputs/%s.bin" % n)
EOF
ssh "$HOST" 'mkdir -p ~/b9/bin/baseline ~/b9/bin/candidate ~/b9/inputs'
scp -q "$BIN/baseline/b9codec" "$HOST:b9/bin/baseline/"
scp -q "$BIN/candidate/b9codec" "$HOST:b9/bin/candidate/"
scp -q "$HERE/results/x86_host_expected.sha256" "$HOST:b9/expected.sha256"
for n in $INPUTS; do scp -q "$WORK/inputs/$n.bin" "$HOST:b9/inputs/"; done
cat > "$HERE/results/.x86_host_remote.sh" <<'EOF'
#!/bin/sh
# runs on the host in ~/b9: checks, then hashes under nice -n 19
set -eu
cd ~/b9
chmod +x bin/*/b9codec
sha256sum -c expected.sha256
{ uname -a; grep -m1 'model name' /proc/cpuinfo; grep -m1 -o -w avx2 /proc/cpuinfo || echo "no avx2 flag"; } > host.txt
: > hashes.jsonl
for arm in baseline candidate; do
  for n in $INPUTS; do
    line=$(nice -n 19 ./bin/$arm/b9codec hash inputs/$n.bin)
    printf '{"platform":"linux-amd64-native","arm":"%s","input":"%s","at":"%s","hash":%s}\n' \
      "$arm" "$n" "$(date -u +%FT%TZ)" "$line" >> hashes.jsonl
  done
done
EOF
scp -q "$HERE/results/.x86_host_remote.sh" "$HOST:b9/run.sh"
rm -f "$HERE/results/.x86_host_remote.sh"
ssh "$HOST" "INPUTS='$INPUTS' nice -n 19 sh ~/b9/run.sh"
scp -q "$HOST:b9/hashes.jsonl" "$OUT"
scp -q "$HOST:b9/host.txt" "${OUT%.jsonl}_host.txt"
ssh "$HOST" 'rm -rf ~/b9/inputs'
echo "fetched $(wc -l < "$OUT") lines into $OUT; inputs deleted from $HOST"
