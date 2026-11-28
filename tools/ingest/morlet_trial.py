#!/usr/bin/env python3
"""Try MorletDB on your own data, locally: import an export into a disposable database, prove it, and report.

  export DATABASE_URL='host=localhost port=54329 dbname=trial user=morlet' PGPASSWORD=...
  python tools/ingest/morlet_trial.py export.lp

Any format the importer reads works (InfluxDB line protocol `.lp`, CSV, WAV, EDF, miniSEED, TDMS, MorletDB Parquet).
The trial:

1. reads the files and shows which series become recordings and which are left out, and why;
2. imports them into a new collection (default `trial.signals`) of the database you name, which should be a
   disposable one (for example the release image in Docker); an existing collection is refused, never changed;
3. reads every recording back and compares it with the input, bit for bit (SHA-256 of the float64 samples,
   signed zero included);
4. for inputs with timestamps (line protocol), compares MorletDB's time of the samples with the original
   timestamps: exactly at the time anchors, and how far elsewhere, against the reported uncertainty;
5. reports storage (input bytes, MorletDB's tables) and import time, in report.md and report.json.

Nothing leaves this machine: the trial reads local files and talks only to the database you give it. The report
contains series names and numbers, no samples. Pass --influx-bytes with the size of the same data in your InfluxDB
(measured by you, e.g. `du -sb` of its data directory for these measurements) to put it beside MorletDB's size.
This is a trial on your data, not a benchmark: one machine, one run, no tuning.
"""
import argparse
from contextlib import redirect_stdout
from decimal import Decimal
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import morlet_import as mi  # noqa: E402

PAGE = 1 << 20                     # samples per read-back request
TIME_CHECKS = 2000                 # samples per recording whose time is compared (all anchors in addition)


def sha_input(samples):
    return hashlib.sha256(np.asarray(samples, dtype="<f8").tobytes()).hexdigest()


def sha_stored(conn, coll, rid, n):
    h = hashlib.sha256()
    for a in range(0, n, PAGE):
        b = min(n, a + PAGE)
        raw = conn.execute("SELECT public.morlet_read_interval_raw(%s::regclass, %s, %s, %s)", (coll, rid, a, b)).fetchone()[0]
        h.update(bytes(raw))
    return h.hexdigest()


def time_check(conn, coll, rec):
    """Largest |MorletDB time - original timestamp| over the anchors and up to TIME_CHECKS evenly spaced samples (a
    sample of the points, not all of them), exact in decimal nanoseconds, and how many of them fall outside the
    uncertainty MorletDB reports. Anchors must be exact."""
    if rec.times_ns is None:
        return None
    n = len(rec.times_ns)
    anchors = {a[0] for a in rec.anchors}
    idx = sorted(anchors | set(np.linspace(0, n - 1, min(n, TIME_CHECKS)).astype(int).tolist()))
    rows = conn.execute("""SELECT p.n, s.utc_ns, s.uncertainty_ns FROM unnest(%s::bigint[]) p(n),
        LATERAL public.morlet_sample_utc(%s::regclass, %s, p.n) s ORDER BY p.n""", (idx, coll, rec.recording_id)).fetchall()
    worst, outside, exact = Decimal(0), 0, 0
    for k, utc, unc in rows:
        d = abs(Decimal(utc) - rec.times_ns[k])
        worst = max(worst, d)
        outside += unc is not None and d > Decimal(unc)
        exact += k in anchors and d == 0
    return {"checked_samples": len(rows), "of_samples": n, "max_deviation_ns": str(worst),
            "outside_reported_uncertainty": outside, "anchors": len(anchors), "anchors_exact": exact}


def storage(conn, coll):
    schema, name = coll.split(".", 1)
    rows = conn.execute("""SELECT c.relname, pg_total_relation_size(c.oid) FROM pg_class c JOIN pg_namespace n
        ON n.oid = c.relnamespace WHERE n.nspname = %s AND c.relkind = 'r'
        AND (c.relname = %s OR left(c.relname, length(%s) + 1) = %s || '_') ORDER BY 1""",
                        (schema, name, name, name)).fetchall()
    tables = {r[0]: r[1] for r in rows}
    chunks = psycopg_sql_identifier(conn, schema, f"{name}_chunks")
    col = "encoded" if conn.execute("""SELECT 1 FROM information_schema.columns WHERE table_schema = %s
        AND table_name = %s AND column_name = 'encoded'""", (schema, f"{name}_chunks")).fetchone() else "samples"
    data = conn.execute(f"SELECT coalesce(sum(pg_column_size({col})), 0) FROM {chunks}").fetchone()[0]
    return {"sample_data_bytes": int(data), "sample_storage": "encoded chunks" if col == "encoded" else "float8[]",
            "chunks_table_bytes": tables.get(f"{name}_chunks", 0), "all_tables_bytes": sum(tables.values()),
            "tables": tables}


def psycopg_sql_identifier(conn, *parts):
    from psycopg import sql
    return sql.Identifier(*parts).as_string(conn)


def qualified(name):
    return name if "." in name else f"public.{name}"


def create_collection(psycopg, dsn, coll):
    """Create the trial's collection, refusing an existing one. Check and creation share one transaction under an
    advisory lock, and the creation itself fails if the table appeared meanwhile, so an existing collection is never
    written to."""
    schema, name = coll.split(".", 1)
    with psycopg.connect(dsn) as conn:
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('morlet-trial', 0))")
        if conn.execute("SELECT to_regclass(%s)", (coll,)).fetchone()[0]:
            raise SystemExit(f"{coll} exists; the trial only writes to a new collection (use --collection)")
        conn.execute(psycopg.sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(psycopg.sql.Identifier(schema)))
        conn.execute("SELECT public.morlet_create_stream_collection(%s, %s)", (schema, name))


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""),
                   help="a disposable database with pg_morlet (default: DATABASE_URL, else the libpq environment; "
                   "keep passwords in PGPASSWORD or ~/.pgpass, not on the command line)")
    p.add_argument("--collection", default="trial.signals")
    p.add_argument("--out", type=Path, default=Path("morlet-trial-report"))
    p.add_argument("--influx-bytes", type=int, help="bytes the same data takes in your InfluxDB (you measure it)")
    p.add_argument("--precision", choices=sorted(mi.influx_lp.PRECISION_NS), default="ns")
    p.add_argument("--min-rate", type=float, default=100.0)
    p.add_argument("--max-points", type=int, default=10_000_000, help="per file (memory bound, about 1 GB)")
    p.add_argument("--unit")
    p.add_argument("--window", type=int, default=4096)
    p.add_argument("--no-build", action="store_true", help="skip building search envelopes")
    opts = p.parse_args(argv)
    import psycopg
    import psycopg.sql  # noqa: F401
    coll = qualified(opts.collection)

    with psycopg.connect(opts.dsn, autocommit=True) as conn:
        caps = conn.execute("""SELECT public.morlet_version(),
            to_regprocedure('public.morlet_read_interval_raw(regclass,text,bigint,bigint)') IS NOT NULL,
            to_regprocedure('public.morlet_sample_utc(regclass,text,bigint,double precision)') IS NOT NULL""").fetchone()
    version, can_read, can_time = caps
    if not can_read:
        raise SystemExit(f"SQL {version} lacks morlet_read_interval_raw; the trial cannot verify samples")
    ropts = argparse.Namespace(format=None, rate=None, time_column=None, time_origin=None, anchor_tolerance_s=None,
                               no_anchors=False, context_columns=None, unit=opts.unit, tdms_values="scaled", prefix="",
                               extractor=None, precision=opts.precision, min_rate=opts.min_rate,
                               max_points=opts.max_points)
    mi.SKIPPED.clear()
    plans = [(f, [(r.recording_id, int(r.samples.size)) for r in mi.recordings(f, ropts)]) for f in opts.files]
    skipped = [dict(s, file=Path(f).name, path=f) for f in sorted(mi.SKIPPED) for s in mi.SKIPPED[f]]
    if not any(ids for _f, ids in plans):
        raise SystemExit("nothing to import: " + json.dumps(skipped, indent=1))
    create_collection(psycopg, opts.dsn, coll)

    os.environ["DATABASE_URL"] = opts.dsn          # the importer reads it; never put on a command line
    results, import_s, imported = [], 0.0, {}
    for f, ids in plans:                           # one file at a time: --max-points bounds memory per file
        if not ids:
            continue
        args = [str(f), "--collection", coll, "--no-build", "--precision", opts.precision,
                "--min-rate", str(opts.min_rate), "--max-points", str(opts.max_points)]
        args += ["--unit", opts.unit] if opts.unit else []
        buf = io.StringIO()
        t0 = time.monotonic()
        with redirect_stdout(buf):
            code = mi.main(args)
        import_s += time.monotonic() - t0
        if code != 0:
            raise SystemExit(f"import of {f.name} failed (see the message above)")
        imported = json.loads(buf.getvalue())
        recs = mi.recordings(f, ropts)             # read again for verification, then released
        with psycopg.connect(opts.dsn, autocommit=True) as conn:
            for r in recs:
                n = int(r.samples.size)
                stored = sha_stored(conn, coll, r.recording_id, n)
                t = time_check(conn, coll, r) if can_time else (
                    {"unavailable": f"SQL {version} has no morlet_sample_utc"} if r.times_ns is not None else None)
                results.append({"file": f.name, "recording_id": r.recording_id, "rate_hz": r.rate, "samples": n,
                                "bit_exact": stored == sha_input(r.samples), "sha256": stored, "time": t})
        del recs
    build_s = None
    if not opts.no_build:
        t0 = time.monotonic()
        with psycopg.connect(opts.dsn) as conn:
            mi.build(conn, coll, [x["recording_id"] for x in results], opts.window, False)
        build_s = round(time.monotonic() - t0, 3)
    with psycopg.connect(opts.dsn, autocommit=True) as conn:
        size = storage(conn, coll)
    input_bytes = sum(os.path.getsize(f) for f in opts.files)
    samples = sum(x["samples"] for x in results)
    report = {"morletdb_sql": version, "collection": coll, "files": [f.name for f in opts.files],
              "input_bytes": input_bytes, "samples": samples, "recordings": results, "skipped": skipped,
              "storage": size, "import_seconds": round(import_s, 3),
              "samples_per_second": round(samples / import_s) if import_s else None,
              "envelope_build_seconds": build_s, "influx_bytes": opts.influx_bytes,
              "importer": {k: imported.get(k) for k in ("status", "time_anchors", "warnings") if k in imported}}
    opts.out.mkdir(parents=True, exist_ok=True)
    (opts.out / "report.json").write_text(json.dumps(report, indent=1, default=str))
    (opts.out / "report.md").write_text(markdown(report))
    ok = all(x["bit_exact"] for x in results) and all(
        not x["time"] or "unavailable" in x["time"] or (not x["time"]["outside_reported_uncertainty"]
                                                         and x["time"]["anchors_exact"] == x["time"]["anchors"])
        for x in results)
    print(f"{'PASS' if ok else 'FAIL'}: {len(results)} recordings, {samples} samples; report in {opts.out}/report.md")
    return 0 if ok else 1


def _mib(b):
    return f"{b / 1048576:.2f} MiB"


def markdown(r):
    exact = sum(x["bit_exact"] for x in r["recordings"])
    lines = ["# MorletDB trial report", "",
             f"MorletDB SQL {r['morletdb_sql']}. Files: {', '.join(r['files'])}. Generated locally; nothing was sent "
             "anywhere. A trial on this data on this machine, not a benchmark.", "",
             "| | |", "|---|---|",
             f"| Recordings imported | {len(r['recordings'])} ({r['samples']:,} samples) |",
             f"| Bit-exact read-back | {exact} of {len(r['recordings'])} |",
             f"| Series left out | {len(r['skipped'])} (below) |",
             f"| Input files | {_mib(r['input_bytes'])} |",
             f"| MorletDB, sample data ({r['storage']['sample_storage']}) | {_mib(r['storage']['sample_data_bytes'])} "
             f"({8 * r['storage']['sample_data_bytes'] / max(1, r['samples']):.2f} bits per sample) |",
             f"| MorletDB, all tables on disk (incl. search envelopes) | {_mib(r['storage']['all_tables_bytes'])} |"]
    if r["influx_bytes"]:
        lines.append(f"| InfluxDB, as you measured it | {_mib(r['influx_bytes'])} |")
    lines += [f"| Import (append, time anchors) | {r['import_seconds']} s ({r['samples_per_second']:,} samples/s) |"]
    if r["envelope_build_seconds"] is not None:
        lines.append(f"| Search envelopes, built afterwards | {r['envelope_build_seconds']} s |")
    lines += ["",
              "## Recordings", "",
              "| Recording | Rate (Hz) | Samples | Bit-exact | Time: max deviation | Anchors exact | Beyond reported uncertainty |",
              "|---|---:|---:|---|---:|---:|---:|"]
    for x in r["recordings"]:
        t = x["time"]
        if not t:
            cells = "no timestamps | | |"
        elif "unavailable" in t:
            cells = f"not checked ({t['unavailable']}) | | |"
        else:
            cells = (f"{Decimal(t['max_deviation_ns']):,} ns ({t['checked_samples']:,} of {t['of_samples']:,} samples) | "
                     f"{t['anchors_exact']} of {t['anchors']} | {t['outside_reported_uncertainty']} |")
        lines.append(f"| `{x['recording_id']}` | {x['rate_hz']:g} | {x['samples']:,} | "
                     f"{'yes' if x['bit_exact'] else '**NO**'} | " + cells)
    if r["skipped"]:
        lines += ["", "## Left out", "", "| File | Series | Why |", "|---|---|---|"]
        lines += [f"| {s['file']} | `{s['series']}` | {s['reason']} |" for s in r["skipped"]]
    lines += ["", "## How to read this", "",
              "- **Bit-exact** compares the SHA-256 of the float64 samples read back from MorletDB with the input's.",
              "- **Time** compares MorletDB's time of a sample with its original timestamp at every time anchor (which "
              "must be exact) and at up to 2,000 evenly spaced samples per recording: a sample of the points, not all "
              "of them. Between anchors MorletDB interpolates; a deviation within the reported uncertainty is by design, "
              "one beyond it is a fault.",
              "- **Storage:** *sample data* is the stored size of the chunk values (what must be kept); *all tables* is "
              "PostgreSQL's size on disk right after the import, with search envelopes, indexes and page overhead, which "
              "dominates for small inputs (pages are 8 KiB). Sizes in InfluxDB depend on its compaction state.",
              "- **Not shown:** search quality, behaviour over weeks, other machines. Series below the rate limit are "
              "trends and are best left in InfluxDB; the metric bridge (docs/metric-bridge.md) links them to the "
              "waveforms.", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
