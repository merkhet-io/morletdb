#!/usr/bin/env python3
"""F2 decisions (PROTOCOL.md, frozen) from the confirmation results: H1 per budget, H2 (a)-(e) at 0.1, H4.

  python3 evaluation/edge_relevance_f2/analyze_f2.py results/f2_confirm_*.json
"""
import json
import math
import sys
from collections import defaultdict

DS = ("xjtu", "paderborn", "pyscrew", "seismic", "bosch")


def load(paths):
    out = {}
    for p in paths:
        d = json.load(open(p))
        out[d["dataset"]] = d
    return out


def medians(d):
    """{(method, budget): (median rel_err with missed = inf, missed, n)}"""
    by = defaultdict(list)
    for r in d["rows"]:
        by[(r["method"], r["budget"])].append(math.inf if r["rel_err"] is None else r["rel_err"])
    res = {}
    for k, v in by.items():
        v = sorted(v)
        n = len(v)
        med = v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2
        res[k] = (med, sum(math.isinf(x) for x in v), n)
    return res


def main(paths):
    D = load(paths)
    out = {"H1": {}, "H2": {}, "H4": {}}
    meds = {ds: medians(D[ds]) for ds in DS if ds in D}
    print("H1: median relative error, plain decode vs SZ3 / ZFP / decimation (missed windows)")
    for b in (0.05, 0.1, 0.2, 0.3):
        wins = 0
        line = []
        for ds in DS:
            if ds not in meds:
                continue
            m = meds[ds]
            w = m.get(("wavelet", b), (math.inf, 0, 0))
            s = m.get(("sz3", b), (math.inf, 0, 0))
            z = m.get(("zfp", b), (math.inf, 0, 0))
            dec = m.get(("decimate", b), (math.inf, 0, 0))
            win = w[0] < s[0] and w[0] < z[0]
            wins += win
            line.append(f"{ds} {w[0]:.3f}({w[1]}) / {s[0]:.3f} / {z[0]:.3f} / {dec[0]:.3f} {'W' if win else 'L'}")
        holds = wins >= 4
        out["H1"][str(b)] = {"datasets_won": wins, "holds": holds}
        print(f"  {b}: {'HOLDS' if holds else 'REFUTED'} ({wins}/5)  " + " | ".join(line))

    # H2 (a) XJTU
    if "xjtu" in D:
        det = D["xjtu"]["detection"]
        ok, rows = True, []
        for name, e in det.items():
            d0 = e["original"]
            if d0 is None:
                rows.append(f"{name}: original does not detect (excluded)")
                continue
            tol = math.ceil(0.01 * e["files"])
            dw = e.get("wavelet@0.1")
            good = dw is not None and dw - d0 <= tol
            ok &= good
            rows.append(f"{name}: orig {d0}, wavelet {dw}, sz3 {e.get('sz3@0.1')}, decim {e.get('decimate@0.1')}, "
                        f"tol {tol} -> {'ok' if good else 'FAIL'}; alarms orig {e['false_alarms']['original']} "
                        f"wavelet {e['false_alarms'].get('wavelet@0.1')}")
        out["H2"]["a"] = ok
        print("H2a XJTU-SY:", "pass" if ok else "FAIL")
        for r in rows:
            print("   ", r)
    # (b) seismic, noise-filled
    if "seismic" in D:
        agg = defaultdict(lambda: [0, 0])
        for t in D["seismic"]["triggers"]:
            k = (t["method"], t["budget"])
            agg[k][0] += t["hits"]
            agg[k][1] += t["false"]
        h0, f0 = agg[("original", None)]
        hn, fn = agg[("wavelet-nf", 0.1)]
        ok = hn >= 0.95 * h0 and fn <= 1.10 * f0
        out["H2"]["b"] = ok
        print(f"H2b seismic (noise-filled @0.1): hits {hn} vs {h0}, elsewhere {fn} vs {f0} -> {'pass' if ok else 'FAIL'}")
        for m in ("wavelet", "wavelet-nf", "sz3", "decimate"):
            print("    ", m, [(b, agg[(m, b)]) for b in (0.05, 0.1, 0.2, 0.3)])
    # (c) PyScrew
    if "pyscrew" in D:
        a = D["pyscrew"]["auroc_kurtosis"]
        o, w = a["original"]["auroc"], a.get("wavelet@0.1", {})
        ok = w.get("cycles") == w.get("of") and abs(w["auroc"] - o) <= 0.02
        out["H2"]["c"] = ok
        print(f"H2c PyScrew: AUROC orig {o}, wavelet {w} -> {'pass' if ok else 'FAIL'}")
    # (d) Bosch
    if "bosch" in D:
        a = D["bosch"]["auroc_score"]
        ok = a.get("wavelet@0.1") is not None and abs(a["wavelet@0.1"] - a["orig"]) <= 0.02
        out["H2"]["d"] = ok
        print(f"H2d Bosch: AUROC orig {a['orig']}, wavelet {a.get('wavelet@0.1')}, nf {a.get('wavelet-nf@0.1')} -> "
              f"{'pass' if ok else 'FAIL'}; RMS dev {D['bosch']['rms_dev']}")
    # (e) Paderborn
    if "paderborn" in D:
        a = D["paderborn"]["auroc_kurtosis"]
        ok = a.get("wavelet@0.1") is not None and abs(a["wavelet@0.1"] - a["original"]) <= 0.02
        out["H2"]["e"] = ok
        print(f"H2e Paderborn: AUROC orig {a['original']}, wavelet {a.get('wavelet@0.1')}, nf "
              f"{a.get('wavelet-nf@0.1')}, sz3 {a.get('sz3@0.1')}, decim {a.get('decimate@0.1')} -> "
              f"{'pass' if ok else 'FAIL'}")
    out["H2"]["holds"] = all(out["H2"].get(k, False) for k in "abcde")
    print("H2:", "HOLDS" if out["H2"]["holds"] else "REFUTED")
    # H4
    refuted = []
    for ds in ("xjtu", "paderborn"):
        if ds not in D:
            continue
        rows = [r for r in D[ds]["rows"] if r["method"] in ("wavelet", "wavelet-bayes")]
        items = {r["item"] for r in rows if r["method"] == "wavelet-bayes"}
        better = False
        for b in (0.05, 0.1, 0.2, 0.3):
            def med(m):
                v = sorted(math.inf if r["rel_err"] is None else r["rel_err"] for r in rows
                           if r["method"] == m and r["budget"] == b and r["item"] in items)
                return v[len(v) // 2] if v else math.inf
            wb, wp = med("wavelet-bayes"), med("wavelet")
            out["H4"][f"{ds}@{b}"] = (wb, wp)
            if wb < 0.95 * wp:
                better = True
        refuted.append(better)
        print(f"H4 {ds}: bayes vs plain (median on {len(items)} windows):",
              {b: tuple(round(x, 3) for x in out['H4'][f'{ds}@{b}']) for b in (0.05, 0.1, 0.2, 0.3)})
    out["H4"]["holds"] = not (len(refuted) == 2 and all(refuted))
    print("H4:", "HOLDS" if out["H4"]["holds"] else "REFUTED")
    return out


if __name__ == "__main__":
    res = main([a for a in sys.argv[1:] if not a.startswith("--")])
    if "--json" in sys.argv:
        print(json.dumps(res, default=str))
