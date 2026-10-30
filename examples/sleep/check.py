#!/usr/bin/env python3
"""Engineering checks for the sleep demo database (decide nothing; S1 is already evaluated).

    MORLET_DSN=... .venv-seismic/bin/python -I examples/sleep/check.py \
        data/sleep-edfx/1.0.0/sleep-cassette data/sleep-edfx/run examples/sleep/results

1. Raw provenance: samples read back with morlet_read_interval equal the EDF samples bitwise.
2. Features: pg_morlet features computed from the stored samples equal S1's stored features.
3. Index: recall of the HNSW top 40 against an exact scan of the same vectors (tie-aware).
4. Reproduction: the demo's staging of every held-out epoch against S1's M-KNN arm
   (float64 numpy, exact neighbours): epoch agreement and mean per-subject kappa.
"""
import json
import os
import sys
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE.parents[1] / "evaluation" / "sleep"), str(HERE)]
import s1  # noqa: E402
import app  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_sleep_demo")
RNG = np.random.default_rng(11)


def raw_and_features(con, data, run, n=300):
    rows = con.execute("SELECT recording_id, epoch, start_sample, subject, night, features FROM sleep_epochs").fetchall()
    pick = [rows[i] for i in RNG.choice(len(rows), n, replace=False)]
    signals, raw_ok = {}, 0
    for rid, epoch, start, subject, night, _ in pick:
        if rid not in signals:
            psg = next(p for nt, p, _ in s1.night_files(data, subject) if nt == night)
            signals[rid] = s1.read_signal(psg, s1.CHANNEL)[0]
        got = np.asarray(con.execute("SELECT samples FROM morlet_read_interval(%s, %s, %s, %s)",
                                     (app.COLLECTION, rid, start, start + s1.EPOCH)).fetchone()[0])
        raw_ok += np.array_equal(got, signals[rid][epoch * s1.EPOCH:(epoch + 1) * s1.EPOCH])
    stored = {}
    for p in sorted((Path(run) / "features").glob("SC4*.npz")):
        z = np.load(p)
        stored[p.stem] = dict(zip(z["epoch"].tolist(), z["morlet"]))
    diffs = [np.max(np.abs(np.asarray(f) - stored[rid][epoch])) for rid, epoch, _, _, _, f in rows]
    return {"raw_epochs_checked": n, "raw_bitwise_equal": raw_ok,
            "feature_epochs": len(rows), "feature_epochs_in_s1": sum(len(v) for v in stored.values()),
            "feature_max_abs_difference": float(np.max(diffs)), "feature_exact": int(np.sum(np.array(diffs) == 0))}


def recall(con, n=500):
    q = con.execute("SELECT recording_id, epoch FROM sleep_epochs WHERE split = 'held-out'").fetchall()
    pick = [q[i] for i in RNG.choice(len(q), n, replace=False)]
    con.execute(f"SET hnsw.ef_search = {app.EF_SEARCH}")
    con.execute("SET hnsw.iterative_scan = strict_order")
    plan = "\n".join(r[0] for r in con.execute("EXPLAIN " + app.NEIGHBOURS, pick[0]).fetchall())
    hits, total = 0, 0
    for rid, epoch in pick:
        approx = con.execute(app.NEIGHBOURS, (rid, epoch)).fetchall()
        with con.transaction():
            con.execute("SET LOCAL enable_indexscan = off")
            exact = con.execute(app.NEIGHBOURS, (rid, epoch)).fetchall()
        kth = exact[-1][-1]
        hits += sum(1 for r in approx if r[-1] <= kth + 1e-6)
        total += app.K
    return {"queries": n, "k": app.K, "ef_search": app.EF_SEARCH, "recall_tie_aware": hits / total,
            "uses_hnsw": "sleep_gallery_hnsw" in plan}


def reproduction(demo, run):
    split = s1.load_split()
    sel = json.loads((HERE.parents[1] / "evaluation" / "sleep" / "results" / "selection.json").read_text())
    dev, held = s1.load(run, split["development"]), s1.load(run, split["held_out"])
    y, p_s1, g, _ = s1.predict("M-KNN", sel, dev, held)
    p_demo = []
    for n in held:
        night = demo.night(f"SC4{int(n['subject']):02d}{int(n['night'])}")
        if night["epochs"] != n["epoch"].tolist():
            raise SystemExit(f"epoch mismatch in SC4{int(n['subject']):02d}{int(n['night'])}")
        p_demo += night["retrieved"]
    p_demo = np.array(p_demo)
    k_s1, k_demo = s1.per_subject_kappa(y, p_s1, g), s1.per_subject_kappa(y, p_demo, g)
    reported = json.loads((HERE.parents[1] / "evaluation" / "sleep" / "results" / "s1.json").read_text())
    return {"held_out_epochs": int(len(y)), "epoch_agreement_with_s1": float(np.mean(p_s1 == p_demo)),
            "mean_kappa_demo": float(np.mean(list(k_demo.values()))),
            "mean_kappa_s1_recomputed": float(np.mean(list(k_s1.values()))),
            "mean_kappa_s1_reported": reported["arms"]["M-KNN"]["mean_kappa"],
            "max_subject_kappa_difference": float(max(abs(k_demo[s] - k_s1[s]) for s in k_s1))}


def main():
    data, run, out = (Path(a) for a in sys.argv[1:4])
    res = {}
    with psycopg.connect(DSN, autocommit=True) as con:
        res["raw_and_features"] = raw_and_features(con, data, run)
        print(res["raw_and_features"], flush=True)
        res["index"] = recall(con)
        print(res["index"], flush=True)
        res["sizes"] = dict(zip(("chunks_bytes", "epochs_bytes", "index_bytes"), con.execute(
            "SELECT pg_total_relation_size('sleep_eeg_chunks'), pg_total_relation_size('sleep_epochs'), "
            "pg_relation_size('sleep_gallery_hnsw')").fetchone()))
        res["samples_stored"] = con.execute("SELECT sum(sample_count) FROM sleep_eeg_chunks").fetchone()[0]
    res["reproduction"] = reproduction(app.Demo(DSN), run)
    print(res["reproduction"], flush=True)
    out.mkdir(parents=True, exist_ok=True)
    (out / "check.json").write_text(json.dumps(res, indent=1, default=int))


if __name__ == "__main__":
    main()
