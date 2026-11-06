#!/usr/bin/env python3
"""F1 on seismic data (PROTOCOL.md): the held-out CX gallery of E5-H/BL1-C with the EMSC catalogue as known events.

Episode model: recordings as BL1-C loads them (station-day segments of CX HHZ at 100 Hz, PB05, PB11, PB14, PB18,
2021-03-01..14); each recording is cut from its start into episodes of 15 chunks (61,440 samples, about 10.2 min;
the last one shorter); a known-event episode contains the predicted P arrival (iasp91, as E5) of any catalogued
event at that station. Search windows of 4,096 samples with stride 2,048 (as BL1-C). Rare queries: E5-H's query
rule (M >= 2.5 within 2 degrees, P about 5 s into a stride-aligned window); typical queries: 300 random windows
(seeded) whose episode holds no known event. Query samples come from the source files. Age is recording time.

  .venv-seismic/bin/python -I evaluation/forgetting_f1/f1_seismic.py data/cx-confirm-2021 data/e5h-catalog
"""
import json
import random
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import psycopg
from obspy import UTCDateTime

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "evaluation/task_relevance"))
sys.path.insert(0, str(ROOT / "tools/memory"))
import f1_pyscrew as f  # noqa: E402  (database, search and reference helpers)
from e5 import MAX_DIST, MIN_MAG, arrivals, catalogue, stations  # noqa: E402
from policy import (BudgetSelector, DepthPicker, ExemptFilter, KeepValue, novelty_importance,  # noqa: E402
                    novelty_policy, recency, run_task)
from store_pg import PgStore  # noqa: E402

RATE, CHUNK, WIN, STRIDE, EPISODE_CHUNKS, LEAD = 100.0, 4096, 4096, 2048, 15, 5.0
f.WIN, f.SEARCH = WIN, {"rate": RATE, "channel": "HHZ", "unit": "counts"}


class TimeStore(PgStore):
    def episodes(self):
        eps = super().episodes()
        last = max(e.context["t0"] for e in eps)
        return [replace(e, age_s=float(last - e.context["t0"]), context={}) for e in eps]


def load(root):
    recs = {}
    for m in json.loads((root / "manifest.json").read_text()):
        for i, seg in enumerate(m["segments"]):
            if seg["samples"] < WIN:
                continue
            path = root / seg["file"]
            if not path.exists() and m.get("role"):          # development manifest: files under <role>/
                path = root / m["role"] / seg["file"]
            x = np.load(path).astype(np.float64)
            rid = f"{m['station']}.d{m['day']:02d}.{i}" if "day" in m else f"{m['station']}.{m.get('role', 'x')}.{i}"
            recs[rid] = (m["station"], UTCDateTime(seg["start"]), seg["rate"], x)
    return recs


def main(data_dir, cat_dir, image="morletdb:mem", typical=300, seed=20261105, limit=None):
    """limit: use only the first `limit` recordings and fewer typical queries (smoke check on development data)."""
    t0 = time.time()
    recs = load(Path(data_dir))
    if limit:
        recs = dict(list(recs.items())[:limit])
        typical = min(typical, 20)
    events = catalogue(Path(cat_dir) / "emsc_events.txt")
    sts = stations(Path(cat_dir) / "stations.txt")
    arr = arrivals(events, sts)
    ep_len = CHUNK * EPISODE_CHUNKS
    episodes = []          # (rid, start, end, known, t0_epoch)
    for rid, (st, start, rate, x) in recs.items():
        p_samples = [int(round((float(tp) - float(start)) * RATE)) for (eid, s), (tp, _) in arr.items() if s == st]
        for a in range(0, len(x), ep_len):
            b = min(a + ep_len, len(x))
            known = any(a <= p < b for p in p_samples)
            episodes.append((rid, a, b, known, float(start) + a / RATE))
    # Rare queries: E5-H's rule; typical: random windows in episodes without known events.
    rare = []
    for e in events:
        if not (e["mag"] >= MIN_MAG):
            continue
        for s in sts:
            if (e["id"], s) not in arr or arr[(e["id"], s)][1] > MAX_DIST:
                continue
            target = float(arr[(e["id"], s)][0]) - LEAD
            for rid, (st, start, rate, x) in recs.items():
                if st != s:
                    continue
                q = int(np.floor((target - float(start)) * RATE / STRIDE)) * STRIDE
                if 0 <= q and q + WIN <= len(x):
                    rare.append((rid, q, q + WIN, True, e["id"]))
    rare = list({(r[0], r[1]): r for r in rare}.values())
    rng = random.Random(seed)
    quiet = [e for e in episodes if not e[3] and e[2] - e[1] >= WIN]
    typ = []
    for e in rng.sample(quiet, typical):
        s = e[1] + rng.randrange(0, (e[2] - e[1] - WIN) // STRIDE + 1) * STRIDE
        typ.append((e[0], s, s + WIN, False, None))
    queries = rare + typ
    print(f"{len(recs)} recordings, {len(episodes)} episodes, {sum(e[3] for e in episodes)} known, "
          f"{len(rare)} rare and {len(typ)} typical queries, load {time.time() - t0:.0f} s", flush=True)

    f.start_db(image)
    with psycopg.connect(f.DSN + " dbname=postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS f1base")
        c.execute("CREATE DATABASE f1base")
    with psycopg.connect(f.DSN + " dbname=f1base", autocommit=True) as c:
        c.execute("CREATE EXTENSION pg_morlet")
        c.execute("SELECT morlet_create_stream_collection('public','f1')")
        for rid, (st, start, rate, x) in recs.items():
            c.execute("SELECT morlet_start_recording('f1',%s,%s,'HHZ','counts',%s,512,256,'zscore','none')",
                      (rid, rate, json.dumps({"station": st, "start": str(start)})))
            for s in range(0, len(x), CHUNK * 64):
                c.execute("SELECT morlet_append_chunks_raw('f1',%s,%s,%s,%s)",
                          (rid, s, x[s:s + CHUNK * 64].astype("<f8").tobytes(), CHUNK))
        c.execute("SELECT morlet_enable_envelopes('f1')")
        for rid in recs:
            c.execute("SELECT morlet_build_envelopes('f1',%s,%s,%s,1000000)", (rid, WIN, STRIDE))
        c.execute("SELECT morlet_enable_memory('f1')")
        rows = [{"recording_id": r, "start_sample": a, "end_sample": b, "context": {"t0": t},
                 "importance": 1.0 if k else 0.0} for r, a, b, k, t in episodes]
        for i in range(0, len(rows), 50000):
            c.execute("SELECT count(*) FROM morlet_add_episodes('f1',%s)", (json.dumps(rows[i:i + 50000]),))
        c.execute("VACUUM ANALYZE")
    print(f"template built {time.time() - t0:.0f} s", flush=True)

    def qs(q):
        return recs[q[0]][3][q[1]:q[1] + WIN]

    with psycopg.connect(f.DSN + " dbname=f1base") as c:
        ref_full = {(q[0], q[1]): f.top10_full(f.search(c, qs(q)), q[0], q[1], q[2]) for q in queries}
        nn = {}
        for r, a, b, k, t in episodes:
            if b - a < WIN:
                continue
            hits = [h for h in f.search(c, recs[r][3][a:a + WIN]) if not (h[0] == r and h[1] < b and h[1] + WIN > a)]
            nn[(r, a)] = hits[0][2] if hits else None
    top = max(v for v in nn.values() if v is not None)
    ref = {k: [(r, s) for r, s, _ in v] for k, v in ref_full.items()}
    print(f"reference and novelty {time.time() - t0:.0f} s", flush=True)

    def novelty(ep, kept):
        v = nn.get((ep.recording_id, ep.start_sample))
        return 1.0 if v is None else v / top

    policies = {"recency": (recency(), None, False), "random": (KeepValue(name="random"), seed, False),
                "novelty": (novelty_policy(novelty), None, False),
                "novelty+importance": (novelty_importance(novelty), None, True)}
    out = {"dataset": "seismic held-out (E5-H)" if not limit else "seismic smoke (development)", "image": image,
           "image_id": f.image_id(image), "episodes": len(episodes),
           "known": sum(e[3] for e in episodes), "queries": {"rare": len(rare), "typical": len(typ)},
           "episode_samples": ep_len, "window": WIN, "stride": STRIDE,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []}
    for pname, (scorer, pseed, exempt) in policies.items():
        for budget in f.BUDGETS:
            db = f"f1_{pname.replace('+', '_')}_{int(budget * 100)}"
            with psycopg.connect(f.DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f"DROP DATABASE IF EXISTS {db}")
                c.execute(f"CREATE DATABASE {db} TEMPLATE f1base")
            with psycopg.connect(f.DSN + f" dbname={db}") as c:
                if exempt:
                    c.execute("UPDATE f1_episodes SET exempt=true WHERE importance=1")
                    c.commit()
                store = TimeStore(c, "f1")
                full = store.bytes_at()
                picker = DepthPicker(scorer, [ExemptFilter()], seed=pseed)
                t = time.time()
                runs = 0
                while runs < 10 and run_task(store, BudgetSelector(int(full * budget)), picker, dry_run=False):
                    runs += 1
                policy_s, after = time.time() - t, store.bytes_at()
                per_query = []
                for q in queries:
                    hits = f.top10_full(f.search(c, qs(q)), q[0], q[1], q[2])
                    fd = statistics.mean(d for _, _, d in ref_full[(q[0], q[1])]) or 1e-12
                    fb = max(ref_full[(q[0], q[1])][0][2], 1e-12)
                    per_query.append({"query": f"{q[0]}:{q[1]}", "group": q[4] or f"typical:{q[0]}", "rare": q[3],
                                      "best_ratio": round(hits[0][2] / fb, 6) if hits else float("inf"),
                                      "distance_ratio": round((statistics.mean(d for _, _, d in hits) if hits
                                                               else float("inf")) / fd, 6),
                                      "recall10": len({(r, s) for r, s, _ in hits} & set(ref[(q[0], q[1])])) / TOPK})
                known = c.execute("SELECT avg((level=0)::int) FROM f1_episodes WHERE importance=1").fetchone()[0]
                levels = dict(c.execute("SELECT level, count(*) FROM f1_episodes GROUP BY 1").fetchall())
            with psycopg.connect(f.DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f"DROP DATABASE {db}")
            rr = [p for p in per_query if p["rare"]]
            tt = [p for p in per_query if not p["rare"]]
            r = {"policy": pname, "budget": budget, "bytes_full": full, "bytes_after": after,
                 "fraction_kept": round(after / full, 4), "policy_runs": runs, "policy_s": round(policy_s, 1),
                 "known_searchable": None if known is None else round(float(known), 4),
                 "best_ratio_rare": round(statistics.median(p["best_ratio"] for p in rr), 4) if rr else None,
                 "best_ratio_typical": round(statistics.median(p["best_ratio"] for p in tt), 4),
                 "distance_ratio_rare": round(statistics.median(p["distance_ratio"] for p in rr), 4) if rr else None,
                 "distance_ratio_typical": round(statistics.median(p["distance_ratio"] for p in tt), 4),
                 "recall10_rare": round(statistics.mean(p["recall10"] for p in rr), 4) if rr else None,
                 "recall10_typical": round(statistics.mean(p["recall10"] for p in tt), 4),
                 "levels": {str(k): v for k, v in levels.items()}, "per_query": per_query}
            out["runs"].append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "per_query"}), flush=True)
    import subprocess
    subprocess.run(["docker", "rm", "-f", f.NAME], capture_output=True)
    p = HERE / "results" / ("dev" if limit else "") / f"f1_seismic_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(p, f"total {time.time() - t0:.0f} s")


TOPK = f.TOPK

if __name__ == "__main__":
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    main(sys.argv[1], sys.argv[2], limit=lim)
