#!/usr/bin/env python3
"""Cross-condition bearing retrieval (PLAN.md). Stages write to --out with provenance.

  verify    check archives against SHA256SUMS.archives and the Zenodo MD5s
  prepare   vibration windows per PLAN.md from extracted .mat files -> windows.npz
  features  S, F, FS and frozen Morlet features for every window and configuration
  evaluate  retrieval metrics for one role (dev, or heldout: read once after the freeze)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent)]  # also under python -I
from retrieval import baseline_features  # noqa: E402  (the existing evaluator's baselines)

RATE = 64000.0
WINDOW = 4096
WINDOWS_PER_FILE = 8
QUERY_MEASUREMENTS = range(1, 6)
QUERY_WINDOW_SLOTS = (0, 4)
TOP_K = 10
SETTINGS = ("N15_M07_F10", "N09_M07_F10", "N15_M01_F10", "N15_M07_F04")
# Paper Tables 4, 5 and 7 (Lessmeier et al., 2016): location, origin, extent level.
BEARINGS = {
    **{f"K00{i}": ("healthy", "healthy", 0) for i in range(1, 7)},
    "KA01": ("outer", "artificial", 1), "KA03": ("outer", "artificial", 2),
    "KA05": ("outer", "artificial", 1), "KA06": ("outer", "artificial", 2),
    "KA07": ("outer", "artificial", 1), "KA08": ("outer", "artificial", 2),
    "KA09": ("outer", "artificial", 2),
    "KI01": ("inner", "artificial", 1), "KI03": ("inner", "artificial", 1),
    "KI05": ("inner", "artificial", 1), "KI07": ("inner", "artificial", 2),
    "KI08": ("inner", "artificial", 2),
    "KA04": ("outer", "real", 1), "KA15": ("outer", "real", 1), "KA16": ("outer", "real", 2),
    "KA22": ("outer", "real", 1), "KA30": ("outer", "real", 1),
    "KB23": ("combined", "real", 2), "KB24": ("combined", "real", 3), "KB27": ("combined", "real", 1),
    "KI04": ("inner", "real", 1), "KI14": ("inner", "real", 1), "KI16": ("inner", "real", 3),
    "KI17": ("inner", "real", 1), "KI18": ("inner", "real", 2), "KI21": ("inner", "real", 1),
}
DEV = ("K001", "K002", "KA01", "KI01", "KA04", "KA15", "KI04", "KI14")
EXCLUDED = ("KA08",)  # absent from the only reachable copy (DATASET.md)
HELDOUT = tuple(b for b in BEARINGS if b not in DEV and b not in EXCLUDED)
METHODS = ("S", "F", "FS", "M")
CONFIGS = ("native-zscore", "native-none", "decim4-zscore", "decim4-none")
FROZEN = "native-zscore"


def provenance() -> dict:
    def run(*cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        return r.stdout.strip() if r.returncode == 0 else None
    import scipy
    return {"commit": run("git", "rev-parse", "HEAD"),
            "dirty": bool(run("git", "status", "--porcelain", "--untracked-files=no")),
            "python": platform.python_version(), "platform": platform.platform(),
            "numpy": np.__version__, "scipy": scipy.__version__}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def verify(archives: Path, out: Path):
    expected = dict(line.split()[::-1] for line in (archives.parent / "SHA256SUMS.archives").read_text().splitlines())
    md5 = dict(line.split() for line in (archives.parent / "md5.txt").read_text().splitlines())
    bad = []
    for name, digest in sorted(expected.items()):
        path = archives / name
        if sha256(path) != digest or hashlib.md5(path.read_bytes()).hexdigest() != md5.get(name):
            bad.append(name)
    report = {"archives": len(expected), "mismatched": bad, "source": "https://zenodo.org/records/15845309",
              "sha256": expected}
    (out / "verify.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "sha256"}))
    if bad or len(expected) != 31:
        raise SystemExit("archive verification failed")


# -- windows -------------------------------------------------------------------------
def window_starts(length: int) -> np.ndarray:
    """8 evenly spaced disjoint windows (PLAN.md)."""
    count = length // WINDOW
    if count < WINDOWS_PER_FILE:
        raise ValueError(f"recording too short: {length} samples")
    return np.linspace(0, count - 1, WINDOWS_PER_FILE).astype(int) * WINDOW


def decimate4(x: np.ndarray) -> np.ndarray:
    from scipy.signal import decimate
    return decimate(x, 4, ftype="fir", zero_phase=True)


def build_windows(recordings):
    """recordings: iterable of (bearing, setting, measurement, samples at 64 kHz).

    Returns windows for the native and the decimated variant plus per-window metadata.
    """
    native, decim, meta = [], [], []
    for bearing, setting, measurement, x in recordings:
        x = np.asarray(x, dtype=np.float64)
        if x.ndim != 1 or not np.isfinite(x).all():
            raise ValueError(f"bad samples: {bearing} {setting} {measurement}")
        d = decimate4(x)
        for slot, (s_n, s_d) in enumerate(zip(window_starts(len(x)), window_starts(len(d)))):
            native.append(x[s_n:s_n + WINDOW])
            decim.append(d[s_d:s_d + WINDOW])
            location, origin, extent = BEARINGS[bearing]
            meta.append((bearing, setting, measurement, slot, int(s_n), location, origin, extent,
                         "dev" if bearing in DEV else "heldout",
                         measurement in QUERY_MEASUREMENTS and slot in QUERY_WINDOW_SLOTS))
    columns = list(zip(*meta))
    names = ["bearing", "setting", "measurement", "slot", "start", "location", "origin", "extent",
             "role", "query"]
    return np.stack(native), np.stack(decim), {n: np.array(c) for n, c in zip(names, columns)}


# -- features ------------------------------------------------------------------------
def morlet_features(windows: np.ndarray, rate: float, normalization: str, binary: Path):
    out = []
    for lo in range(0, len(windows), 256):
        payload = "".join(json.dumps({"samples": w.tolist(), "sample_rate_hz": rate,
                                      "normalization": normalization}) + "\n" for w in windows[lo:lo + 256])
        result = subprocess.run([str(binary)], input=payload, text=True, capture_output=True, check=True)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        if len(rows) != len(windows[lo:lo + 256]) or {r["version"] for r in rows} != {"morlet-energy-v1"}:
            raise ValueError("unexpected extractor output")
        out += [r["morlet"] for r in rows]
    return np.array(out)


def compute_features(native, decim, binary: Path):
    feats, cost = {}, {}
    for config in CONFIGS:
        windows, rate = (native, RATE) if config.startswith("native") else (decim, RATE / 4)
        norm = config.split("-")[1]
        started = time.perf_counter()
        base = [baseline_features(w, rate, norm) for w in windows]
        base_s = time.perf_counter() - started
        feats[f"{config}/S"] = np.array([b["statistics"] for b in base])
        feats[f"{config}/F"] = np.array([b["fft_bands"] for b in base])
        feats[f"{config}/FS"] = np.hstack([feats[f"{config}/F"], feats[f"{config}/S"]])
        started = time.perf_counter()
        feats[f"{config}/M"] = morlet_features(windows, rate, norm, binary)
        m_s = time.perf_counter() - started
        cost[config] = {"numpy_S_F_per_window_ms": 1e3 * base_s / len(windows),
                        "rust_morlet_incl_json_adapter_per_window_ms": 1e3 * m_s / len(windows)}
        for k in METHODS:
            if not np.isfinite(feats[f"{config}/{k}"]).all():
                raise ValueError(f"non-finite features: {config}/{k}")
    return feats, cost


# -- retrieval -----------------------------------------------------------------------
def distances(q: np.ndarray, g: np.ndarray) -> np.ndarray:
    """RMS Euclidean distance (the evaluator's metric) for every query x gallery pair."""
    d2 = (q ** 2).sum(1)[:, None] + (g ** 2).sum(1)[None, :] - 2 * q @ g.T
    return np.sqrt(np.maximum(d2, 0) / q.shape[1])


def task_mask(task: str, meta, qi: np.ndarray, gi: np.ndarray) -> np.ndarray:
    b, s, loc, org = meta["bearing"], meta["setting"], meta["location"], meta["origin"]
    m = b[gi][None, :] != b[qi][:, None]
    if task == "T1":
        m &= s[gi][None, :] == s[qi][:, None]
    elif task == "T2":
        m &= s[gi][None, :] != s[qi][:, None]
    elif task == "T3":  # damaged queries vs a gallery of the other origin plus healthy
        other = np.where(org[qi] == "real", "artificial", "real")
        m &= (org[gi][None, :] == other[:, None]) | (org[gi][None, :] == "healthy")
        m &= (org[qi] != "healthy")[:, None]
    elif task == "N":  # the query's class is removed from the gallery
        m &= loc[gi][None, :] != loc[qi][:, None]
        m &= np.isin(loc[qi], ["healthy", "combined"])[:, None]
    elif task != "ALL":
        raise ValueError(task)
    return m


def rank_metrics(dist: np.ndarray, eligible: np.ndarray, relevant: np.ndarray):
    """Per query: P@10, AP, RR and nearest distance over eligible gallery windows."""
    rows = []
    for i in range(len(dist)):
        idx = np.where(eligible[i])[0]
        if len(idx) == 0:
            rows.append(None)
            continue
        order = idx[np.lexsort((idx, dist[i, idx]))]  # stable ties by gallery index
        rel = relevant[i, order]
        hits = np.cumsum(rel)
        n_rel = int(rel.sum())
        ap = float((hits[rel] / (np.where(rel)[0] + 1)).mean()) if n_rel else None
        rr = float(1 / (np.argmax(rel) + 1)) if n_rel else None
        rows.append({"p10": float(rel[:TOP_K].sum() / TOP_K), "ap": ap, "rr": rr, "relevant": n_rel,
                     "nearest": float(dist[i, order[0]]), "top": order[:TOP_K]})
    return rows


def per_bearing(values, bearings):
    groups = {}
    for v, b in zip(values, bearings):
        if v is not None:
            groups.setdefault(b, []).append(v)
    return {b: float(np.mean(v)) for b, v in sorted(groups.items())}


def bootstrap_diff(a: dict, b: dict, resamples=2000, seed=7):
    keys = sorted(set(a) & set(b))
    diffs = np.array([a[k] - b[k] for k in keys])
    rng = np.random.default_rng(seed)
    boots = [diffs[rng.integers(0, len(diffs), len(diffs))].mean() for _ in range(resamples)]
    return {"diff": float(diffs.mean()), "ci95": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            "groups": len(keys)}


def auroc(pos, neg):
    """P(nearest distance of a no-match query > that of a positive query)."""
    pos, neg = np.asarray(pos), np.asarray(neg)
    if not len(pos) or not len(neg):
        return None
    return float(((neg[:, None] > pos[None, :]).mean() + 0.5 * (neg[:, None] == pos[None, :]).mean()))


def evaluate(feats: dict, meta: dict, role: str, dev_thresholds: dict | None = None, keep_matches=True):
    """Every method x configuration x task for queries and gallery of one role."""
    gi = np.where(meta["role"] == role)[0]
    qi = gi[meta["query"][gi]]
    dev_g = np.where(meta["role"] == "dev")[0]
    loc, org = meta["location"], meta["origin"]
    rel_loc = loc[gi][None, :] == loc[qi][:, None]
    rel_origin = rel_loc & (org[gi][None, :] == org[qi][:, None])
    report = {"role": role, "query_windows": int(len(qi)), "gallery_windows": int(len(gi)),
              "query_bearings": sorted(set(meta["bearing"][qi])), "results": {}, "thresholds": {}}
    for config in CONFIGS:
        for variant in ("raw", "standardized"):
            for method in METHODS:
                x = feats[f"{config}/{method}"]
                if variant == "standardized":  # mean/SD from development gallery windows only
                    mu, sd = x[dev_g].mean(0), x[dev_g].std(0)
                    x = (x - mu) / np.where(sd > 0, sd, 1.0)
                started = time.perf_counter()
                dist = distances(x[qi], x[gi])
                search_s = (time.perf_counter() - started) / len(qi)
                key = f"{config}/{variant}/{method}"
                entry = {"search_per_query_ms": 1e3 * search_s, "tasks": {}}
                for task in ("T1", "T2", "T3", "N", "ALL"):
                    eligible = task_mask(task, meta, qi, gi)
                    for rel_name, rel in (("location", rel_loc), ("location+origin", rel_origin)):
                        if task == "N" and rel_name != "location":
                            continue
                        rows = rank_metrics(dist, eligible, rel)
                        used = [i for i, r in enumerate(rows) if r is not None]
                        bear = meta["bearing"][qi]
                        res = {"queries": len(used)}
                        for metric in ("p10", "ap", "rr", "nearest"):
                            vals = [rows[i][metric] if rows[i] is not None else None for i in range(len(rows))]
                            pb = per_bearing(vals, bear)
                            res[metric] = {"mean_over_bearings": float(np.mean(list(pb.values()))) if pb else None,
                                           "per_bearing": pb}
                        if task == "T2" and rel_name == "location":
                            res["by_setting"] = {s: per_bearing([rows[i]["p10"] if rows[i] and meta["setting"][qi][i] == s
                                                                 else None for i in range(len(rows))], bear)
                                                 for s in SETTINGS}
                            for field in ("origin", "extent"):
                                res[f"by_{field}"] = {str(v): float(np.mean([rows[i]["p10"] for i in used
                                                                             if meta[field][qi][i] == v]))
                                                      for v in sorted(set(meta[field][qi][used]))}
                            if keep_matches and config == FROZEN and variant == "raw":
                                res["matches"] = [{"query": [str(meta["bearing"][qi][i]), str(meta["setting"][qi][i]),
                                                             int(meta["measurement"][qi][i]), int(meta["slot"][qi][i])],
                                                   "p10": rows[i]["p10"],
                                                   "top": [[str(meta["bearing"][gi][j]), str(meta["setting"][gi][j]),
                                                            int(meta["measurement"][gi][j]), int(meta["slot"][gi][j]),
                                                            round(float(dist[i, j]), 6)] for j in rows[i]["top"]]}
                                                  for i in used]
                        if task == "N":
                            res["nearest_all"] = [rows[i]["nearest"] for i in used]
                        entry["tasks"][f"{task}/{rel_name}"] = res
                # No-match behaviour: positives = class-present queries over the full eligible gallery.
                pos = [r["nearest"] for r in rank_metrics(dist, task_mask("ALL", meta, qi, gi), rel_loc)
                       if r is not None and r["relevant"] > 0]
                neg = entry["tasks"]["N/location"]["nearest_all"]
                entry["no_match"] = {"auroc_nearest_distance": auroc(pos, neg), "positives": len(pos),
                                     "negatives": len(neg)}
                if role == "dev":
                    thr = float(np.percentile(pos, 90)) if pos else None
                    report["thresholds"][key] = thr
                elif dev_thresholds and dev_thresholds.get(key) is not None:
                    thr = dev_thresholds[key]
                    entry["no_match"].update({"dev_threshold": thr,
                                              "false_accept_rate": float(np.mean(np.array(neg) <= thr)) if neg else None,
                                              "positive_accept_rate": float(np.mean(np.array(pos) <= thr))})
                report["results"][key] = entry
    return report


def comparisons(report: dict):
    """Pre-registered H2 comparison and the reported ones (PLAN.md), bearing-level bootstrap."""
    out = {}
    for config in CONFIGS:
        for variant in ("raw", "standardized"):
            per = {m: report["results"][f"{config}/{variant}/{m}"]["tasks"]["T2/location"]["p10"]["per_bearing"]
                   for m in METHODS}
            for a, b in (("M", "FS"), ("M", "F"), ("M", "S"), ("FS", "F")):
                out[f"{config}/{variant}: {a} vs {b}"] = bootstrap_diff(per[a], per[b])
    primary = out[f"{FROZEN}/raw: M vs FS"]
    lo, hi = primary["ci95"]
    verdict = ("H2 supported" if primary["diff"] >= 0.05 and lo > 0 else
               "H2 not supported" if hi < 0.05 else "inconclusive")
    return {"primary": {"comparison": f"{FROZEN}/raw: M vs FS (T2, P@10, location)", **primary,
                        "verdict_provisional_margin_0.05": verdict}, "all": out}


# -- stages --------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("stage", choices=["verify", "prepare", "features", "evaluate"])
    parser.add_argument("--archives", type=Path)
    parser.add_argument("--extracted", type=Path, help="directory of extracted bearing folders")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--features-bin", type=Path, default=HERE.parents[1] / "target/release/examples/features")
    parser.add_argument("--role", choices=["dev", "heldout"])
    a = parser.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    if a.stage == "verify":
        verify(a.archives, a.out)
    elif a.stage == "prepare":
        from paderborn_mat import RATE_TOLERANCE_HZ, load_recordings
        started = time.perf_counter()
        checks = []
        native, decim, meta = build_windows(load_recordings(a.extracted, checks, DEV + HELDOUT))
        np.savez(a.out / "windows.npz", native=native, decim=decim, **meta)
        mats = sorted(p for b in DEV + HELDOUT for p in (a.extracted / b).glob("*.mat"))
        (a.out / "SHA256SUMS.mat").write_text("".join(f"{sha256(p)}  {p.parent.name}/{p.name}\n" for p in mats))
        excluded = [c for c in checks if not c["rate_ok"]]
        per = {}
        for c in checks:
            key = c["file"][:11] + "/" + c["file"].split("_")[3]
            per.setdefault(key, {"files": 0, "excluded": 0})
            per[key]["files"] += 1
            per[key]["excluded"] += not c["rate_ok"]
        summary = {"windows": int(len(native)), "bearings": sorted(set(meta["bearing"])),
                   "query_windows": int(meta["query"].sum()), "files": len(checks),
                   "excluded_files": len(excluded), "rate_tolerance_hz": RATE_TOLERANCE_HZ,
                   "excluded": [{k: c[k] for k in ("file", "samples", "vibration_rate_hz")} for c in excluded],
                   "per_setting_bearing": per,
                   "vibration_units": sorted({c["vibration_unit"] for c in checks}),
                   "measured_context": {s: {k: [float(np.min(v)), float(np.max(v))] for k, v in
                                            (("speed", [c["speed_mean"] for c in checks if c["file"].startswith(s)]),
                                             ("torque", [c["torque_mean"] for c in checks if c["file"].startswith(s)]),
                                             ("force", [c["force_mean"] for c in checks if c["file"].startswith(s)]))}
                                        for s in SETTINGS},
                   "prepare_s": time.perf_counter() - started, "provenance": provenance()}
        (a.out / "prepare.json").write_text(json.dumps(summary, indent=1))
        print(json.dumps(summary))
    elif a.stage == "features":
        w = np.load(a.out / "windows.npz")
        feats, cost = compute_features(w["native"], w["decim"], a.features_bin)
        np.savez(a.out / "features.npz", **feats)
        (a.out / "features.json").write_text(json.dumps({"cost": cost, "features_bin_sha256": sha256(a.features_bin),
                                                         "provenance": provenance()}, indent=1))
        print(json.dumps(cost, indent=1))
    else:
        w = np.load(a.out / "windows.npz")
        meta = {k: w[k] for k in w.files if k not in ("native", "decim")}
        feats = dict(np.load(a.out / "features.npz"))
        dev_thr = None
        if a.role == "heldout":
            dev_thr = json.loads((a.out / "evaluation-dev.json").read_text())["thresholds"]
        report = evaluate(feats, meta, a.role, dev_thr)
        report["comparisons"] = comparisons(report)
        report["provenance"] = provenance()
        (a.out / f"evaluation-{a.role}.json").write_text(json.dumps(report, indent=1, default=str))
        print(json.dumps(report["comparisons"]["primary"], indent=1))


if __name__ == "__main__":
    main()
