#!/usr/bin/env python3
"""F1 development harness on PyScrew (exploratory; protocol draft in PROTOCOL.md, not frozen).

Episode model: one recording per workpiece and screw position (left/right), its cycles concatenated in run order
(torque, Nm, nominal 833.3 Hz from the 1.2 ms time step; the few irregular steps are ignored, as a station recording
at a fixed rate would). One episode per cycle; NOK cycles are the known events; the run order within a workpiece is
the slow drift (thread wear). Each cycle is appended on its own, as a station sends it, so it is exactly one chunk:
the ladder removes only chunks entirely inside an episode (development runs with 4,096- and 256-sample chunks over
concatenated cycles could remove none, or left the partial edge chunks of every cycle).

Per policy and storage budget: a fresh copy of one template database (chunks, search envelopes of 512 samples with
stride 128, episodes), the policy engine (tools/memory) moves episodes down the ladder until the budget is met, then
every query searches the archive again. Queries are the first 512 raw samples of episodes (all NOK episodes of at
least 512 samples, plus 300 random OK ones), from the source data, so they do not depend on what was forgotten; the
query's own episode is excluded from its hits.

Measures: recall@10 against the full archive's top ten (1 − retrieval regret), separately for NOK and OK queries;
answer quality, the median over queries of the mean top-10 distance in the forgotten archive over the full archive's;
relevance, the share of NOK cycles among a NOK query's ten hits (as E5's precision at ten), full and forgotten;
the share of NOK episodes still searchable (level 0); bytes before and after; policy run time.
Novelty (v1): distance of an episode's first window to its nearest neighbour in the full archive (excluding itself),
scaled to [0, 1]; a static approximation of "distance to the nearest kept episode".

  .venv-b3/bin/python evaluation/forgetting_f1/f1_pyscrew.py s01 [--image morletdb:mem]
"""
import argparse
import csv
import json
import random
import statistics
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools/memory"))
from policy import (BudgetSelector, DepthPicker, ExemptFilter, KeepValue, novelty_importance,  # noqa: E402
                    novelty_policy, recency, run_task)
from store_pg import PgStore  # noqa: E402
from dataclasses import replace  # noqa: E402


class CycleStore(PgStore):
    """Age is recording time (the station's run number), not the time the episode row was written; context keys
    other than the run index are not used by the policies."""

    def episodes(self):
        eps = super().episodes()
        last = max(e.context["run_id"] for e in eps)
        return [replace(e, age_s=float(last - e.context["run_id"]), context={"run_index": e.context["run_index"]})
                for e in eps]

DATA = ROOT / "data/pyscrew/zenodo-15393134"
OUT = Path(__file__).resolve().parent / "results" / "dev"
RATE = 1 / 0.0012
WIN, STRIDE, TOPK = 512, 128, 10
CHUNK = 4096  # each cycle is appended on its own (366-769 samples), so it is exactly one chunk
BUDGETS = (0.50, 0.25, 0.10, 0.05)
NAME, PORT, PW = "f1-db", 55440, "f1-dev-only"
DSN = f"host=127.0.0.1 port={PORT} user=postgres password={PW}"


def image_id(image):
    r = subprocess.run(["docker", "image", "inspect", "-f", "{{.Id}}", image], capture_output=True, text=True)
    return r.stdout.strip() or None


def load(scenario):
    """{recording_id: (samples, [(episode_start, episode_end, run_id, nok, run_index)])}."""
    base = next(DATA.glob(f"{scenario}"))
    rows = list(csv.DictReader(open(base / "labels.csv")))
    groups = {}
    for r in rows:
        groups.setdefault((r["workpiece_id"], r["workpiece_location"]), []).append(r)
    recs = {}
    for (wp, loc), rs in sorted(groups.items()):
        rs.sort(key=lambda r: int(r["run_id"]))
        parts, eps, pos = [], [], 0
        for i, r in enumerate(rs):
            d = json.load(open(next(base.glob(f"json/*/{r['file_name']}"))))
            t = [v for s in d["tightening steps"] for v in s["graph"]["torque values"]]
            parts.append(np.asarray(t, dtype=np.float64))
            eps.append((pos, pos + len(t), int(r["run_id"]), r["workpiece_result"] == "NOK", i))
            pos += len(t)
        recs[f"{wp}-{loc}"] = (np.concatenate(parts), eps)
    return recs


def sh(*a):
    return subprocess.run(a, check=True, capture_output=True, text=True).stdout


def start_db(image):
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    sh("docker", "run", "-d", "--name", NAME, "-e", f"POSTGRES_PASSWORD={PW}", "-p", f"127.0.0.1:{PORT}:5432",
       "--cpus", "8", "--memory", "16g", image)
    for _ in range(120):
        try:
            with psycopg.connect(DSN + " dbname=postgres", connect_timeout=2) as c:
                c.execute("SELECT 1")
            break
        except Exception:
            time.sleep(0.5)
    time.sleep(3)


def build_template(recs):
    with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS f1base")
        c.execute("CREATE DATABASE f1base")
    with psycopg.connect(DSN + " dbname=f1base", autocommit=True) as c:
        c.execute("CREATE EXTENSION pg_morlet")
        c.execute("SELECT morlet_create_stream_collection('public','f1')")
        for rid, (x, eps) in recs.items():
            c.execute("SELECT morlet_start_recording('f1',%s,%s,'torque','Nm','{}',512,256,'zscore','none')", (rid, RATE))
            with c.pipeline():
                for a, b, *_ in eps:   # one append per cycle, as a station sends them: chunk boundaries = cycle boundaries
                    c.execute("SELECT morlet_append_chunk_raw('f1',%s,%s,%s)", (rid, a, x[a:b].astype("<f8").tobytes()))
        c.execute("SELECT morlet_enable_envelopes('f1')")
        for rid in recs:
            c.execute("SELECT morlet_build_envelopes('f1',%s,%s,%s,1000000)", (rid, WIN, STRIDE))
        c.execute("SELECT morlet_enable_memory('f1')")
        rows = [{"recording_id": rid, "start_sample": a, "end_sample": b,
                 "context": {"run_id": run, "run_index": i}, "importance": 1.0 if nok else 0.0}
                for rid, (_, eps) in recs.items() for a, b, run, nok, i in eps]
        c.execute("SELECT count(*) FROM morlet_add_episodes('f1',%s)", (json.dumps(rows),))
        c.execute("VACUUM ANALYZE")


SEARCH = {"rate": RATE, "channel": "torque", "unit": "Nm"}   # the seismic harness overrides these


def search(conn, q):
    return conn.execute("SELECT recording_id,start_sample,distance FROM morlet_search_envelopes('f1',%s,%s,%s,%s,"
                        "'{}',%s)", (q.tolist(), SEARCH["rate"], SEARCH["channel"], SEARCH["unit"], TOPK + 6)).fetchall()


def top10(hits, rid, a, b):
    """Hits outside the query's own episode, first ten (recording, window start)."""
    return [(r, s) for r, s, _ in hits if not (r == rid and s < b and s + WIN > a)][:TOPK]


def top10_full(hits, rid, a, b):
    """The same, with distances."""
    return [(r, s, d) for r, s, d in hits if not (r == rid and s < b and s + WIN > a)][:TOPK]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario", default="s01")
    ap.add_argument("--image", default="morletdb:mem")
    ap.add_argument("--queries-ok", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20261105)
    a = ap.parse_args()
    t0 = time.time()
    recs = load(a.scenario)
    eps = [(rid, s, e, run, nok, i) for rid, (_, ee) in recs.items() for s, e, run, nok, i in ee]
    print(f"{len(recs)} recordings, {len(eps)} episodes, {sum(e[4] for e in eps)} NOK, "
          f"{sum(len(x) for x, _ in recs.values())} samples, load {time.time() - t0:.0f} s", flush=True)
    start_db(a.image)
    build_template(recs)
    print(f"template built {time.time() - t0:.0f} s", flush=True)
    rng = random.Random(a.seed)
    long_eps = [e for e in eps if e[2] - e[1] >= WIN]
    queries = [e for e in long_eps if e[4]] + rng.sample([e for e in long_eps if not e[4]], a.queries_ok)

    def qsamples(e):
        return recs[e[0]][0][e[1]:e[1] + WIN]

    # Reference top ten on the full archive, and novelty (nearest-neighbour distance) for every episode
    # NOK membership of every window start, for label precision: a hit is NOK when its window starts in a NOK cycle
    nok_at = {}
    for rid, (_, ee) in recs.items():
        nok_at[rid] = [(s, e, nok) for s, e, _, nok, _ in ee]

    def is_nok(rid, start):
        return any(s <= start < e and nok for s, e, nok in nok_at[rid])

    with psycopg.connect(DSN + " dbname=f1base") as c:
        ref_full = {(e[0], e[1]): top10_full(search(c, qsamples(e)), e[0], e[1], e[2]) for e in queries}
        ref = {k: [(r, s) for r, s, _ in v] for k, v in ref_full.items()}
        nn = {}
        for e in long_eps:
            hits = [h for h in search(c, qsamples(e)) if not (h[0] == e[0] and h[1] < e[2] and h[1] + WIN > e[1])]
            nn[(e[0], e[1])] = hits[0][2] if hits else None
    top = max(v for v in nn.values() if v is not None)
    print(f"reference and novelty {time.time() - t0:.0f} s", flush=True)

    def novelty(ep, kept):
        v = nn.get((ep.recording_id, ep.start_sample))
        return 1.0 if v is None else v / top

    policies = {"recency": (recency(), None, False), "random": (KeepValue(name="random"), a.seed, False),
                "novelty": (novelty_policy(novelty), None, False),
                "novelty+importance": (novelty_importance(novelty), None, True)}
    out = {"scenario": a.scenario, "image": a.image, "image_id": image_id(a.image), "episodes": len(eps), "nok": sum(e[4] for e in eps),
           "queries": {"nok": sum(q[4] for q in queries), "ok": a.queries_ok}, "window": WIN, "stride": STRIDE,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "runs": []}
    for pname, (scorer, seed, exempt_nok) in policies.items():
        for budget in BUDGETS:
            db = f"f1_{pname.replace('+', '_')}_{int(budget * 100)}"
            with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f"DROP DATABASE IF EXISTS {db}")
                c.execute(f"CREATE DATABASE {db} TEMPLATE f1base")
            with psycopg.connect(DSN + f" dbname={db}") as c:
                if exempt_nok:
                    c.execute("UPDATE f1_episodes SET exempt=true WHERE importance=1")
                    c.commit()
                store = CycleStore(c, "f1")
                full = store.bytes_at()
                picker = DepthPicker(scorer, [ExemptFilter()], seed=seed)
                t = time.time()
                runs = 0
                while runs < 10 and run_task(store, BudgetSelector(int(full * budget)), picker, dry_run=False):
                    runs += 1
                policy_s = time.time() - t
                after = store.bytes_at()
                rec = {"nok": [], "ok": []}
                dist = {"nok": [], "ok": []}
                best = {"nok": [], "ok": []}
                prec = {"full": [], "forgotten": []}
                per_query = []
                for q in queries:
                    hits = top10_full(search(c, qsamples(q)), q[0], q[1], q[2])
                    got = {(r, s) for r, s, _ in hits}
                    want = ref[(q[0], q[1])]
                    key = "nok" if q[4] else "ok"
                    rec[key].append(len(got & set(want)) / max(1, len(want)))
                    # answer quality: mean top-10 distance, forgotten over full archive (1 = as good)
                    full_d = statistics.mean(d for _, _, d in ref_full[(q[0], q[1])]) or 1e-12
                    dist[key].append((statistics.mean(d for _, _, d in hits) if hits else float("inf")) / full_d)
                    # primary (PROTOCOL): nearest hit, forgotten over full archive (is something as similar still found?)
                    full_b = max(ref_full[(q[0], q[1])][0][2], 1e-12)
                    best[key].append(hits[0][2] / full_b if hits else float("inf"))
                    if q[4]:   # relevance: share of NOK cycles among a NOK query's hits (as E5's precision at ten)
                        prec["full"].append(sum(is_nok(r, s) for r, s, _ in ref_full[(q[0], q[1])]) / TOPK)
                        prec["forgotten"].append(sum(is_nok(r, s) for r, s, _ in hits) / TOPK)
                    per_query.append({"query": f"{q[0]}:{q[1]}", "group": q[0].rsplit("-", 1)[0], "rare": q[4],
                                      "best_ratio": round(best[key][-1], 6),
                                      "distance_ratio": round(dist[key][-1], 6), "recall10": rec[key][-1]})
                levels = dict(c.execute("SELECT level, count(*) FROM f1_episodes GROUP BY 1").fetchall())
                nok_searchable = c.execute("SELECT avg((level=0)::int) FROM f1_episodes WHERE importance=1").fetchone()[0]
            with psycopg.connect(DSN + " dbname=postgres", autocommit=True) as c:
                c.execute(f"DROP DATABASE {db}")
            r = {"policy": pname, "budget": budget, "bytes_full": full, "bytes_after": after,
                 "fraction_kept": round(after / full, 4), "policy_runs": runs, "policy_s": round(policy_s, 1),
                 "recall10_nok": round(statistics.mean(rec["nok"]), 4), "recall10_ok": round(statistics.mean(rec["ok"]), 4),
                 "best_ratio_nok": round(statistics.median(best["nok"]), 4),
                 "best_ratio_ok": round(statistics.median(best["ok"]), 4),
                 "distance_ratio_nok": round(statistics.median(dist["nok"]), 4),
                 "distance_ratio_ok": round(statistics.median(dist["ok"]), 4),
                 "nok_precision10_full": round(statistics.mean(prec["full"]), 4),
                 "nok_precision10": round(statistics.mean(prec["forgotten"]), 4),
                 "nok_searchable": round(float(nok_searchable), 4), "levels": {str(k): v for k, v in levels.items()},
                 "per_query": per_query}
            out["runs"].append(r)
            print(json.dumps({k: v for k, v in r.items() if k != "per_query"}), flush=True)
    subprocess.run(["docker", "rm", "-f", NAME], capture_output=True)
    outdir = OUT if a.scenario == "s01" else OUT.parent      # s01 is development data; others are confirmations
    outdir.mkdir(parents=True, exist_ok=True)
    p = outdir / f"f1_{a.scenario}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    p.write_text(json.dumps(out, indent=1))
    print(p, f"total {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
