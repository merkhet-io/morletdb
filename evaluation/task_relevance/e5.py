#!/usr/bin/env python3
"""E5 (PROTOCOL.md): catalogued earthquakes as relevance labels; bounded-lag (L = 30) against zero-lag (L = 0)
exact search on the development gallery.

  .venv-seismic/bin/python -I evaluation/task_relevance/e5.py data/e5-catalog evaluation/task_relevance/results/e5.json
"""
import json
import sys
from pathlib import Path

import numpy as np
import psycopg
from obspy import UTCDateTime
from obspy.geodetics import locations2degrees
from obspy.taup import TauPyModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "strong_exact"))
import e3  # noqa: E402

RATE, W, STRIDE = 100.0, 4096, 2048
MIN_MAG, MAX_DIST, LEAD, LO, HI = 2.5, 2.0, 5.0, 2.0, 30.0
SEED, B, TOP = 20261102, 10_000, 10


def catalogue(path):
    ev = []
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("|")
        depth = float(f[4]) if f[4].strip() else 10.0
        mag = float(f[10]) if f[10].strip() else float("nan")
        ev.append({"id": f[0], "time": UTCDateTime(f[1]), "lat": float(f[2]), "lon": float(f[3]),
                   "depth_km": depth, "mag": mag, "magtype": f[9]})
    return ev


def stations(path):
    st = {}
    for line in path.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        f = line.split("|")
        st[f[1]] = (float(f[2]), float(f[3]))
    return st


def arrivals(events, sts):
    model = TauPyModel("iasp91")
    out = {}
    for e in events:
        for s, (la, lo) in sts.items():
            d = locations2degrees(e["lat"], e["lon"], la, lo)
            arr = model.get_travel_times(source_depth_in_km=max(e["depth_km"], 0.0), distance_in_degree=d,
                                         phase_list=["p", "P"])
            if arr:
                out[(e["id"], s)] = (e["time"] + min(a.time for a in arr), d)
    return out


def search(q, src, lags):
    e3.LAGS = lags
    top, _ = e3.search64(q, src, {})
    return [(r, st) for r, st, _ in top]


def bootstrap_diff(per, key_a, key_b, events_of):
    """Mean P@10 difference, resampling events (each with all its station queries)."""
    evs = sorted({events_of[q] for q in per})
    by_ev = {e: [q for q in per if events_of[q] == e] for e in evs}
    a = {e: [per[q][key_a] for q in by_ev[e]] for e in evs}
    b = {e: [per[q][key_b] for q in by_ev[e]] for e in evs}
    rng = np.random.default_rng(SEED)
    diffs = np.empty(B)
    for i in range(B):
        pick = rng.integers(0, len(evs), len(evs))
        xa = np.concatenate([a[evs[j]] for j in pick])
        xb = np.concatenate([b[evs[j]] for j in pick])
        diffs[i] = xa.mean() - xb.mean()
    point = np.mean([per[q][key_a] for q in per]) - np.mean([per[q][key_b] for q in per])
    ci = [float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))]
    verdict = "bounded lag better" if ci[0] > 0 else ("zero lag better" if ci[1] < 0 else "no difference demonstrated")
    return {"point": float(point), "ci95": ci, "verdict": verdict, "events": len(evs)}


def main(cat_dir, out_path):
    cat_dir = Path(cat_dir)
    events = catalogue(cat_dir / "emsc_events.txt")
    sts = stations(cat_dir / "stations.txt")
    arr = arrivals(events, sts)
    with psycopg.connect(f"host=localhost port=28817 dbname={e3.DB}", autocommit=True) as conn:
        gal, _ = e3.materialize(conn)
        e3.GAL = gal
        src_attr = {}
        rec = {}
        for r, rate, ch, unit, meta in conn.execute(f"SELECT recording_id, sample_rate_hz, channel, unit, metadata FROM {e3.COLL}"):
            key = (rate, ch, unit)
            src_attr[r] = gal["attrs"].index(key) if key in gal["attrs"] else -1
            rec[r] = (meta["station"], UTCDateTime(meta["start"]))
        gal["src_attr"] = src_attr
        names = gal["names"]
        wst = np.array([rec[names[i]][0] for i in gal["rid"]])
        wt0 = np.array([float(rec[names[i]][1]) for i in gal["rid"]]) + gal["start"] / RATE   # window start, epoch s
        index = {(names[r], int(s)): i for i, (r, s) in enumerate(zip(gal["rid"], gal["start"]))}
        # Event windows: predicted P in [start + LO, start + HI].
        label = {}                                   # window index -> set of event ids
        for (eid, s), (tp, _) in arr.items():
            sel = np.flatnonzero((wst == s) & (wt0 >= float(tp) - HI) & (wt0 <= float(tp) - LO))
            for i in sel:
                label.setdefault(int(i), set()).add(eid)
        chance = len(label) / len(wt0)
        # Queries.
        queries, seen = [], set()
        for e in events:
            if not (e["mag"] >= MIN_MAG):
                continue
            for s in sts:
                if (e["id"], s) not in arr:
                    continue
                tp, dist = arr[(e["id"], s)]
                if dist > MAX_DIST:
                    continue
                target = float(tp) - LEAD
                for r, (station, t0) in rec.items():
                    if station != s:
                        continue
                    off = (target - float(t0)) * RATE
                    start = int(np.floor(off / STRIDE)) * STRIDE
                    if start >= 0 and (r, start) in index and (r, start) not in seen:
                        seen.add((r, start))
                        queries.append({"event": e["id"], "station": s, "recording_id": r, "start": start,
                                        "mag": e["mag"], "dist_deg": dist})
        per = {}
        for qd in queries:
            r, s = qd["recording_id"], qd["start"]
            q = e3.e1.query_env(conn, r, s)
            res = {}
            for arm, lags in (("BL", list(range(-30, 31))), ("ZL", [0])):
                top = search(q, (r, s), lags)
                hits = [index[(rr, st)] for rr, st in top]
                rel = [bool(label.get(h)) for h in hits]
                rel2 = [bool(label.get(h, set()) - {qd["event"]}) for h in hits]
                res[arm] = {"top": top, "p10": sum(rel) / TOP, "p10_other_event": sum(rel2) / TOP,
                            "hit_stations": [str(wst[h]) for h in hits]}
            key = f"{r}:{s}"
            per[key] = {**qd, "BL_p10": res["BL"]["p10"], "ZL_p10": res["ZL"]["p10"],
                        "BL_p10_other": res["BL"]["p10_other_event"], "ZL_p10_other": res["ZL"]["p10_other_event"],
                        "overlap": len(set(map(tuple, res["BL"]["top"])) & set(map(tuple, res["ZL"]["top"]))) / TOP,
                        "BL": res["BL"], "ZL": res["ZL"]}
    events_of = {k: v["event"] for k, v in per.items()}
    summary = {"events_in_catalogue": len(events), "arrivals": len(arr), "queries": len(per),
               "query_events": len(set(events_of.values())), "event_windows": len(label),
               "gallery_windows": int(len(wt0)), "chance_p10": chance}
    if len(per) >= 30:
        summary["primary"] = bootstrap_diff(per, "BL_p10", "ZL_p10", events_of)
        summary["secondary_other_event"] = bootstrap_diff(per, "BL_p10_other", "ZL_p10_other", events_of)
    else:
        summary["primary"] = "underpowered (fewer than 30 queries)"
    for k in ("BL_p10", "ZL_p10", "BL_p10_other", "ZL_p10_other", "overlap"):
        summary["mean_" + k] = float(np.mean([v[k] for v in per.values()])) if per else None
    summary["by_station"] = {s: {"queries": n, "BL": float(np.mean([v["BL_p10"] for v in per.values() if v["station"] == s])),
                                 "ZL": float(np.mean([v["ZL_p10"] for v in per.values() if v["station"] == s]))}
                             for s in sts if (n := sum(v["station"] == s for v in per.values()))}
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(json.dumps({"summary": summary, "per_query": per}, indent=1, default=str) + "\n")
    print(json.dumps(summary, indent=1))


if __name__ == "__main__":
    main(*sys.argv[1:3])
