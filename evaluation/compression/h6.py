#!/usr/bin/env python3
"""H6: triggered retention on held-out CX days (PROTOCOL.md).

  catalog  (ObsPy environment) ComCat events in the held-out period with predicted P per station
  run      keep raw FLAC only in [on - 30 s, off + 60 s] around STA/LTA triggers; ST features for all
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "decision")]
PERIOD = ("2024-03-01", "2024-03-03")
PRE_S, POST_S = 30.0, 60.0
ST_BYTES_PER_20S = 51 * 4


def catalog(out: Path):
    import urllib.parse
    import timing
    from obspy import UTCDateTime
    from obspy.geodetics import locations2degrees
    from obspy.taup import TauPyModel
    import datasets
    manifest = json.loads((datasets.DATA / "cx-continuous/manifest.json").read_text())
    stations = sorted({m["station"] for m in manifest if m["role"] == "heldout"})
    url = timing.COMCAT + "?" + urllib.parse.urlencode(
        {"format": "geojson", "starttime": PERIOD[0], "endtime": PERIOD[1], "latitude": timing.CENTER[0],
         "longitude": timing.CENTER[1], "maxradiuskm": 300, "minmagnitude": 2.5})
    data = timing.fetch_json(url)
    coords = timing.station_coords(out)
    model = TauPyModel("iasp91")
    arrivals = []
    for f in data["features"]:
        lon, lat, depth = f["geometry"]["coordinates"]
        t0 = f["properties"]["time"] / 1000.0
        for st in stations:
            hypo, _ = timing.hypocentral_km(lat, lon, depth, *coords[st])
            if hypo > timing.MAX_HYPO_KM:
                continue
            deg = locations2degrees(lat, lon, *coords[st])
            arr = model.get_travel_times(source_depth_in_km=max(depth, 0.0), distance_in_degree=deg,
                                         phase_list=["p", "P", "Pn", "Pg"])
            if arr:
                arrivals.append({"event": f["id"], "station": st, "mag": f["properties"]["mag"], "hypo_km": hypo,
                                 "p_time": str(UTCDateTime(t0 + min(a.time for a in arr)))})
    (out / "h6-catalog.json").write_text(json.dumps({"url": url, "events": len(data["features"]),
                                                     "arrivals": arrivals}, indent=1))
    print(len(data["features"]), "events", len(arrivals), "station arrivals")


def trigger_intervals(x, rate=100.0, on=4.0, off=1.5, sta=1.0, lta=30.0, warmup=120.0):
    from scipy.signal import butter, lfilter, sosfilt
    sos = butter(4, [2.0, 10.0], btype="bandpass", fs=rate, output="sos")
    a = sosfilt(sos, np.asarray(x, float) - np.mean(x)) ** 2
    ns, nl = int(sta * rate), int(lta * rate)
    s = lfilter([1 / ns], [1, -(1 - 1 / ns)], a)
    l_ = lfilter([1 / nl], [1, -(1 - 1 / nl)], a)
    cf = np.where(l_ > 0, s / np.maximum(l_, 1e-300), 0.0)
    cf[:nl] = 0
    rises = np.flatnonzero(np.diff(np.r_[False, cf > on].astype(np.int8)) == 1)
    lows = np.flatnonzero(cf < off)
    out, until = [], -1
    for i in rises:
        if i > until:
            j = np.searchsorted(lows, i)
            until = lows[j] if j < len(lows) else len(cf)
            if i >= warmup * rate:
                out.append((i, until))
    return out


def run(out: Path):
    import blockcodecs as bc
    import datasets
    from datetime import datetime, timezone
    cat = json.loads((out / "h6-catalog.json").read_text())
    root = datasets.DATA / "cx-continuous"
    manifest = [m for m in json.loads((root / "manifest.json").read_text()) if m["role"] == "heldout"]
    report = {"stations": {}, "policy": {"pre_s": PRE_S, "post_s": POST_S, "detector": "STA/LTA 2-10 Hz, 1/30 s, on 4, off 1.5"}}
    tot = {"samples": 0, "kept": 0, "full_flac": 0, "kept_flac": 0, "features": 0}
    covered, present = 0, 0
    for m in manifest:
        st = m["station"]
        kept_spans = []
        s_tot = {"samples": 0, "kept": 0, "full_flac": 0, "kept_flac": 0, "segments": len(m["segments"])}
        for seg in m["segments"]:
            x = np.load(root / "heldout" / seg["file"]).astype(np.int64)
            start = datetime.fromisoformat(seg["start"].replace("Z", "+00:00")).timestamp()
            rate = seg["rate"]
            s_tot["samples"] += len(x)
            s_tot["full_flac"] += sum(len(bc.flac(x[i:i + 65536])) for i in range(0, len(x), 65536))
            keep = np.zeros(len(x), bool)
            for on, off in trigger_intervals(x, rate):
                keep[max(0, on - int(PRE_S * rate)):min(len(x), off + int(POST_S * rate))] = True
            idx = np.flatnonzero(np.diff(np.r_[0, keep.astype(np.int8), 0]))
            for a, b in zip(idx[0::2], idx[1::2]):
                s_tot["kept_flac"] += len(bc.flac(x[a:b])) if b - a >= 16 else 2 * (b - a)
                kept_spans.append((start + a / rate, start + b / rate))
            s_tot["kept"] += int(keep.sum())
            seg_end = start + len(x) / rate
            for arr in cat["arrivals"]:
                if arr["station"] != st:
                    continue
                t = datetime.fromisoformat(arr["p_time"].replace("Z", "+00:00")).timestamp()
                if start <= t < seg_end:
                    present += 1
                    covered += any(a <= t <= b for a, b in kept_spans)
        s_tot["features"] = int(s_tot["samples"] / rate / 20 * ST_BYTES_PER_20S)
        report["stations"][st] = s_tot
        for k in tot:
            tot[k] += s_tot[k]
    report["total"] = tot
    report["fraction_raw_kept"] = tot["kept"] / tot["samples"]
    report["bytes_retained_over_full_flac"] = (tot["kept_flac"] + tot["features"]) / tot["full_flac"]
    report["catalog_arrivals_in_data"] = present
    report["catalog_arrivals_retained"] = covered
    report["catalog_coverage"] = covered / present if present else None
    (out / "h6.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report[k] for k in ("fraction_raw_kept", "bytes_retained_over_full_flac",
                                             "catalog_arrivals_in_data", "catalog_arrivals_retained", "catalog_coverage")}))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["catalog", "run"])
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    {"catalog": catalog, "run": run}[a.stage](a.out)


if __name__ == "__main__":
    main()
