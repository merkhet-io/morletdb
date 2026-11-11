#!/usr/bin/env python3
"""Check the agent's evidence against the source files (needs numpy and h5py; not part of the agent).

  python evaluation/agent_demo/verify_evidence.py BOSCH_REPO RESULTS_DIR

For every investigation, the samples that read_interval returned for the top window are compared with the same
samples of the HDF5 file (M01 only), as float64 bytes. Writes RESULTS_DIR/evidence_check.json.
"""
import gzip
import json
import sys
from pathlib import Path

import h5py
import numpy as np


def main():
    repo, res = Path(sys.argv[1]), Path(sys.argv[2])
    rows = []
    for t in sorted((res / "transcripts").glob("*.json.gz")):
        doc = json.load(gzip.open(t, "rt"))
        call = next((c for c in reversed(doc["calls"]) if c["tool"] == "read_interval" and "samples" in c.get("result", {})),
                    None)                      # the evidence read (v2 also reads min/max buckets for alignment)
        if call is None:
            rows.append({"run": doc["run"], "checked": False, "why": "no read_interval result"})
            continue
        r = call["result"]
        rec = next(c["result"] for c in doc["calls"] if c["tool"] == "list_recordings" and "result" in c)
        meta = rec["recordings"][0]["metadata"]
        assert meta["file"].startswith("data/M01/"), meta["file"]
        axis = "xyz".index(r["recording_id"].rsplit("-", 1)[1])
        with h5py.File(repo / meta["file"], "r") as f:
            src = np.asarray(f["vibration_data"][r["start_sample"]:r["end_sample"], axis]).astype("<f8")
        got = np.asarray(r["samples"], "<f8")
        rows.append({"run": doc["run"], "checked": True, "recording_id": r["recording_id"],
                     "start_sample": r["start_sample"], "end_sample": r["end_sample"], "values": int(got.size),
                     "bit_identical": got.size == src.size and got.tobytes() == src.tobytes()})
    ok = sum(1 for x in rows if x.get("bit_identical"))
    out = {"investigations": len(rows), "bit_identical": ok, "rows": rows}
    (res / "evidence_check.json").write_text(json.dumps(out, indent=1) + "\n")
    print(f"{ok} of {len(rows)} evidence windows bit for bit identical to the HDF5 source")


if __name__ == "__main__":
    main()
