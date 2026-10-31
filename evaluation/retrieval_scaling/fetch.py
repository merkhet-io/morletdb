#!/usr/bin/env python3
"""R2 data (PROTOCOL.md): CX HHZ, 4 stations, 2025-03-01..15, one miniSEED per station-day from GEOFON,
plus int32 arrays per contiguous segment and a manifest with SHA-256 sums.

  .venv-seismic/bin/python -I evaluation/retrieval_scaling/fetch.py data/cx-scale
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
START, DAYS = UTCDateTime("2025-03-01"), 14


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    client = Client(GEOFON)
    manifest = []
    for day in range(1, DAYS + 1):
        t0 = START + 86400 * (day - 1)
        for st in STATIONS:
            path = out / f"CX.{st}.HHZ.d{day:02d}.mseed"
            if not path.exists():
                client.get_waveforms("CX", st, "", "HHZ", t0, t0 + 86400, filename=str(path))
            stream = read(str(path))
            stream.merge(method=-1)          # join only touching records; real gaps stay separate traces
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
