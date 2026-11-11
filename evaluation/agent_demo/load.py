#!/usr/bin/env python3
"""Stage 2 of the agent demo: load the extracted M01 runs into MorletDB (needs numpy and psycopg).

  DATABASE_URL=postgresql://morlet:morlet-demo-only@127.0.0.1:54351/morlet \
      python evaluation/agent_demo/load.py EXTRACT_DIR [OUT_JSON]

EXTRACT_DIR is what extract_m01.py wrote. Creates public.bosch_m01 (one recording per run and axis, integer
counts, 2 kHz), search envelopes (4,096 samples, stride 2,048), context (machine and operation per recording,
run as the occurrence) with segments and simple-v1 values, the dataset's bad label on every bad recording, a
logged decision "s1:flagged" per bad run (the flag is the dataset label; no model is run), and the read-only role
agent_reader. See QUESTIONS.md for the layout.
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "ingest"))
sys.path.insert(0, str(ROOT / "tools" / "mcp"))
from parquet_import import Config, import_files  # noqa: E402
from morlet_import import build, prepare  # noqa: E402
import decision  # noqa: E402

COLLECTION = "public.bosch_m01"
RATE = 2000.0
WINDOW = 4096
AXES = ("x", "y", "z")
READER, READER_PASSWORD = "agent_reader", "agent-demo-only"
MONTHS = {"Feb": 2, "Aug": 8}
CITATION = "dataset label (Tnani, Feil and Diepold, Procedia CIRP 107 (2022), doi:10.1016/j.procir.2022.04.022)"


def nominal_starts(runs):
    """First day of the period's month plus 10 minutes per run, in file-name order within (operation, period)."""
    out, groups = {}, {}
    for r in sorted(runs, key=lambda r: (r["operation"], r["period"], r["index"], r["run"])):
        groups.setdefault((r["operation"], r["period"]), []).append(r)
    for (_, period), rs in groups.items():
        month, year = period.split("_")
        t0 = datetime(int(year), MONTHS[month], 1, tzinfo=timezone.utc)
        for k, r in enumerate(rs):
            out[r["run"]] = (t0 + timedelta(minutes=10 * k)).isoformat().replace("+00:00", "Z")
    return out


def main():
    src = Path(sys.argv[1])
    out_json = Path(sys.argv[2]) if len(sys.argv) > 2 else None
    manifest = json.loads((src / "manifest.json").read_text())
    runs = manifest["runs"]
    assert all(r["machine"] == "M01" for r in runs)
    starts = nominal_starts(runs)
    by_path = {str(src / f"{r['run']}.npy"): r for r in runs}
    dsn = os.environ["DATABASE_URL"]
    report = {"collection": COLLECTION, "dataset_commit": manifest["dataset_commit"], "runs": len(runs)}
    t = time.perf_counter()

    def reader(path, config, mapping):
        r = by_path[str(path)]
        a = np.load(path)
        for i, ax in enumerate(AXES):
            meta = {"machine": r["machine"], "operation": r["operation"], "period": r["period"], "run": r["run"],
                    "axis": ax, "label": r["label"], "file": r["file"], "sha256": r["sha256"],
                    "start": starts[r["run"]],
                    "time_basis": "nominal: period month + 10 min per run in file order; not a real timestamp"}
            spec = (RATE, ax, "count", meta)
            x = a[:, i].astype(np.float64)
            for s in range(0, len(x), config.chunk_size):
                yield f"{r['run']}-{ax}", spec, s, x[s:s + config.chunk_size]

    with psycopg.connect(dsn) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS pg_morlet")
        report["morlet_version"] = conn.execute("SELECT morlet_version()").fetchone()[0]
        if conn.execute("SELECT to_regclass(%s)", (COLLECTION,)).fetchone()[0] is not None:
            raise SystemExit(f"{COLLECTION} exists already; start from an empty database")
        prepare(conn, COLLECTION, True)
        imp = import_files(list(by_path), COLLECTION, Config(), reader=reader, conn=conn)
        report["import"] = {k: imp.get(k) for k in ("status", "committed_sample_submissions", "rpc_calls")}
        report["import_s"] = round(time.perf_counter() - t, 1)
        ids = [f"{r['run']}-{ax}" for r in runs for ax in AXES]
        t1 = time.perf_counter()
        built = build(conn, COLLECTION, ids, WINDOW, False)
        report["envelope_windows"] = sum(built.values())
        report["envelopes_s"] = round(time.perf_counter() - t1, 1)

        t1 = time.perf_counter()
        notes = {}
        try:
            conn.execute("SELECT morlet_enable_context(%s::regclass)", (COLLECTION,))
            conn.commit()
            for r in runs:
                for ax in AXES:
                    rid = f"{r['run']}-{ax}"
                    n = r["samples"]
                    ctx = [{"recording_id": rid, "key": k, "value": v, "start_sample": 0, "end_sample": n}
                           for k, v in (("machine", r["machine"]), ("operation", r["operation"]), ("run", r["run"]))]
                    conn.execute("SELECT morlet_add_context(%s::regclass, %s, %s)", (COLLECTION, f"ctx-{rid}",
                                                                                     json.dumps(ctx)))
                    conn.execute("SELECT morlet_build_context_segments(%s::regclass, %s, ARRAY['machine','operation'], "
                                 "ARRAY['run'])", (COLLECTION, rid))
                    conn.execute("SELECT morlet_build_context_values(%s::regclass, %s)", (COLLECTION, rid))
                    if r["label"] == "bad":
                        conn.execute("SELECT morlet_add_label(%s::regclass, %s, 0, %s, 'bad', %s)",
                                     (COLLECTION, rid, n, CITATION))
                if r["label"] == "bad":
                    decision.log_decision(conn, COLLECTION, [(f"{r['run']}-{ax}", 0, r["samples"]) for ax in AXES],
                                          "flagged", model="dataset-label", question="investigate this part?",
                                          probability=1.0, extra={"note": "the flag is the dataset's bad label; "
                                                                          "no model was run"})
                conn.commit()
            report["context"] = "enabled: segments, simple-v1 values, bad labels, s1:flagged decisions"
        except psycopg.Error as exc:
            conn.rollback()
            notes["context"] = f"not available in this image: {exc.diag.message_primary or exc}"
            report["context"] = notes["context"]
        report["context_s"] = round(time.perf_counter() - t1, 1)

        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (READER,)).fetchone():
            conn.execute(f"CREATE ROLE {READER} LOGIN PASSWORD '{READER_PASSWORD}'")
        conn.execute(f"GRANT USAGE ON SCHEMA public TO {READER}")
        conn.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {READER}")
        conn.commit()
        n, windows = conn.execute(f"SELECT (SELECT count(*) FROM {COLLECTION}), "
                                  f"(SELECT count(*) FROM {COLLECTION}_envelopes)").fetchone()
        report.update(recordings=n, search_windows=windows, total_s=round(time.perf_counter() - t, 1))
    print(json.dumps(report, indent=1))
    if out_json:
        out_json.write_text(json.dumps(report, indent=1) + "\n")


if __name__ == "__main__":
    main()
