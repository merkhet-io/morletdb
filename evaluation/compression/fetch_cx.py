#!/usr/bin/env python3
"""Continuous IPOC CX HHZ counts for the compression benchmark (native int32, Steim2 at source).

Writes raw miniSEED (the archive's own compressed size) and per-segment int32 arrays with
start times, so gaps and clock information are preserved.
"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

GEOFON = "https://geofon.gfz.de"
STATIONS = ("PB01", "PB02", "PB06", "PSGCX")
PERIODS = {"dev": ("2023-03-01", "2023-03-03"), "heldout": ("2024-03-01", "2024-03-03")}


def main():
    from obspy import UTCDateTime, read
    from obspy.clients.fdsn import Client
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    client = Client(GEOFON)
    manifest = []
    for role, (t0, t1) in PERIODS.items():
        for st in STATIONS:
            path = a.out / role / f"CX.{st}.HHZ.mseed"
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                client.get_waveforms("CX", st, "", "HHZ", UTCDateTime(t0), UTCDateTime(t1), filename=str(path))
            stream = read(str(path))
            segments = []
            for i, tr in enumerate(sorted(stream, key=lambda t: t.stats.starttime)):
                seg = path.with_name(f"CX.{st}.HHZ.{i:03d}.npy")
                np.save(seg, tr.data.astype(np.int32))
                segments.append({"file": seg.name, "start": str(tr.stats.starttime), "samples": int(tr.stats.npts),
                                 "rate": float(tr.stats.sampling_rate), "encoding": str(tr.stats.mseed.encoding)})
            manifest.append({"role": role, "station": st, "period": [t0, t1], "mseed": path.name,
                             "mseed_bytes": path.stat().st_size,
                             "mseed_sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "segments": segments})
    (a.out / "manifest.json").write_text(json.dumps(manifest, indent=1))
    print(json.dumps([{k: m[k] for k in ("role", "station", "mseed_bytes")} | {"segments": len(m["segments"])}
                      for m in manifest], indent=0))


if __name__ == "__main__":
    main()
