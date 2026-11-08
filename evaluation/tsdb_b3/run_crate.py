#!/usr/bin/env python3
"""B3.4 runner: CrateDB (PROTOCOL.md, "Amendment B3.4"). Subcommands, each writing results/<B3_RUN>/<name>.json:

  subset <arm>                 correctness and retry semantics on 3 acquisitions
  load <arm> <D1|D2> <rep>     one measured fresh load, then full correctness; D2 rep 1 also measures storage
                               (after OPTIMIZE, and again after OPTIMIZE to one segment), reads and analytics

arm: row, row-best or array. Run with .venv-b3c/bin/python (crate 2.3.0); B3_RUN=b34.
"""
import json
import sys
import threading
import time

import numpy as np

import common
import crate_adapter as c
import workload as wl

SUBSET = [0, 61, 123]


def save(name, obj):
    common.RESULTS.mkdir(parents=True, exist_ok=True)
    (common.RESULTS / f"{name}.json").write_text(json.dumps(obj, indent=1, default=str) + "\n")
    print(json.dumps(obj, indent=1, default=str)[:3000], flush=True)


def preload(m, ks):
    t = time.perf_counter()
    data = {k: wl.load(wl.ROOT / m["acquisitions"][k]["path"]) for k in ks}
    return data, time.perf_counter() - t


def expected(m, ks, arm):
    if arm == "array":
        return {ch: sum(m["acquisitions"][k]["channels"][ch]["samples"] for k in ks) for ch in wl.UNITS}
    return {g: sum(m["acquisitions"][k]["channels"][s["channels"][0]]["samples"] for k in ks) for g, s in wl.GROUPS.items()}


def settle(conn, arm, want, timeout=3600):
    """Refresh until every row is queryable; seconds taken."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        c.refresh(conn, arm)
        if c.count(conn, arm) == want:
            return time.perf_counter() - t0
        time.sleep(0.05)
    raise RuntimeError("CrateDB did not settle")


def check_reads(conn, arm, m, ks):
    out = {"acquisitions": len(ks), "channels_checked": 0, "mismatches": []}
    for k in ks:
        a = m["acquisitions"][k]
        for g, spec in wl.GROUPS.items():
            n = a["channels"][spec["channels"][0]]["samples"]
            idx, vals = c.read(conn, arm, a, spec["channels"], 0, n, group=g)
            if idx is not None and not np.array_equal(idx, np.arange(n, dtype=np.int64)):
                out["mismatches"].append(f"{a['acquisition']} {g}: sample indexes")
            for ch in spec["channels"]:
                out["channels_checked"] += 1
                if common.sha(vals[ch]) != a["channels"][ch]["sha256"]:
                    out["mismatches"].append(f"{a['acquisition']} {ch}: samples")
    return out


def sample_reads(conn, arm, m, ks, data, rng):
    bad = []
    for _ in range(20):
        k = int(rng.choice(ks))
        a = m["acquisitions"][k]
        n = a["channels"]["vibration_1"]["samples"]
        length = int(rng.integers(1, 70000))
        off = int(rng.integers(0, n - length))
        _, v = c.read(conn, arm, a, ["vibration_1"], off, length)
        if common.sha(v["vibration_1"]) != common.sha(data[k]["vibration_1"][off:off + length]):
            bad.append((a["acquisition"], off, length))
    return {"intervals": 20, "mismatches": bad}


def subset(arm):
    m = common.manifest()
    data, _ = preload(m, SUBSET)
    rng = np.random.default_rng(1)
    vol = f"b3-c-subset-{arm}"
    conn = c.start(vol)
    c.create(conn, arm, "D2")
    for k in SUBSET:
        c.load_one(conn, arm, m["acquisitions"][k], data[k])
    want = expected(m, SUBSET, arm)
    settle(conn, arm, want)
    r = {"arm": arm, "counts": {"got": c.count(conn, arm), "want": want},
         "reads": check_reads(conn, arm, m, SUBSET), "intervals": sample_reads(conn, arm, m, SUBSET, data, rng)}
    r["counts"]["ok"] = r["counts"]["got"] == want
    a = m["acquisitions"][SUBSET[1]]
    c.load_one(conn, arm, a, data[SUBSET[1]])                     # identical retry
    settle(conn, arm, want)
    r["identical_retry"] = {"counts_unchanged": c.count(conn, arm) == want,
                            "reads": check_reads(conn, arm, m, [SUBSET[1]])["mismatches"]}
    altered = {ch: v.copy() for ch, v in data[SUBSET[1]].items()}
    altered["vibration_1"][1000] += 1.0
    try:
        c.load_one(conn, arm, a, altered)
        r["altered_retry"] = {"accepted": True}
    except Exception as exc:
        r["altered_retry"] = {"accepted": False, "error": str(exc)[:200]}
    settle(conn, arm, want)
    _, v = c.read(conn, arm, a, ["vibration_1"], 1000, 1)
    r["altered_retry"].update({"counts_unchanged": c.count(conn, arm) == want,
                               "stored_value_is_altered": bool(v["vibration_1"][0] == altered["vibration_1"][1000])})
    r["ok"] = r["counts"]["ok"] and not r["reads"]["mismatches"] and not r["intervals"]["mismatches"]
    common.remove(c.NAME, vol)
    save(f"correctness_subset_{arm}", r)


def total_samples(m):
    return sum(ch["samples"] for a in m["acquisitions"] for ch in a["channels"].values())


def _same(x, y):
    return x.shape == y.shape and np.array_equal(x.view(np.int64), y.view(np.int64))


def reads(arm, m, data, reps=50):
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for rq, spec in plan.items():
        n, chans = spec["length"], spec["channels"]
        if spec.get("threads"):
            lat, lock = [], threading.Lock()
            pairs = spec["pairs"]

            def worker(sub):
                conn, mine = c.connect(), []
                for k, off in sub:
                    c.read(conn, arm, acq[k], chans, off, n)
                for _ in range(reps):
                    for k, off in sub:
                        t = time.perf_counter()
                        _, v = c.read(conn, arm, acq[k], chans, off, n)
                        mine.append(time.perf_counter() - t)
                    if not _same(v[chans[0]], data[k][chans[0]][off:off + n]):
                        raise RuntimeError("read mismatch")
                with lock:
                    lat.extend(mine)
            th = [threading.Thread(target=worker, args=(pairs[i::spec["threads"]],)) for i in range(spec["threads"])]
            t0 = time.perf_counter()
            for t in th:
                t.start()
            for t in th:
                t.join()
            wall = time.perf_counter() - t0
            out[rq] = {**common.pcts(lat), "threads": spec["threads"], "queries": len(lat),
                       "queries_per_s": round(len(lat) / wall, 1)}
            print(arm, rq, out[rq], flush=True)
            continue
        conn, lat, bad = c.connect(), [], 0
        for k, off in spec["pairs"]:
            _, v = c.read(conn, arm, acq[k], chans, off, n)
            bad += sum(not _same(v[ch], data[k][ch][off:off + n]) for ch in chans)
            for _ in range(reps):
                t = time.perf_counter()
                _, v = c.read(conn, arm, acq[k], chans, off, n)
                lat.append(time.perf_counter() - t)
            bad += sum(not _same(v[ch], data[k][ch][off:off + n]) for ch in chans)
        out[rq] = {**common.pcts(lat), "samples_per_query": n * len(chans), "mismatches": bad}
        print(arm, rq, out[rq], flush=True)
    return out


def analytics(arm, m, data, reps=20):
    plan = common.read_plan(m)["R2"]
    acq = {a["k"]: a for a in m["acquisitions"]}
    conn, lat, worst = c.connect(), [], 0.0
    for k, off in plan["pairs"]:
        x = data[k]["vibration_1"][off:off + plan["length"]]
        want = [x.min(), x.max(), x.mean(), np.sqrt(np.mean(x * x))]
        got = c.analytics(conn, arm, acq[k], off, plan["length"])
        if int(got[4]) != x.size:
            raise RuntimeError("analytics count mismatch")
        worst = max(worst, max(abs(g - w) / max(abs(w), 1e-300) for g, w in zip(got[:4], want)))
        for _ in range(reps):
            t = time.perf_counter()
            c.analytics(conn, arm, acq[k], off, plan["length"])
            lat.append(time.perf_counter() - t)
    return {**common.pcts(lat), "max_relative_difference": worst, "within_1e-12": worst <= 1e-12,
            "computed": "client, after the read" if arm == "array" else "database"}


def reads_child(arm):
    """B3.13: the read benchmark against the running server from a new process (common.reads_maybe_fresh)."""
    m = common.manifest()
    data, _ = preload(m, list(range(len(m["acquisitions"]))))
    common.print_reads_child(reads(arm, m, data), analytics(arm, m, data))


def load(arm, durability, rep):
    rep = int(rep)
    m = common.manifest()
    ks = list(range(len(m["acquisitions"])))
    data, parse_s = preload(m, ks)
    name = f"load_crate-{arm}_{durability}_r{rep}"
    vol = f"b3-crate-{arm}-{durability.lower()}-r{rep}"
    res = {"system": f"crate-{arm}", "durability": durability, "rep": rep, "parse_s": parse_s,
           "samples": total_samples(m), "image": c.IMAGE,
           "image_id": common.sh(["docker", "image", "inspect", c.IMAGE, "--format", "{{.Id}}"]).strip(),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    conn = c.start(vol)
    c.create(conn, arm, durability)
    cur = conn.cursor()
    cur.execute("SELECT version['number'] FROM sys.nodes")
    res["server_version"] = cur.fetchone()[0]
    conv, acks, calls, failures = 0.0, [], [], []
    with common.Stats(c.NAME) as st:
        t0 = time.perf_counter()
        for a in m["acquisitions"]:
            t = time.perf_counter()
            cv, per_call = c.load_one(conn, arm, a, data[a["k"]])
            conv += cv
            calls += per_call
            acks.append(time.perf_counter() - t)
        t_last = time.perf_counter()
        wall = t_last - t0                     # includes client conversion (as B3's QuestDB arm), also reported apart
        queryable = settle(conn, arm, expected(m, ks, arm))
    res["ingest"] = {"wall_s": round(wall, 3), "samples_per_s": round(res["samples"] / wall),
                     "conversion_s": round(conv, 3), "ack_per_acquisition": common.pcts(acks),
                     "ack_per_call": common.pcts(calls), "queryable_after_last_ack_s": round(queryable, 3),
                     "failures": failures, "server": st.summary()}
    t = time.perf_counter()
    res["correctness"] = {"counts": {"got": c.count(conn, arm), "want": expected(m, ks, arm)},
                          "reads": check_reads(conn, arm, m, ks)}
    res["correctness"]["reads"]["seconds"] = round(time.perf_counter() - t, 1)
    res["correctness"]["counts"]["ok"] = res["correctness"]["counts"]["got"] == res["correctness"]["counts"]["want"]
    res["correct"] = res["correctness"]["counts"]["ok"] and not res["correctness"]["reads"]["mismatches"]
    save(name, res)
    if not res["correct"]:
        raise SystemExit("correctness failed")
    if durability == "D2" and rep == 1:
        res["optimize_s"] = round(c.optimize(conn, arm), 3)
        c.refresh(conn, arm)
        shards = c.shard_paths(conn)
        res["shards"] = shards
        common.stop(c.NAME)
        res["storage"] = c.storage(vol, shards)
        conn = c.restart(vol)
        res["reads"], res["analytics"] = common.reads_maybe_fresh(__file__, [arm], lambda: (reads(arm, m, data),
                                                                                            analytics(arm, m, data)))
        res["optimize_one_segment_s"] = round(c.optimize(conn, arm, single_segment=True), 3)
        c.refresh(conn, arm)
        shards1 = c.shard_paths(conn)
        common.stop(c.NAME)
        res["storage_one_segment"] = c.storage(vol, shards1)
    common.remove(c.NAME, vol)
    save(name, res)


if __name__ == "__main__":
    {"subset": subset, "load": load, "reads-child": reads_child}[sys.argv[1]](*sys.argv[2:])
