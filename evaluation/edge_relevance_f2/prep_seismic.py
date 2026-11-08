#!/usr/bin/env python3
"""Predicted P arrivals (iasp91, as E5) of catalogued events in a CX gallery, as sample offsets per recording.

  .venv-seismic/bin/python -I evaluation/edge_relevance_f2/prep_seismic.py data/cx-continuous data/e5-catalog dev out.json
"""
import json
import sys
from pathlib import Path

from obspy import UTCDateTime

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation/task_relevance"))
from e5 import arrivals, catalogue, stations  # noqa: E402


def main(data, cat, role, out):
    data, cat = Path(data), Path(cat)
    events = catalogue(cat / "emsc_events.txt")
    arr = arrivals(events, stations(cat / "stations.txt"))
    mag = {e["id"]: e["mag"] for e in events}
    recs = []
    for m in json.loads((data / "manifest.json").read_text()):
        if role and m.get("role") != role:
            continue
        for i, seg in enumerate(m["segments"]):
            path = data / seg["file"]
            if not path.exists():
                path = data / m["role"] / seg["file"]
            start = UTCDateTime(seg["start"])
            p = [{"event": eid, "sample": int(round((float(tp) - float(start)) * seg["rate"])), "mag": mag[eid],
                  "dist_deg": float(d)}
                 for (eid, s), (tp, d) in arr.items() if s == m["station"]]
            p = [x for x in p if 0 <= x["sample"] < seg["samples"]]
            recs.append({"id": f"{m['station']}.{m.get('role', 'x')}.{i}", "station": m["station"], "file": str(path),
                         "rate": seg["rate"], "samples": seg["samples"], "start": str(start), "arrivals": p})
    Path(out).write_text(json.dumps(recs, indent=1))
    print(len(recs), "recordings,", sum(len(r["arrivals"]) for r in recs), "arrivals")


if __name__ == "__main__":
    main(*sys.argv[1:5])
