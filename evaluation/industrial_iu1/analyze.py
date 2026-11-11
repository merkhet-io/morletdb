"""IU1 analysis: decisions per PROTOCOL.md from results/*.json. Prints a report and writes results/summary.json."""
import json, sys
from pathlib import Path
import numpy as np

OUT = Path("/spike/results") if Path("/spike").exists() else Path(__file__).parent / "results"
SEED, B = 20261111, 10000


def boot_mean_diff(a, b, seed=SEED):
    a, b = np.asarray(a, float), np.asarray(b, float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(a), (B, len(a)))
    d = (a[idx] - b[idx]).mean(1)
    return float((a - b).mean()), float(np.quantile(d, 0.025)), float(np.quantile(d, 0.975))


def decide(lo, hi):
    return "better" if lo > 0 else "worse" if hi < 0 else "no difference"


def auroc(pos, neg):
    pos, neg = np.asarray(pos, float), np.asarray(neg, float)
    allv = np.concatenate([pos, neg])
    ranks = allv.argsort().argsort() + 1.0
    # average ranks for ties
    _, inv, cnt = np.unique(allv, return_inverse=True, return_counts=True)
    sums = np.bincount(inv, ranks)
    ranks = (sums / cnt)[inv]
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def t12(name):
    p = OUT / f"t12_{name}.json"
    if not p.exists():
        return None
    D = json.loads(p.read_text())
    rows = D["rows"]
    labels = {}
    for r in rows:
        labels[r["run"]] = r["label"]
    bad = {r for r, l in labels.items() if l == "bad"}
    # T1: per bad run, mean P@10 over its queries and axes
    per = {}
    for r in rows:
        if r["label"] != "bad":
            continue
        e = per.setdefault(r["run"], {"BL": [], "ZL": [], "RMS": [], "chance": [], "short": 0, "overlap": [], "gain": []})
        for arm in ("BL", "ZL"):
            h = r[f"t1_{arm}"]
            e["short"] += len(h) < 10
            e[arm].append(sum(x[0] in bad for x in h) / 10)
        e["RMS"].append(sum(x[0] in bad for x in r["t1_RMS"]) / 10)
        e["chance"].append(r["chance"])
        bl = {(x[0], x[1]) for x in r["t1_BL"]}
        zl = {(x[0], x[1]) for x in r["t1_ZL"]}
        e["overlap"].append(len(bl & zl) / 10)
        if r["t1_BL"] and r["t1_ZL"]:
            e["gain"].append(r["t1_ZL"][0][2] - r["t1_BL"][0][2])
    runs = sorted(per)
    m = {k: [float(np.mean(per[x][k])) for x in runs] for k in ("BL", "ZL", "RMS", "chance", "overlap")}
    t1 = {k: float(np.mean(v)) for k, v in m.items()}
    t1["bad_runs"] = len(runs)
    t1["queries_short_of_10"] = int(sum(per[x]["short"] for x in runs))
    d, lo, hi = boot_mean_diff(m["BL"], m["ZL"])
    t1["BL_minus_ZL"] = [d, lo, hi, decide(lo, hi)]
    d, lo, hi = boot_mean_diff(m["BL"], m["RMS"])
    t1["BL_minus_RMS"] = [d, lo, hi, decide(lo, hi)]
    gains = np.concatenate([per[x]["gain"] for x in runs])
    t3 = dict(top10_overlap_BL_ZL=t1.pop("overlap"), top1_distance_gain_median=float(np.median(gains)),
              top1_distance_gain_p90=float(np.quantile(gains, 0.9)), share_gain_gt_0_01=float((gains > 0.01).mean()))
    # T2: run score = median over windows and axes of nearest good distance
    sc = {}
    for r in rows:
        e = sc.setdefault(r["run"], {"BL": [], "ZL": [], "RMS": []})
        for arm in ("BL", "ZL", "RMS"):
            if r[f"t2_{arm}"] is not None:
                e[arm].append(r[f"t2_{arm}"])
    t2 = {}
    rr = sorted(sc)
    y = np.array([labels[x] == "bad" for x in rr])
    S = {arm: np.array([np.median(sc[x][arm]) if sc[x][arm] else np.nan for x in rr]) for arm in ("BL", "ZL", "RMS")}
    ok = ~np.isnan(S["BL"]) & ~np.isnan(S["ZL"]) & ~np.isnan(S["RMS"])
    rng = np.random.default_rng(SEED)
    pos, neg = np.where(ok & y)[0], np.where(ok & ~y)[0]
    boots = {a: [] for a in S}
    for _ in range(B):  # stratified run bootstrap
        bp, bn = rng.choice(pos, len(pos)), rng.choice(neg, len(neg))
        for a in S:
            boots[a].append(auroc(S[a][bp], S[a][bn]))
    for a in S:
        v = np.array(boots[a])
        t2[a] = [auroc(S[a][pos], S[a][neg]), float(np.quantile(v, .025)), float(np.quantile(v, .975))]
    for a, b in (("BL", "ZL"), ("BL", "RMS")):
        dv = np.array(boots[a]) - np.array(boots[b])
        lo, hi = float(np.quantile(dv, .025)), float(np.quantile(dv, .975))
        t2[f"{a}_minus_{b}"] = [t2[a][0] - t2[b][0], lo, hi, decide(lo, hi)]
    t2["runs"], t2["bad"] = int(ok.sum()), int((ok & y).sum())
    lat = [r["t2_BL_ms"] for r in rows]
    t2["search_ms_p50_p95"] = [float(np.percentile(lat, 50)), float(np.percentile(lat, 95))]
    return dict(t1=t1, t2=t2, t3=t3, queries=len(rows), seconds=D["seconds"])


def t4():
    p = OUT / "t4.json"
    if not p.exists():
        return None
    D = json.loads(p.read_text())
    rows = [v for k, v in D["rows"].items() if not k.startswith("load")]
    rec = [len({tuple(x) for x in r["probe"]} & {tuple(x) for x in r["exact"]}) / max(1, len(r["exact"])) for r in rows]
    ex = np.concatenate([r["exact_ms"] for r in rows]); pr = np.concatenate([r["probe_ms"] for r in rows])
    res = dict(gallery=D["gallery"], pgvector=D["pgvector"], enable_s=D["enable_s"], recall10=float(np.mean(rec)),
               recall_min=float(np.min(rec)), below_09=int(sum(x < 0.9 for x in rec)),
               exact_p50_p95=[float(np.percentile(ex, 50)), float(np.percentile(ex, 95))],
               probe_p50_p95=[float(np.percentile(pr, 50)), float(np.percentile(pr, 95))],
               load=[D["rows"].get("load_round1"), D["rows"].get("load_round2")])
    res["decision"] = "probes hold" if res["recall10"] >= 0.95 and res["probe_p50_p95"][1] <= res["exact_p50_p95"][1] / 2 else "probes do not hold"
    return res


def t5():
    p = OUT / "t5.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    out = {}
    for arm in ("BL", "ZL"):
        for st in (0, 0.01, 0.02, 0.05, 0.10):
            rs = [r for r in rows if r["arm"] == arm and r["stretch"] == st]
            found = [r for r in rs if r["rank"] is not None and r["rank"] <= 10]
            d = [r["dist"] for r in rs if r["dist"] is not None]
            out[f"{arm}@{st}"] = dict(in_top10=len(found) / len(rs), median_dist=float(np.median(d)) if d else None)
    return out


def t6():
    p = OUT / "t6.json"
    if not p.exists():
        return None
    rows = json.loads(p.read_text())
    bl = [r["BL_same_op"] for r in rows]; zl = [r["ZL_same_op"] for r in rows]; ch = [r["chance"] for r in rows]
    d, lo, hi = boot_mean_diff(bl, zl)
    return dict(BL=float(np.mean(bl)), ZL=float(np.mean(zl)), chance=float(np.mean(ch)),
                BL_minus_ZL=[d, lo, hi, decide(lo, hi)], queries=len(rows))


if __name__ == "__main__":
    S = dict(dev=t12("dev"), conf=t12("conf"), t4=t4(), t5=t5(), t6=t6())
    load = OUT / "load.json"
    if load.exists():
        S["load"] = json.loads(load.read_text())
    (OUT / "summary.json").write_text(json.dumps(S, indent=1))
    print(json.dumps(S, indent=1))
