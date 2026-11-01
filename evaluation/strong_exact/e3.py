"""E3: optimized exact baseline against PostgreSQL exact and probe search (PROTOCOL.md).

Thread count for the matrix arms is fixed per process before numpy loads:
  VECLIB_MAXIMUM_THREADS=18 .venv-seismic/bin/python -I evaluation/strong_exact/e3.py gate out.json <68594d7 checkout>
  VECLIB_MAXIMUM_THREADS=18 .venv-seismic/bin/python -I evaluation/strong_exact/e3.py run  out.json <68594d7 checkout> full
  VECLIB_MAXIMUM_THREADS=4  .venv-seismic/bin/python -I evaluation/strong_exact/e3.py run  out.json <68594d7 checkout> matched
"""
import json
import os
import random
import resource
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "consolidated"))
import e1  # noqa: E402  (E1's arm code, environment record and helpers)
import probes as P  # noqa: E402

DB, QUERIES = "morlet_r2", HERE.parent / "bounded_lag/dev_queries.json"
COLL, ENV, W, L, N_ENV, TOP, M0 = "public.r2", "public.r2_envelopes", 4096, 30, 410, 10, 200
LAGS = P.GRIDS["G61"]
BLOCK = 32768
U32 = 2.0 ** -24
GAMMA = N_ENV * U32 / (1 - N_ENV * U32)
C32 = GAMMA + 3 * U32          # per-lag float32 error factor on |s|.|g| (inputs rounded once each, any order)
TIMED = ["X-PG", "P-PG", "X-MAT64", "X-MAT32R"]


# ---------- materialization ----------

def decode_all(blobs):
    """morlet_core::envelope::decode for every row: binary16 -> f64, then z-score with sequential sums."""
    raw = np.frombuffer(b"".join(bytes(b)[1:] for b in blobs), dtype="<f2").reshape(len(blobs), N_ENV)
    v = raw.astype(np.float64)
    zero = np.full(len(blobs), -0.0)
    s = zero.copy()
    for j in range(N_ENV):
        s = s + v[:, j]
    mean = s / N_ENV
    ss = zero.copy()
    for j in range(N_ENV):
        d = v[:, j] - mean
        ss = ss + d * d
    sd = np.sqrt(ss / N_ENV)
    sd = np.where(sd > 0, sd, 1.0)
    return (v - mean[:, None]) / sd[:, None]


def materialize(conn):
    t0 = time.perf_counter()
    rows = conn.execute(f"""SELECT e.recording_id, e.start_sample, e.envelope, r.sample_rate_hz, r.channel, r.unit
        FROM {ENV} e JOIN {COLL} r ON r.recording_id = e.recording_id WHERE e.window_size = {W}""").fetchall()
    t_read = time.perf_counter() - t0
    order = [r for (r,) in conn.execute(f"SELECT recording_id FROM {COLL} ORDER BY recording_id").fetchall()]
    rank = {r: i for i, r in enumerate(order)}           # recording_id order as PostgreSQL sorts it here
    rid = np.array([rank[r[0]] for r in rows], dtype=np.int64)
    start = np.array([int(r[1]) for r in rows], dtype=np.int64)
    attrs = sorted({(r[3], r[4], r[5]) for r in rows})
    attr = np.array([attrs.index((r[3], r[4], r[5])) for r in rows], dtype=np.int64)
    t1 = time.perf_counter()
    G64 = np.ascontiguousarray(decode_all([r[2] for r in rows]))
    G32 = G64.astype(np.float32)
    gnorm = np.sqrt((G64 * G64).sum(axis=1))
    t_build = time.perf_counter() - t1
    gal = {"rid": rid, "start": start, "attr": attr, "attrs": attrs, "rank": rank, "names": order,
           "G64": G64, "G32": G32, "gnorm": gnorm, "blobs": [r[2] for r in rows]}
    info = {"windows": len(rows), "read_s": t_read, "decode_build_s": t_build,
            "bytes_G64": G64.nbytes, "bytes_G32": G32.nbytes, "layout": "C-contiguous rows x 410",
            "block_rows": BLOCK, "maxrss_bytes_after": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    return gal, info


# ---------- scoring ----------

def qmatrix(q, dtype):
    return np.ascontiguousarray(P.probe_matrix(q, LAGS).astype(dtype))        # 410 x 61, morlet_envelope_probe


def kernel(G, Q):
    mx = np.empty(G.shape[0], dtype=np.float64)
    # Accelerate's GEMM raises spurious floating-point flags on finite inputs (numpy then warns); results are
    # finite and equal to einsum. The gate checks finiteness explicitly.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        for b in range(0, G.shape[0], BLOCK):
            mx[b:b + BLOCK] = (G[b:b + BLOCK] @ Q).max(axis=1)
    return 1.0 - mx / N_ENV


def admissible(gal, src):
    """Eligible (same rate, channel, unit; filter '{}') and not overlapping the source interval."""
    rid, s = src
    a = gal["attr"] == gal["src_attr"][rid]
    same = gal["rid"] == gal["rank"][rid]
    return a & ~(same & (gal["start"] < s + W) & (gal["start"] + W > s))


def ordered(idx, d, gal, m):
    """The first m of idx in (distance, recording_id, start_sample) order; ties at the boundary included."""
    if len(idx) > m:
        part = idx[np.argpartition(d[idx], m - 1)[:m]]
        kth = d[part].max()
        idx = idx[d[idx] <= kth]
    o = np.lexsort((gal["start"][idx], gal["rid"][idx], d[idx]))
    return idx[o][:m]


def suppress(pool, gal, k=TOP):
    picked = []
    for i in pool:
        r, s = gal["rid"][i], gal["start"][i]
        if any(gal["rid"][p] == r and gal["start"][p] < s + W and gal["start"][p] + W > s for p in picked):
            continue
        picked.append(i)
        if len(picked) == k:
            break
    return picked


def select(pool_of, n_adm):
    """morlet_envelope_ranked's loop: pool of m, greedy suppression, grow 4x if used up early."""
    m = M0
    while True:
        pool = pool_of(m)
        picked = suppress(pool, GAL)
        if len(picked) == TOP or len(pool) < m or m >= n_adm:
            return picked, m
        m *= 4


def search64(q, src, timing):
    Q = qmatrix(q, np.float64)
    t, c = time.perf_counter(), time.process_time()
    d = kernel(GAL["G64"], Q)
    timing["kernel_ms"], timing["kernel_cpu_ms"] = 1e3 * (time.perf_counter() - t), 1e3 * (time.process_time() - c)
    adm = np.flatnonzero(admissible(GAL, src))
    picked, m = select(lambda m: ordered(adm, d, GAL, m), len(adm))
    return [(GAL["names"][GAL["rid"][i]], int(GAL["start"][i]), float(d[i])) for i in picked], {"pool": m}


def search32r(q, src, timing):
    Q32, Q64 = qmatrix(q, np.float32), qmatrix(q, np.float64)
    t, c = time.perf_counter(), time.process_time()
    d32 = kernel(GAL["G32"], Q32)
    timing["kernel_ms"], timing["kernel_cpu_ms"] = 1e3 * (time.perf_counter() - t), 1e3 * (time.process_time() - c)
    delta = C32 * float(np.sqrt(q @ q)) * GAL["gnorm"] / N_ENV
    adm = np.flatnonzero(admissible(GAL, src))
    dmax = float(delta[adm].max())
    exact = {}

    def pool_of(m):
        if len(adm) <= m:
            cand = adm
        else:
            thr = np.partition(d32[adm], m - 1)[m - 1]
            cand = adm[d32[adm] <= thr + 2 * dmax]
        need = np.array([i for i in cand if i not in exact], dtype=np.int64)
        if len(need):
            for i, v in zip(need, kernel(GAL["G64"][need], Q64)):
                exact[int(i)] = v
        d = np.full(len(d32), np.inf)
        d[cand] = [exact[int(i)] for i in cand]
        return ordered(cand, d, GAL, m)

    picked, m = select(pool_of, len(adm))
    return [(GAL["names"][GAL["rid"][i]], int(GAL["start"][i]), float(exact[int(i)])) for i in picked], \
        {"pool": m, "rescored": len(exact), "delta_max": dmax}


# ---------- arms with the request boundary ----------

def arm_mat(conn, rid, s, which):
    timing = {}
    t = time.perf_counter()
    q = e1.query_env(conn, rid, s)                       # source interval and query envelope, in PostgreSQL
    timing["prep_ms"] = 1e3 * (time.perf_counter() - t)
    t = time.perf_counter()
    top, work = (search64 if which == 64 else search32r)(q, (rid, s), timing)
    timing["search_ms"] = 1e3 * (time.perf_counter() - t)
    return top, {**work, **timing}


ARMS = {
    "X-PG": lambda c, r, s: e1.ARMS["EX-8"](c, r, s),
    "P-PG": lambda c, r, s: e1.ARMS["PR"](c, r, s),
    "X-MAT64": lambda c, r, s: arm_mat(c, r, s, 64),
    "X-MAT32R": lambda c, r, s: arm_mat(c, r, s, 32),
}


# ---------- correctness gate ----------

def synthetic_gate(trials=3000, seed=7):
    """select/ordered/suppress against a brute-force full sort, on small galleries with ties and overlaps."""
    global GAL
    saved, rng, fails = GAL, random.Random(seed), []
    for t in range(trials):
        nrec = rng.randint(1, 4)
        rows = [(r, s) for r in range(nrec) for s in range(0, rng.randint(1, 12) * 2048, 2048)]
        n = len(rows)
        d = np.array([rng.choice([0.1, 0.2, 0.2, 0.3]) if rng.random() < 0.5 else rng.random() for _ in rows])
        GAL = {"rid": np.array([r for r, _ in rows]), "start": np.array([s for _, s in rows])}
        srec, ss = rng.randrange(nrec), rng.randrange(0, 8) * 2048
        keep = ~((GAL["rid"] == srec) & (GAL["start"] < ss + W) & (GAL["start"] + W > ss))
        adm = np.flatnonzero(keep)
        brute = sorted(adm, key=lambda i: (d[i], GAL["rid"][i], GAL["start"][i]))
        want = suppress(brute, GAL)
        for m0 in (1, 2, 3, 200):
            global M0
            old, M0 = M0, m0
            got, _ = select(lambda m: ordered(adm, d, GAL, m), len(adm))
            M0 = old
            if [int(i) for i in got] != [int(i) for i in want]:
                fails.append({"trial": t, "m0": m0, "got": [int(i) for i in got], "want": [int(i) for i in want]})
    GAL = saved
    return {"trials": trials, "pool_sizes": [1, 2, 3, 200], "failures": len(fails), "examples": fails[:3]}


def scorer_gate(conn, queries, pairs=500, seed=11):
    rng = np.random.default_rng(seed)
    blobs = GAL["blobs"]
    idx = rng.choice(len(blobs), size=20, replace=False)
    dec_equal = all(np.array_equal(np.asarray(conn.execute("SELECT morlet_envelope_decode(%s)", (blobs[i],)).fetchone()[0]),
                                   GAL["G64"][i]) for i in idx)
    worst, n = 0.0, 0
    qs = [e1.query_env(conn, r, s) for r, s, _ in queries[:10]]
    finite = all(bool(np.isfinite(kernel(GAL["G64"], qmatrix(q, np.float64))).all())
                 and bool(np.isfinite(kernel(GAL["G32"], qmatrix(q, np.float32))).all()) for q in qs)
    for qi, q in enumerate(qs):
        Q = qmatrix(q, np.float64)
        rows = np.concatenate([rng.choice(len(blobs), size=pairs // len(qs) - 2, replace=False), [0, len(blobs) - 1]])
        d = kernel(GAL["G64"][rows], Q)
        for i, di in zip(rows, d):
            ref = conn.execute("SELECT morlet_envelope_distance(%s::float8[], %s, %s)", (q.tolist(), blobs[i], L)).fetchone()[0]
            worst = max(worst, abs(ref - di))
            n += 1
    # extreme lags and zero padding: queries that are stored envelopes shifted by +-30 / +-29, so the best lag
    # sits at the boundary of the lag range
    edge, at_edge = 0.0, 0
    for i in rng.choice(len(blobs), size=25, replace=False):
        for lag in (-30, -29, 29, 30):
            q = P.shifted(GAL["G64"][i], -lag)
            ref = conn.execute("SELECT morlet_envelope_distance(%s::float8[], %s, %s)", (q.tolist(), blobs[i], L)).fetchone()[0]
            S = GAL["G64"][i] @ qmatrix(q, np.float64)
            at_edge += abs(LAGS[int(np.argmax(S))]) >= 29
            edge = max(edge, abs(ref - (1 - S.max() / N_ENV)))
    return {"kernel_finite_full_gallery_10_queries": finite, "decode_equal_20_rows": bool(dec_equal), "pairs": n, "max_abs_distance_diff": max(worst, edge),
            "edge_lag_pairs": 100, "edge_pairs_with_best_lag_at_29_or_30": int(at_edge), "edge_max_abs_diff": edge}


def list_gate(conn, queries):
    out, mism = {}, []
    for rid, s, typ in queries:
        conn.execute("SET max_parallel_workers_per_gather = 8")
        refd = [(r, int(st), float(dd)) for r, st, dd in conn.execute(
            "SELECT recording_id, start_sample, distance FROM morlet_search_envelope_interval(%s, %s, %s, %s, '{}'::jsonb, 10,"
            " 0.075, true, true, 1000000)", (COLL, rid, s, s + W)).fetchall()]
        conn.execute("RESET max_parallel_workers_per_gather")
        q = e1.query_env(conn, rid, s)
        rec = {"type": typ, "ref": refd}
        for name, fn in (("X-MAT64", search64), ("X-MAT32R", search32r)):
            top, work = fn(q, (rid, s), {})
            same = [(a, b) for a, b, _ in top] == [(a, b) for a, b, _ in refd]
            dd = max((abs(x[2] - y[2]) for x, y in zip(top, refd)), default=0.0) if same else None
            rec[name] = {"same_list": same, "max_abs_distance_diff": dd, "work": work}
            if not same or dd > 1e-12:
                mism.append({"query": f"{rid}:{s}", "arm": name, "got": top, "ref": refd})
        out[f"{rid}:{s}"] = rec
    return out, mism


# ---------- timed run ----------

def run(conn, queries, config):
    for rid, s, _ in queries[:5]:
        for a in TIMED:
            ARMS[a](conn, rid, s)
    per, rounds = {}, []
    for rnd in (1, 2):
        la0 = list(os.getloadavg())
        order = queries if rnd == 1 else list(reversed(queries))
        for qi, (rid, s, typ) in enumerate(order):
            key = f"{rid}:{s}"
            rec = per.setdefault(key, {"type": typ, "ms": {}, "parts": {}, "top": {}, "round2_differs": []})
            k = qi % len(TIMED)
            for a in TIMED[k:] + TIMED[:k]:
                t = time.perf_counter()
                top, work = ARMS[a](conn, rid, s)
                ms = 1e3 * (time.perf_counter() - t)
                rec["ms"].setdefault(a, []).append(ms)
                if a.startswith("X-MAT"):
                    rec["parts"].setdefault(a, []).append({k2: v for k2, v in work.items() if k2.endswith("_ms")})
                lst = [(x[0], int(x[1])) for x in top]
                if rnd == 1:
                    rec["top"][a] = lst
                elif lst != rec["top"][a]:
                    rec["round2_differs"].append(a)
            if qi % 25 == 0:
                print(config, "round", rnd, qi, {a: round(rec["ms"][a][-1], 1) for a in TIMED}, flush=True)
        rounds.append({"round": rnd, "loadavg_start": la0, "loadavg_end": list(os.getloadavg())})
    for key, rec in per.items():
        ref = set(map(tuple, rec["top"]["X-PG"]))
        rec["recall"] = {a: len(ref & set(map(tuple, rec["top"][a]))) / TOP for a in TIMED}
    return per, rounds


def main(mode, out_path, build, config="full"):
    global GAL
    queries = [(q["recording_id"], int(q["start"]), q["type"]) for q in json.loads(QUERIES.read_text())["queries"]]
    with psycopg.connect(f"host=localhost port=28817 dbname={DB}", autocommit=True) as conn:
        res = {"mode": mode, "config": config, "environment": e1.environment(conn, build),
               "threads_env": {k: os.environ.get(k) for k in ("VECLIB_MAXIMUM_THREADS", "OMP_NUM_THREADS")},
               "blas": np.show_config(mode="dicts")["Build Dependencies"]["blas"], "numpy": np.__version__,
               "probe_ef_search": "set by morlet_search_envelope_probes to max(50, 100) = 100"}
        GAL, res["materialization"] = materialize(conn)
        src_attr = {}
        for (r, rate, ch, unit) in conn.execute(f"SELECT recording_id, sample_rate_hz, channel, unit FROM {COLL}").fetchall():
            key = (rate, ch, unit)
            src_attr[r] = GAL["attrs"].index(key) if key in GAL["attrs"] else -1
        GAL["src_attr"] = src_attr
        if mode == "gate":
            res["synthetic"] = synthetic_gate()
            res["scorer"] = scorer_gate(conn, queries)
            res["lists"], res["mismatches"] = list_gate(conn, queries)
            res["passed"] = (res["synthetic"]["failures"] == 0 and res["scorer"]["decode_equal_20_rows"]
                             and res["scorer"]["kernel_finite_full_gallery_10_queries"]
                             and res["scorer"]["max_abs_distance_diff"] <= 1e-12 and not res["mismatches"])
            print("gate", {k: res[k] for k in ("synthetic", "scorer")}, "mismatches", len(res["mismatches"]),
                  "passed", res["passed"], flush=True)
        else:
            res["per_query"], res["rounds"] = run(conn, queries, config)
        res["environment"]["loadavg_end"] = list(os.getloadavg())
        res["maxrss_bytes"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        Path(out_path).write_text(json.dumps(res, indent=1, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))


GAL = None
if __name__ == "__main__":
    main(*sys.argv[1:5])
