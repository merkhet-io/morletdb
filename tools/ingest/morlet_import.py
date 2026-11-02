#!/usr/bin/env python3
"""Import WAV, EDF/EDF+, miniSEED or CSV files into a MorletDB collection and make them searchable.

  export DATABASE_URL=postgresql://localhost/mydb
  python tools/ingest/morlet_import.py recordings/*.wav --collection public.signals --create

Each channel (and each contiguous miniSEED segment) becomes one recording. The sample grid that makes
storage lossless and compact (the quantum) is taken from the file: integer counts for WAV and integer
miniSEED, the calibration step for EDF, the decimal places for CSV. After the append, envelopes are built
for exact search and, with --probes, probe search is enabled.
"""
import argparse
import csv
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import sys
import wave

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parquet_import import Config, ImportFailure, import_files  # noqa: E402

SUFFIXES = {".wav": "wav", ".edf": "edf", ".bdf": None, ".mseed": "mseed", ".miniseed": "mseed",
            ".msd": "mseed", ".csv": "csv", ".parquet": "exchange", ".tdms": "tdms"}


@dataclass
class Recording:
    recording_id: str
    rate: float
    channel: str
    unit: str
    quantum: float
    samples: np.ndarray
    metadata: dict = field(default_factory=dict)


def extractor_for(rate):
    """Segment features by sample rate; envelope search works for every rate."""
    return "morlet-energy-v1" if rate >= 128 else ("morlet-energy-lowrate-v1" if rate >= 1 else "none")


def read_wav(path, opts):
    """PCM WAV (8/16/24/32-bit integer): one recording per channel, as integer counts (32-bit at full scale)."""
    with wave.open(str(path), "rb") as w:
        if w.getcomptype() != "NONE":
            raise ValueError(f"{path.name}: compressed WAV is not supported")
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 3:
        b = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.int32)
        data = (b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)).astype(np.int32)
        data = np.where(data >= 1 << 23, data - (1 << 24), data)
    elif width in (1, 2, 4):
        data = np.frombuffer(raw, {1: np.uint8, 2: "<i2", 4: "<i4"}[width]).astype(np.int64)
        if width == 1:
            data = data - 128
    else:
        raise ValueError(f"{path.name}: unsupported sample width {width}")
    data = data.reshape(-1, ch).astype(np.float64)
    # 32-bit counts can exceed the +/-1e9 sample range: store them at full scale, divided by 2^31,
    # which is exact in float64; the quantum 2^-31 keeps them on the integer grid.
    scale, unit = (2.0 ** -31, "full-scale") if width == 4 else (1.0, "counts")
    return [Recording(f"{path.stem}.ch{i}", float(rate), f"ch{i}", opts.unit or unit, scale,
                      data[:, i] * scale, {"source": path.name, "format": "wav", "bits": 8 * width})
            for i in range(ch)]


def read_edf(path, opts):
    """EDF and continuous EDF+ (EDF+C): one recording per ordinary signal, physical values with the
    calibration step as quantum. Patient and recording identification fields are not copied."""
    with open(path, "rb") as f:
        head = f.read(256)
        if len(head) < 256 or head[:8].strip() not in (b"0",):
            raise ValueError(f"{path.name}: not an EDF file")
        reserved = head[192:236].decode("ascii", "replace").strip()
        if reserved.startswith("EDF+D"):
            raise ValueError(f"{path.name}: discontinuous EDF+ (EDF+D) is not supported")
        nrec, dur, ns = int(head[236:244]), float(head[244:252]), int(head[252:256])
        fields = [(16, "label"), (80, "transducer"), (8, "unit"), (8, "pmin"), (8, "pmax"), (8, "dmin"),
                  (8, "dmax"), (80, "prefilter"), (8, "nsamp"), (32, "reserved")]
        sig = [dict() for _ in range(ns)]
        for width, name in fields:
            block = f.read(width * ns)
            for i in range(ns):
                sig[i][name] = block[i * width:(i + 1) * width].decode("ascii", "replace").strip()
        if nrec < 0:
            raise ValueError(f"{path.name}: unknown number of data records")
        per = [int(s["nsamp"]) for s in sig]
        data = np.frombuffer(f.read(2 * sum(per) * nrec), "<i2")
    if data.size != sum(per) * nrec:
        raise ValueError(f"{path.name}: truncated data records")
    records = data.reshape(nrec, sum(per))
    out, at = [], 0
    for s, k in zip(sig, per):
        cols = records[:, at:at + k].reshape(-1).astype(np.float64)
        at += k
        if s["label"] == "EDF Annotations":
            continue
        pmin, pmax, dmin, dmax = (float(s[x]) for x in ("pmin", "pmax", "dmin", "dmax"))
        step = (pmax - pmin) / (dmax - dmin)
        phys = (cols - dmin) * (pmax - pmin) / (dmax - dmin) + pmin       # as EDF readers compute it
        label = re.sub(r"\s+", "-", s["label"]) or f"signal{len(out)}"
        out.append(Recording(f"{path.stem}.{label}", k / dur, label, opts.unit or s["unit"] or "unknown",
                             abs(step) if step else 1.0, phys,
                             {"source": path.name, "format": "edf", "transducer": s["transducer"],
                              "prefilter": s["prefilter"]}))
    return out


def read_mseed(path, opts):
    """miniSEED via ObsPy: one recording per contiguous segment (touching records merged, gaps kept)."""
    try:
        import obspy
    except ImportError as exc:
        raise ValueError("miniSEED needs ObsPy: python -m pip install obspy") from exc
    st = obspy.read(str(path))
    st.merge(method=-1)                     # join touching records; keep real gaps as separate traces
    out = []
    for tr in sorted(st, key=lambda t: (t.id, t.stats.starttime)):
        s = tr.stats
        integer = np.issubdtype(tr.data.dtype, np.integer)
        rid = f"{tr.id}.{s.starttime.strftime('%Y%m%dT%H%M%S')}"
        out.append(Recording(rid, float(s.sampling_rate), s.channel or "ch", opts.unit or ("counts" if integer else "unknown"),
                             1.0, tr.data.astype(np.float64),
                             {"source": path.name, "format": "mseed", "network": s.network, "station": s.station,
                              "location": s.location, "channel": s.channel, "starttime": str(s.starttime)}))
    return out


def read_tdms(path, opts):
    """NI TDMS (npTDMS): one recording per waveform channel, i.e. a numeric channel with wf_increment.

    Values are the scaled (physical) values npTDMS computes from the file's scaling information, stored exactly
    as float64; with --tdms-values raw, the unscaled stored values instead (integer DAQ counts keep quantum 1).
    The sample rate is 1 / wf_increment; the start time is wf_start_time + wf_start_offset when the file has
    them. Channels without waveform timing (irregular, or timed by a separate time channel), non-numeric
    channels and empty channels are refused with the reason, as is the whole file if none is importable."""
    try:
        from nptdms import TdmsFile
    except ImportError as exc:
        raise ValueError("TDMS needs npTDMS: python -m pip install nptdms") from exc
    raw = getattr(opts, "tdms_values", "scaled") == "raw"
    tdms = TdmsFile.read(str(path))
    out, refused = [], []
    for group in tdms.groups():
        for ch in group.channels():
            where = f"{group.name}/{ch.name}"
            props = dict(ch.properties)
            dtype = getattr(ch, "dtype", None)
            if dtype is None or not (np.issubdtype(dtype, np.integer) or np.issubdtype(dtype, np.floating)):
                refused.append(f"{where}: not numeric ({dtype})")
                continue
            dt = props.get("wf_increment")
            if not dt or not float(dt) > 0:
                refused.append(f"{where}: no waveform timing (wf_increment); irregular or separately timed data "
                               "is not imported")
                continue
            data = ch.read_data(scaled=not raw) if hasattr(ch, "read_data") else ch[:]
            data = np.asarray(data)
            if data.size == 0:
                refused.append(f"{where}: empty")
                continue
            scaled_props = {k: _plain(v) for k, v in props.items() if k.startswith("NI_Scal")}
            integer = np.issubdtype(data.dtype, np.integer)
            unit = opts.unit or props.get("unit_string") or ("counts" if integer else "unknown")
            meta = {"source": path.name, "format": "tdms", "group": group.name, "channel": ch.name,
                    "values": "raw" if raw else "scaled", "tdms_type": str(dtype), "wf_increment": float(dt)}
            if scaled_props:
                meta["scaling"] = scaled_props
            start = props.get("wf_start_time")
            if start is not None:
                t0 = np.datetime64(start, "ns") + np.timedelta64(int(round(float(props.get("wf_start_offset", 0)) * 1e9)), "ns")
                meta["start"] = np.datetime_as_string(t0, unit="us", timezone="UTC")
            name = re.sub(r"[^A-Za-z0-9_.:-]+", "-", f"{path.stem}.{group.name}.{ch.name}")
            out.append(Recording(name, 1.0 / float(dt), ch.name, unit, 1.0, data.astype(np.float64), meta))
    if refused and not out:
        raise ValueError(f"{path.name}: no importable waveform channel: " + "; ".join(refused))
    if refused:
        print(json.dumps({"tdms_channels_not_imported": refused}), file=sys.stderr)
    return out


def _plain(value):
    """TDMS property values as JSON-friendly values."""
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.datetime64):
        return np.datetime_as_string(value, unit="ns", timezone="UTC")
    return value if isinstance(value, (int, float, str, bool)) or value is None else str(value)


def read_exchange(path, opts):
    """A MorletDB exchange file (morlet_export.py, docs/exchange-format.md): one recording per recording_id,
    with its rate, channel, unit, metadata and sample quantum as exported; samples are checked against the
    file's per-recording SHA-256 before anything is imported."""
    import hashlib
    import pyarrow.parquet as pq
    pf = pq.ParquetFile(path)
    meta = pf.metadata.metadata or {}
    if b"morletdb.exchange" not in meta:
        raise ValueError(f"{path.name}: not a MorletDB exchange file (no morletdb.exchange metadata); "
                         "use parquet_import.py for other Parquet layouts")
    manifest = json.loads(meta[b"morletdb.exchange"])
    if manifest.get("format") != "morlet-exchange" or manifest.get("version") != 1:
        raise ValueError(f"{path.name}: unsupported exchange format {manifest.get('format')} "
                         f"version {manifest.get('version')}")
    groups, order = {}, []
    for batch in pf.iter_batches(batch_size=64):
        for row in batch.to_pylist():
            rid = row["recording_id"]
            if rid not in groups:
                order.append(rid)
                groups[rid] = {"row": row, "parts": [], "next": 0}
            g = groups[rid]
            if row["start_sample"] != g["next"]:
                raise ValueError(f"{path.name}: {rid} is not contiguous at sample {row['start_sample']}")
            g["parts"].append(np.asarray(row["samples"], dtype="<f8"))
            g["next"] += len(row["samples"])
    out = []
    for rid in order:
        g, want = groups[rid], manifest["recordings"].get(rid)
        x = np.concatenate(g["parts"]) if g["parts"] else np.zeros(0)
        if want is None or x.size != want["samples"] or hashlib.sha256(x.tobytes()).hexdigest() != want["sha256"]:
            raise ValueError(f"{path.name}: {rid} does not match the file's checksum manifest")
        r = g["row"]
        rec = Recording(rid, float(r["sample_rate_hz"]), r["channel"], r["unit"], float(r["sample_quantum"] or 1.0), x,
                        json.loads(r["metadata"] or "{}"))
        rec.metadata = rec.metadata if isinstance(rec.metadata, dict) else {}
        out.append(rec)
    return out


def decimals(text):
    m = re.fullmatch(r"\s*[-+]?\d*\.(\d+)\s*", text)
    return len(m.group(1)) if m else 0


def read_csv(path, opts):
    """CSV with a header row: every numeric column (except --time-column) becomes a recording at --rate.
    The quantum is 10^-d for the most decimal places d seen in a column (d <= 9), else 1."""
    if not opts.rate:
        raise ValueError("CSV needs --rate (samples per second)")
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    if len(rows) < 2:
        raise ValueError(f"{path.name}: no data rows")
    header, body = rows[0], rows[1:]
    out = []
    for j, name in enumerate(header):
        if name == opts.time_column:
            continue
        col = [r[j] for r in body]
        try:
            values = np.array([float(v) for v in col], dtype=np.float64)
        except ValueError:
            continue                        # non-numeric column
        d = max(decimals(v) for v in col)
        label = re.sub(r"\s+", "-", name.strip()) or f"col{j}"
        out.append(Recording(f"{path.stem}.{label}", float(opts.rate), label, opts.unit or "unknown",
                             10.0 ** -d if d <= 9 else 1.0, values, {"source": path.name, "format": "csv"}))
    if not out:
        raise ValueError(f"{path.name}: no numeric columns")
    return out


READERS = {"wav": read_wav, "edf": read_edf, "mseed": read_mseed, "csv": read_csv, "exchange": read_exchange,
           "tdms": read_tdms}


def recordings(path, opts):
    fmt = opts.format or SUFFIXES.get(path.suffix.lower())
    if fmt not in READERS:
        raise ValueError(f"{path.name}: unknown format (use --format wav|edf|mseed|csv|tdms|exchange)")
    recs = READERS[fmt](path, opts)
    for r in recs:
        if not (math.isfinite(r.rate) and r.rate > 0):
            raise ValueError(f"{r.recording_id}: invalid sample rate {r.rate}")
        if r.samples.size == 0 or not np.all(np.isfinite(r.samples)) or np.abs(r.samples).max() > 1e9:
            raise ValueError(f"{r.recording_id}: samples must be finite and within +/-1e9")
        if opts.prefix:
            r.recording_id = opts.prefix + r.recording_id
    return sorted(recs, key=lambda r: r.recording_id)


def chunk_reader(opts):
    """import_files reader: canonical chunks, with each recording's quantum and extractor in its spec."""
    def read(path, config, _mapping):
        for r in recordings(path, opts):
            extractor = opts.extractor or extractor_for(r.rate)
            spec = (r.rate, r.channel, r.unit, r.metadata, r.quantum, extractor)
            for start in range(0, r.samples.size, config.chunk_size):
                yield r.recording_id, spec, start, np.asarray(r.samples[start:start + config.chunk_size], np.float64)
    return read


def prepare(conn, collection, create):
    if conn.execute("SELECT to_regclass(%s)", (collection,)).fetchone()[0] is None:
        if not create:
            raise ValueError(f"collection {collection} does not exist (use --create)")
        schema, name = collection.split(".", 1) if "." in collection else ("public", collection)
        conn.execute("SELECT morlet_create_stream_collection(%s, %s)", (schema, name))
    if conn.execute("SELECT to_regclass(%s)", (collection + "_envelopes",)).fetchone()[0] is None:
        conn.execute("SELECT morlet_enable_envelopes(%s::regclass)", (collection,))
    conn.commit()


def build(conn, collection, ids, window, probes):
    built = {}
    if probes:
        has_vector = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
        if not has_vector:
            raise ValueError("--probes needs pgvector (CREATE EXTENSION vector)")
        try:
            conn.execute("SELECT morlet_enable_envelope_probes(%s::regclass, %s)", (collection, window))
            conn.commit()
        except Exception as exc:                 # already enabled: keep going
            conn.rollback()
            if "already enabled" not in str(exc):
                raise
    for rid in ids:
        total = 0
        while True:
            n = conn.execute("SELECT morlet_build_envelopes(%s::regclass, %s, %s, %s)",
                             (collection, rid, window, window // 2)).fetchone()[0]
            conn.commit()
            total += n
            if n == 0:
                break
        built[rid] = total
    return built


def first_search(opts, plan, built, has_similar=True):
    """A query to run right after the import: the ten intervals most similar to one window of the
    longest recording. morlet_search_similar (SQL 0.3.2) uses probe search when it is enabled, else exact
    search. On older servers exact search refuses more eligible windows than its default max_candidates
    (4096), so the cap is given explicitly there."""
    first = max(plan, key=lambda r: r["samples"])
    start = max(0, (first["samples"] // 2 // opts.window) * opts.window)
    args = f"'{opts.collection}', '{first['recording_id']}', {start}, {start + opts.window}, '{{}}'::jsonb, 10"
    if has_similar:
        return f"SELECT * FROM morlet_search_similar({args});"
    if opts.probes:
        return f"SELECT * FROM morlet_search_envelope_probes({args});"
    total = sum(built.values()) if built else 0
    return f"SELECT * FROM morlet_search_envelope_interval({args}, 0.075, true, true, {max(4096, 2 * total)});"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("files", nargs="+", type=Path)
    p.add_argument("--collection", required=True, help="schema.name, e.g. public.signals")
    p.add_argument("--create", action="store_true", help="create the collection if it does not exist")
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--format", choices=sorted(READERS), help="override detection by file suffix")
    p.add_argument("--rate", type=float, help="CSV: samples per second")
    p.add_argument("--time-column", help="CSV: column to ignore")
    p.add_argument("--unit", help="override the unit")
    p.add_argument("--tdms-values", choices=["scaled", "raw"], default="scaled",
                   help="TDMS: store scaled (physical) values or the raw stored values")
    p.add_argument("--prefix", default="", help="prefix for recording identifiers")
    p.add_argument("--extractor", choices=["morlet-energy-v1", "morlet-energy-lowrate-v1", "none"],
                   help="segment features (default: by sample rate)")
    p.add_argument("--window", type=int, default=4096, help="envelope window for search (power of two, 512..32768)")
    p.add_argument("--probes", action="store_true", help="enable probe search (needs pgvector)")
    p.add_argument("--no-build", action="store_true", help="append only; build envelopes later")
    p.add_argument("--dry-run", action="store_true", help="read and validate files, print what would be imported")
    opts = p.parse_args(argv)
    try:
        recs = [(f.name, r) for f in opts.files for r in recordings(f, opts)]
        plan = [{"file": f, "recording_id": r.recording_id, "rate_hz": r.rate, "channel": r.channel, "unit": r.unit,
                 "samples": int(r.samples.size), "quantum": r.quantum,
                 "extractor": opts.extractor or extractor_for(r.rate)} for f, r in recs]
        if opts.dry_run:
            print(json.dumps({"status": "dry-run", "recordings": plan}, indent=2))
            return 0
        import psycopg
        with psycopg.connect(opts.dsn) as conn:
            prepare(conn, opts.collection, opts.create)
        report = import_files(opts.files, opts.collection, Config(), dsn=opts.dsn, reader=chunk_reader(opts))
        report["recordings"] = plan
        with psycopg.connect(opts.dsn) as conn:
            if not opts.no_build:
                report["envelopes_built"] = build(conn, opts.collection, [r["recording_id"] for r in plan],
                                                  opts.window, opts.probes)
            has_similar = conn.execute("SELECT to_regproc('public.morlet_search_similar') IS NOT NULL").fetchone()[0]
        report["try"] = first_search(opts, plan, report.get("envelopes_built", {}), has_similar)
    except ImportFailure as exc:
        print(json.dumps(exc.report, indent=2), file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
