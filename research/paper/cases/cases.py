"""Illustrative cases from E1 (rule: SELECTION.md, committed before this figure was made).

  select:  python3 research/paper/cases/cases.py select            -> research/paper/cases/cases.json (stored results only)
  figure:  .venv-seismic/bin/python -I research/paper/cases/cases.py figure morlet_bl1c
           -> research/paper/fig/cases.pdf; needs the held-out database (signals are not redistributed)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
E1 = ROOT / "evaluation/consolidated/results/e1_heldout.json"
QUERIES = ROOT / "evaluation/bounded_lag/confirm/results/queries.json"
OUT = HERE / "cases.json"
COLL, W, L = "public.r2", 4096, 30
ARMS = ("PR", "Z-1000", "Z-650", "PX-13", "EZ")


def select():
    e1 = json.loads(E1.read_text())["per_query"]
    order = [f"{q['recording_id']}:{q['start']}" for q in json.loads(QUERIES.read_text())["queries"]]
    ref = {k: [tuple(x) for x in e1[k]["top"]["EX-2"]] for k in order}
    got = {k: {a: {tuple(x) for x in e1[k]["top"][a]} for a in ARMS} for k in order}

    def item(k, i):
        r, s = ref[k][i]
        return {"recording_id": r, "start": s, "end": s + W, "exact_rank": i + 1, "best_lag_values": e1[k]["lstar"][i],
                "returned_by": [a for a in ARMS if (r, s) in got[k][a]]}

    gain = {k: e1[k]["recall"]["PR"] - e1[k]["recall"]["Z-1000"] for k in order}
    a = max(order, key=lambda k: (gain[k], -order.index(k)))
    cand = [i for i, it in enumerate(ref[a]) if it in got[a]["PR"] and it not in got[a]["Z-1000"]]
    ia = max(cand, key=lambda i: (abs(e1[a]["lstar"][i]), -i))
    b = min(order, key=lambda k: (e1[k]["recall"]["PR"], order.index(k)))
    ib = min(i for i, it in enumerate(ref[b]) if it not in got[b]["PR"])
    cases = []
    for name, k, i in (("A: zero-lag misses, probes find", a, ia), ("B: probes miss", b, ib)):
        rid, s = k.rsplit(":", 1)
        cases.append({"case": name, "query": {"recording_id": rid, "start": int(s), "end": int(s) + W, "type": e1[k]["type"]},
                      "recall": {x: e1[k]["recall"][x] for x in ("PR", "Z-1000", "PX-13")}, "item": item(k, i)})
    OUT.write_text(json.dumps({"rule": "SELECTION.md", "source": str(E1.relative_to(ROOT)), "cases": cases}, indent=1) + "\n")
    print(json.dumps(cases, indent=1))


def figure(db):
    import numpy as np
    import psycopg
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    sys.path.insert(0, str(ROOT / "evaluation/bounded_lag"))
    import probes as P

    data = json.loads(OUT.read_text())
    fig, axes = plt.subplots(3, 2, figsize=(7.0, 5.2), gridspec_kw={"height_ratios": [1, 1, 1.25]})
    with psycopg.connect(f"host=localhost port=28817 dbname={db}") as c:
        for col, case in enumerate(data["cases"]):
            q, it = case["query"], case["item"]
            qs = np.asarray(c.execute("SELECT samples FROM morlet_read_interval(%s,%s,%s,%s)",
                                      (COLL, q["recording_id"], q["start"], q["end"])).fetchone()[0])
            gs = np.asarray(c.execute("SELECT samples FROM morlet_read_interval(%s,%s,%s,%s)",
                                      (COLL, it["recording_id"], it["start"], it["end"])).fetchone()[0])
            qe = np.asarray(c.execute("SELECT morlet_envelope(%s::float8[])", (qs.tolist(),)).fetchone()[0])
            ge_raw = c.execute(f"SELECT envelope FROM {COLL}_envelopes WHERE recording_id=%s AND window_size=%s AND start_sample=%s",
                               (it["recording_id"], W, it["start"])).fetchone()[0]
            d = c.execute("SELECT morlet_envelope_distance(%s::float8[], %s, %s)", (qe.tolist(), ge_raw, L)).fetchone()[0]
            ge = P.decode(ge_raw)
            n = len(qe)
            terms = {l: P.lagged_sum(qe, ge, l) for l in range(-L, L + 1)}
            lstar = max(range(-L, L + 1), key=lambda l: terms[l])          # first maximum, as in E1
            assert abs((1 - terms[lstar] / n) - d) < 1e-9, (d, 1 - terms[lstar] / n)
            assert lstar == it["best_lag_values"], (lstar, it["best_lag_values"])
            assert abs(float(np.dot(P.shifted(qe, lstar), ge)) - terms[lstar]) < 1e-6   # plotted alignment = scorer term
            it["distance"], it["best_lag_samples"] = d, 10 * lstar
            t = np.arange(W) / 100.0
            axes[0, col].plot(t, qs, lw=0.5, color="#1f4e79")
            axes[0, col].set_title(f"{case['case']}\nquery {q['recording_id']} [{q['start']}, {q['end']})", fontsize=7)
            axes[1, col].plot(t, gs, lw=0.5, color="#7a3e00")
            axes[1, col].set_title(f"exact rank {it['exact_rank']}: {it['recording_id']} [{it['start']}, {it['end']}), "
                                   f"returned by {', '.join(it['returned_by']) or 'none of the shown arms'}", fontsize=6.5)
            te = np.arange(n) / 10.0
            axes[2, col].plot(te, P.shifted(qe, lstar), lw=1.0, color="#1f4e79", label=f"query envelope, shifted by ℓ*={lstar}")
            axes[2, col].plot(te, ge, lw=1.0, color="#7a3e00", ls="--", label="stored envelope")
            axes[2, col].set_title(f"D = {d:.3f}, ℓ* = {lstar} values ({10 * lstar} samples)\n"
                                   f"this query's recall: probes {case['recall']['PR']:.1f}, zero-lag (1000) {case['recall']['Z-1000']:.1f}",
                                   fontsize=6.5)
            axes[2, col].legend(fontsize=5.5, loc="lower center", bbox_to_anchor=(0.5, -0.55), ncol=2, frameon=False)
    for ax in axes[:2].ravel():
        ax.set_ylabel("counts", fontsize=6)
        ax.set_xlabel("s (raw samples, unaligned)", fontsize=6)
    for ax in axes[2]:
        ax.set_xlabel("s (envelope, z-scored)", fontsize=6)
    for ax in axes.ravel():
        ax.tick_params(labelsize=6)
    fig.tight_layout()
    fig.savefig(ROOT / "research/paper/fig/cases.pdf", metadata={"CreationDate": None})
    OUT.write_text(json.dumps(data, indent=1) + "\n")
    print(json.dumps(data["cases"], indent=1))


if __name__ == "__main__":
    {"select": lambda: select(), "figure": lambda: figure(sys.argv[2])}[sys.argv[1]]()
