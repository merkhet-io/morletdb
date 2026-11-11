"""IU1 addendum A1 analysis (drift checks). Reads results/drift.json, writes results/drift_summary.json."""
import json, collections, statistics as st
from pathlib import Path
import numpy as np

OUT = Path("/spike/results") if Path("/spike").exists() else Path(__file__).parent / "results"
SEED, B = 20261111, 10000
P = ["Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020", "Feb_2021", "Aug_2021"]
D = json.loads((OUT / "drift.json").read_text())
rows, LEN, META = D["rows"], D["grid_length"], D["meta"]


def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    allv = np.concatenate([pos, neg])
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    ranks = ((np.cumsum(cnt) - (cnt - 1) / 2.0))[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def hist_periods(kind, own, avail):
    i = P.index(own)
    if kind == "all": return avail
    if kind == "same": return [p for p in avail if p == own]
    if kind == "past": return [p for p in avail if P.index(p) < i]
    if kind == "notfuture": return [p for p in avail if P.index(p) <= i]
    if kind == "all_no_feb19": return [p for p in avail if p != "Feb_2019"]
    raise ValueError(kind)


# run scores per history kind and arm
runs = {}
for r in rows:
    e = runs.setdefault(r["run"], dict(m=r["m"], op=r["op"], label=r["label"], period=r["period"], w=[]))
    e["w"].append(r)
KINDS = ("all", "same", "past", "notfuture", "all_no_feb19")
ARMS = ("BL", "ZL", "RMS")
score = {k: {a: {} for a in ARMS} for k in KINDS}
nhist = {k: {} for k in KINDS}
for run, e in runs.items():
    for k in KINDS:
        for a in ARMS:
            vals = []
            for w in e["w"]:
                ps = hist_periods(k, e["period"], list(w[a].keys()))
                if ps: vals.append(min(w[a][p] for p in ps))
            if vals: score[k][a][run] = st.median(vals)
        w0 = e["w"][0]
        nhist[k][run] = sum(w0["n_good"].get(p, 0) for p in hist_periods(k, e["period"], list(w0["n_good"].keys())))
# duration baseline: |length - median length of good history runs|
good_len = collections.defaultdict(list)
for run, (m, op, label) in META.items():
    if label == "good": good_len[(m, op, "_".join(run.split("_")[1:3]))].append((run, LEN[run]))
for k in KINDS:
    score[k]["DUR"] = {}
    for run, e in runs.items():
        ps = hist_periods(k, e["period"], [p for p in P if good_len.get((e["m"], e["op"], p))])
        h = [l for p in ps for r2, l in good_len[(e["m"], e["op"], p)] if r2 != run]
        if h: score[k]["DUR"][run] = abs(LEN[run] - st.median(h))
ARMS4 = ARMS + ("DUR",)
lab = {r: e["label"] == "bad" for r, e in runs.items()}
grp = {r: (e["m"], e["period"]) for r, e in runs.items()}
rng = np.random.default_rng(SEED)


def ci(fn, pos, neg):
    pos, neg = list(pos), list(neg)
    if not pos or not neg: return None
    est = fn(pos, neg); bs = []
    for _ in range(B):
        bp = [pos[i] for i in rng.integers(0, len(pos), len(pos))]; bn = [neg[i] for i in rng.integers(0, len(neg), len(neg))]
        bs.append(fn(bp, bn))
    bs = np.array(bs)
    return [float(est), float(np.quantile(bs, .025)), float(np.quantile(bs, .975))]


def au(k, a, keep=lambda r: True, with_diff=None):
    s = score[k][a]
    common = [r for r in s if keep(r) and all(r in score[k][x] for x in ARMS)]
    pos = [r for r in common if lab[r]]; neg = [r for r in common if not lab[r]]
    out = dict(bad=len(pos), good=len(neg))
    if pos and neg:
        out["auroc"] = ci(lambda p, n: auroc([s[r] for r in p], [s[r] for r in n]), pos, neg)
        if with_diff:
            s2 = score[k][with_diff]
            out["minus_" + with_diff] = ci(lambda p, n: auroc([s[r] for r in p], [s[r] for r in n]) - auroc([s2[r] for r in p], [s2[r] for r in n]), pos, neg)
    return out


def pooled_within(k, a, with_diff=None):
    """Concordance over pairs (bad, good) from the same machine and half-year; bootstrap over runs inside groups."""
    s = score[k][a]; s2 = score[k][with_diff] if with_diff else None
    groups = collections.defaultdict(lambda: ([], []))
    for r in s:
        if all(r in score[k][x] for x in ARMS): groups[grp[r]][0 if lab[r] else 1].append(r)
    groups = {g: v for g, v in groups.items() if v[0] and v[1]}

    def conc(gs, sc):
        num = den = 0.0
        for pos, neg in gs:
            num += auroc([sc[r] for r in pos], [sc[r] for r in neg]) * len(pos) * len(neg); den += len(pos) * len(neg)
        return num / den
    gl = list(groups.values()); est = conc(gl, s); bs = []; bd = []
    for _ in range(B):
        res = [([p[i] for i in rng.integers(0, len(p), len(p))], [n[i] for i in rng.integers(0, len(n), len(n))]) for p, n in gl]
        v = conc(res, s); bs.append(v)
        if s2: bd.append(v - conc(res, s2))
    out = dict(groups=len(gl), bad=sum(len(p) for p, _ in gl), good=sum(len(n) for _, n in gl),
               auroc=[float(est), float(np.quantile(bs, .025)), float(np.quantile(bs, .975))])
    if s2: out["minus_" + with_diff] = [float(est - conc(gl, s2)), float(np.quantile(bd, .025)), float(np.quantile(bd, .975))]
    return out


S = {}
S["1_all_runs"] = {k: {a: au(k, a, with_diff=("ZL" if a == "BL" else None)) for a in ARMS4} for k in ("all", "same", "past", "notfuture")}
nofeb = lambda r: grp[r] != ("M02", "Feb_2019")
S["2_without_M02_Feb2019"] = {"history_all": {a: au("all", a, nofeb, "ZL" if a == "BL" else None) for a in ARMS4},
                              "history_without_Feb2019": {a: au("all_no_feb19", a, nofeb, "ZL" if a == "BL" else None) for a in ARMS4}}
S["3_within_group"] = {}
for g in sorted(set(grp.values())):
    k0 = lambda r, g=g: grp[r] == g
    e = {k: {a: au(k, a, k0) for a in ARMS4} for k in ("all", "same")}
    if e["all"]["BL"]["bad"] and e["all"]["BL"]["good"]: S["3_within_group"]["/".join(g)] = e
S["3_pooled_within"] = {k: {a: pooled_within(k, a, "ZL" if a == "BL" else ("RMS" if a == "ZL" else None)) for a in ARMS4} for k in ("all", "same")}
S["3_pooled_within"]["same"]["BL_minus_RMS"] = pooled_within("same", "BL", "RMS").get("minus_RMS")
S["3_pooled_within"]["same"]["BL_minus_DUR"] = pooled_within("same", "BL", "DUR").get("minus_DUR")
# 4 history size
hs = collections.defaultdict(list)
for r, e in runs.items(): hs[(e["m"], e["period"])].append(nhist["same"][r])
S["4_history_size_same_halfyear"] = {"/".join(g): dict(runs=len(v), median_good_history_runs=st.median(v), min=min(v), max=max(v)) for g, v in sorted(hs.items())}
good = [r for r in runs if not lab[r] and r in score["all"]["BL"]]
n_all = np.array([nhist["all"][r] for r in good]); sc_all = np.array([score["all"]["BL"][r] for r in good])
S["4_good_runs_score_vs_history_size"] = dict(spearman=float(np.corrcoef(n_all.argsort().argsort(), sc_all.argsort().argsort())[0, 1]),
    by_group={"/".join(g): dict(n=len(v), median_score=float(np.median(v))) for g, v in sorted(collections.defaultdict(list, {gg: [score["all"]["BL"][r] for r in good if grp[r] == gg] for gg in set(grp.values())}).items()) if v})
# 6 T1 inside the half-year
bad_runs = {r for r in runs if lab[r]}
badset = {r for r, (m, op, l) in META.items() if l == "bad"}
per = {}
for r in rows:
    if r["label"] != "bad" or "t1_BL" not in r: continue
    e = per.setdefault(r["run"], collections.defaultdict(list))
    for a in ARMS:
        h = r["t1_" + a]; e[a].append(sum(x in badset for x in h) / 10); e[a + "_short"].append(len(h) < 10)
    e["chance"].append(r["t1_chance"])
rl = sorted(per)
m = {a: [float(np.mean(per[x][a])) for x in rl] for a in ARMS + ("chance",)}
def bdiff(a, b):
    a, b = np.array(a), np.array(b); idx = rng.integers(0, len(a), (B, len(a))); d = (a[idx] - b[idx]).mean(1)
    return [float((a - b).mean()), float(np.quantile(d, .025)), float(np.quantile(d, .975))]
S["6_T1_same_halfyear"] = dict(bad_runs=len(rl), **{a: float(np.mean(v)) for a, v in m.items()}, BL_minus_ZL=bdiff(m["BL"], m["ZL"]),
                               BL_minus_RMS=bdiff(m["BL"], m["RMS"]), BL_minus_chance=bdiff(m["BL"], m["chance"]),
                               queries_short_of_10=int(sum(sum(per[x]["BL_short"]) for x in rl)))
pw = S["3_pooled_within"]["same"]
S["decision"] = dict(
    separates_bad_from_good="stands" if pw["BL"]["auroc"][1] > 0.5 else "does not stand (interval includes 0.5)",
    BL_better_than_ZL="stands" if pw["BL"]["minus_ZL"][1] > 0 else "does not stand")
(OUT / "drift_summary.json").write_text(json.dumps(S, indent=1))
f = lambda v: "n/a" if v is None else f"{v[0]:.3f} [{v[1]:.3f}, {v[2]:.3f}]"
print("1 all runs"); [print(f"  {k:10s}", {a: (x["bad"], x["good"], f(x.get("auroc"))) for a, x in v.items()}, "BL-ZL", f(v["BL"].get("minus_ZL"))) for k, v in S["1_all_runs"].items()]
print("2 without M02 Feb 2019"); [print(f"  {k}", {a: (x["bad"], x["good"], f(x.get("auroc"))) for a, x in v.items()}, "BL-ZL", f(v["BL"].get("minus_ZL"))) for k, v in S["2_without_M02_Feb2019"].items()]
print("3 within group"); [print(f"  {g}", {k: {a: (x["bad"], x["good"], f(x.get("auroc"))) for a, x in e.items()} for k, e in v.items()}) for g, v in S["3_within_group"].items()]
print("3 pooled within"); [print(f"  {k}", {a: f(x["auroc"]) for a, x in v.items() if isinstance(x, dict)}, "BL-ZL", f(v["BL"]["minus_ZL"]), "BL-RMS", f(v.get("BL_minus_RMS")), "BL-DUR", f(v.get("BL_minus_DUR"))) for k, v in S["3_pooled_within"].items()]
print("4", S["4_history_size_same_halfyear"]); print("  ", S["4_good_runs_score_vs_history_size"])
print("6", S["6_T1_same_halfyear"]); print("decision", S["decision"])
