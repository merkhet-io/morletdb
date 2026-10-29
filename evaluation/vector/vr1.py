#!/usr/bin/env python3
"""VR1 (PROTOCOL.md): pg_morlet features and text vectors side by side in pgvector.

  vr1.py load PTBXL_DIR [MAX_FOLD]   read folds 1-8 (or 1..MAX_FOLD, for harness development
                                    in a separate database), build vr1.ecg and its HNSW indexes
  vr1.py check OUT          run C1-C6 and the reported measurements; write OUT/vr1.json

Seismic environment (numpy, psycopg), run with python -I. DSN from MORLET_DSN.
"""
import csv
import hashlib
import json
import math
import os
import platform
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import psycopg

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_vector_check")
FOLDS = range(1, 9)            # folds 9 and 10 are never read
RATE = 100.0
LEAD = "II"
EXTRACTOR = "morlet-energy-lowrate-v1"
DIM = 256
TEXT_MODEL = "hash-char35-tfidf-256-v1"
QUERIES, SEED, K, FUSE_DEPTH, RRF_K = 200, 7, 10, 50, 60
EF_SEARCH = (40, 100, 200)
READER, NOGRANT = "vr1_reader", "vr1_nogrant"
READER_FOLDS = 4               # the reader's policy: strat_fold <= 4


# -- data --------------------------------------------------------------------------------
def checksums(root: Path):
    out = {}
    for line in (root / "SHA256SUMS.txt").read_text().splitlines():
        digest, name = line.split(maxsplit=1)
        out[name.strip()] = digest
    return out


def verified(root: Path, sums, name):
    data = (root / name).read_bytes()
    if hashlib.sha256(data).hexdigest() != sums[name]:
        raise SystemExit(f"{name}: SHA-256 mismatch")
    return data


def lead_ii(root: Path, sums, stem):
    """Lead II in mV from a PTB-XL 100 Hz record (format 16, interleaved int16)."""
    header = verified(root, sums, stem + ".hea").decode("ascii").splitlines()
    nsig, rate, n = header[0].split()[1:4]
    nsig, n = int(nsig), int(n)
    if float(rate) != RATE or n != 1000:
        raise SystemExit(f"{stem}: unexpected rate {rate} or length {n}")
    lines = [h.split() for h in header[1:1 + nsig]]
    j = [ln[-1] for ln in lines].index(LEAD)
    fmt, gain, init = lines[j][1], lines[j][2], int(lines[j][5])
    if fmt != "16" or not gain.startswith("1000.0(0)/mV"):
        raise SystemExit(f"{stem}: unexpected format {fmt} {gain}")
    raw = np.frombuffer(verified(root, sums, stem + ".dat"), dtype="<i2").reshape(n, nsig)
    if int(raw[0, j]) != init:
        raise SystemExit(f"{stem}: first sample {raw[0, j]} != header {init}")
    return raw[:, j].astype(np.float64) / 1000.0


def grams(text):
    t = f" {text.lower().strip()} "
    return [t[i:i + n] for n in (3, 4, 5) for i in range(len(t) - n + 1)]


def bucket(gram):
    h = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
    v = int.from_bytes(h, "little")
    return v % DIM, 1.0 if (v >> 63) & 1 else -1.0


def text_vectors(reports):
    """Deterministic lexical stand-in for an embedding model (PROTOCOL.md)."""
    counts = [Counter(grams(r)) for r in reports]
    df = Counter(g for c in counts for g in c)
    n = len(reports)
    idf = {g: math.log((1 + n) / (1 + d)) + 1 for g, d in df.items()}
    out = np.zeros((n, DIM))
    for i, c in enumerate(counts):
        for g, tf in c.items():
            b, s = bucket(g)
            out[i, b] += s * (1 + math.log(tf)) * idf[g]
        norm = np.linalg.norm(out[i])
        if norm > 0:
            out[i] /= norm
    return out


def vec(x):
    return "[" + ",".join(repr(float(v)) for v in x) + "]"


# -- load --------------------------------------------------------------------------------
SCHEMA = f"""
DROP SCHEMA IF EXISTS vr1 CASCADE;
CREATE SCHEMA vr1;
CREATE TABLE vr1.ecg (
  ecg_id bigint PRIMARY KEY,
  patient_id bigint NOT NULL,
  strat_fold integer NOT NULL,
  site integer,
  device text,
  report text NOT NULL,
  sample_rate_hz float8 NOT NULL,
  samples float8[] NOT NULL,
  features float8[] GENERATED ALWAYS AS (public.morlet_features(
    public.morlet_normalize(samples, 'zscore'), sample_rate_hz, '{EXTRACTOR}')) STORED,
  wave_vec vector(15) GENERATED ALWAYS AS (public.morlet_features(
    public.morlet_normalize(samples, 'zscore'), sample_rate_hz, '{EXTRACTOR}')::vector(15)) STORED,
  extractor text GENERATED ALWAYS AS ('{EXTRACTOR}') STORED,
  text_vec vector({DIM}) NOT NULL,
  text_model text NOT NULL
);
"""

POLICY = f"""
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{READER}') THEN CREATE ROLE {READER}; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{NOGRANT}') THEN CREATE ROLE {NOGRANT}; END IF;
END $$;
GRANT USAGE ON SCHEMA vr1 TO {READER}, {NOGRANT};
GRANT SELECT ON vr1.ecg TO {READER};
ALTER TABLE vr1.ecg ENABLE ROW LEVEL SECURITY;
CREATE POLICY reader_folds ON vr1.ecg FOR SELECT TO {READER} USING (strat_fold <= {READER_FOLDS});
"""


def cmd_load(root, max_fold=8):
    root = Path(root)
    folds = [f for f in FOLDS if f <= int(max_fold)]
    sums = checksums(root)
    with open(root / "ptbxl_database.csv", newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if int(r["strat_fold"]) in folds]
    reports = [r["report"] for r in rows]
    tv = text_vectors(reports)
    print(len(rows), "records;", sum(1 for r in reports if not r.strip()), "empty reports", flush=True)
    with psycopg.connect(DSN, autocommit=True) as con:
        con.execute(SCHEMA)
        t0 = time.perf_counter()
        with con.cursor().copy("COPY vr1.ecg (ecg_id, patient_id, strat_fold, site, device, report, "
                               "sample_rate_hz, samples, text_vec, text_model) FROM STDIN") as cp:
            for r, t in zip(rows, tv):
                x = lead_ii(root, sums, r["filename_lr"])
                cp.write_row((int(r["ecg_id"]), int(float(r["patient_id"])), int(r["strat_fold"]),
                              int(float(r["site"])) if r["site"] else None, r["device"] or None,
                              r["report"], RATE, x.tolist(), vec(t), TEXT_MODEL))
        load_s = time.perf_counter() - t0
        build = {}
        for name, col, ops in (("wave", "wave_vec", "vector_l2_ops"), ("text", "text_vec", "vector_cosine_ops")):
            t0 = time.perf_counter()
            con.execute(f"CREATE INDEX ecg_{name}_hnsw ON vr1.ecg USING hnsw ({col} {ops}) "
                        "WITH (m = 16, ef_construction = 64)")
            build[name] = {"seconds": time.perf_counter() - t0,
                           "bytes": con.execute(f"SELECT pg_relation_size('vr1.ecg_{name}_hnsw')").fetchone()[0]}
        con.execute(POLICY)
        con.execute("ANALYZE vr1.ecg")
        meta = {"records": len(rows), "empty_reports": sum(1 for r in reports if not r.strip()),
                "load_seconds": load_s, "index": build,
                "table_bytes": con.execute("SELECT pg_total_relation_size('vr1.ecg')").fetchone()[0]}
    (Path(__file__).resolve().parent / "results").mkdir(exist_ok=True)
    print(json.dumps(meta, indent=1))
    return meta


# -- queries -----------------------------------------------------------------------------
ARMS = {"wave": ("wave_vec", "<->"), "text": ("text_vec", "<=>")}


def exact(cur, arm, q, k=K):
    col, op = ARMS[arm]
    cur.execute("SET LOCAL enable_indexscan = off")
    cur.execute(f"SELECT ecg_id, {col} {op} %(v)s::vector AS d, strat_fold FROM vr1.ecg "
                "WHERE ecg_id <> %(id)s AND patient_id <> %(p)s ORDER BY d, ecg_id LIMIT %(k)s",
                {"v": q[arm], "id": q["id"], "p": q["patient"], "k": k})
    return cur.fetchall()


def approximate(cur, arm, q, ef, iterative="off", k=K):
    col, op = ARMS[arm]
    cur.execute("SET LOCAL enable_indexscan = on")
    cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef)}")
    cur.execute(f"SET LOCAL hnsw.iterative_scan = {iterative}")
    cur.execute(f"SELECT ecg_id, {col} {op} %(v)s::vector AS d, strat_fold FROM vr1.ecg "
                f"WHERE ecg_id <> %(id)s AND patient_id <> %(p)s ORDER BY {col} {op} %(v)s::vector LIMIT %(k)s",
                {"v": q[arm], "id": q["id"], "p": q["patient"], "k": k})
    return cur.fetchall()


def uses_index(cur, arm, q, ef, iterative="off"):
    col, op = ARMS[arm]
    cur.execute("SET LOCAL enable_indexscan = on")
    cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef)}")
    cur.execute(f"SET LOCAL hnsw.iterative_scan = {iterative}")
    cur.execute(f"EXPLAIN SELECT ecg_id FROM vr1.ecg WHERE ecg_id <> %(id)s AND patient_id <> %(p)s "
                f"ORDER BY {col} {op} %(v)s::vector LIMIT %(k)s",
                {"v": q[arm], "id": q["id"], "p": q["patient"], "k": K})
    return any(f"ecg_{arm}_hnsw" in r[0] for r in cur.fetchall())


FUSED = f"""
WITH w AS (
  SELECT ecg_id, row_number() OVER (ORDER BY d, ecg_id) AS r FROM (
    SELECT ecg_id, wave_vec <-> %(wv)s::vector AS d FROM vr1.ecg
    WHERE ecg_id <> %(id)s AND patient_id <> %(p)s
    ORDER BY wave_vec <-> %(wv)s::vector LIMIT {FUSE_DEPTH}) s),
t AS (
  SELECT ecg_id, row_number() OVER (ORDER BY d, ecg_id) AS r FROM (
    SELECT ecg_id, text_vec <=> %(tv)s::vector AS d FROM vr1.ecg
    WHERE ecg_id <> %(id)s AND patient_id <> %(p)s
    ORDER BY text_vec <=> %(tv)s::vector LIMIT {FUSE_DEPTH}) s),
f AS (
  SELECT ecg_id, w.r AS wave_rank, t.r AS text_rank,
         coalesce(1.0 / ({RRF_K} + w.r), 0) + coalesce(1.0 / ({RRF_K} + t.r), 0) AS score
  FROM w FULL JOIN t USING (ecg_id))
SELECT f.ecg_id, f.wave_rank, f.text_rank, f.score, e.strat_fold
FROM f JOIN vr1.ecg e USING (ecg_id)
ORDER BY f.score DESC, f.ecg_id LIMIT {K}
"""


def fused(cur, q, ef=100, iterative="relaxed_order"):
    cur.execute(f"SET LOCAL hnsw.ef_search = {int(ef)}")
    cur.execute(f"SET LOCAL hnsw.iterative_scan = {iterative}")
    cur.execute(FUSED, {"wv": q["wave"], "tv": q["text"], "id": q["id"], "p": q["patient"]})
    return cur.fetchall()


def ranks(cur, arm, q, ef=100, iterative="relaxed_order"):
    rows = approximate(cur, arm, q, ef, iterative, k=FUSE_DEPTH)
    rows.sort(key=lambda r: (r[1], r[0]))
    return {r[0]: i + 1 for i, r in enumerate(rows)}


def overlap(a, b):
    """Plain top-k set overlap (C1, and how different two arms' rankings are)."""
    ids = {r[0] for r in b}
    return len(ids & {r[0] for r in a}) / len(ids) if ids else float("nan")


def recall(got, ref):
    """Tie-aware recall@k (change after freeze 1): a returned row counts if its distance is no larger
    than the exact k-th distance. Identical reports make many distances equal, so the exact top-k set
    is not unique."""
    if not ref:
        return float("nan")
    kth = ref[-1][1]
    hits = sum(1 for r in got if r[1] <= kth + 1e-6 * abs(kth) + 1e-12)
    return min(hits, len(ref)) / len(ref)


def as_role(con, role):
    con.execute(f"SET ROLE {role}")


def timed(fn, *args):
    t0 = time.perf_counter()
    fn(*args)
    return (time.perf_counter() - t0) * 1000


def pct(xs):
    return {"median_ms": float(np.median(xs)), "p95_ms": float(np.percentile(xs, 95))}


# -- check -------------------------------------------------------------------------------
def pick_queries(cur, ids):
    cur.execute("SELECT ecg_id, patient_id, wave_vec::text, text_vec::text, features, report "
                "FROM vr1.ecg WHERE ecg_id = ANY(%s)", (ids,))
    by = {r[0]: {"id": r[0], "patient": r[1], "wave": r[2], "text": r[3], "features": r[4],
                 "empty_report": not r[5].strip()} for r in cur.fetchall()}
    return [by[i] for i in ids]


def c1(cur, qs):
    overlaps, rel = [], []
    for q in qs:
        with cur.connection.transaction():
            pv = exact(cur, "wave", q)
            cur.execute("SELECT ecg_id, public.morlet_feature_vector_distance(features, %(f)s) AS d FROM vr1.ecg "
                        "WHERE ecg_id <> %(id)s AND patient_id <> %(p)s ORDER BY d, ecg_id LIMIT %(k)s",
                        {"f": q["features"], "id": q["id"], "p": q["patient"], "k": K})
            f8 = cur.fetchall()
        overlaps.append(overlap(pv, f8))
        f8d = dict(f8)
        for i, d, _ in pv:
            if i in f8d and f8d[i] > 0:
                rel.append(abs(d / math.sqrt(15) - f8d[i]) / f8d[i])
    res = {"mean_overlap": float(np.mean(overlaps)), "min_overlap": float(np.min(overlaps)),
           "max_relative_distance_error": float(np.max(rel)), "median_relative_distance_error": float(np.median(rel))}
    res["pass"] = res["mean_overlap"] >= 0.99 and res["max_relative_distance_error"] <= 1e-5
    return res


def c2(cur, qs, refs):
    res = {}
    for arm in ARMS:
        res[arm] = {}
        for ef in EF_SEARCH:
            rs, short = [], 0
            for q in qs:
                if arm == "text" and q["empty_report"]:
                    continue
                with cur.connection.transaction():
                    got = approximate(cur, arm, q, ef)
                short += len(got) < K
                rs.append(recall(got, refs[arm][q["id"]]))
            res[arm][f"ef_search={ef}"] = {"recall_at_10": float(np.mean(rs)), "queries": len(rs),
                                           "fewer_than_10_rows": short}
        with cur.connection.transaction():
            res[arm]["plan_uses_hnsw"] = uses_index(cur, arm, qs[0], 40)
    res["pass"] = all(any(res[a][f"ef_search={ef}"]["recall_at_10"] >= 0.95 for ef in EF_SEARCH)
                      and res[a]["plan_uses_hnsw"] for a in ARMS)
    return res


def c3(con, qs):
    cur = con.cursor()
    as_role(con, READER)
    res = {}
    try:
        for arm in ARMS:
            res[arm] = {}
            for it in ("off", "relaxed_order"):
                rs, short, outside = [], 0, 0
                for q in qs:
                    if arm == "text" and q["empty_report"]:
                        continue
                    with con.transaction():
                        ref = exact(cur, arm, q)
                        got = approximate(cur, arm, q, 100, it)
                    outside += sum(1 for r in ref + got if r[2] > READER_FOLDS)
                    short += len(got) < K
                    rs.append(recall(got, ref))
                res[arm][it] = {"recall_at_10": float(np.mean(rs)), "fewer_than_10_rows": short,
                                "rows_outside_policy": outside, "queries": len(rs)}
            with con.transaction():
                res[arm]["plan_uses_hnsw"] = uses_index(cur, arm, qs[0], 100, "relaxed_order")
    finally:
        con.execute("RESET ROLE")
    res["pass"] = all(res[a]["relaxed_order"]["recall_at_10"] >= 0.95
                      and res[a]["relaxed_order"]["fewer_than_10_rows"] == 0 for a in ARMS)
    return res


def c4(con, qs):
    cur = con.cursor()
    refused = {}
    as_role(con, NOGRANT)
    try:
        for name, fn in (("wave", lambda: approximate(cur, "wave", qs[0], 100)),
                         ("text", lambda: approximate(cur, "text", qs[0], 100)),
                         ("fused", lambda: fused(cur, qs[0]))):
            try:
                with con.transaction():
                    fn()
                refused[name] = False
            except psycopg.errors.InsufficientPrivilege:
                refused[name] = True
    finally:
        con.execute("RESET ROLE")
    as_role(con, READER)
    outside = {"wave": 0, "text": 0, "fused": 0}
    seen = 0
    try:
        for q in qs:
            with con.transaction():
                for arm in ARMS:
                    rows = approximate(cur, arm, q, 100, "relaxed_order")
                    outside[arm] += sum(1 for r in rows if r[2] > READER_FOLDS)
                    seen += len(rows)
                rows = fused(cur, q)
                outside["fused"] += sum(1 for r in rows if r[4] > READER_FOLDS)
                seen += len(rows)
    finally:
        con.execute("RESET ROLE")
    with con.transaction():
        owner_folds = con.execute("SELECT max(strat_fold) FROM vr1.ecg").fetchone()[0]
    res = {"refused_without_grant": refused, "reader_rows_outside_policy": outside,
           "reader_rows_seen": seen, "owner_max_fold": owner_folds}
    res["pass"] = all(refused.values()) and not any(outside.values()) and owner_folds > READER_FOLDS
    return res


def c5(con):
    t = np.arange(1000) / RATE
    x = (np.sin(2 * np.pi * 1.2 * t) + 0.1 * np.sin(2 * np.pi * 17 * t)).tolist()
    y = (np.sin(2 * np.pi * 2.5 * t) + 0.3 * np.sin(2 * np.pi * 9 * t)).tolist()
    res = {}
    try:
        with con.transaction(force_rollback=True):
            con.execute("INSERT INTO vr1.ecg (ecg_id, patient_id, strat_fold, report, sample_rate_hz, samples, "
                        "text_vec, text_model) VALUES (-1, -1, 1, '', %s, %s, %s::vector, %s)",
                        (RATE, x, vec(np.zeros(DIM)), TEXT_MODEL))
            for step, s in (("insert", x), ("update", y)):
                if step == "update":
                    con.execute("UPDATE vr1.ecg SET samples = %s WHERE ecg_id = -1", (s,))
                stored, ext = con.execute("SELECT wave_vec::real[], extractor FROM vr1.ecg WHERE ecg_id = -1").fetchone()
                ref = con.execute("SELECT public.morlet_features(public.morlet_normalize(%s, 'zscore'), %s, %s)",
                                  (s, RATE, EXTRACTOR)).fetchone()[0]
                err = max(abs(a - b) / max(abs(b), 1e-12) for a, b in zip(stored, ref))
                res[step] = {"max_relative_error": err, "extractor": ext}
    except Exception as e:  # report, do not hide
        res["error"] = repr(e)
    res["pass"] = ("error" not in res and all(res[s]["max_relative_error"] <= 1e-6 and res[s]["extractor"] == EXTRACTOR
                                              for s in ("insert", "update")))
    return res


def c6(con, qs):
    cur = con.cursor()
    runs = []
    for _ in range(2):
        out = []
        for q in qs:
            with con.transaction():
                out.append(fused(cur, q))
        runs.append(out)
    identical = runs[0] == runs[1]
    mismatched = 0
    for q, rows in zip(qs, runs[0]):
        with con.transaction():
            wr, tr = ranks(cur, "wave", q), ranks(cur, "text", q)
        for ecg_id, w, t, _, _ in rows:
            mismatched += (w != wr.get(ecg_id)) + (t != tr.get(ecg_id))
    res = {"queries": len(qs), "identical_across_runs": identical, "rank_mismatches": mismatched}
    res["pass"] = identical and mismatched == 0
    return res


def latency(con, qs):
    cur = con.cursor()
    plans = {
        "wave exact": lambda q: exact(cur, "wave", q),
        "wave hnsw": lambda q: approximate(cur, "wave", q, 100),
        "text exact": lambda q: exact(cur, "text", q),
        "text hnsw": lambda q: approximate(cur, "text", q, 100),
        "fused hnsw": lambda q: fused(cur, q),
        "wave float8 scorer": lambda q: cur.execute(
            "SELECT ecg_id FROM vr1.ecg WHERE ecg_id <> %(id)s AND patient_id <> %(p)s "
            "ORDER BY public.morlet_feature_vector_distance(features, %(f)s), ecg_id LIMIT 10",
            {"f": q["features"], "id": q["id"], "p": q["patient"]}).fetchall(),
    }
    out = {}
    for name, fn in plans.items():
        for q in qs[:20]:          # warm-up
            with con.transaction():
                fn(q)
        xs = []
        for q in qs:
            with con.transaction():
                xs.append(timed(fn, q))
        out[name] = pct(xs)
    return out


def cmd_check(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    with psycopg.connect(DSN, autocommit=True) as con:
        cur = con.cursor()
        ids = [r[0] for r in cur.execute("SELECT ecg_id FROM vr1.ecg ORDER BY ecg_id").fetchall()]
        sample = sorted(np.random.default_rng(SEED).choice(ids, QUERIES, replace=False).tolist())
        qs = pick_queries(cur, sample)
        refs = {}
        for arm in ARMS:
            refs[arm] = {}
            for q in qs:
                with con.transaction():
                    refs[arm][q["id"]] = exact(cur, arm, q)
        arm_overlap = [overlap(refs["wave"][q["id"]], refs["text"][q["id"]]) for q in qs if not q["empty_report"]]
        res = {
            "protocol": "VR1",
            "versions": {
                "postgres": con.execute("SHOW server_version").fetchone()[0],
                "extensions": dict(con.execute("SELECT extname, extversion FROM pg_extension "
                                               "WHERE extname IN ('pg_morlet','vector')").fetchall()),
            },
            "machine": {"platform": platform.platform(), "machine": platform.machine(),
                        "processor": platform.processor()},
            "rows": con.execute("SELECT count(*) FROM vr1.ecg").fetchone()[0],
            "queries": len(qs),
            "empty_report_queries": sum(q["empty_report"] for q in qs),
            "C1 float4 fidelity": c1(cur, qs),
            "C2 hnsw recall": c2(cur, qs, refs),
            "C3 filtered under RLS": c3(con, qs),
            "C4 access": c4(con, qs),
            "C5 generated columns": c5(con),
            "C6 fusion determinism": c6(con, qs[:20]),
            "reported": {
                "latency": latency(con, qs),
                "wave_text_top10_overlap": {"mean": float(np.mean(arm_overlap)), "max": float(np.max(arm_overlap))},
            },
        }
    res["pass"] = {k: v["pass"] for k, v in res.items() if isinstance(v, dict) and "pass" in v}
    (out / "vr1.json").write_text(json.dumps(res, indent=1, default=str))
    print(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    {"load": lambda: cmd_load(*sys.argv[2:4]), "check": lambda: cmd_check(sys.argv[2])}[sys.argv[1]]()
