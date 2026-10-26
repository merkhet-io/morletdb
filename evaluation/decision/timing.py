#!/usr/bin/env python3
"""Timing test (PLAN_TIMING.md): earthquake retrieval by hypocentral distance.

  catalog   ComCat events for one year (regional M>=2.5, global M>=6) -> catalog-<year>.json
  windows   event x station windows (TauP P - 5 s, hypocentral distance <= 250 km) + no-match times
  fetch     GEOFON FDSN miniSEED per event / no-match window, with sha256 manifest
  prepare   exact 4096-sample windows + data-quality rule -> windows-<year>.npz
  features  S, FS, M, ST, MT, XC envelopes -> features-<year>.npz
  evaluate  dev (2023) or heldout (2024, once): metrics, comparisons, no-match
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]
from retrieval import baseline_features  # noqa: E402
from bearings import provenance, sha256  # noqa: E402

RATE = 100.0
WINDOW = 4096
PRE_P_S = 5.0
MARGIN_S = 30.0
MAX_HYPO_KM = 250.0
CENTER = (-20.8, -69.5)
STATIONS = ("PB01", "PB02", "PB03", "PB07", "PB08", "PB09", "PB20", "HMBCX", "PATCX", "PSGCX", "MNMCX")
NOISE_PER_PERIOD = 300
CLIP = 8_000_000
EXCLUDE_HOURS, EXCLUDE_KM = 24.0, 30.0
BANDS_KM = (0, 80, 120, 160, 200, 250.0001)
TOP_K = 10
METHODS = ("S", "FS", "M", "ST", "MT", "XC")
VECTOR_METHODS = ("S", "FS", "M", "ST", "MT")
EXTRACTOR = "morlet-energy-lowrate-v1"
COMCAT = "https://earthquake.usgs.gov/fdsnws/event/1/query"
GEOFON = "https://geofon.gfz.de/fdsnws/dataselect/1/query"
ROLE = {2023: "dev", 2024: "heldout"}


# -- catalogue and window planning -----------------------------------------------------
def fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=120) as r:
        return json.loads(r.read())


def catalog(year: int, out: Path):
    regional = {"format": "geojson", "starttime": f"{year}-01-01", "endtime": f"{year + 1}-01-01",
                "latitude": CENTER[0], "longitude": CENTER[1], "maxradiuskm": 300, "minmagnitude": 2.5,
                "orderby": "time-asc"}
    wide = {"format": "geojson", "starttime": f"{year}-01-01", "endtime": f"{year + 1}-01-01",
            "latitude": CENTER[0], "longitude": CENTER[1], "maxradiuskm": 400, "minmagnitude": 2.5}
    glob = {"format": "geojson", "starttime": f"{year - 1}-12-31", "endtime": f"{year + 1}-01-02",
            "minmagnitude": 6.0}
    report = {}
    for name, params in (("regional", regional), ("wide", wide), ("global", glob)):
        url = COMCAT + "?" + urllib.parse.urlencode(params)
        data = fetch_json(url)
        path = out / f"catalog-{year}-{name}.json"
        path.write_text(json.dumps(data))
        report[name] = {"url": url, "events": len(data["features"]), "sha256": sha256(path)}
    (out / f"catalog-{year}.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def events(out: Path, year: int, name: str):
    data = json.loads((out / f"catalog-{year}-{name}.json").read_text())
    rows = []
    for f in data["features"]:
        lon, lat, depth = f["geometry"]["coordinates"]
        rows.append({"id": f["id"], "time": f["properties"]["time"] / 1000.0, "lat": lat, "lon": lon,
                     "depth": depth, "mag": f["properties"]["mag"]})
    return rows


def station_coords(out: Path):
    path = out / "stations.json"
    if not path.exists():
        url = ("https://geofon.gfz.de/fdsnws/station/1/query?" + urllib.parse.urlencode(
            {"network": "CX", "station": ",".join(STATIONS), "channel": "HHZ", "level": "station",
             "format": "text"}))
        with urllib.request.urlopen(url, timeout=120) as r:
            lines = r.read().decode().splitlines()[1:]
        coords = {line.split("|")[1]: (float(line.split("|")[2]), float(line.split("|")[3])) for line in lines}
        path.write_text(json.dumps({"url": url, "coords": coords}, indent=1))
    return json.loads(path.read_text())["coords"]


def hypocentral_km(lat, lon, depth, slat, slon):
    from obspy.geodetics import gps2dist_azimuth
    epi = gps2dist_azimuth(lat, lon, slat, slon)[0] / 1000.0
    return float(np.hypot(epi, depth)), epi


def plan_windows(year: int, out: Path):
    from obspy.geodetics import locations2degrees
    from obspy.taup import TauPyModel
    model = TauPyModel("iasp91")
    coords = station_coords(out)
    rows = []
    for e in events(out, year, "regional"):
        for st in STATIONS:
            slat, slon = coords[st]
            hypo, epi = hypocentral_km(e["lat"], e["lon"], e["depth"], slat, slon)
            if hypo > MAX_HYPO_KM:
                continue
            deg = locations2degrees(e["lat"], e["lon"], slat, slon)
            arr = model.get_travel_times(source_depth_in_km=max(e["depth"], 0.0), distance_in_degree=deg,
                                         phase_list=["p", "P", "Pn", "Pg"])
            if not arr:
                continue
            p = min(a.time for a in arr)
            rows.append({"kind": "event", "event": e["id"], "station": st, "start": e["time"] + p - PRE_P_S,
                         "hypo_km": hypo, "epi_km": epi, "depth": e["depth"], "mag": e["mag"],
                         "lat": e["lat"], "lon": e["lon"], "origin": e["time"]})
    # No-match windows: fixed seed per year; quiet with respect to the catalogue.
    wide = [e["time"] for e in events(out, year, "wide")]
    big = [e["time"] for e in events(out, year, "global")]
    rng = np.random.default_rng(year)
    t0, t1 = calendar.timegm((year, 1, 1, 0, 0, 0)), calendar.timegm((year + 1, 1, 1, 0, 0, 0))
    wide, big = np.array(wide), np.array(big)
    noise = []
    while len(noise) < NOISE_PER_PERIOD:
        t = float(rng.uniform(t0 + 3600, t1 - 3600))
        st = STATIONS[int(rng.integers(len(STATIONS)))]
        end = t + WINDOW / RATE
        if ((wide > t - 600) & (wide < end + 120)).any() or ((big > t - 3600) & (big < end + 120)).any():
            continue
        noise.append({"kind": "noise", "event": f"noise-{year}-{len(noise):03d}", "station": st, "start": t})
    plan = {"year": year, "event_windows": rows, "noise_windows": noise}
    (out / f"windows-plan-{year}.json").write_text(json.dumps(plan, indent=1))
    print(json.dumps({"year": year, "events": len({r['event'] for r in rows}), "event_windows": len(rows),
                      "noise_windows": len(noise)}))


def fetch(year: int, out: Path, data: Path):
    """One GEOFON request per event (all its stations) and per no-match window."""
    from obspy import UTCDateTime
    plan = json.loads((out / f"windows-plan-{year}.json").read_text())
    target = data / str(year)
    target.mkdir(parents=True, exist_ok=True)
    groups = {}
    for w in plan["event_windows"] + plan["noise_windows"]:
        groups.setdefault(w["event"], []).append(w)
    failures = []
    for i, (name, ws) in enumerate(sorted(groups.items())):
        path = target / f"{name}.mseed"
        if path.exists() and path.stat().st_size > 0:
            continue
        lines = []
        for w in ws:
            s = UTCDateTime(w["start"] - MARGIN_S)
            e = UTCDateTime(w["start"] + WINDOW / RATE + MARGIN_S)
            lines.append(f"CX {w['station']} -- HHZ {s.strftime('%Y-%m-%dT%H:%M:%S')} {e.strftime('%Y-%m-%dT%H:%M:%S')}")
        body = "\n".join(lines).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(GEOFON, data=body, method="POST")
                with urllib.request.urlopen(req, timeout=180) as r:
                    content = r.read() if r.status == 200 else b""
                path.write_bytes(content)
                break
            except Exception as exc:  # noqa: BLE001 - recorded, retried, then reported
                if attempt == 3:
                    failures.append({"group": name, "error": repr(exc)})
                    path.write_bytes(b"")
                time.sleep(5 * (attempt + 1))
    manifest = {p.name: sha256(p) for p in sorted(target.glob("*.mseed"))}
    (out / f"fetch-{year}.json").write_text(json.dumps({"files": len(manifest), "failures": failures,
                                                       "empty": sorted(k for k in manifest if (target / k).stat().st_size == 0),
                                                       "sha256": manifest, "provenance": provenance()}, indent=1))
    print(json.dumps({"files": len(manifest), "failures": len(failures)}))


# -- windows ---------------------------------------------------------------------------
def cut(stream, station: str, start: float):
    """Exactly 4096 samples from the first sample at or after `start`; None plus reason if not clean."""
    from obspy import UTCDateTime
    tr = stream.select(station=station, channel="HHZ")
    if len(tr) == 0:
        return None, "missing"
    t0 = UTCDateTime(start)
    t1 = t0 + WINDOW / RATE
    covering = [t for t in tr if t.stats.starttime <= t0 + 0.5 / RATE and t.stats.endtime >= t1]
    if len(covering) != 1 or len([t for t in tr if t.stats.endtime > t0 and t.stats.starttime < t1]) != 1:
        return None, "gap_or_overlap"
    t = covering[0]
    if abs(t.stats.sampling_rate - RATE) > 1e-6:
        return None, "rate"
    i0 = int(np.ceil((t0 - t.stats.starttime) * RATE - 1e-6))
    x = t.data[i0:i0 + WINDOW].astype(np.float64)
    if len(x) != WINDOW:
        return None, "short"
    if np.ma.is_masked(x) or not np.isfinite(x).all() or x.std() == 0:
        return None, "invalid_or_flat"
    if np.abs(x).max() >= CLIP:
        return None, "clipped"
    return x, None


def prepare(year: int, out: Path, data: Path):
    from obspy import read
    plan = json.loads((out / f"windows-plan-{year}.json").read_text())
    xs, meta, reasons = [], [], {}
    cache = {}
    for w in plan["event_windows"] + plan["noise_windows"]:
        path = data / str(year) / f"{w['event']}.mseed"
        if w["event"] not in cache:
            cache.clear()
            cache[w["event"]] = read(str(path)) if path.exists() and path.stat().st_size else None
        st = cache[w["event"]]
        x, reason = (None, "no_data") if st is None else cut(st, w["station"], w["start"])
        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        xs.append(x)
        meta.append((w["kind"], w["event"], w["station"], w["start"], w.get("hypo_km", np.nan), w.get("depth", np.nan),
                     w.get("mag", np.nan), w.get("lat", np.nan), w.get("lon", np.nan), w.get("origin", np.nan)))
    names = ["kind", "event", "station", "start", "hypo_km", "depth", "mag", "lat", "lon", "origin"]
    cols = {n: np.array(c) for n, c in zip(names, zip(*meta))}
    np.savez(out / f"windows-{year}.npz", x=np.stack(xs), **cols)
    ev = cols["kind"] == "event"
    summary = {"year": year, "windows": len(xs), "event_windows": int(ev.sum()),
               "events": int(len(set(cols["event"][ev]))), "noise_windows": int((~ev).sum()),
               "excluded": reasons, "per_station": {s: int((cols["station"][ev] == s).sum()) for s in STATIONS},
               "hypo_km_quantiles": np.percentile(cols["hypo_km"][ev].astype(float), [0, 25, 50, 75, 100]).round(1).tolist(),
               "provenance": provenance()}
    (out / f"prepare-{year}.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps(summary, indent=1))


# -- features --------------------------------------------------------------------------
def band_edges(rate):
    centers = np.geomspace(rate / 64, rate * 0.4, 12)  # the existing baseline layout
    return np.r_[centers[0] / np.sqrt(centers[1] / centers[0]), np.sqrt(centers[:-1] * centers[1:]),
                 centers[-1] * np.sqrt(centers[-1] / centers[-2])]


def spectrogram_bins(x: np.ndarray, rate: float) -> np.ndarray:
    """12 bands x 4 equal time bins of Hann-windowed power, log1p, band-major (as MT)."""
    from retrieval import normalize
    z = normalize(x, "zscore")
    z = z - z.mean()
    edges = band_edges(rate)
    n = len(z) // 4
    freqs = np.fft.rfftfreq(n, 1 / rate)
    win = np.hanning(n)
    out = np.zeros((12, 4))
    for k in range(4):
        seg = z[k * n:(k + 1) * n] * win
        # Same power scale as the pooled FFT baseline (|X|^2 / n^2), Hann power compensated.
        power = np.abs(np.fft.rfft(seg)) ** 2 / n ** 2 / np.mean(win ** 2)
        out[:, k] = [power[(freqs >= a) & (freqs < b)].sum() for a, b in zip(edges[:-1], edges[1:])]
    return np.log1p(out).ravel()


def envelope(x: np.ndarray) -> np.ndarray:
    from scipy.signal import hilbert
    from retrieval import normalize
    env = np.abs(hilbert(normalize(x, "zscore")))
    env = np.convolve(env, np.ones(50) / 50, mode="same")[::10]
    s = env.std()
    return (env - env.mean()) / (s if s > 0 else 1.0)


def morlet(windows: np.ndarray, binary: Path):
    pooled, temporal = [], []
    for lo in range(0, len(windows), 256):
        payload = "".join(json.dumps({"samples": w.tolist(), "sample_rate_hz": RATE, "normalization": "zscore",
                                      "extractor_version": EXTRACTOR}) + "\n" for w in windows[lo:lo + 256])
        r = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True)
        rows = [json.loads(line) for line in r.stdout.splitlines()]
        if len(rows) != len(windows[lo:lo + 256]) or {x["version"] for x in rows} != {EXTRACTOR}:
            raise ValueError("unexpected extractor output")
        pooled += [x["morlet"] for x in rows]
        temporal += [x["morlet_temporal_experimental"] for x in rows]
    return np.array(pooled), np.array(temporal)


def features(year: int, out: Path, binary: Path):
    w = np.load(out / f"windows-{year}.npz")
    x = w["x"]
    cost, feats = {}, {}
    t = time.perf_counter()
    base = [baseline_features(s, RATE, "zscore") for s in x]
    feats["S"] = np.array([b["statistics"] for b in base])
    feats["FS"] = np.hstack([np.array([b["fft_bands"] for b in base]), feats["S"]])
    cost["S+FS"] = (time.perf_counter() - t) / len(x)
    t = time.perf_counter()
    feats["ST"] = np.hstack([np.array([spectrogram_bins(s, RATE) for s in x]), feats["S"]])
    cost["ST"] = (time.perf_counter() - t) / len(x)
    t = time.perf_counter()
    feats["M"], feats["MT"] = morlet(x, binary)
    cost["M+MT (rust, incl. JSON adapter)"] = (time.perf_counter() - t) / len(x)
    t = time.perf_counter()
    feats["XC"] = np.array([envelope(s) for s in x])
    cost["XC envelope"] = (time.perf_counter() - t) / len(x)
    for k, v in feats.items():
        if not np.isfinite(v).all():
            raise ValueError(f"non-finite {k}")
    np.savez(out / f"features-{year}.npz", **feats)
    (out / f"features-{year}.json").write_text(json.dumps(
        {"per_window_ms": {k: 1e3 * v for k, v in cost.items()}, "features_bin_sha256": sha256(binary),
         "extractor": EXTRACTOR, "dims": {k: int(v.shape[1]) for k, v in feats.items()},
         "provenance": provenance()}, indent=1))
    print(json.dumps({k: round(1e3 * v, 3) for k, v in cost.items()}))


# -- retrieval -------------------------------------------------------------------------
def vector_distances(q: np.ndarray, g: np.ndarray) -> np.ndarray:
    d2 = (q ** 2).sum(1)[:, None] + (g ** 2).sum(1)[None, :] - 2 * q @ g.T
    return np.sqrt(np.maximum(d2, 0) / q.shape[1])


def xcorr_distances(q: np.ndarray, g: np.ndarray, max_lag: int = 30) -> np.ndarray:
    """1 - max over |lag| <= 3 s of the (biased) normalized cross-correlation of envelopes."""
    n = q.shape[1]
    size = 1 << int(np.ceil(np.log2(2 * n)))
    fq, fg = np.fft.rfft(q, size), np.fft.rfft(g, size)
    out = np.empty((len(q), len(g)))
    lags = np.r_[0:max_lag + 1, size - max_lag:size]
    for i in range(len(q)):
        cc = np.fft.irfft(fq[i][None, :] * np.conj(fg), size)[:, lags] / n
        out[i] = 1 - cc.max(1)
    return out


def method_distances(feats, method, qi, gi, standardize_from=None):
    if method == "XC":
        return xcorr_distances(feats["XC"][qi], feats["XC"][gi])
    x = feats[method]
    if standardize_from is not None:
        mu, sd = standardize_from
        x = (x - mu) / np.where(sd > 0, sd, 1.0)
    return vector_distances(x[qi], x[gi])


def gallery_mask(meta, qi, gi, same_station: bool):
    """Other events only; drop events within 24 h and 30 km (hypocentres) of the query event."""
    ev = meta["kind"][gi] == "event"
    m = ev[None, :] & (meta["event"][gi][None, :] != meta["event"][qi][:, None])
    if same_station:
        m &= meta["station"][gi][None, :] == meta["station"][qi][:, None]
    q_event = meta["kind"][qi] == "event"
    dt = np.abs(meta["origin"][gi][None, :].astype(float) - meta["origin"][qi][:, None].astype(float)) / 3600
    dist = 111.2 * np.hypot(meta["lat"][gi][None, :].astype(float) - meta["lat"][qi][:, None].astype(float),
                            np.cos(np.radians(meta["lat"][qi][:, None].astype(float)))
                            * (meta["lon"][gi][None, :].astype(float) - meta["lon"][qi][:, None].astype(float)))
    dist = np.hypot(dist, meta["depth"][gi][None, :].astype(float) - meta["depth"][qi][:, None].astype(float))
    near = (dt < EXCLUDE_HOURS) & (dist < EXCLUDE_KM) & q_event[:, None]
    return m & ~near


def band(km):
    return np.digitize(km, BANDS_KM[1:-1])


def query_metrics(dist, mask, meta, qi, gi, rng=None):
    rows = []
    hq, hg = meta["hypo_km"][qi].astype(float), meta["hypo_km"][gi].astype(float)
    for i in range(len(qi)):
        idx = np.where(mask[i])[0]
        if len(idx) < TOP_K:
            rows.append(None)
            continue
        order = idx[np.lexsort((idx, dist[i, idx]))]
        top = order[:TOP_K]
        row = {"nearest": float(dist[i, order[0]]), "top": top}
        if meta["kind"][qi[i]] == "event":
            row["median_abs_dkm"] = float(np.median(np.abs(hg[top] - hq[i])))
            row["p10_band"] = float((band(hg[top]) == band(hq[i])).mean())
            if rng is not None:
                draws = [np.median(np.abs(hg[rng.choice(idx, TOP_K, replace=False)] - hq[i])) for _ in range(200)]
                row["chance_median_abs_dkm"] = float(np.mean(draws))
                row["chance_p10_band"] = float((band(hg[idx]) == band(hq[i])).mean())
        rows.append(row)
    return rows


def per_event(rows, events_):
    groups = {}
    for r, e in zip(rows, events_):
        if r is not None:
            groups.setdefault(e, []).append(r)
    return groups


def mean_over_events(groups, key):
    vals = [np.mean([r[key] for r in rs]) for rs in groups.values() if key in rs[0]]
    return float(np.mean(vals)) if vals else None


def bootstrap(a: dict, b: dict, resamples=2000, seed=7):
    """Positive diff: method A has the smaller median |dd| (A better)."""
    keys = sorted(set(a) & set(b))
    diffs = np.array([b[k] - a[k] for k in keys])
    rng = np.random.default_rng(seed)
    boots = [diffs[rng.integers(0, len(diffs), len(diffs))].mean() for _ in range(resamples)]
    return {"diff_km": float(diffs.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "events": len(keys)}


def verdict(c, margin=5.0):
    lo, hi = c["ci95"]
    return "supported" if c["diff_km"] >= margin and lo > 0 else ("rejected" if hi < margin else "inconclusive")


def auroc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    if not len(pos) or not len(neg):
        return None
    return float((neg[:, None] > pos[None, :]).mean() + 0.5 * (neg[:, None] == pos[None, :]).mean())


def evaluate(year: int, out: Path, dev_year: int = 2023):
    w = np.load(out / f"windows-{year}.npz")
    meta = {k: w[k] for k in w.files if k != "x"}
    feats = dict(np.load(out / f"features-{year}.npz"))
    dev = dict(np.load(out / f"features-{dev_year}.npz"))
    role = ROLE[year]
    gi = np.arange(len(meta["kind"]))
    ev = np.where(meta["kind"] == "event")[0]
    noise = np.where(meta["kind"] == "noise")[0]
    rng = np.random.default_rng(11)
    report = {"year": year, "role": role, "event_windows": int(len(ev)), "events": int(len(set(meta["event"][ev]))),
              "noise_windows": int(len(noise)), "results": {}}
    thresholds = {}
    if role == "heldout":
        thresholds = json.loads((out / f"evaluation-{dev_year}.json").read_text())["thresholds"]
    per_method = {}
    chance_done = False
    for variant in ("frozen", "standardized"):
        for method in METHODS:
            if variant == "standardized" and method == "XC":
                continue
            std = (dev[method].mean(0), dev[method].std(0)) if variant == "standardized" else None
            key = f"{variant}/{method}"
            t = time.perf_counter()
            d_ev = method_distances(feats, method, ev, gi, std)
            search_ms = 1e3 * (time.perf_counter() - t) / len(ev)
            entry = {"search_per_query_ms": search_ms}
            for gallery in ("same_station", "any_station"):
                mask = gallery_mask(meta, ev, gi, gallery == "same_station")
                rows = query_metrics(d_ev, mask, meta, ev, gi, rng if not chance_done else None)
                groups = per_event(rows, meta["event"][ev])
                res = {"queries": sum(r is not None for r in rows), "events": len(groups),
                       "median_abs_dkm": mean_over_events(groups, "median_abs_dkm"),
                       "p10_band": mean_over_events(groups, "p10_band")}
                if not chance_done:
                    res["chance_median_abs_dkm"] = mean_over_events(groups, "chance_median_abs_dkm")
                    res["chance_p10_band"] = mean_over_events(groups, "chance_p10_band")
                    report.setdefault("chance", {})[gallery] = {k: res.pop(k) for k in
                                                                ("chance_median_abs_dkm", "chance_p10_band")}
                if gallery == "same_station":
                    per_method[key] = {e: float(np.mean([r["median_abs_dkm"] for r in rs])) for e, rs in groups.items()}
                    q_events = {e: i for i, e in enumerate(meta["event"][ev])}
                    for name, sel in (("depth<60", lambda i: float(meta["depth"][ev][i]) < 60),
                                      ("depth>=60", lambda i: float(meta["depth"][ev][i]) >= 60)):
                        res[f"by_{name}"] = float(np.mean([np.mean([r["median_abs_dkm"] for r in rs])
                                                            for e, rs in groups.items() if sel(q_events[e])] or [np.nan]))
                    mags = np.array([float(meta["mag"][ev][q_events[e]]) for e in groups])
                    terciles = np.percentile(mags, [33.33, 66.67])
                    for t_i, name in enumerate(("mag_low", "mag_mid", "mag_high")):
                        sel = [e for e, m in zip(groups, mags) if np.digitize(m, terciles) == t_i]
                        res[f"by_{name}"] = float(np.mean([np.mean([r["median_abs_dkm"] for r in groups[e]]) for e in sel]))
                    if variant == "frozen":
                        res["matches"] = [{"query": [str(meta["event"][ev][i]), str(meta["station"][ev][i]),
                                                     round(float(meta["hypo_km"][ev][i]), 1)],
                                           "median_abs_dkm": r["median_abs_dkm"],
                                           "top": [[str(meta["event"][j]), round(float(meta["hypo_km"][j]), 1),
                                                    round(float(d_ev[i, j]), 6)] for j in r["top"]]}
                                          for i, r in enumerate(rows) if r is not None]
                entry[gallery] = res
            chance_done = True
            # No-match: noise windows vs event queries, same-station gallery.
            d_no = method_distances(feats, method, noise, gi, std)
            mask_no = gallery_mask(meta, noise, gi, True)
            neg = [r["nearest"] for r in query_metrics(d_no, mask_no, meta, noise, gi) if r is not None]
            pos_rows = query_metrics(d_ev, gallery_mask(meta, ev, gi, True), meta, ev, gi)
            pos = [r["nearest"] for r in pos_rows if r is not None]
            nm = {"auroc_nearest": auroc(pos, neg), "positives": len(pos), "negatives": len(neg)}
            if role == "dev":
                thresholds[key] = float(np.percentile(pos, 90))
            elif key in thresholds:
                nm["dev_threshold"] = thresholds[key]
                nm["false_accept_rate"] = float(np.mean(np.array(neg) <= thresholds[key]))
                nm["event_accept_rate"] = float(np.mean(np.array(pos) <= thresholds[key]))
            entry["no_match"] = nm
            report["results"][key] = entry
    per_method["standardized/XC"] = per_method["frozen/XC"]  # XC has no feature scaling
    comps = {}
    for a, b in (("ST", "FS"), ("MT", "ST"), ("MT", "XC"), ("M", "FS"), ("MT", "M"), ("ST", "XC")):
        for variant in ("standardized", "frozen"):
            c = bootstrap(per_method[f"{variant}/{a}"], per_method[f"{variant}/{b}"])
            comps[f"{variant}: {a} vs {b}"] = c
    report["comparisons"] = comps
    # Primary: standardized (development-fit) features, so no method wins by feature scale.
    report["decisions"] = {name: {**comps[f"standardized: {pair}"],
                                  "verdict_provisional_5km": verdict(comps[f"standardized: {pair}"])}
                           for name, pair in (("1 timing helps", "ST vs FS"), ("2 Morlet-specific", "MT vs ST"),
                                              ("3 decomposition over time series", "MT vs XC"))}
    report["thresholds"] = thresholds
    report["provenance"] = provenance()
    (out / f"evaluation-{year}.json").write_text(json.dumps(report, indent=1, default=str))
    print(json.dumps({"chance": report["chance"], "decisions": report["decisions"]}, indent=1))
    for key, e in report["results"].items():
        print(f"{key:20s} same-station med|dd| {e['same_station']['median_abs_dkm']:.1f} km  P@10band "
              f"{e['same_station']['p10_band']:.3f}  any-station {e['any_station']['median_abs_dkm']:.1f}  "
              f"noMatch AUROC {e['no_match']['auroc_nearest']}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["catalog", "windows", "fetch", "prepare", "features", "evaluate"])
    p.add_argument("--year", type=int, required=True, choices=sorted(ROLE))
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--data", type=Path, help="miniSEED directory (git-ignored)")
    p.add_argument("--features-bin", type=Path, default=HERE.parents[1] / "target/release/examples/features")
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    {"catalog": lambda: catalog(a.year, a.out), "windows": lambda: plan_windows(a.year, a.out),
     "fetch": lambda: fetch(a.year, a.out, a.data), "prepare": lambda: prepare(a.year, a.out, a.data),
     "features": lambda: features(a.year, a.out, a.features_bin),
     "evaluate": lambda: evaluate(a.year, a.out)}[a.stage]()


if __name__ == "__main__":
    main()
