"""B3.3 part A (PROTOCOL.md, "Amendment B3.3"): time-aligned reads of every channel of one acquisition.

Each acquisition holds 7 channels at three rates (64 kHz, 4 kHz, 1 Hz). A query asks for the time interval
[t0, t1) of one acquisition on every channel; sample n of a channel (at start + n * period) belongs to it when its
time does, so each channel returns samples [ceil(t0 * rate), ceil(t1 * rate)) clamped to its length (the rule of
morlet_time_to_samples). Arms:

- `morlet_read_acquisition`: MorletDB's acquisition API, one call (channels registered with
  morlet_create_acquisition / morlet_add_acquisition_channel, offset 0, clock 'daq').
- `morlet_read_acquisition_raw` (SQL 0.3.3, added by this amendment): the same rows, samples as raw
  little-endian float64 bytes, built in one statement.
- `client_raw`: the client maps the interval to sample ranges itself and reads every non-empty channel with
  morlet_read_interval_raw in one statement (B3.2's multi-channel read, extended to mixed rates).
- `questdb`: one time-range query per group table (3 queries), as QuestDB's idiomatic wide tables are laid out.

Every result is compared bit for bit with the manifest's arrays; QuestDB's timestamps must equal
start + n * period exactly, MorletDB's start_time_ns the time of the first returned sample.
"""
import time

import numpy as np

import common
import workload as wl

WINDOWS = {"AQ1": 64_000_000, "AQ2": 500_000_000}     # 64 ms (4,096 samples at 64 kHz), 0.5 s (32,000)
CHANNELS = [c for g in wl.GROUPS.values() for c in g["channels"]]
RATE = {c: g["rate"] for g in wl.GROUPS.values() for c in g["channels"]}
PERIOD = {c: g["period_ns"] for g in wl.GROUPS.values() for c in g["channels"]}


def plan(m, seed=20261103, count=20):
    """Fixed (acquisition index, t0 ns relative to the acquisition start) per window, chosen once by the seed."""
    rng = np.random.default_rng(seed)
    acq = m["acquisitions"]
    out = {}
    for name, w in WINDOWS.items():
        pairs = []
        for _ in range(count):
            a = acq[int(rng.integers(0, len(acq)))]
            dur = a["channels"]["vibration_1"]["samples"] * PERIOD["vibration_1"]
            pairs.append((a["k"], int(rng.integers(0, dur - w))))
        out[name] = {"window_ns": w, "pairs": pairs}
    return out


def sample_range(c, n, t0, t1):
    """[ceil(t0 * rate / 1e9), ceil(t1 * rate / 1e9)) clamped to the channel's n samples; periods are integers."""
    p = PERIOD[c]
    return min(-(-t0 // p), n), min(-(-t1 // p), n)


def expected(a, data, t0, t1):
    out = {}
    for c in CHANNELS:
        s, e = sample_range(c, a["channels"][c]["samples"], t0, t1)
        if e > s:
            out[c] = (s, data[c][s:e])
    return out


def register(conn, m, collection):
    """Registers every acquisition and its 7 channels; returns seconds."""
    t = time.perf_counter()
    conn.execute("SELECT morlet_enable_acquisitions(%s::regclass)", (collection,))
    with conn.transaction():
        for a in m["acquisitions"]:
            conn.execute("SELECT morlet_create_acquisition(%s::regclass, %s, 'paderborn-test-rig', "
                         "to_timestamp(%s::numeric / 1e9))", (collection, a["acquisition"], a["start_ns"]))
            for c in CHANNELS:
                conn.execute("SELECT morlet_add_acquisition_channel(%s::regclass, %s, %s, %s, 0, 'daq')",
                             (collection, a["acquisition"], f"{a['acquisition']}.{c}", c))
    return time.perf_counter() - t


def reader_acq(conn, collection):
    def read(a, t0, t1):
        rows = conn.execute("SELECT role, start_sample, start_time_ns, samples FROM morlet_read_acquisition("
                            "%s::regclass, %s, %s, %s)", (collection, a["acquisition"], t0, t1),
                            binary=True, prepare=True).fetchall()
        return {r[0]: (int(r[1]), np.asarray(r[3], dtype=np.float64), int(r[2])) for r in rows}
    return read


def reader_acq_raw(conn, collection):
    def read(a, t0, t1):
        rows = conn.execute("SELECT role, start_sample, start_time_ns, samples FROM morlet_read_acquisition_raw("
                            "%s::regclass, %s, %s, %s)", (collection, a["acquisition"], t0, t1),
                            binary=True, prepare=True).fetchall()
        return {r[0]: (int(r[1]), np.frombuffer(r[3], dtype="<f8"), int(r[2])) for r in rows}
    return read


def reader_client(conn, collection):
    def read(a, t0, t1):
        ids, starts, ends, chans = [], [], [], []
        for c in CHANNELS:
            s, e = sample_range(c, a["channels"][c]["samples"], t0, t1)
            if e > s:
                ids.append(f"{a['acquisition']}.{c}")
                starts.append(s)
                ends.append(e)
                chans.append(c)
        rows = conn.execute(
            "SELECT morlet_read_interval_raw(%s::regclass, r.id, r.s, r.e) FROM unnest(%s::text[], %s::bigint[], "
            "%s::bigint[]) WITH ORDINALITY r(id, s, e, o) ORDER BY r.o", (collection, ids, starts, ends),
            binary=True, prepare=True).fetchall()
        return {c: (s, np.frombuffer(r[0], dtype="<f8"), s * PERIOD[c]) for c, s, r in zip(chans, starts, rows)}
    return read


def reader_questdb(db):
    import questdb_adapter as q

    def read(a, t0, t1):
        out = {}
        for g, spec in wl.GROUPS.items():
            cols = spec["channels"]
            t = db.query(f"select ts, {', '.join(cols)} from {q.TABLES[g]} where acquisition = '{a['acquisition']}' "
                         f"and ts >= {a['start_ns'] + t0}::timestamp_ns and ts < {a['start_ns'] + t1}::timestamp_ns "
                         f"order by ts").to_arrow()
            if t.num_rows == 0:
                continue
            ts = t.column("ts").cast("int64").to_numpy() - a["start_ns"]
            s = int(ts[0] // spec["period_ns"])
            if not np.array_equal(ts, (s + np.arange(t.num_rows, dtype=np.int64)) * spec["period_ns"]):
                raise RuntimeError("questdb timestamps are not start + n * period")
            for c in cols:
                out[c] = (s, t.column(c).to_numpy(), s * spec["period_ns"])
        return out
    return read


def _check(got, want):
    """Number of channels that differ from the manifest (missing, extra, offset, time or any bit of a sample)."""
    bad = len(set(got) ^ set(want))
    for c, (s, x) in want.items():
        if c not in got:
            continue
        gs, gx, gt = got[c]
        exact_t = s * PERIOD[c]
        if gs != s or gt != exact_t or gx.shape != x.shape or not np.array_equal(gx.view(np.int64), x.view(np.int64)):
            bad += 1
    return bad


def measure(read, m, data, reps=50, seed=20261103):
    """p50/p95 per window over 20 intervals x reps (after one checked warm-up each), and mismatches."""
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for name, spec in plan(m, seed).items():
        lat, bad, samples = [], 0, 0
        for k, t0 in spec["pairs"]:
            a, t1 = acq[k], t0 + spec["window_ns"]
            want = expected(a, data[k], t0, t1)
            samples += sum(x.size for _, x in want.values())
            bad += _check(read(a, t0, t1), want)
            for _ in range(reps):
                t = time.perf_counter()
                v = read(a, t0, t1)
                lat.append(time.perf_counter() - t)
            bad += _check(v, want)
        out[name] = {**common.pcts(lat), "window_ns": spec["window_ns"], "queries": len(lat),
                     "samples_per_query_mean": samples / len(spec["pairs"]), "mismatches": bad}
        print(name, out[name], flush=True)
    return out
