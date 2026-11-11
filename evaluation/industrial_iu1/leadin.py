"""IU1 addendum A2: T2 run score without each run's first (and last) query window. Reads results/drift.json."""
import json, collections, statistics as st
from pathlib import Path
import numpy as np
OUT = Path("/spike/results"); SEED, B = 20261111, 10000
P = ["Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020", "Feb_2021", "Aug_2021"]
rows = json.loads((OUT / "drift.json").read_text())["rows"]
rng = np.random.default_rng(SEED)

def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float); allv = np.concatenate([pos, neg])
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    ranks = (np.cumsum(cnt) - (cnt - 1) / 2.0)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))

by = collections.defaultdict(list)
for r in rows: by[(r["run"], r["ax"])].append(r)
last = {k: max(x["s"] for x in v) for k, v in by.items()}
res = {}
for variant, keep in (("all_windows", lambda r: True), ("without_first", lambda r: r["s"] != 0),
                      ("without_first_and_last", lambda r: r["s"] != 0 and r["s"] != last[(r["run"], r["ax"])])):
    sc = {h: {a: collections.defaultdict(list) for a in ("BL", "ZL", "RMS")} for h in ("all", "same")}
    info = {}
    for r in rows:
        info[r["run"]] = (r["label"] == "bad", (r["m"], r["period"]))
        if not keep(r): continue
        for a in ("BL", "ZL", "RMS"):
            if r[a]: sc["all"][a][r["run"]].append(min(r[a].values()))
            if r["period"] in r[a]: sc["same"][a][r["run"]].append(r[a][r["period"]])
    out = {}
    for h in ("all", "same"):
        s = {a: {k: st.median(v) for k, v in sc[h][a].items()} for a in sc[h]}
        common = [k for k in s["BL"] if k in s["ZL"] and k in s["RMS"]]
        pos = [k for k in common if info[k][0]]; neg = [k for k in common if not info[k][0]]
        e = dict(bad=len(pos), good=len(neg))
        for a in s:
            est = auroc([s[a][k] for k in pos], [s[a][k] for k in neg]); bs = []
            for _ in range(B):
                bp = [pos[i] for i in rng.integers(0, len(pos), len(pos))]; bn = [neg[i] for i in rng.integers(0, len(neg), len(neg))]
                bs.append(auroc([s[a][k] for k in bp], [s[a][k] for k in bn]))
            e[a] = [est, float(np.quantile(bs, .025)), float(np.quantile(bs, .975))]
        out[h] = e
        if h == "same":
            groups = collections.defaultdict(lambda: ([], []))
            for k in common: groups[info[k][1]][0 if info[k][0] else 1].append(k)
            gl = [v for v in groups.values() if v[0] and v[1]]
            def conc(gs, sa):
                num = den = 0.0
                for p_, n_ in gs:
                    num += auroc([sa[k] for k in p_], [sa[k] for k in n_]) * len(p_) * len(n_); den += len(p_) * len(n_)
                return num / den
            pw = {}
            for a in s:
                est = conc(gl, s[a]); bs = []
                for _ in range(2000):
                    rs_ = [([p_[i] for i in rng.integers(0, len(p_), len(p_))], [n_[i] for i in rng.integers(0, len(n_), len(n_))]) for p_, n_ in gl]
                    bs.append(conc(rs_, s[a]))
                pw[a] = [est, float(np.quantile(bs, .025)), float(np.quantile(bs, .975))]
            out["pooled_within_same"] = pw
    res[variant] = out
(OUT / "leadin_summary.json").write_text(json.dumps(res, indent=1))
f = lambda v: f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
for v, o in res.items():
    print(v)
    for h in ("all", "same"): print("  ", h, o[h]["bad"], o[h]["good"], {a: f(o[h][a]) for a in ("BL", "ZL", "RMS")})
    print("   pooled within", {a: f(x) for a, x in o["pooled_within_same"].items()})
