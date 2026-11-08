#!/usr/bin/env python3
"""B3.6 runner: TDengine (PROTOCOL.md, "Amendment B3.6"). Subcommands, each writing results/<B3_RUN>/<name>.json:

  subset <arm>                 correctness and retry semantics on 3 acquisitions
  load <arm> <D1|D2> <rep>     one measured fresh load, then full correctness; D2 rep 1 also measures storage
                               (settled: FLUSH, data directory quiet for 60 s, clean stop), reads and analytics

arm: default, zstd or tsz. Run with .venv-b3t/bin/python (taos-ws-py 0.7.0); B3_RUN=b36.
"""
import json
import sys
import threading
import time

import numpy as np

import common
import tdengine_adapter as c
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


def expected(m, ks):
    return {g: sum(m["acquisitions"][k]["channels"][s["channels"][0]]["samples"] for k in ks) for g, s in wl.GROUPS.items()}


def settle(session, want, timeout=3600):
    """Seconds until every row is queryable (TDengine needs no refresh; measured as for the other systems)."""
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < timeout:
        if c.count(session) == want:
            return time.perf_counter() - t0
        time.sleep(0.05)
    raise RuntimeError("TDengine did not settle")


def check_reads(session, m, ks):
    out = {"acquisitions": len(ks), "channels_checked": 0, "mismatches": []}
    for k in ks:
        a = m["acquisitions"][k]
        for g, spec in wl.GROUPS.items():
            n = a["channels"][spec["channels"][0]]["samples"]
            times, vals = c.read(session, a, spec["channels"], 0, n, group=g)
            want_t = a["start_ns"] + np.arange(n, dtype=np.int64) * spec["period_ns"]
            if not np.array_equal(times, want_t):
                out["mismatches"].append(f"{a['acquisition']} {g}: times")
            for ch in spec["channels"]:
                out["channels_checked"] += 1
                if common.sha(vals[ch]) != a["channels"][ch]["sha256"]:
                    out["mismatches"].append(f"{a['acquisition']} {ch}: samples")
    return out


def sample_reads(session, m, ks, data, rng):
    bad = []
    for _ in range(20):
        k = int(rng.choice(ks))
        a = m["acquisitions"][k]
        n = a["channels"]["vibration_1"]["samples"]
        length = int(rng.integers(1, 70000))
        off = int(rng.integers(0, n - length))
        _, v = c.read(session, a, ["vibration_1"], off, length)
        if common.sha(v["vibration_1"]) != common.sha(data[k]["vibration_1"][off:off + length]):
            bad.append((a["acquisition"], off, length))
    return {"intervals": 20, "mismatches": bad}


def subset(arm):
    m = common.manifest()
    data, _ = preload(m, SUBSET)
    rng = np.random.default_rng(1)
    vol = f"b3-t-subset-{arm}"
    c.start(vol, arm, "D2")
    s = c.connect(None)
    c.create(s)
    for k in SUBSET:
        c.load_one(s, m["acquisitions"][k], data[k])
    want = expected(m, SUBSET)
    settle(s, want)
    r = {"arm": arm, "settings": c.server_settings(), "counts": {"got": c.count(s), "want": want},
         "reads": check_reads(s, m, SUBSET), "intervals": sample_reads(s, m, SUBSET, data, rng)}
    r["counts"]["ok"] = r["counts"]["got"] == want
    a = m["acquisitions"][SUBSET[1]]
    c.load_one(s, a, data[SUBSET[1]])                       # identical retry
    settle(s, want)
    r["identical_retry"] = {"counts_unchanged": c.count(s) == want,
                            "reads": check_reads(s, m, [SUBSET[1]])["mismatches"]}
    altered = {ch: v.copy() for ch, v in data[SUBSET[1]].items()}
    altered["vibration_1"][1000] += 1.0
    try:
        c.load_one(s, a, altered)
        r["altered_retry"] = {"accepted": True}
    except Exception as exc:
        r["altered_retry"] = {"accepted": False, "error": str(exc)[:200]}
    settle(s, want)
    _, v = c.read(s, a, ["vibration_1"], 1000, 1)
    r["altered_retry"].update({"counts_unchanged": c.count(s) == want,
                               "stored_value_is_altered": bool(v["vibration_1"][0] == altered["vibration_1"][1000])})
    r["ok"] = r["counts"]["ok"] and not r["reads"]["mismatches"] and not r["intervals"]["mismatches"]
    s.close()
    common.remove(c.NAME, vol)
    save(f"correctness_subset_{arm}", r)


def total_samples(m):
    return sum(ch["samples"] for a in m["acquisitions"] for ch in a["channels"].values())


def _same(x, y):
    return x.shape == y.shape and np.array_equal(x.view(np.int64), y.view(np.int64))


def reads(m, data, reps=50):
    plan = common.read_plan(m)
    acq = {a["k"]: a for a in m["acquisitions"]}
    out = {}
    for rq, spec in plan.items():
        n, chans = spec["length"], spec["channels"]
        if spec.get("threads"):
            lat, lock = [], threading.Lock()
            pairs = spec["pairs"]

            def worker(sub):
                s, mine = c.connect(), []
                for k, off in sub:
                    c.read(s, acq[k], chans, off, n)
                for _ in range(reps):
                    for k, off in sub:
                        t = time.perf_counter()
                        _, v = c.read(s, acq[k], chans, off, n)
                        mine.append(time.perf_counter() - t)
                    if not _same(v[chans[0]], data[k][chans[0]][off:off + n]):
                        raise RuntimeError("read mismatch")
                s.close()
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
            print(rq, out[rq], flush=True)
            continue
        s, lat, bad = c.connect(), [], 0
        for k, off in spec["pairs"]:
            _, v = c.read(s, acq[k], chans, off, n)
            bad += sum(not _same(v[ch], data[k][ch][off:off + n]) for ch in chans)
            for _ in range(reps):
                t = time.perf_counter()
                _, v = c.read(s, acq[k], chans, off, n)
                lat.append(time.perf_counter() - t)
            bad += sum(not _same(v[ch], data[k][ch][off:off + n]) for ch in chans)
        s.close()
        out[rq] = {**common.pcts(lat), "samples_per_query": n * len(chans), "mismatches": bad}
        print(rq, out[rq], flush=True)
    return out


def analytics(m, data, reps=20):
    plan = common.read_plan(m)["R2"]
    acq = {a["k"]: a for a in m["acquisitions"]}
    s, lat, worst = c.connect(), [], 0.0
    for k, off in plan["pairs"]:
        x = data[k]["vibration_1"][off:off + plan["length"]]
        want = [x.min(), x.max(), x.mean(), np.sqrt(np.mean(x * x))]
        got = c.analytics(s, acq[k], off, plan["length"])
        if int(got[4]) != x.size:
            raise RuntimeError("analytics count mismatch")
        worst = max(worst, max(abs(g - w) / max(abs(w), 1e-300) for g, w in zip(got[:4], want)))
        for _ in range(reps):
            t = time.perf_counter()
            c.analytics(s, acq[k], off, plan["length"])
            lat.append(time.perf_counter() - t)
    s.close()
    return {**common.pcts(lat), "max_relative_difference": worst, "within_1e-12": worst <= 1e-12,
            "computed": "database"}


def reads_child():
    """B3.13: the read benchmark against the running server from a new process (common.reads_maybe_fresh)."""
    m = common.manifest()
    data, _ = preload(m, list(range(len(m["acquisitions"]))))
    common.print_reads_child(reads(m, data), analytics(m, data))


def load(arm, durability, rep):
    rep = int(rep)
    m = common.manifest()
    ks = list(range(len(m["acquisitions"])))
    data, parse_s = preload(m, ks)
    name = f"load_tdengine-{arm}_{durability}_r{rep}"
    vol = f"b3-tdengine-{arm}-{durability.lower()}-r{rep}"
    res = {"system": f"tdengine-{arm}", "durability": durability, "rep": rep, "parse_s": parse_s,
           "samples": total_samples(m), "image": c.IMAGE,
           "image_id": common.sh(["docker", "image", "inspect", c.IMAGE, "--format", "{{.Id}}"]).strip(),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S")}
    c.start(vol, arm, durability)
    s = c.connect(None)
    c.create(s)
    res["settings"] = c.server_settings()
    res["server_version"] = c.server_version(s)
    conv, acks, calls = 0.0, [], []
    with common.Stats(c.NAME) as st:
        t0 = time.perf_counter()
        for a in m["acquisitions"]:
            t = time.perf_counter()
            cv, per_call = c.load_one(s, a, data[a["k"]])
            conv += cv
            calls += per_call
            acks.append(time.perf_counter() - t)
        wall = time.perf_counter() - t0       # includes building tablets (as the other systems), also reported apart
        queryable = settle(s, expected(m, ks))
    res["ingest"] = {"wall_s": round(wall, 3), "samples_per_s": round(res["samples"] / wall),
                     "conversion_s": round(conv, 3), "ack_per_acquisition": common.pcts(acks),
                     "ack_per_call": common.pcts(calls), "queryable_after_last_ack_s": round(queryable, 3),
                     "server": st.summary()}
    t = time.perf_counter()
    res["correctness"] = {"counts": {"got": c.count(s), "want": expected(m, ks)}, "reads": check_reads(s, m, ks)}
    res["correctness"]["reads"]["seconds"] = round(time.perf_counter() - t, 1)
    res["correctness"]["counts"]["ok"] = res["correctness"]["counts"]["got"] == res["correctness"]["counts"]["want"]
    res["correct"] = res["correctness"]["counts"]["ok"] and not res["correctness"]["reads"]["mismatches"]
    save(name, res)
    if not res["correct"]:
        raise SystemExit("correctness failed")
    if durability == "D2" and rep == 1:
        t = time.perf_counter()
        c.flush(s)
        res["flush_s"] = round(time.perf_counter() - t, 3)
        res["settle_files_s"] = c.settle_files(vol)
        s.close()
        common.stop(c.NAME)
        res["storage"] = c.storage(vol)
        c.restart(vol, arm, durability)
        res["reads"], res["analytics"] = common.reads_maybe_fresh(__file__, [], lambda: (reads(m, data),
                                                                                         analytics(m, data)))
        common.stop(c.NAME)   # COMPACT DATABASE is not available in the open-source edition (development)
    else:
        s.close()
    common.remove(c.NAME, vol)
    save(name, res)


if __name__ == "__main__":
    {"subset": subset, "load": load, "reads-child": reads_child}[sys.argv[1]](*sys.argv[2:])
