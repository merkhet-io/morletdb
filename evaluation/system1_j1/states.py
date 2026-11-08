#!/usr/bin/env python3
"""J1 step 1 (no network): load NASA milling through pg_morlet as C1's SQL check does, and write the decision state
of every scored run (PROTOCOL.md). Also writes the baselines: C1's conditioned score from the same database calls,
and time in use.

Usage: states.py MILL_MAT OUT_DIR   (numpy, scipy, psycopg; MORLET_DSN for the database, which it rebuilds)
Writes OUT_DIR/states.json: one entry per scored run with its key, VB (kept out of the state), time in use, the
conditioned score, and the state sent to the model.
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from scipy import io as sio

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "context"))
sys.path.insert(0, str(HERE.parents[1] / "tools" / "mcp"))
import c1  # noqa: E402
import decision  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_j1")
CHANNELS = {"dc": ("smcDC", "p75"), "ac": ("smcAC", "std"), "vt": ("vib_table", "p75"),
            "vs": ("vib_spindle", "p75"), "at": ("AE_table", "p75"), "as": ("AE_spindle", "p75")}
DESCRIPTION = {"smcDC": "spindle motor current, DC", "smcAC": "spindle motor current, AC",
               "vib_table": "vibration at the table", "vib_spindle": "vibration at the spindle",
               "AE_table": "acoustic emission at the table", "AE_spindle": "acoustic emission at the spindle"}
SETTING = ("doc", "feed", "material")


def load(conn, mill):
    for t in ("milling_labels", "milling_ctx_values", "milling_ctx_segments", "milling_ctx",
              "milling_chunks", "milling_segments", "milling"):
        conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
    conn.execute("SELECT morlet_create_stream_collection('public','milling')")
    conn.execute("SELECT morlet_enable_context('milling')")
    with conn.transaction():
        for r in mill:
            case, run = int(r.case), int(r.run)
            vb = float(r.VB) if np.size(r.VB) == 1 and np.isfinite(float(r.VB)) else None
            for value, (field, _) in CHANNELS.items():
                rid = f"c{case:02d}r{run:03d}-{value}"
                x = np.asarray(getattr(r, field), float)
                conn.execute("SELECT morlet_start_recording('milling',%s,250,%s,'V',%s,512,256,'zscore','none',1)",
                             (rid, field, json.dumps({"case": case})))
                conn.execute("SELECT morlet_append_chunk('milling',%s,0,%s::float8[])", (rid, x.tolist()))
                ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": len(x)}
                       for k, v in (("machine", "MC-510V"), ("doc", float(r.DOC)), ("feed", float(r.feed)),
                                    ("material", int(r.material)), ("run", f"{case}/{run}"))]
                conn.execute("SELECT morlet_add_context('milling',%s,%s)", (f"load-{rid}", json.dumps(ctx)))
                conn.execute("SELECT morlet_build_context_segments('milling',%s,ARRAY['machine','doc','feed',"
                             "'material'],ARRAY['run'])", (rid,))
                conn.execute("SELECT morlet_build_context_values('milling',%s)", (rid,))
                if case in c1.EVALUATION:
                    conn.execute("SELECT morlet_add_label('milling',%s,0,%s,'evaluation')", (rid, len(x)))
                elif vb is None or vb > c1.SHARP_VB:
                    conn.execute("SELECT morlet_add_label('milling',%s,0,%s,'not_sharp')", (rid, len(x)))


def run_state(conn, case, run):
    """The state of one milling run: the setting once, then per channel its values against the same-setting
    history of sharp runs, and the summary score; no case, run number, time or wear."""
    channels, conditioned, ms = {}, [], []
    for value, (field, c1_value) in CHANNELS.items():
        rid = f"c{case:02d}r{run:03d}-{value}"
        seg = decision.segment_at(conn, "public.milling", rid, 0)
        t = time.perf_counter()
        st = decision.decision_state(conn, "public.milling", seg, exclude_labels=["evaluation", "not_sharp"],
                                     min_history=c1.MIN_HISTORY, neighbors=0)
        ms.append(1e3 * (time.perf_counter() - t))
        setting = {k: st["segment"]["context"][k] for k in SETTING}
        dev = {v["name"]: v for v in st["values"]}
        if dev[c1_value]["relative_deviation"] is None:
            raise ValueError(f"{rid}: {dev[c1_value]['status']}")
        conditioned.append(abs(dev[c1_value]["relative_deviation"]))
        channels[field] = {"sensor": DESCRIPTION[field], "history_runs": st["history"]["size"],
                           "values": {v["name"]: {k: v[k] for k in ("value", "history_median", "relative_deviation",
                                                                    "history_quantile")}
                                      for v in st["values"]},
                           "summary_score": st["summary"]["score"], "summary_quantile": st["summary"]["score_quantile"]}
    state = {"machine": "milling machine MC-510V, one cut per run",
             "setting": {"depth_of_cut_mm": setting["doc"], "feed_mm_per_rev": setting["feed"],
                         "material": {1: "cast iron", 2: "steel"}[int(setting["material"])]},
             "history": "earlier runs of the same setting with a sharp insert",
             "channels": channels}
    # C1's conditioned score, recomputed from the same calls with the frozen rounding of the state (6 digits)
    return state, max(conditioned), ms


def main():
    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    mill = sio.loadmat(path, squeeze_me=True, struct_as_record=False)["mill"]
    rejected = sorted({(int(r.case), int(r.run)) for r in mill for _, (f, _) in CHANNELS.items()
                       if not np.all(np.isfinite(getattr(r, f))) or np.abs(getattr(r, f)).max() > 1e9})
    mill = [r for r in mill if (int(r.case), int(r.run)) not in rejected]
    reference = c1.analyse([r for r in c1.load(path) if (r["case"], r["run"]) not in rejected], resamples=200)
    scored = {(r["case"], r["run"]): r for r in reference["rows"]}
    runs = {(int(r.case), int(r.run)): r for r in mill}
    with psycopg.connect(DSN, autocommit=True) as conn:
        load(conn, mill)
        rows, ms = [], []
        for key in sorted(scored):
            state, cond, t = run_state(conn, *key)
            ms += t
            rows.append({"case": key[0], "run": key[1], "vb": scored[key]["vb"],
                         "time_in_use_min": float(runs[key].time), "conditioned_score": cond,
                         "c1_conditioned_score": scored[key]["conditioned"], "state": state})
    worst = max(abs(r["conditioned_score"] - r["c1_conditioned_score"]) / max(1e-12, r["c1_conditioned_score"])
                for r in rows)
    report = {"rejected_runs": rejected, "scored_runs": len(rows),
              "worn_runs": sum(r["vb"] >= c1.WORN_VB for r in rows),
              "max_relative_difference_vs_c1_score": worst,
              "state_latency_ms": {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95))},
              "state_chars": {"median": int(np.median([len(json.dumps(r["state"])) for r in rows])),
                              "max": max(len(json.dumps(r["state"])) for r in rows)},
              "rows": rows}
    (out / "states.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: v for k, v in report.items() if k != "rows"}, indent=1))


if __name__ == "__main__":
    main()
