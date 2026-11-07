#!/usr/bin/env python3
"""Study N1 harness (PROTOCOL.md): PLAID turn-on windows, MorletDB exact envelope search (M), current-feature
1-NN (B1) and current+voltage 1-NN (B2), leave one location out.

  n1.py windows --plaid DIR --out windows.npz     event windows as 0.01 A / 0.01 V counts (exact on every machine)
  n1.py m --windows windows.npz --dsn DSN --out m.json        M on one machine (desktop or Pi, for H3)
  n1.py report --windows windows.npz --m m.json --out results.json [--m-pi m_pi.json]

The database needs pg_morlet installed from this repository; the collection `n1` is created and dropped.
"""
import argparse
import json
import platform
import struct
import time
from collections import Counter
from pathlib import Path

import numpy as np

RATE, CYCLE, WINDOW, PRE = 30000, 500, 16384, 5000
QUANTUM = 0.01
MAX_LAG = 0.075
COLLECTION = "n1"


# ---------------------------------------------------------------- data and windows

def select(meta):
    """Primary and unknown record ids from the metadata, as PROTOCOL.md defines them."""
    on = {k: e for k, e in meta.items() if e["appliance"]["status"] == "off-on"}
    places = {}
    for e in on.values():
        places.setdefault(e["appliance"]["type"], set()).add(e["location"])
    primary = sorted((k for k, e in on.items() if len(places[e["appliance"]["type"]]) >= 2), key=int)
    unknown = sorted((k for k, e in on.items() if len(places[e["appliance"]["type"]]) == 1), key=int)
    return primary, unknown


def onset(current):
    """(window start, flagged) by the fixed per-cycle RMS rule."""
    n = len(current) // CYCLE
    rms = np.sqrt(np.mean(current[: n * CYCLE].reshape(n, CYCLE) ** 2, axis=1))
    b, m = float(np.median(rms[:3])), float(rms.max())
    if m - b < 0.05:
        return 0, True
    c = int(np.argmax(rms > b + 0.1 * (m - b)))
    return max(0, min(CYCLE * c - PRE, len(current) - WINDOW)), False


def counts(x, name):
    k = np.round(x / QUANTUM)
    if np.max(np.abs(x / QUANTUM - k)) > 1e-6:
        raise SystemExit(f"{name}: values are not multiples of {QUANTUM}")
    return k.astype(np.int32)


def windows(plaid, out):
    meta = json.loads((plaid / "metadata_submetered.json").read_text())
    primary, unknown = select(meta)
    ids, ic, vc, starts, flags = [], [], [], [], []
    for k in primary + unknown:
        a = np.loadtxt(plaid / "extracted" / "submetered_new" / f"{k}.csv", delimiter=",")
        cur, volt = a[:, 0], a[:, 1]
        if len(cur) < WINDOW:
            raise SystemExit(f"record {k} is shorter than the window")
        s, f = onset(cur)
        ids.append(k)
        ic.append(counts(cur[s:s + WINDOW], k))
        vc.append(counts(volt[s:s + WINDOW], k))
        starts.append(s)
        flags.append(f)
    np.savez_compressed(out, ids=np.array(ids), i=np.stack(ic), v=np.stack(vc), start=np.array(starts),
                        flagged=np.array(flags), primary=np.array([k in set(primary) for k in ids]),
                        type=np.array([meta[k]["appliance"]["type"] for k in ids]),
                        location=np.array([meta[k]["location"] for k in ids]))
    print(f"{len(primary)} primary, {len(unknown)} unknown, {sum(flags)} without a clear onset")


def load(path):
    z = np.load(path)
    return {k: z[k] for k in z.files}


def samples(c):
    """Counts back to values: k / 100 is the correctly rounded double of the CSV's two-decimal value."""
    return c.astype(np.float64) / 100.0


def rid(k):
    return f"p{int(k):04d}"


# ---------------------------------------------------------------- B1 / B2

def features(i, v=None):
    st = i[-10 * CYCLE:]
    irms = float(np.sqrt(np.mean(st ** 2)))
    x = np.fft.rfft(st)
    i1 = abs(x[10])
    harm = [abs(x[10 * h]) / i1 if i1 > 0 else 0.0 for h in (2, 3, 5, 7, 9)]
    cyc = np.sqrt(np.mean(i[: (len(i) // CYCLE) * CYCLE].reshape(-1, CYCLE) ** 2, axis=1))
    f = [np.log10(max(irms, 1e-6)), float(np.max(np.abs(st))) / irms if irms > 0 else 0.0, *harm,
         float(cyc.max()) / irms if irms > 0 else 0.0]
    if v is not None:
        sv = v[-10 * CYCLE:]
        p = float(np.mean(sv * st))
        vrms = float(np.sqrt(np.mean(sv ** 2)))
        y = np.fft.rfft(sv)
        ph = float(np.angle(x[10]) - np.angle(y[10]))
        f += [np.log10(abs(p) + 1e-6), p / (vrms * irms) if vrms * irms > 0 else 0.0, np.cos(ph), np.sin(ph)]
    return np.array(f, dtype=np.float64)


def neighbours_features(F, loc, gallery, query, k=5):
    """For each query index: the k nearest gallery indices from other locations, ordered by (distance, id)."""
    out = {}
    for q in query:
        g = np.array([j for j in gallery if loc[j] != loc[q]])
        assert len(g) and not np.any(loc[g] == loc[q])
        mu, sd = F[g].mean(axis=0), F[g].std(axis=0)
        sd[sd == 0] = 1
        d = np.sqrt((((F[g] - mu) / sd - (F[q] - mu) / sd) ** 2).sum(axis=1))
        order = sorted(range(len(g)), key=lambda t: (d[t], int(g[t])))[:k]
        out[q] = [(int(g[t]), float(d[t])) for t in order]
    return out


# ---------------------------------------------------------------- M

def run_m(w, dsn, out):
    import psycopg
    ids, loc = w["ids"], w["location"]
    prim = np.flatnonzero(w["primary"])
    per_loc = Counter(loc[prim])
    res = {"machine": platform.node(), "platform": platform.platform(), "load_before": _load(), "queries": {}}
    with psycopg.connect(dsn, autocommit=True) as c:
        res["server"] = c.execute("SELECT version()").fetchone()[0]
        res["extension"] = c.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
        c.execute(f"DROP TABLE IF EXISTS {COLLECTION}, {COLLECTION}_chunks, {COLLECTION}_envelopes CASCADE")
        c.execute("SELECT morlet_create_stream_collection('public', %s)", (COLLECTION,))
        c.execute("SELECT morlet_enable_envelopes(%s::regclass)", (COLLECTION,))
        t0 = time.perf_counter()
        for j in prim:
            meta = json.dumps({"type": str(w["type"][j]), "location": str(loc[j])})
            with c.transaction():
                c.execute("SELECT morlet_start_recording(%s::regclass, %s, %s, 'i', 'A', %s::jsonb, 512, 256, "
                          "'zscore', 'morlet-energy-v1', %s)", (COLLECTION, rid(ids[j]), RATE, meta, QUANTUM))
                c.execute("SELECT morlet_append_chunk(%s::regclass, %s, 0, %s)",
                          (COLLECTION, rid(ids[j]), samples(w["i"][j]).tolist()))
                c.execute("SELECT morlet_build_envelopes(%s::regclass, %s, %s, %s)",
                          (COLLECTION, rid(ids[j]), WINDOW, WINDOW))
        res["ingest_s"] = time.perf_counter() - t0
        res["bytes"] = dict(zip(("chunks", "envelopes"), c.execute(
            f"SELECT (SELECT sum(octet_length(encoded)) FROM {COLLECTION}_chunks), "
            f"(SELECT sum(octet_length(envelope)) FROM {COLLECTION}_envelopes)").fetchone()))
        lat = []
        for q in range(len(ids)):
            top = per_loc[loc[q]] + 5
            t = time.perf_counter()
            rows = c.execute("SELECT recording_id, distance FROM morlet_search_envelopes(%s::regclass, %s, %s, 'i', "
                             "'A', '{}'::jsonb, %s, %s)",
                             (COLLECTION, samples(w["i"][q]).tolist(), RATE, top, MAX_LAG)).fetchall()
            lat.append(time.perf_counter() - t)
            res["queries"][rid(ids[q])] = [[r, struct.pack(">d", d).hex()] for r, d in rows]
        res["latency_s"] = {"median": float(np.median(lat)), "p95": float(np.percentile(lat, 95))}
        res["load_after"] = _load()
        c.execute(f"DROP TABLE IF EXISTS {COLLECTION} CASCADE")
    Path(out).write_text(json.dumps(res))
    print("M done:", len(res["queries"]), "queries, median", round(res["latency_s"]["median"] * 1000, 1), "ms")


def _load():
    try:
        import os
        return os.getloadavg()
    except OSError:
        return None


def neighbours_m(m, w, k=5):
    """M's rows with the query's own location dropped, first k, as (gallery index, distance)."""
    index = {rid(x): j for j, x in enumerate(w["ids"])}
    loc = w["location"]
    out = {}
    for q, x in enumerate(w["ids"]):
        rows = [(index[r], struct.unpack(">d", bytes.fromhex(h))[0]) for r, h in m["queries"][rid(x)]]
        keep = [(j, d) for j, d in rows if loc[j] != loc[q]]
        assert all(w["primary"][j] for j, _ in keep)
        if len(keep) < k:
            raise SystemExit(f"query {x}: fewer than {k} rows from other locations")
        out[q] = keep[:k]
    return out


# ---------------------------------------------------------------- metrics

def vote(nb, types, k):
    first = nb[:k]
    c = Counter(types[j] for j, _ in first)
    best = max(c.values())
    for j, _ in first:
        if c[types[j]] == best:
            return types[j]


def f1(y, p, t):
    tp = sum(1 for a, b in zip(y, p) if a == t and b == t)
    fp = sum(1 for a, b in zip(y, p) if a != t and b == t)
    fn = sum(1 for a, b in zip(y, p) if a == t and b != t)
    return 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0


def macro_f1(y, p):
    """Mean F1 over the types present in y or p."""
    return float(np.mean([f1(y, p, t) for t in sorted(set(y) | set(p))]))


def auroc(pos, neg):
    pos, neg = np.asarray(pos), np.asarray(neg)
    gt = (pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()
    return float(gt / (len(pos) * len(neg)))


def bootstrap_locations(y, preds, loc, seed=7, n=2000):
    rng = np.random.default_rng(seed)
    places = sorted(set(loc))
    by = {p: [i for i, l in enumerate(loc) if l == p] for p in places}
    out = {k: [] for k in preds}
    out["delta"] = []
    for _ in range(n):
        idx = [i for p in rng.choice(places, len(places), replace=True) for i in by[p]]
        yy = [y[i] for i in idx]
        f = {k: macro_f1(yy, [v[i] for i in idx]) for k, v in preds.items()}
        for k in preds:
            out[k].append(f[k])
        out["delta"].append(f["M"] - f["B1"])
    return {k: [float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5))] for k, v in out.items()}


def report(w, m, out, m_pi=None):
    types, loc, prim = w["type"], w["location"], np.flatnonzero(w["primary"])
    unk = np.flatnonzero(~w["primary"])
    nbm = neighbours_m(m, w)
    I = np.array([samples(x) for x in w["i"]])
    V = np.array([samples(x) for x in w["v"]])
    F1 = np.array([features(I[j]) for j in range(len(I))])
    F2 = np.array([features(I[j], V[j]) for j in range(len(I))])
    nb1 = neighbours_features(F1, loc, prim, prim)
    nb2 = neighbours_features(F2, loc, prim, prim)
    y = [types[q] for q in prim]
    preds = {"M": [vote(nbm[q], types, 1) for q in prim], "B1": [vote(nb1[q], types, 1) for q in prim],
             "B2": [vote(nb2[q], types, 1) for q in prim]}
    k5 = {f"{a}_k5": [vote(nb[q], types, 5) for q in prim] for a, nb in (("M", nbm), ("B1", nb1), ("B2", nb2))}
    ci = bootstrap_locations(y, preds, [loc[q] for q in prim])
    f = {k: macro_f1(y, v) for k, v in preds.items()}
    delta = f["M"] - f["B1"]
    lo, hi = ci["delta"]
    h1 = "PASS" if delta >= -0.05 and lo >= -0.10 else "FAIL" if hi < -0.05 else "INCONCLUSIVE"
    near = {q: nbm[q][0][1] for q in range(len(types))}
    h2 = None
    if len(unk):
        a = auroc([near[q] for q in unk], [near[q] for q in prim])
        rng = np.random.default_rng(7)
        boots = [auroc(rng.choice([near[q] for q in unk], len(unk)),
                       rng.choice([near[q] for q in prim], len(prim))) for _ in range(2000)]
        h2 = {"auroc": a, "ci": [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
              "verdict": "PASS" if a >= 0.80 else "FAIL"}
    res = {
        "records": {"primary": int(len(prim)), "unknown": int(len(unk)), "locations": len(set(loc[prim])),
                    "flagged_no_onset": {t: int(c) for t, c in Counter(types[w["flagged"]]).items()}},
        "macro_f1": f, "macro_f1_ci": ci, "accuracy": {k: float(np.mean([a == b for a, b in zip(y, v)]))
                                                      for k, v in preds.items()},
        "k5": {k: {"macro_f1": macro_f1(y, v), "accuracy": float(np.mean([a == b for a, b in zip(y, v)]))}
               for k, v in k5.items()},
        "per_type_f1": {k: {t: f1(y, v, t) for t in sorted(set(y))}
                        for k, v in preds.items()},
        "confusion": {k: Counter(f"{a} -> {b}" for a, b in zip(y, v)).most_common() for k, v in preds.items()},
        "H1": {"delta": delta, "ci": ci["delta"], "verdict": h1},
        "H2": h2,
        "storage": {"morlet": m["bytes"], "float32_bytes": int(len(prim) * WINDOW * 4)},
        "m_run": {k: m[k] for k in ("machine", "platform", "server", "extension", "ingest_s", "latency_s",
                                    "load_before", "load_after")},
    }
    if m_pi is not None:
        same = m_pi["queries"] == m["queries"]
        res["H3"] = {"identical": same, "verdict": "PASS" if same else "FAIL",
                     "pi": {k: m_pi[k] for k in ("machine", "platform", "server", "extension", "latency_s",
                                                 "load_before", "load_after")}}
    Path(out).write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("macro_f1", "H1", "H2")} | ({"H3": res["H3"]["verdict"]}
                                                                    if m_pi else {}), indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    s = p.add_subparsers(dest="cmd", required=True)
    a = s.add_parser("windows")
    a.add_argument("--plaid", type=Path, required=True)
    a.add_argument("--out", type=Path, required=True)
    b = s.add_parser("m")
    b.add_argument("--windows", type=Path, required=True)
    b.add_argument("--dsn", required=True)
    b.add_argument("--out", type=Path, required=True)
    r = s.add_parser("report")
    r.add_argument("--windows", type=Path, required=True)
    r.add_argument("--m", type=Path, required=True)
    r.add_argument("--m-pi", type=Path)
    r.add_argument("--out", type=Path, required=True)
    x = p.parse_args()
    if x.cmd == "windows":
        windows(x.plaid, x.out)
    elif x.cmd == "m":
        run_m(load(x.windows), x.dsn, x.out)
    else:
        report(load(x.windows), json.loads(x.m.read_text()), x.out,
               json.loads(x.m_pi.read_text()) if x.m_pi else None)


if __name__ == "__main__":
    main()
