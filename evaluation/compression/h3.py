#!/usr/bin/env python3
"""H3: cascade and progressive access for envelope cross-correlation search (PROTOCOL.md).

Uses the timing test's earthquake windows (2023 dev, 2024 held-out) and its gallery rule.
Raw windows are stored losslessly (FLAC) and, for progressive access, as dwt-q at the CX
dev operating point; partial decodes read only the first k subbands.
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "decision")]
import blockcodecs as bc  # noqa: E402
import timing  # noqa: E402

N_GRID = (20, 50, 100)
K_GRID = (2, 3, 4, 5, 6, 7)
ST_BYTES = 51 * 4
ENV_BYTES = 410 * 4


def partial_bytes(blob, k):
    parts = bc.split_parts(blob[bc.DWT_HEAD:])
    return bc.DWT_HEAD + 1 + sum(4 + len(p) for p in parts[:k])


def prepare(run: Path, year: int, eps: float):
    w = np.load(run / f"windows-{year}.npz")
    meta = {k: w[k] for k in w.files if k != "x"}
    x = w["x"]
    if not np.array_equal(x, np.round(x)):
        raise ValueError("windows are not integer counts")
    xi = x.astype(np.int64)
    flac = [bc.flac(v) for v in xi]
    dwt = [bc.dwt_q(v.astype(float), eps) for v in xi]
    feats = dict(np.load(run / f"features-{year}.npz"))
    return meta, xi, flac, dwt, feats


def run_queries(meta, xi, flac, dwt, st, env_index, settings):
    ev = np.where(meta["kind"] == "event")[0]
    gi = np.arange(len(meta["kind"]))
    mask = timing.gallery_mask(meta, ev, gi, True)
    hypo = meta["hypo_km"].astype(float)
    st_d = timing.vector_distances(st[ev], st)
    results = {s: {"recall": [], "dkm": [], "bytes": [], "ms": [], "event": []} for s in settings}
    for qi_pos, q in enumerate(ev):
        elig = np.where(mask[qi_pos])[0]
        if len(elig) < timing.TOP_K:
            continue
        q_env = timing.envelope(xi[q].astype(float))[None]
        truth = None
        for s in settings:
            kind = s[0]
            t = time.perf_counter()
            if kind == "exhaustive":
                cand, nbytes = elig, sum(len(flac[j]) for j in elig)
                envs = np.stack([timing.envelope(bc.flac_dec(flac[j]).astype(float)) for j in cand])
            elif kind == "envelope-index":  # exploratory: envelopes stored as the retrieval representation
                cand, nbytes = elig, ENV_BYTES * len(elig)
                envs = env_index[cand]
            else:
                n = s[1]
                order = elig[np.lexsort((elig, st_d[qi_pos, elig]))][:n]
                cand = order
                nbytes = ST_BYTES * len(elig)
                if kind == "cascade":
                    nbytes += sum(len(flac[j]) for j in cand)
                    envs = np.stack([timing.envelope(bc.flac_dec(flac[j]).astype(float)) for j in cand])
                else:
                    k = s[2]
                    nbytes += sum(partial_bytes(dwt[j], k) for j in cand)
                    envs = np.stack([timing.envelope(bc.dwt_q_dec(dwt[j], keep_levels=k)) for j in cand])
            d = timing.xcorr_distances(q_env, envs)[0]
            top = cand[np.lexsort((cand, d))][:timing.TOP_K]
            ms = 1e3 * (time.perf_counter() - t)
            if kind == "exhaustive":
                truth = set(top.tolist())
            results[s]["recall"].append(len(truth & set(top.tolist())) / timing.TOP_K if truth is not None else None)
            results[s]["dkm"].append(float(np.median(np.abs(hypo[top] - hypo[q]))))
            results[s]["bytes"].append(int(nbytes))
            results[s]["ms"].append(ms)
            results[s]["event"].append(str(meta["event"][q]))
    return results


def summarize(results):
    out = {}
    for s, r in results.items():
        per_event = {}
        for e, dkm in zip(r["event"], r["dkm"]):
            per_event.setdefault(e, []).append(dkm)
        out[str(s)] = {"queries": len(r["dkm"]), "recall_at10_vs_exhaustive": float(np.mean(r["recall"])),
                       "median_abs_dkm": float(np.mean([np.mean(v) for v in per_event.values()])),
                       "bytes_per_query_mean": float(np.mean(r["bytes"])),
                       "latency_ms_p50": float(np.percentile(r["ms"], 50)),
                       "latency_ms_p90": float(np.percentile(r["ms"], 90))}
    return out


def choose(summary, kind):
    ex = summary[str(("exhaustive",))]
    ok = {k: v for k, v in summary.items() if k.startswith(f"('{kind}'")
          and v["recall_at10_vs_exhaustive"] >= 0.9 and v["median_abs_dkm"] <= ex["median_abs_dkm"] + 1.0}
    return min(ok, key=lambda k: ok[k]["bytes_per_query_mean"]) if ok else None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--timing-run", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    op = json.loads((a.out / "operating.json").read_text())
    eps = op["cx"]["operating"].get(f"dwt-q|{op['cx']['params'].get('dwt-q')}", {}).get("eps")
    if eps is None:
        raise SystemExit("dwt-q has no CX operating point; progressive variant not applicable")
    dev = dict(np.load(a.timing_run / "features-2023.npz"))
    mu, sd = dev["ST"].mean(0), dev["ST"].std(0)
    report = {"dwt_eps": eps, "years": {}}
    settings_all = ([("exhaustive",), ("envelope-index",)] + [("cascade", n) for n in N_GRID]
                    + [("progressive", n, k) for n in N_GRID for k in K_GRID])
    for year in (2023, 2024):
        meta, xi, flac, dwt, feats = prepare(a.timing_run, year, eps)
        st = (feats["ST"] - mu) / np.where(sd > 0, sd, 1.0)
        if year == 2023:
            settings = settings_all
        else:
            settings = [("exhaustive",), ("envelope-index",), tuple(eval(report["chosen"]["cascade"])) if report["chosen"]["cascade"] else None,
                        tuple(eval(report["chosen"]["progressive"])) if report["chosen"]["progressive"] else None]
            settings = [s for s in settings if s is not None]
        res = run_queries(meta, xi, flac, dwt, st, feats["XC"], settings)
        summ = summarize(res)
        report["years"][year] = {"role": "dev" if year == 2023 else "heldout", "summary": summ,
                                 "raw_flac_bytes_per_window": float(np.mean([len(b) for b in flac])),
                                 "dwt_bytes_per_window": float(np.mean([len(b) for b in dwt]))}
        if year == 2023:
            report["chosen"] = {"cascade": choose(summ, "cascade"), "progressive": choose(summ, "progressive")}
        print(year, json.dumps({k: {kk: round(vv, 3) for kk, vv in v.items()} for k, v in summ.items()}, indent=0)[:3000])
    held = report["years"][2024]["summary"]
    c, pr = report["chosen"]["cascade"], report["chosen"]["progressive"]
    if c and pr and c in held and pr in held:
        report["progressive_bytes_over_cascade"] = held[pr]["bytes_per_query_mean"] / held[c]["bytes_per_query_mean"]
        report["progressive_helps_beyond_cascade"] = report["progressive_bytes_over_cascade"] <= 0.5
    (a.out / "h3.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: report.get(k) for k in ("chosen", "progressive_bytes_over_cascade", "progressive_helps_beyond_cascade")}))


if __name__ == "__main__":
    main()
