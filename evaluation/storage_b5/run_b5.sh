#!/usr/bin/env bash
# B5's benchmark run (PROTOCOL.md sections 3, 4A, 4B): build record, storage and codec speed, then the B3 session.
# A failure in storage or speed (S1, or the harness) stops B5 before the B3 session; a budget stop there does not.
#   nohup caffeinate -ims evaluation/storage_b5/run_b5.sh > evaluation/storage_b5/results/b5_run.log 2>&1 &
set -uo pipefail
H="$(cd "$(dirname "$0")" && pwd)"
V=/Users/zin/sophia/morletdb
echo "== B5 run $(date +%FT%T) at $(git -C "$H" rev-parse --short HEAD)"
B5_NATIVE_LIB=$H/bin/b5/libmorlet_ffi.dylib REL_NATIVE_LIB=$H/bin/release/libmorlet_ffi.dylib \
  B5_BENCH=$H/bin/b5/rawcodec_bench B5_LOCK_SHA=d3eb41593f14efc399fd316033121e19a9742e9dd08dceb0d6eb3f2cd0cf7e38 \
  REL_WHEEL_SHA=a08b6cb00f858ac9116998f0b9cd1f54dea93b44eb72f93c8750b2df1b6f165f \
  REL_WHEEL_SHA_LISTED=a08b6cb00f858ac9116998f0b9cd1f54dea93b44eb72f93c8750b2df1b6f165f \
  "$V/.venv-b4/bin/python" -I "$H/measure_b5.py" build "$H/results/build.json"
"$H/run_b5_storage.sh"; rc=$?
echo "storage and speed exit $rc $(date +%H:%M:%S)"
case $rc in 0|4) ;; *) echo "B5 STOPPED before the B3 session (exit $rc)"; exit $rc;; esac
"$H/run_b5_b3.sh"; rc=$?
echo "== B5 run finished $(date +%FT%T) (B3 exit $rc)"
