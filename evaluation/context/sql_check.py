#!/usr/bin/env python3
"""Engineering check: evaluation C1 reproduced through pg_morlet's context API.

Every milling run becomes six single-channel recordings with context (machine, doc, feed,
material) and occurrence (run). Labels keep evaluation runs and non-sharp reference runs out of
history. For each scored run, morlet_compare_to_history gives the relative deviation of the C1
value of each channel. The run's score is the maximum |deviation|; Spearman rho with VB must
match c1.py. It reuses the consumed C1 evaluation, so it checks the implementation only.

Usage: sql_check.py MILL_MAT OUT   (needs numpy, scipy, psycopg; MORLET_DSN for the database)
"""
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import psycopg
from scipy import io as sio
from scipy import stats

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import c1  # noqa: E402

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_context_check")
# C1 value -> (channel field, simple-v1 value name)
CHANNELS = {"dc": ("smcDC", "p75"), "ac": ("smcAC", "std"), "vt": ("vib_table", "p75"),
            "vs": ("vib_spindle", "p75"), "at": ("AE_table", "p75"), "as": ("AE_spindle", "p75")}


def main():
    path, out = Path(sys.argv[1]), Path(sys.argv[2])
    out.mkdir(parents=True, exist_ok=True)
    mill = sio.loadmat(path, squeeze_me=True, struct_as_record=False)["mill"]
    # The database rejects samples beyond +/-1e9; case 2 run 1 holds such corrupt values. The
    # reference analysis is c1.analyse without the rejected runs (a C1 sensitivity check).
    rejected = sorted({(int(r.case), int(r.run)) for r in mill for _, (f, _) in CHANNELS.items()
                       if not np.all(np.isfinite(getattr(r, f))) or np.abs(getattr(r, f)).max() > 1e9})
    mill = [r for r in mill if (int(r.case), int(r.run)) not in rejected]
    reference = c1.analyse([r for r in c1.load(path) if (r["case"], r["run"]) not in rejected], resamples=2000)
    expected = {(r["case"], r["run"]): r for r in reference["rows"]}
    report = {"runs": len(mill), "rejected_runs": rejected,
              "c1_without_rejected": {k: reference[k] for k in ("rho_conditioned", "rho_context_free", "delta",
                                                                 "ci95", "decision", "excluded_cases")}}
    with psycopg.connect(DSN, autocommit=True) as conn:
        for t in ("milling_labels", "milling_ctx_values", "milling_ctx_segments", "milling_ctx",
                  "milling_chunks", "milling_segments", "milling"):
            conn.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
        conn.execute("SELECT morlet_create_stream_collection('public','milling')")
        conn.execute("SELECT morlet_enable_context('milling')")
        t0 = time.perf_counter()
        with conn.transaction():
            for r in mill:
                case, run = int(r.case), int(r.run)
                vb = float(r.VB) if np.size(r.VB) == 1 and np.isfinite(float(r.VB)) else None
                for value, (field, _) in CHANNELS.items():
                    rid = f"c{case:02d}r{run:03d}-{value}"
                    x = np.asarray(getattr(r, field), float)
                    conn.execute("SELECT morlet_start_recording('milling',%s,250,%s,'V',%s,512,256,'zscore','none',1)",
                                 (rid, field, json.dumps({"case": case})))
                    conn.execute("SELECT morlet_append_chunk('milling',%s,0,%s::float8[])", (rid, x.tolist()))
                    ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": len(x)}
                           for k, v in (("machine", "MC-510V"), ("doc", float(r.DOC)), ("feed", float(r.feed)),
                                        ("material", int(r.material)), ("run", f"{case}/{run}"))]
                    conn.execute("SELECT morlet_add_context('milling',%s,%s)", (f"load-{rid}", json.dumps(ctx)))
                    conn.execute("SELECT morlet_build_context_segments('milling',%s,ARRAY['machine','doc','feed','material'],"
                                 "ARRAY['run'])", (rid,))
                    conn.execute("SELECT morlet_build_context_values('milling',%s)", (rid,))
                    if case in c1.EVALUATION:
                        conn.execute("SELECT morlet_add_label('milling',%s,0,%s,'evaluation')", (rid, len(x)))
                    elif vb is None or vb > c1.SHARP_VB:
                        conn.execute("SELECT morlet_add_label('milling',%s,0,%s,'not_sharp')", (rid, len(x)))
        report["load_s"] = time.perf_counter() - t0
        rows, ms, worst = [], [], 0.0
        for key, exp in sorted(expected.items()):
            case, run = key
            scores = {}
            for arm, keys in (("conditioned", "{}"), ("context_free", "{machine}")):
                devs = []
                for value, (_, name) in CHANNELS.items():
                    seg = conn.execute("SELECT segment_id FROM milling_ctx_segments WHERE recording_id=%s",
                                       (f"c{case:02d}r{run:03d}-{value}",)).fetchone()[0]
                    t1 = time.perf_counter()
                    rel, status = conn.execute(
                        "SELECT relative_deviation,status FROM morlet_compare_to_history('milling',%s,%s::text[],"
                        "exclude_labels=>ARRAY['evaluation','not_sharp'],min_history=>3) WHERE value_name=%s",
                        (seg, keys, name)).fetchone()
                    ms.append(1e3 * (time.perf_counter() - t1))
                    if rel is None:
                        raise ValueError(f"{case}/{run} {value}: {status}")
                    devs.append(abs(rel))
                scores[arm] = max(devs)
                worst = max(worst, abs(scores[arm] - exp[arm]))
            rows.append({"case": case, "run": run, "vb": exp["vb"], **scores})
        vb = [x["vb"] for x in rows]
        report.update({
            "scored_runs": len(rows),
            "rho_conditioned": float(stats.spearmanr([x["conditioned"] for x in rows], vb).statistic),
            "rho_context_free": float(stats.spearmanr([x["context_free"] for x in rows], vb).statistic),
            "max_abs_score_difference_vs_c1": worst,
            "compare_latency_ms": {"p50": float(np.percentile(ms, 50)), "p95": float(np.percentile(ms, 95))},
            "storage": dict(zip(("recordings", "chunk_table_bytes", "total_bytes"), conn.execute(
                "SELECT recordings,chunk_table_bytes,total_bytes FROM morlet_collection_storage('milling')").fetchone())),
        })
    (out / "sql-check.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
