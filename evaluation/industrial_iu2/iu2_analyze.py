"""IU2 analysis: decisions per PROTOCOL.md from results/scores.json. Writes results/summary.json."""
import csv, json, math, collections, statistics as st
from pathlib import Path
import numpy as np

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from analysis_io import directories, read_json, write_json, spearman

OUT, DEST = directories(__file__)
SEED, B = 20261111, 10000
ARMS = ["BL", "ZL", "RMSW", "VIB", "CUR", "TIU", "LEN"]
NAMES = dict(BL="bounded lag", ZL="zero lag", RMSW="RMS window", VIB="vibration level", CUR="spindle current", TIU="time in use", LEN="cycle length")
S = []


def med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else float("nan")


def load_scores(cycle_scores=False, features=None):
    if cycle_scores:
        # Explicit offline mode: replay archived per-cycle statistics, not a new feature extraction.
        scores = read_json(OUT / "cycle_scores.json")
        raw = {r["file"]: r for r in read_json(OUT / "scores.json")["rows"]}
        if set(raw) != {s["file"] for s in scores}:
            raise ValueError("cycle and window score universes differ")
        for s in scores:
            r = raw[s["file"]]
            for arm in ("BL", "ZL", "RMSW"):
                if s[arm] != med(r[arm]):
                    raise ValueError(f"{s['file']}: {arm} differs from raw window scores")
        return scores
    if features is None:
        raise ValueError("provide --features CSV for a new analysis, or --from-cycle-scores for archived replay")
    cyc = {c["file"]: c for c in read_json(OUT / "cycles.json")["cycles"]}
    rows = read_json(OUT / "scores.json")["rows"]
    with open(features, encoding="utf-8-sig") as stream:
        F = list(csv.reader(stream, delimiter=";"))
    H = {h: i for i, h in enumerate(F[1])}
    feat = {r[H["FileName"]]: r for r in F[2:]}
    num = lambda s: float(s.replace(",", "."))
    vib_cols = ["Accelerometer - Spindle +Y - std", "Accelerometer - Spindle -Z - std", "Accelerometer - Spindle -X - std"]
    scores = []
    for r in rows:
        b = cyc[r["file"]]["baseline_file"]; f, fb = feat[r["file"]], feat[b]
        vib = abs(math.log(st.mean(num(f[H[c]]) / num(fb[H[c]]) for c in vib_cols)))
        cur = abs(math.log(num(f[H["Current - Spindle L1 - mean"]]) / num(fb[H["Current - Spindle L1 - mean"]])))
        scores.append(dict(file=r["file"], tool=r["tool"], split=r["split"], tiu=r["tiu"], ctfn=r["ctfn"], life=r["life"],
                      pos=r["ctfn"] <= 0.1, BL=med(r["BL"]), ZL=med(r["ZL"]), RMSW=med(r["RMSW"]), VIB=vib, CUR=cur,
                      TIU=float(r["tiu"]), LEN=abs(math.log(r["n"] / r["n_base"])),
                      gain=float(np.mean([(z - b_) > 0.01 for b_, z in zip(r["BL"], r["ZL"]) if b_ is not None and z is not None]))))
    return scores


def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float); allv = np.concatenate([pos, neg])
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def w1(split):
    D = [s for s in S if s["split"] == split]; tools = sorted({s["tool"] for s in D})
    by = {t: [s for s in D if s["tool"] == t] for t in tools}
    def A(sel, arm):
        p = [s[arm] for t in sel for s in by[t] if s["pos"]]; n = [s[arm] for t in sel for s in by[t] if not s["pos"]]
        return auroc(p, n) if p and n else float("nan")
    rng = np.random.default_rng(SEED); est = {a: A(tools, a) for a in ARMS}; bs = {a: [] for a in ARMS}
    for _ in range(B):
        sel = [tools[i] for i in rng.integers(0, len(tools), len(tools))]
        for a in ARMS: bs[a].append(A(sel, a))
    out = dict(tools=tools, positives=sum(s["pos"] for s in D), negatives=sum(not s["pos"] for s in D))
    for a in ARMS:
        v = np.array(bs[a]); out[a] = [est[a], float(np.nanquantile(v, .025)), float(np.nanquantile(v, .975))]
        if a != "BL":
            d = np.array(bs["BL"]) - v; lo, hi = float(np.nanquantile(d, .025)), float(np.nanquantile(d, .975))
            out["BL_minus_" + a] = [est["BL"] - est[a], lo, hi, "better" if lo > 0 else "worse" if hi < 0 else "no difference"]
    out["BL_detects_end_of_life"] = bool(out["BL"][1] > 0.5)
    out["per_tool_auroc"] = {str(t): {a: A([t], a) for a in ARMS} for t in tools}
    return out


def w2(split):
    D = [s for s in S if s["split"] == split]; out = {}
    for a in [x for x in ARMS if x != "TIU"]:
        r = {t: spearman([s["TIU"] for s in D if s["tool"] == t], [s[a] for s in D if s["tool"] == t]) for t in sorted({s["tool"] for s in D})}
        v = [x for x in r.values() if not math.isnan(x)]
        out[a] = dict(median=st.median(v), min=min(v), max=max(v), per_tool={str(k): x for k, x in r.items()})
    return out


def w3():
    dev = [s for s in S if s["split"] == "dev"]; conf = [s for s in S if s["split"] == "conf"]; out = {}
    for a in [x for x in ARMS if x != "TIU"]:
        thr = float(np.quantile([s[a] for s in dev if s["ctfn"] > 0.5], 0.95)); flagged = []; remaining = []; failure_cycle = []
        for t in sorted({s["tool"] for s in conf}):
            v = sorted((s for s in conf if s["tool"] == t), key=lambda s: s["tiu"]); hit = next((s for s in v if s[a] > thr), None)
            before_failure = hit is not None and hit["ctfn"] > 0
            flagged.append(before_failure)
            failure_cycle.append(hit is not None and hit["ctfn"] == 0)
            if before_failure: remaining.append(100 * hit["ctfn"])
        fa = [s[a] > thr for s in conf if s["ctfn"] > 0.5]
        out[a] = dict(threshold=thr, tools_flagged=int(sum(flagged)), tools=len(flagged),
                      tools_first_flagged_in_failure_cycle=int(sum(failure_cycle)),
                      tools_never_flagged=len(flagged) - sum(flagged) - sum(failure_cycle),
                      median_remaining_life_percent_at_first_flag=st.median(remaining) if remaining else None,
                      first_half_cycles_flagged=float(np.mean(fa)))
    return out


def w4(split):
    D = [s for s in S if s["split"] == split]
    first = [s for s in D if s["ctfn"] >= 0.9]; last = [s for s in D if s["ctfn"] <= 0.1]
    return dict(first_tenth={a: st.median(s[a] for s in first) for a in ("BL", "ZL")} | {"n": len(first)},
                last_tenth={a: st.median(s[a] for s in last) for a in ("BL", "ZL")} | {"n": len(last)},
                share_windows_BL_lower_by_0_01=float(np.mean([s["gain"] for s in D])))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--from-cycle-scores", action="store_true", help="replay archived per-cycle scores, checking BL/ZL/RMS against raw results")
    mode.add_argument("--features", type=Path, help="source FeatureAndMetadata_Milling.csv for a fresh analysis")
    args = parser.parse_args()
    S = load_scores(args.from_cycle_scores, args.features)
    R = dict(dev=dict(W1=w1("dev"), W2=w2("dev"), W4=w4("dev")), conf=dict(W1=w1("conf"), W2=w2("conf"), W4=w4("conf")), W3=w3(),
             load={k: v for k, v in read_json(OUT / "load.json").items() if k != "lengths"})
    write_json(DEST / "summary.json", R)
    if not args.from_cycle_scores:
        write_json(DEST / "cycle_scores.json", S)
    f = lambda v: f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
    for sp in ("dev", "conf"):
        w = R[sp]["W1"]; print(f"== {sp}: W1 end of life, tools {w['tools']}, {w['positives']} positive / {w['negatives']} negative cycles")
        for a in ARMS: print(f"   {NAMES[a]:16s} {f(w[a])}", "" if a == "BL" else f"   BL minus: {f(w['BL_minus_' + a][:3])} {w['BL_minus_' + a][3]}")
        print("   BL detects end of life:", w["BL_detects_end_of_life"])
        print("   per tool:", {t: {a: round(v, 2) for a, v in x.items()} for t, x in w["per_tool_auroc"].items()})
        print("   W2 trend (Spearman with time in use), median [min, max]:", {NAMES[a]: (round(x["median"], 2), round(x["min"], 2), round(x["max"], 2)) for a, x in R[sp]["W2"].items()})
        print("   W4:", R[sp]["W4"])
    print("== W3 early warning (threshold from development tools):"); [print("  ", NAMES[a], x) for a, x in R["W3"].items()]
    print("load", R["load"])
