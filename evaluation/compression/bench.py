#!/usr/bin/env python3
"""Stage-1 compression benchmark (PROTOCOL.md).

  rd         rate-distortion + fidelity rows per block for one dataset and role
  operating  dev-only operating points and parameter choices -> operating.json
"""
from __future__ import annotations

import argparse
import io
import json
import multiprocessing as mp
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE)]
import blockcodecs as bc  # noqa: E402
import datasets  # noqa: E402
import fidelity  # noqa: E402

R_GRID = (1 / 256, 1 / 128, 1 / 64, 1 / 32, 1 / 16, 1 / 8, 1 / 4, 1 / 2)
HEADER_BYTES = 64
LOSSLESS = {"int-zstd": (bc.int_zstd, bc.int_zstd_dec), "delta-zstd": (bc.delta_zstd, bc.delta_zstd_dec),
            "flac": (bc.flac, bc.flac_dec), "lift53-zstd": (bc.lift53_zstd, bc.lift53_zstd_dec)}
PARQUET = [(enc, lvl) for enc in ("PLAIN", "DELTA_BINARY_PACKED", "BYTE_STREAM_SPLIT") for lvl in (3, 9, 19)]
NOMINAL_RPM = {"N15": 1500.0, "N09": 900.0}


def provenance():
    def run(*cmd):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=HERE)
        return r.stdout.strip() if r.returncode == 0 else None
    import importlib.metadata as md
    return {"commit": run("git", "rev-parse", "HEAD"),
            "dirty": bool(run("git", "status", "--porcelain", "--untracked-files=no")),
            "python": platform.python_version(), "platform": platform.platform(), "cpu_count": os.cpu_count(),
            "versions": {p: md.version(p) for p in ("numpy", "scipy", "pyarrow", "zstandard", "h5py", "hdf5plugin",
                                                     "zfpy", "soundfile", "PyWavelets")}}


def loader(dataset, role):
    return {"paderborn": lambda: datasets.paderborn(role), "ims": lambda: datasets.ims(role),
            "cx": lambda: datasets.cx(role)}[dataset]()


def period_of(block):
    if block["dataset"] == "paderborn":
        return 64000.0 * 60 / NOMINAL_RPM[block["setting"][:3]]
    if block["dataset"] == "ims":
        return 20000.0 * 60 / 2000.0
    return None


# -- CX STA/LTA (the seismic example's detector, re-implemented without ObsPy) ----------
def triggers(x, rate=100.0, on=4.0, off=1.5, sta=1.0, lta=30.0, warmup=120.0):
    from scipy.signal import butter, lfilter, sosfilt
    sos = butter(4, [2.0, 10.0], btype="bandpass", fs=rate, output="sos")
    a = sosfilt(sos, np.asarray(x, float) - np.mean(x)) ** 2
    ns, nl = int(sta * rate), int(lta * rate)
    s = lfilter([1 / ns], [1, -(1 - 1 / ns)], a)
    l_ = lfilter([1 / nl], [1, -(1 - 1 / nl)], a)
    cf = np.where(l_ > 0, s / np.maximum(l_, 1e-300), 0.0)
    cf[:nl] = 0
    above = cf > on
    rises = np.flatnonzero(np.diff(np.r_[False, above].astype(np.int8)) == 1)
    lows = np.flatnonzero(cf < off)
    onsets, active_until = [], -1
    for i in rises:  # a trigger stays active until the characteristic function drops below `off`
        if i > active_until:
            if i >= warmup * rate:
                onsets.append(i)
            j = np.searchsorted(lows, i)
            active_until = lows[j] if j < len(lows) else len(cf)
    return np.array(onsets)


def match(a, b, tol):
    if len(a) == 0 or len(b) == 0:
        return 0
    return int(sum(np.min(np.abs(b - i)) <= tol for i in a))


# -- per block ---------------------------------------------------------------------------
def lossy_variants(dataset, dev):
    out = [("quant-zstd", None), ("quant-flac", None), ("sz3", None), ("zfp", None), ("sperr", None)]
    out += [("dwt-q", c) for c in ((1.0, 2.0, 4.0) if dev else (None,))]
    out += [("harmonic", k) for k in ((4, 16) if dev else (None,))]
    if dataset != "cx":
        out.append(("tsa", None))
    return out


def encode_lossy(name, param, x, eps, block):
    if name == "quant-zstd":
        b = bc.quant_zstd(x, eps); return len(b), lambda: bc.quant_zstd_dec(b)
    if name == "quant-flac":
        b = bc.quant_flac(x, eps); return len(b), lambda: bc.quant_flac_dec(b)
    if name == "sz3":
        blob, size = bc.sz3(x, eps); return size, lambda: bc.h5_dec(blob)
    if name == "zfp":
        b, size = bc.zfp(x, eps); return size, lambda: bc.zfp_dec(b)
    if name == "sperr":
        blob, size = bc.sperr(x, eps); return size, lambda: bc.h5_dec(blob)
    if name == "dwt-q":
        b = bc.dwt_q(x, eps, coef_step_factor=param); return len(b), lambda: bc.dwt_q_dec(b)
    if name == "harmonic":
        b = bc.harmonic_residual(x, eps, k=param); return len(b), lambda: bc.harmonic_residual_dec(b)
    if name == "tsa":
        b = bc.tsa_residual(x, eps, period_of(block)); return len(b), lambda: bc.tsa_residual_dec(b)
    raise ValueError(name)


def block_rows(args):
    block, sigma, dev, chosen = args
    x = block["x"]
    meta = {k: v for k, v in block.items() if k != "x"}
    rows = []
    raw_bytes = len(x) * np.dtype(bc.smallest_int(x)).itemsize
    base = {**meta, "n": len(x), "raw_bytes": raw_bytes}
    for name, (enc, dec) in LOSSLESS.items():
        try:
            t = time.perf_counter(); b = enc(x); te = time.perf_counter() - t
        except ValueError as exc:
            rows.append({**base, "codec": name, "param": None, "eps": 0.0, "r": 0.0, "unsupported": str(exc)})
            continue
        t = time.perf_counter(); y = dec(b); td = time.perf_counter() - t
        if not np.array_equal(y, x):
            raise AssertionError(f"lossless mismatch {name} {block['id']}")
        rows.append({**base, "codec": name, "param": None, "eps": 0.0, "r": 0.0, "bytes": len(b), "enc_s": te, "dec_s": td})
    orig_triggers = triggers(x) if block["dataset"] == "cx" else None
    variants = lossy_variants(block["dataset"], dev)
    for name, param in variants:
        if chosen is not None and param is None and name in ("dwt-q", "harmonic"):
            if name not in chosen:
                rows.append({**base, "codec": name, "param": None, "eps": None, "r": None,
                             "unsupported": "no development operating point"})
                continue
            param = chosen[name]
        for r in R_GRID:
            eps = max(0.5, r * sigma)
            try:
                t = time.perf_counter(); size, decode = encode_lossy(name, param, x, eps, block); te = time.perf_counter() - t
                t = time.perf_counter(); y = decode(); td = time.perf_counter() - t
            except ValueError as exc:
                rows.append({**base, "codec": name, "param": param, "eps": eps, "r": r, "unsupported": str(exc)})
                continue
            err = float(np.abs(np.asarray(y, float) - x).max())
            row = {**base, "codec": name, "param": param, "eps": eps, "r": r, "bytes": size, "enc_s": te, "dec_s": td,
                   "bound_ok": err <= eps * (1 + 1e-9) + 1e-9}
            if block["dataset"] == "cx":
                row.update({"max_abs_err": err, "nrmse": float(np.sqrt(np.mean((y - x) ** 2)) / x.std()) if x.std() else 0.0})
                dt = triggers(y)
                row.update({"trig_orig": len(orig_triggers), "trig_dec": len(dt),
                            "trig_matched_orig": match(orig_triggers, dt, 100), "trig_matched_dec": match(dt, orig_triggers, 100)})
            else:
                row.update(fidelity.compare(x, y, block["rate"], block["dataset"]))
            rows.append(row)
    return rows, (block["unit"], block["id"], x.astype(bc.smallest_int(x)) if block["dataset"] != "cx" else x.astype(np.int32))


def parquet_sizes(blocks_by_unit, dataset, step):
    """One file per unit, one row group per block (most favourable to Parquet)."""
    import pyarrow as pa
    import pyarrow.parquet as pq
    out = {}
    for unit, blocks in blocks_by_unit.items():
        typ = pa.int32() if dataset == "cx" else pa.int16()
        out[unit] = {}
        for enc, lvl in PARQUET:
            buf = io.BytesIO()
            schema = pa.schema([("x", typ)])
            kw = {"compression": "zstd", "compression_level": lvl, "use_dictionary": False}
            if enc == "BYTE_STREAM_SPLIT":
                kw["use_byte_stream_split"] = True
            elif enc != "PLAIN":
                kw["column_encoding"] = {"x": enc}
            try:
                with pq.ParquetWriter(buf, schema, **kw) as w:
                    for _, arr in blocks:
                        w.write_table(pa.table({"x": pa.array(arr, type=typ)}))
                out[unit][f"{enc}/zstd{lvl}"] = len(buf.getvalue())
            except Exception as exc:  # noqa: BLE001 - recorded as unsupported
                out[unit][f"{enc}/zstd{lvl}"] = f"unsupported: {exc}"
        buf = io.BytesIO()
        with pq.ParquetWriter(buf, pa.schema([("x", pa.float64())]), compression="zstd", compression_level=3,
                              use_dictionary=False, use_byte_stream_split=True) as w:
            for _, arr in blocks:
                w.write_table(pa.table({"x": pa.array(arr.astype(np.float64) * step)}))
        out[unit]["float64-naive/BSS-zstd3"] = len(buf.getvalue())
        out[unit]["blocks"] = len(blocks)
        out[unit]["samples"] = int(sum(len(a) for _, a in blocks))
    return out


def rd(dataset, role, out: Path, workers: int):
    out.mkdir(parents=True, exist_ok=True)
    dev = role == "dev"
    sigma_path = out / f"sigma-{dataset}.json"
    blocks = list(loader(dataset, role))
    if dev:
        sigma = float(np.median([b["x"].std() for b in blocks]))
        sigma_path.write_text(json.dumps({"sigma_counts": sigma, "blocks": len(blocks)}))
        chosen = None
    else:
        sigma = json.loads(sigma_path.read_text())["sigma_counts"]
        chosen = json.loads((out / "operating.json").read_text())[dataset]["params"]
    started = time.perf_counter()
    with mp.Pool(workers) as pool:
        results = pool.map(block_rows, [(b, sigma, dev, chosen) for b in blocks], chunksize=1)
    rows = [r for rs, _ in results for r in rs]
    by_unit = {}
    for _, (unit, bid, arr) in results:
        by_unit.setdefault(unit, []).append((bid, arr))
    step = blocks[0]["step"]
    pq = parquet_sizes(by_unit, dataset, step)
    with open(out / f"rd-{dataset}-{role}.jsonl", "w") as f:
        for r in rows:
            f.write(json.dumps(r, default=lambda o: o.item() if hasattr(o, "item") else str(o)) + "\n")
    (out / f"parquet-{dataset}-{role}.json").write_text(json.dumps(pq, indent=1))
    meta = {"dataset": dataset, "role": role, "blocks": len(blocks), "units": sorted(by_unit), "sigma_counts": sigma,
            "rows": len(rows), "bound_violations": sum(1 for r in rows if r.get("bound_ok") is False),
            "wall_s": time.perf_counter() - started, "workers": workers, "provenance": provenance()}
    (out / f"rd-{dataset}-{role}.json").write_text(json.dumps(meta, indent=1))
    print(json.dumps({k: v for k, v in meta.items() if k != "provenance"}))


# -- operating points (dev only) -----------------------------------------------------------
def load_rows(out, dataset, role):
    with open(out / f"rd-{dataset}-{role}.jsonl") as f:
        return [json.loads(line) for line in f]


def qualifies(rows, dataset):
    rows = [r for r in rows if "unsupported" not in r]
    if not rows or not all(r["bound_ok"] for r in rows):
        return False
    if dataset == "cx":
        mo, md_ = sum(r["trig_matched_orig"] for r in rows), sum(r["trig_matched_dec"] for r in rows)
        to, td = sum(r["trig_orig"] for r in rows), sum(r["trig_dec"] for r in rows)
        recall = mo / to if to else 1.0
        precision = md_ / td if td else 1.0
        return recall >= 0.98 and precision >= 0.98
    return (np.percentile(np.abs([r["d_kurt_rel"] for r in rows]), 95) <= 0.05
            and np.percentile(np.abs([r["d_env_rel"] for r in rows]), 95) <= 0.05)


def operating(out: Path):
    path = out / "operating.json"
    result = json.loads(path.read_text()) if path.exists() else {}
    for dataset in ("paderborn", "ims", "cx"):
        if not (out / f"rd-{dataset}-dev.jsonl").exists():
            continue
        rows = load_rows(out, dataset, "dev")
        pq = json.loads((out / f"parquet-{dataset}-dev.json").read_text())
        configs = [k for k in next(iter(pq.values())) if "/" in k and not k.startswith("float64")]
        totals = {c: sum(u[c] for u in pq.values()) for c in configs if all(isinstance(u[c], int) for u in pq.values())}
        best_parquet = min(totals, key=totals.get)
        ops, params = {}, {}
        variants = sorted({(r["codec"], r["param"]) for r in rows if r["eps"] > 0}, key=str)
        for name, param in variants:
            per_r = {}
            for r in rows:
                if r["codec"] == name and r["param"] == param and r["eps"] > 0:
                    per_r.setdefault(r["r"], []).append(r)
            ok = [rr for rr in sorted(per_r) if qualifies(per_r[rr], dataset)]
            best = max(ok) if ok else None
            ops[f"{name}|{param}"] = {"r": best, "eps": max(0.5, best * json.loads((out / f"sigma-{dataset}.json").read_text())["sigma_counts"]) if best else None,
                                      "dev_bytes": sum(r["bytes"] for r in per_r[best]) if best else None}
        for name in {n for n, _ in variants}:
            cands = {k: v for k, v in ops.items() if k.startswith(name + "|") and v["r"] is not None}
            if cands:
                pick = min(cands, key=lambda k: cands[k]["dev_bytes"])
                p = pick.split("|", 1)[1]
                params[name] = None if p == "None" else (float(p) if name == "dwt-q" else int(float(p)))
        result[dataset] = {"parquet": best_parquet, "parquet_dev_totals": totals, "operating": ops, "params": params}
    path.write_text(json.dumps(result, indent=1))
    print(json.dumps({d: {"parquet": v["parquet"], "params": v["params"],
                          "operating_r": {k: o["r"] for k, o in v["operating"].items()}} for d, v in result.items()}, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("stage", choices=["rd", "operating"])
    p.add_argument("--dataset", choices=["paderborn", "ims", "cx"])
    p.add_argument("--role", choices=["dev", "heldout"])
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    a = p.parse_args()
    if a.stage == "rd":
        rd(a.dataset, a.role, a.out, a.workers)
    else:
        operating(a.out)


if __name__ == "__main__":
    main()
