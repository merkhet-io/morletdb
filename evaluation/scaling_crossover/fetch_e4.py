#!/usr/bin/env python3
"""E4 data (PROTOCOL.md): days 15-61 (2025-03-15..04-30) of the four R2 stations. Same procedure as R2's
evaluation/retrieval_scaling/fetch.py (one miniSEED per station-day from GEOFON, touching records merged, real
gaps kept as separate segments, int32 arrays, manifest with SHA-256), with R2's day numbering continued.

  .venv-seismic/bin/python -I evaluation/scaling_crossover/fetch_e4.py data/cx-scale-e4
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
from obspy import UTCDateTime, read
from obspy.clients.fdsn import Client

GEOFON = "https://geofon.gfz.de"
STATIONS = ("PB01", "PB02", "PB06", "PSGCX")
START, FIRST, LAST = UTCDateTime("2025-03-01"), 15, 61     # R2 day 1 = 2025-03-01


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    client = Client(GEOFON)
    manifest = []
    for day in range(FIRST, LAST + 1):
        t0 = START + 86400 * (day - 1)
        for st in STATIONS:
            path = out / f"CX.{st}.HHZ.d{day:02d}.mseed"
            if not path.exists():
                client.get_waveforms("CX", st, "", "HHZ", t0, t0 + 86400, filename=str(path))
            stream = read(str(path))
            stream.merge(method=-1)
            segments = []
            for i, tr in enumerate(sorted(stream, key=lambda t: t.stats.starttime)):
                tr = tr.slice(t0, t0 + 86400 - tr.stats.delta)
                if tr.stats.npts == 0:
                    continue
                seg = path.with_name(f"CX.{st}.HHZ.d{day:02d}.{i:03d}.npy")
                np.save(seg, tr.data.astype(np.int32))
                segments.append({"file": seg.name, "start": str(tr.stats.starttime), "samples": int(tr.stats.npts),
                                 "rate": float(tr.stats.sampling_rate)})
            manifest.append({"station": st, "day": day, "mseed": path.name, "mseed_bytes": path.stat().st_size,
                             "mseed_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "segments": segments})
            print(st, day, len(segments), sum(s["samples"] for s in segments), flush=True)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1))


if __name__ == "__main__":
    main()
