#!/usr/bin/env python3
"""Batched envelope build vs CW1's measurements: the in-database arm (morlet_build_envelopes) at N = 1, 2, 4, 8 on
CW1's database and workload, using CW1's harness (evaluation/external_worker/cw1.py, PR #67), plus the external arm at
N = 4 for byte identity with the pre-batching reference.

  .venv-seismic/bin/python -I evaluation/envelope_build/measure.py <path to cw1.py dir> <worker binary> OUT
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, sys.argv[1])
import cw1  # noqa: E402

binary, out = sys.argv[2], Path(sys.argv[3])
out.mkdir(parents=True, exist_ok=True)
rids = cw1.recordings()
ref, ref_digest = cw1.run_arm("ext", 4, binary, rids)          # external path = the old builder's bytes (CW1 identity)
res = {"external_reference": ref, "batched_in": []}
for n in cw1.NS:
    r, dig = cw1.run_arm("in", n, binary, rids)
    r["identical_to_old_bytes"] = dig == ref_digest
    res["batched_in"].append(r)
    print(json.dumps({k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items() if k != "backend_pids"}), flush=True)
json.dump(res, open(out / "batched.json", "w"), indent=1)
