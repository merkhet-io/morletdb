#!/usr/bin/env python3
"""C8: context sensitivity of morlet_history_neighbors (PROTOCOL.md). Never prints signal values.

  c8.py export-bosch BOSCH_REPO SHA256SUMS OUT [--dev]   compression env (numpy, h5py): checks SHA-256, writes
                                                       OUT/bosch.npz (float64 per run and axis) and bosch.json
  c8.py load-bosch OUT [--dev]                         seismic env (numpy, psycopg; MORLET_DSN)
  c8.py load-phm PHM_DIR OUT [--dev]                   seismic env (numpy, pandas, psycopg); records SHA-256
  c8.py query bosch|phm OUT [--dev]                    neighbours of every query segment under every choice
  c8.py check bosch|phm OUT [--dev]                    product check: ranking recomputed from stored values
  c8.py analyse OUT RESULTS_DIR [--dev]                overlaps, bootstrap and verdicts -> RESULTS_DIR/c8.json

--dev uses the development subset (PROTOCOL.md, "Development and checks") and separate collections.
"""
import gzip
import hashlib
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

import numpy as np

DSN = os.environ.get("MORLET_DSN", "host=127.0.0.1 port=28861 dbname=morlet user=morlet password=c8-local-only")
KS = (10, 50)
TOP_K = 51
SEED = 20261116
RESAMPLES = 2000
SCORE_VALUES = ("duration_s", "std", "crest", "kurtosis")

# Bosch (as C2: history = good runs of 2019-2020, queries = every run of 2021)
B_RATE = 2000.0
B_HISTORY = ("Feb_2019", "Aug_2019", "Feb_2020", "Aug_2020")
B_QUERY = ("Feb_2021", "Aug_2021")
B_AXES = ("x", "y", "z")
B_NAME = re.compile(r"^(M\d\d)_(\w+_\d{4})_(OP\d\d)_(\d+)\.h5$")
B_CHOICES = {"S": ["site"], "M": ["site", "machine"], "O": ["site", "operation"],
             "MO": ["site", "machine", "operation"]}
B_PAIRS = [("S", "M"), ("S", "O"), ("S", "MO"), ("M", "O"), ("M", "MO"), ("O", "MO")]
B_DEV = ("M01", ("OP00", "OP01"))

# PHM 2010
P_RATE = 50000.0
P_CHANNELS = ("force_x", "force_y", "force_z", "vib_x", "vib_y", "vib_z", "ae_rms")
P_UNITS = {"force_x": "N", "force_y": "N", "force_z": "N", "vib_x": "g", "vib_y": "g", "vib_z": "g", "ae_rms": "V"}
P_CUTTERS = ("c1", "c2", "c3", "c4", "c5", "c6")
P_CUTS = 315
P_CHOICES = {"P": ["process"], "C": ["process", "cutter"]}
P_PAIRS = [("P", "C")]
P_DEV = (("c1", "c2"), 10)

PIECE = 262144


def coll(dataset, dev):
    return f"public.c8{dataset}{'_dev' if dev else ''}"


def connect():
    import psycopg
    return psycopg.connect(DSN, autocommit=True)


def sha256(b):
    return hashlib.sha256(b).hexdigest()


# ------------------------------------------------------------------ Bosch export (compression env)
def bosch_runs(repo):
    for p in sorted((Path(repo) / "data").glob("M0*/OP*/*/*.h5")):
        m = B_NAME.match(p.name)
        label = p.parent.name
        if not m or label not in ("good", "bad"):
            continue
        machine, period, op, _ = m.groups()
        if period in B_HISTORY and label == "good":
            split = "history"
        elif period in B_QUERY:
            split = "query"
        else:
            continue
        yield {"path": p, "rel": str(p.relative_to(repo)), "machine": machine, "operation": op, "period": period,
               "label": label, "split": split, "run": f"{p.stem}_{label}"}


def export_bosch(repo, sums, out, dev):
    import h5py
    expected = {}
    for line in Path(sums).read_text().splitlines():
        h, name = line.split(maxsplit=1)
        expected[name.strip()] = h
    arrays, meta = {}, []
    for r in bosch_runs(repo):
        if dev and not (r["machine"] == B_DEV[0] and r["operation"] in B_DEV[1]):
            continue
        b = r["path"].read_bytes()
        if expected.get(r["rel"]) != sha256(b):
            raise SystemExit(f"SHA-256 mismatch or missing: {r['rel']}")
        with h5py.File(io.BytesIO(b), "r") as f:
            a = f["vibration_data"][()]
        x = np.asarray(a, dtype="<f8")
        if a.ndim != 2 or a.shape[1] != 3 or not np.isfinite(x).all():
            raise SystemExit(f"malformed: {r['rel']}")
        if a.dtype.kind == "i":
            assert np.array_equal(x.astype(np.int64), a), "int64 -> float64 not exact"
        else:
            assert np.array_equal(x.astype(a.dtype), a), "float -> float64 not exact"
        for i, ax in enumerate(B_AXES):
            arrays[f"{r['run']}|{ax}"] = np.ascontiguousarray(x[:, i])
        meta.append({k: r[k] for k in ("rel", "machine", "operation", "period", "label", "split", "run")}
                    | {"samples": int(len(x)), "dtype": str(a.dtype)})
    Path(out).mkdir(parents=True, exist_ok=True)
    np.savez(Path(out) / f"bosch{'_dev' if dev else ''}.npz", **arrays)
    (Path(out) / f"bosch{'_dev' if dev else ''}.json").write_text(json.dumps(meta, indent=0))
    print({"runs": len(meta), "history": sum(m["split"] == "history" for m in meta),
           "query": sum(m["split"] == "query" for m in meta)})


# ------------------------------------------------------------------ loading (seismic env)
def init_collection(c, name):
    c.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
    schema, table = name.split(".")
    names = [r[0] for r in c.execute("SELECT quote_ident(tablename) FROM pg_tables WHERE schemaname=%s "
                                     "AND (tablename=%s OR tablename LIKE %s)", (schema, table, table + r"\_%"))]
    if names:
        c.execute(f"DROP TABLE {', '.join(names)} CASCADE")
    c.execute("SELECT morlet_create_stream_collection(%s,%s)", (schema, table))
    c.execute("SELECT morlet_enable_context(%s)", (name,))
    return {"version": c.execute("SELECT morlet_version()").fetchone()[0],
            "value_rules": c.execute("SELECT morlet_value_rules(%s)", (name,)).fetchone()[0]}


def store(c, name, rid, rate, channel, unit, meta, x, ctx, keys, occ_keys):
    """One recording, its context over all samples, its segment and simple-v1 values, in one transaction."""
    n = len(x)
    with c.transaction():
        c.execute("SELECT morlet_start_recording(%s,%s,%s,%s,%s,%s::jsonb,512,256,'zscore','none')",
                  (name, rid, rate, channel, unit, json.dumps(meta)))
        for off in range(0, n, PIECE):
            c.execute("SELECT morlet_append_chunks_raw(%s,%s,%s,%s,16384)",
                      (name, rid, off, np.ascontiguousarray(x[off:off + PIECE], dtype="<f8").tobytes()))
        rows = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": n} for k, v in ctx.items()]
        c.execute("SELECT morlet_add_context(%s,%s,%s::jsonb)", (name, f"c8-{rid}", json.dumps(rows)))
        built = c.execute("SELECT morlet_build_context_segments(%s,%s,%s,%s)", (name, rid, keys, occ_keys)).fetchone()[0]
        assert built == 1, (rid, built)
        c.execute("SELECT morlet_build_context_values(%s,%s)", (name, rid))
        got = c.execute("SELECT received_samples FROM morlet_stream_status(%s,%s)", (name, rid)).fetchone()[0]
        assert got == n, (rid, got, n)


def load_bosch(out, dev):
    sfx = "_dev" if dev else ""
    meta = json.loads((Path(out) / f"bosch{sfx}.json").read_text())
    data = np.load(Path(out) / f"bosch{sfx}.npz")
    name = coll("bosch", dev)
    t0 = time.time()
    with connect() as c:
        info = init_collection(c, name)
        for i, m in enumerate(meta):
            for ax in B_AXES:
                store(c, name, f"{m['run']}|{ax}", B_RATE, f"acc_{ax}", "counts",
                      {"split": m["split"], "machine": m["machine"], "operation": m["operation"],
                       "period": m["period"], "label": m["label"], "run": m["run"]},
                      data[f"{m['run']}|{ax}"],
                      {"site": "bosch-cnc", "machine": m["machine"], "operation": m["operation"], "run": m["run"]},
                      ["site", "machine", "operation"], ["run"])
            if i % 200 == 0:
                print(f"bosch {i}/{len(meta)} {time.time() - t0:.0f}s", flush=True)
    info |= {"runs": len(meta), "seconds": round(time.time() - t0, 1)}
    (Path(out) / f"load_bosch{sfx}.json").write_text(json.dumps(info, indent=1))
    print(info)


def phm_cuts(dev):
    cutters, n = (P_DEV if dev else (P_CUTTERS, P_CUTS))
    return [(ct, k) for ct in cutters for k in range(1, n + 1)]


def _phm_one(args):
    import pandas as pd
    phm_dir, name, cutter, cut = args
    p = Path(phm_dir) / cutter / cutter / f"c_{cutter[1:]}_{cut:03d}.csv"
    b = p.read_bytes()
    x = pd.read_csv(io.BytesIO(b), header=None, dtype=np.float64, float_precision="round_trip").to_numpy()
    rec = {"file": str(p.relative_to(phm_dir)), "sha256": sha256(b), "rows": int(x.shape[0])}
    if x.ndim != 2 or x.shape[1] != 7 or not np.isfinite(x).all():
        return rec | {"rejected": f"shape {x.shape} or non-finite"}
    with connect() as c:
        for j, ch in enumerate(P_CHANNELS):
            rid = f"{cutter}-{cut:03d}|{ch}"
            store(c, name, rid, P_RATE, ch, P_UNITS[ch], {"cutter": cutter, "cut": cut},
                  np.ascontiguousarray(x[:, j]),
                  {"process": "phm2010-milling", "cutter": cutter, "cut": f"{cutter}/{cut:03d}"},
                  ["process", "cutter"], ["cut"])
    return rec


def load_phm(phm_dir, out, dev, workers=6):
    sfx = "_dev" if dev else ""
    name = coll("phm", dev)
    t0 = time.time()
    with connect() as c:
        info = init_collection(c, name)
    jobs = [(str(phm_dir), name, ct, k) for ct, k in phm_cuts(dev)]
    files = []
    with ProcessPoolExecutor(workers) as ex:
        for i, rec in enumerate(ex.map(_phm_one, jobs, chunksize=4)):
            files.append(rec)
            if i % 100 == 0:
                print(f"phm {i}/{len(jobs)} {time.time() - t0:.0f}s", flush=True)
    info |= {"cuts": len(files), "rejected": [f for f in files if "rejected" in f],
             "seconds": round(time.time() - t0, 1)}
    Path(out).mkdir(parents=True, exist_ok=True)
    (Path(out) / f"load_phm{sfx}.json").write_text(json.dumps(info, indent=1))
    (Path(out) / f"phm{sfx}_SHA256SUMS").write_text("".join(f"{f['sha256']}  {f['file']}\n" for f in files))
    print({k: v for k, v in info.items() if k != "rejected"}, len(info["rejected"]), "rejected")


# ------------------------------------------------------------------ queries
def setup(dataset):
    if dataset == "bosch":
        return B_CHOICES, B_PAIRS, {"split": "history"}
    return P_CHOICES, P_PAIRS, {}


def query_segments(c, name, dataset):
    """(segment_id, recording_id, recording metadata, context) of every query segment, in recording-id order."""
    rel = c.execute("SELECT segments FROM morlet_context_relations(%s)", (name,)).fetchone()[0]
    rows = c.execute(f"SELECT s.segment_id, s.recording_id, r.metadata, s.context FROM {rel} s "
                     f"JOIN {name} r ON r.recording_id=s.recording_id WHERE NOT s.stale "
                     f"ORDER BY s.recording_id").fetchall()
    if dataset == "bosch":
        rows = [r for r in rows if r[2]["split"] == "query"]
    return rows


def _neighbours(args):
    name, dataset, seg_ids = args
    choices, _, flt = setup(dataset)
    out = {}
    with connect() as c:
        for seg in seg_ids:
            out[seg] = {}
            for ch, keys in choices.items():
                rows = c.execute("SELECT recording_id, distance FROM morlet_history_neighbors(%s,%s,%s,%s::jsonb,"
                                 "'{}'::text[],%s,10000,false,%s)",
                                 (name, seg, keys, json.dumps(flt), TOP_K, list(SCORE_VALUES))).fetchall()
                out[seg][ch] = [[r, d] for r, d in rows]
    return out


def query(dataset, out, dev, workers=6):
    sfx = "_dev" if dev else ""
    name = coll(dataset, dev)
    t0 = time.time()
    with connect() as c:
        qs = query_segments(c, name, dataset)
        rules = c.execute("SELECT morlet_value_rules(%s)", (name,)).fetchone()[0]
        version = c.execute("SELECT morlet_version()").fetchone()[0]
        # metadata of every recording (for the outside share)
        meta = {r: m for r, m in c.execute(f"SELECT recording_id, metadata FROM {name}")}
    ids = [q[0] for q in qs]
    parts = [(name, dataset, ids[i::workers * 4]) for i in range(workers * 4)]
    res = {}
    with ThreadPoolExecutor(workers) as ex:
        for part in ex.map(_neighbours, parts):
            res.update(part)
    queries = [{"segment_id": s, "recording_id": r, "metadata": m, "neighbours": res[s]} for s, r, m, _ in qs]
    doc = {"dataset": dataset, "collection": name, "dev": dev, "version": version, "value_rules": rules,
           "top_k": TOP_K, "score_values": SCORE_VALUES, "metadata_filter": setup(dataset)[2],
           "choices": setup(dataset)[0], "seconds": round(time.time() - t0, 1),
           "recording_metadata": meta, "queries": queries}
    p = Path(out) / f"neighbours_{dataset}{sfx}.json.gz"
    with gzip.open(p, "wt") as f:
        json.dump(doc, f)
    print({"dataset": dataset, "queries": len(queries), "seconds": doc["seconds"], "value_rules": rules, "file": str(p)})


# ------------------------------------------------------------------ product check
def floors(hist_vals, names, rules):
    """Per value, the largest median treated as zero (morlet_core::values::rule_floors)."""
    in_units = {"mean", "std", "rms", "p75", "peak_abs"}
    level = float(np.median(hist_vals[:, names.index("rms")]))
    base = abs(level) * 1e-9 if np.isfinite(level) else 0.0
    out = []
    for i, nm in enumerate(names):
        f = base if nm in in_units else 0.0
        if rules == "simple-v2":
            u = np.unique(hist_vals[:, i][np.isfinite(hist_vals[:, i])])
            if len(u) >= 3:
                g = np.diff(u)
                g = g[g > 0]
                step = g.min()
                m = g / step
                if step > 0 and np.all(m <= 1e6) and np.all(np.abs(m - np.round(m)) <= 1e-6 * m):
                    f = max(f, 2.0 * step)
        out.append(f)
    return out


def expected_ranking(q, hist, names, rules):
    """hist: [(recording_id, vals)]. Ranking by the documented rule; ties by recording id."""
    V = np.array([v for _, v in hist], dtype=float)
    med = np.median(V, axis=0)
    fl = floors(V, names, rules)
    use = [names.index(v) for v in SCORE_VALUES if abs(med[names.index(v)]) > fl[names.index(v)]]
    d = np.sqrt(np.mean(((np.asarray(q)[use] - V[:, use]) / np.abs(med[use])) ** 2, axis=1))
    return sorted(zip([r for r, _ in hist], d.tolist()), key=lambda t: (t[1], t[0]))


def check(dataset, out, dev, per_dataset=5):
    sfx = "_dev" if dev else ""
    name = coll(dataset, dev)
    choices, _, flt = setup(dataset)
    report = []
    with connect() as c:
        rel = c.execute("SELECT segments, vals FROM morlet_context_relations(%s)", (name,)).fetchone()
        names = list(c.execute("SELECT morlet_simple_value_names()").fetchone()[0])
        rules = c.execute("SELECT morlet_value_rules(%s)", (name,)).fetchone()[0]
        allseg = c.execute(f"SELECT s.segment_id, s.recording_id, s.context, s.occurrence, r.metadata, r.channel, v.vals "
                           f"FROM {rel[0]} s JOIN {rel[1]} v ON v.segment_id=s.segment_id AND v.representation='simple-v1' "
                           f"JOIN {name} r ON r.recording_id=s.recording_id WHERE NOT s.stale").fetchall()
        qs = query_segments(c, name, dataset)
        runs = sorted({q[1].split("|")[0] for q in qs})[:per_dataset]
        for seg, rid, _m, ctx in qs:
            if rid.split("|")[0] not in runs:
                continue
            me = next(a for a in allseg if a[0] == seg)
            for ch, keys in choices.items():
                hist = [(a[1], a[6]) for a in allseg if a[0] != seg and a[5] == me[5] and a[3] != me[3]
                        and all(a[2].get(k) == ctx[k] for k in keys)
                        and all(a[4].get(k) == v for k, v in flt.items())]
                exp = expected_ranking(me[6], hist, names, rules)[:TOP_K]
                got = c.execute("SELECT recording_id, distance FROM morlet_history_neighbors(%s,%s,%s,%s::jsonb,"
                                "'{}'::text[],%s,10000,false,%s)",
                                (name, seg, keys, json.dumps(flt), TOP_K, list(SCORE_VALUES))).fetchall()
                worst = max((abs(g[1] - e[1]) / max(abs(e[1]), 1e-300) for g, e in zip(got, exp)), default=0.0)
                same = [g[0] for g in got] == [e[0] for e in exp]
                report.append({"query": rid, "choice": ch, "history": len(hist), "returned": len(got),
                               "same_ids_and_order": same, "max_relative_distance_error": worst,
                               "pass": bool(same and worst <= 1e-9)})
    doc = {"dataset": dataset, "value_rules": rules, "rows": len(report), "passed": sum(r["pass"] for r in report),
           "report": report}
    (Path(out) / f"check_{dataset}{sfx}.json").write_text(json.dumps(doc, indent=1))
    print({k: v for k, v in doc.items() if k != "report"})


# ------------------------------------------------------------------ analysis (pure functions, tested)
def overlap(a, b, k):
    """a, b: recording ids in returned order. Overlap@k, Jaccard@k, containment@k and the list lengths."""
    A, B = set(a[:k]), set(b[:k])
    inter, union = len(A & B), len(A | B)
    return {"overlap": inter / k, "jaccard": inter / union if union else 1.0,
            "containment": inter / min(len(A), len(B)) if min(len(A), len(B)) else None,
            "len_a": len(A), "len_b": len(B)}


def tie_at(dists, k):
    """True if the k-th and (k+1)-th distances are exactly equal (membership decided by the tie-break)."""
    return len(dists) > k and dists[k - 1] == dists[k]


def outside_share(ids, k, query_meta, meta_of, keys):
    """Share of the first k ids whose metadata differs from the query's on any of `keys` (None if empty)."""
    top = ids[:k]
    if not top or not keys:
        return None
    return sum(any(meta_of[r][key] != query_meta[key] for key in keys) for r in top) / len(top)


def cluster_bootstrap(values, clusters, resamples=RESAMPLES, seed=SEED):
    """Percentile 95% interval of the mean of `values`, resampling whole clusters with replacement."""
    groups = {}
    for v, g in zip(values, clusters):
        groups.setdefault(g, []).append(v)
    keys = sorted(groups)
    sums = np.array([sum(groups[g]) for g in keys])
    counts = np.array([len(groups[g]) for g in keys])
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(keys), size=(resamples, len(keys)))
    means = sums[idx].sum(axis=1) / counts[idx].sum(axis=1)
    return [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]


def verdict(lo, hi):
    if hi < 0.5:
        return "most neighbours change"
    if hi < 0.8:
        return "neighbours change"
    if lo >= 0.9:
        return "little change"
    return "intermediate"


def describe(x):
    x = np.asarray(x, float)
    return {"mean": float(x.mean()), "median": float(np.median(x)), "p10": float(np.percentile(x, 10)),
            "p90": float(np.percentile(x, 90)), "n": int(len(x))}


# Context keys of each choice, as recording metadata keys, for the outside share.
META_KEYS = {"site": None, "process": None, "machine": "machine", "operation": "operation", "cutter": "cutter"}


def analyse_dataset(doc):
    dataset = doc["dataset"]
    choices, pairs, _ = setup(dataset)
    meta_of = doc["recording_metadata"]
    unit_key = (lambda m: m["run"]) if dataset == "bosch" else (lambda m: f"{m['cutter']}/{m['cut']:03d}")
    cluster_key = (lambda m: f"{m['machine']}/{m['operation']}") if dataset == "bosch" else (lambda m: m["cutter"])
    channel_of = lambda rid: rid.split("|")[1]  # noqa: E731
    short, ties = {}, {}
    for ch in choices:
        for k in KS:
            lens = [len(q["neighbours"][ch][:k]) for q in doc["queries"]]
            short[f"{ch}@{k}"] = {"short_lists": sum(n < k for n in lens), "lists": len(lens),
                                  "min": min(lens), "median": float(np.median(lens))}
            ties[f"{ch}@{k}"] = sum(tie_at([d for _, d in q["neighbours"][ch]], k) for q in doc["queries"])
    out = {"short_lists": short, "ties_at_boundary": ties, "pairs": {}}
    for a, b in pairs:
        extra = [META_KEYS[x] for x in choices[b] if x not in choices[a] and META_KEYS[x]]
        nested = set(choices[a]) <= set(choices[b])
        for k in KS:
            per_q = []
            for q in doc["queries"]:
                ia = [r for r, _ in q["neighbours"][a]]
                ib = [r for r, _ in q["neighbours"][b]]
                o = overlap(ia, ib, k)
                o["outside"] = outside_share(ia, k, q["metadata"], meta_of, extra) if nested else None
                o["unit"], o["cluster"], o["channel"] = unit_key(q["metadata"]), cluster_key(q["metadata"]), \
                    channel_of(q["recording_id"])
                per_q.append(o)
            units = {}
            for o in per_q:
                units.setdefault(o["unit"], []).append(o)
            unit_rows = [{"unit": u, "cluster": rows[0]["cluster"],
                          **{m: float(np.mean([r[m] for r in rows])) for m in ("overlap", "jaccard")},
                          "containment": (float(np.mean([r["containment"] for r in rows]))
                                          if all(r["containment"] is not None for r in rows) else None),
                          "outside": (float(np.mean([r["outside"] for r in rows]))
                                      if all(r["outside"] is not None for r in rows) else None),
                          "full": all(r["len_a"] == k and r["len_b"] == k for r in rows)}
                         for u, rows in sorted(units.items())]
            ov = [r["overlap"] for r in unit_rows]
            res = {"overlap": describe(ov), "jaccard": describe([r["jaccard"] for r in unit_rows]),
                   "containment": describe([r["containment"] for r in unit_rows if r["containment"] is not None]),
                   "outside_share_of_wider": (describe([r["outside"] for r in unit_rows])
                                              if nested and extra else None),
                   "overlap_full_lists_only": (describe([r["overlap"] for r in unit_rows if r["full"]])
                                               if any(r["full"] for r in unit_rows) else None),
                   "per_channel_overlap": {c: float(np.mean([o["overlap"] for o in per_q if o["channel"] == c]))
                                           for c in sorted({o["channel"] for o in per_q})}}
            if dataset == "bosch":
                lo, hi = cluster_bootstrap(ov, [r["cluster"] for r in unit_rows])
                res["ci95"] = [lo, hi]
                res["clusters"] = len({r["cluster"] for r in unit_rows})
            else:
                per = {c: float(np.mean([r["overlap"] for r in unit_rows if r["cluster"] == c]))
                       for c in sorted({r["cluster"] for r in unit_rows})}
                res["per_cutter_overlap"] = per
                lo, hi = min(per.values()), max(per.values())
                res["range_over_cutters"] = [lo, hi]
            res["verdict"] = verdict(lo, hi)
            out["pairs"][f"{a}-{b}@{k}"] = res
    adj = [v["verdict"] for key, v in out["pairs"].items() if key.endswith("@10")]
    allv = [v["verdict"] for v in out["pairs"].values()]
    if any(v in ("neighbours change", "most neighbours change") for v in adj):
        out["context_choice"] = "matters"
    elif all(v == "little change" for v in allv):
        out["context_choice"] = "does not matter"
    else:
        out["context_choice"] = "mixed"
    return out


def analyse(out, results, dev):
    sfx = "_dev" if dev else ""
    res = {"dev": dev}
    for dataset in ("bosch", "phm"):
        p = Path(out) / f"neighbours_{dataset}{sfx}.json.gz"
        if not p.exists():
            continue
        with gzip.open(p, "rt") as f:
            doc = json.load(f)
        res[dataset] = {"version": doc["version"], "value_rules": doc["value_rules"], "queries": len(doc["queries"]),
                        "query_seconds": doc["seconds"], "neighbours_sha256": sha256(p.read_bytes()),
                        **analyse_dataset(doc)}
    Path(results).mkdir(parents=True, exist_ok=True)
    (Path(results) / f"c8{sfx}.json").write_text(json.dumps(res, indent=1))
    for dataset in ("bosch", "phm"):
        if dataset not in res:
            continue
        print(f"\n{dataset}: {res[dataset]['queries']} query segments, rules {res[dataset]['value_rules']}, "
              f"context choice {res[dataset]['context_choice']}")
        for key, v in res[dataset]["pairs"].items():
            iv = v.get("ci95") or v.get("range_over_cutters")
            print(f"  {key:10s} overlap {v['overlap']['mean']:.3f} [{iv[0]:.3f}, {iv[1]:.3f}] "
                  f"jaccard {v['jaccard']['mean']:.3f} containment {v['containment']['mean']:.3f} "
                  f"outside {'' if not v['outside_share_of_wider'] else round(v['outside_share_of_wider']['mean'], 3)}"
                  f"  {v['verdict']}")


def main():
    a = [x for x in sys.argv[1:] if x != "--dev"]
    dev = "--dev" in sys.argv
    cmd = a[0]
    if cmd == "export-bosch":
        export_bosch(Path(a[1]), Path(a[2]), Path(a[3]), dev)
    elif cmd == "load-bosch":
        load_bosch(Path(a[1]), dev)
    elif cmd == "load-phm":
        load_phm(Path(a[1]), Path(a[2]), dev)
    elif cmd == "query":
        query(a[1], Path(a[2]), dev)
    elif cmd == "check":
        check(a[1], Path(a[2]), dev)
    elif cmd == "analyse":
        analyse(Path(a[1]), Path(a[2]), dev)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
