"""IU2 analysis: decisions per PROTOCOL.md from results/scores.json. Writes results/summary.json."""
import csv, json, math, collections, statistics as st
from pathlib import Path
import numpy as np

OUT = Path("/w/iu2/results"); FEAT = Path("/w/data/toollife/FeatureAndMetadata_Milling.csv")
SEED, B = 20261111, 10000
ARMS = ["BL", "ZL", "RMSW", "VIB", "CUR", "TIU", "LEN"]
NAMES = dict(BL="bounded lag", ZL="zero lag", RMSW="RMS window", VIB="vibration level", CUR="spindle current", TIU="time in use", LEN="cycle length")

cyc = {c["file"]: c for c in json.loads((OUT / "cycles.json").read_text())["cycles"]}
rows = json.loads((OUT / "scores.json").read_text())["rows"]
F = list(csv.reader(open(FEAT, encoding="utf-8-sig"), delimiter=";")); H = {h: i for i, h in enumerate(F[1])}
feat = {r[H["FileName"]]: r for r in F[2:]}
num = lambda s: float(s.replace(",", "."))
vib_cols = ["Accelerometer - Spindle +Y - std", "Accelerometer - Spindle -Z - std", "Accelerometer - Spindle -X - std"]


def med(v):
    v = [x for x in v if x is not None]
    return st.median(v) if v else float("nan")


S = []
for r in rows:
    b = cyc[r["file"]]["baseline_file"]; f, fb = feat[r["file"]], feat[b]
    vib = abs(math.log(st.mean(num(f[H[c]]) / num(fb[H[c]]) for c in vib_cols)))
    cur = abs(math.log(num(f[H["Current - Spindle L1 - mean"]]) / num(fb[H["Current - Spindle L1 - mean"]])))
    S.append(dict(file=r["file"], tool=r["tool"], split=r["split"], tiu=r["tiu"], ctfn=r["ctfn"], life=r["life"],
                  pos=r["ctfn"] <= 0.1, BL=med(r["BL"]), ZL=med(r["ZL"]), RMSW=med(r["RMSW"]), VIB=vib, CUR=cur,
                  TIU=float(r["tiu"]), LEN=abs(math.log(r["n"] / r["n_base"])),
                  gain=float(np.mean([(z - b_) > 0.01 for b_, z in zip(r["BL"], r["ZL"]) if b_ is not None and z is not None]))))


def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float); allv = np.concatenate([pos, neg])
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def spearman(a, b):
    ra = np.argsort(np.argsort(a)); rb = np.argsort(np.argsort(b))
    return float(np.corrcoef(ra, rb)[0, 1]) if len(a) > 2 and np.std(ra) > 0 and np.std(rb) > 0 else float("nan")


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
        thr = float(np.quantile([s[a] for s in dev if s["ctfn"] > 0.5], 0.95)); flagged = []; remaining = []
        for t in sorted({s["tool"] for s in conf}):
            v = sorted((s for s in conf if s["tool"] == t), key=lambda s: s["tiu"]); hit = next((s for s in v if s[a] > thr), None)
            flagged.append(hit is not None)
            if hit: remaining.append(100 * hit["ctfn"])
        fa = [s[a] > thr for s in conf if s["ctfn"] > 0.5]
        out[a] = dict(threshold=thr, tools_flagged=int(sum(flagged)), tools=len(flagged),
                      median_remaining_life_percent_at_first_flag=st.median(remaining) if remaining else None,
                      first_half_cycles_flagged=float(np.mean(fa)))
    return out


def w4(split):
    D = [s for s in S if s["split"] == split]
    first = [s for s in D if s["ctfn"] >= 0.9]; last = [s for s in D if s["ctfn"] <= 0.1]
    return dict(first_tenth={a: st.median(s[a] for s in first) for a in ("BL", "ZL")} | {"n": len(first)},
                last_tenth={a: st.median(s[a] for s in last) for a in ("BL", "ZL")} | {"n": len(last)},
                share_windows_BL_lower_by_0_01=float(np.mean([s["gain"] for s in D])))


R = dict(dev=dict(W1=w1("dev"), W2=w2("dev"), W4=w4("dev")), conf=dict(W1=w1("conf"), W2=w2("conf"), W4=w4("conf")), W3=w3(),
         load={k: v for k, v in json.loads((OUT / "load.json").read_text()).items() if k != "lengths"})
(OUT / "summary.json").write_text(json.dumps(R, indent=1))
(OUT / "cycle_scores.json").write_text(json.dumps(S))
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
