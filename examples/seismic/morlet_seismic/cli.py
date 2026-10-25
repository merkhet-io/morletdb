"""Command line entry points: python -m morlet_seismic <command>."""
import argparse
import json
from pathlib import Path
import platform
import statistics
import time

from . import dataset, investigate, provenance
from .detect import Detector
from .ingest import Ingestor, init_db, run
from .sources import ReplayControl, ReplaySource, SeedLinkSource

DEFAULT_DEFINITION = Path(__file__).resolve().parents[1] / "datasets" / "cx-north-chile-2024.json"
LIVE_DATASET = "live-geofon-v1"
LIVE_SERVER = "geofon.gfz.de:18000"


def distribution(values):
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]  # noqa: E731
    return {"n": len(values), "mean": statistics.fmean(values), "p50": pick(0.5),
            "p90": pick(0.9), "p99": pick(0.99), "max": ordered[-1]}


class LimitedSource:
    """Stops after max_chunks to demonstrate interruption and restart.

    A truncated run is not natural exhaustion, so recordings are not finalized.
    """

    def __init__(self, source, max_chunks):
        self.source, self.max_chunks, self.mode = source, max_chunks, source.mode
        self.exhausted = False

    def chunks(self):
        for count, chunk in enumerate(self.source.chunks(), 1):
            yield chunk
            if count >= self.max_chunks:
                return
        self.exhausted = self.source.exhausted


def build_pipeline(conn, definition, manifest, *, dataset_id=None, mode="replay", detect=True):
    dataset_id = dataset_id or definition["id"]
    detector = Detector(conn, investigate.client(conn), definition["detector"], dataset_id) if detect else None
    split_of = investigate.labels_for(definition) if mode == "replay" else None
    ingestor = Ingestor(conn, dataset_id, definition["representation"],
                        investigate.stream_metadata(manifest), mode=mode, split_of=split_of,
                        tolerance=definition["ingest"]["continuity_tolerance_samples"],
                        batch_chunks=definition["ingest"]["batch_chunks"],
                        on_commit=detector.on_commit if detector else None,
                        on_open=detector.on_open if detector else None)
    return ingestor, detector


def replay(args):
    definition, manifest, base = investigate.load(args.definition, args.data_root)
    with investigate.connect(args.dsn) as conn:
        init_db(conn)
        ingestor, detector = build_pipeline(conn, definition, manifest, detect=not args.no_detect)
        source = ReplaySource(manifest, base, streams=args.streams, periods=args.periods,
                              chunk_samples=args.chunk_samples or definition["ingest"]["chunk_samples"],
                              control=ReplayControl(args.speed))
        driven = LimitedSource(source, args.max_chunks) if args.max_chunks else source
        started = time.perf_counter()
        actions = run(driven, ingestor, detector=detector, log=print if args.verbose else None)
        elapsed = time.perf_counter() - started
    report = {"command": "replay", "dataset": definition["id"], "streams": args.streams or "all",
              "finalized": driven.exhausted,
              "periods": args.periods or "all", "interrupted_after_chunks": args.max_chunks,
              "elapsed_s": elapsed, "actions": actions, "ingest": ingestor.stats,
              "detector": None if detector is None else detector.stats,
              "append_batch_latency_s": distribution(ingestor.latency["append_batch_s"]),
              "chunk_emit_to_commit_s": distribution(ingestor.latency["chunk_commit_s"]),
              "host": platform.platform(),
              "provenance": provenance.collect(definition, manifest, args.data_root)}
    output = json.dumps(report, indent=1)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n")
    print(output)
    return 0


def detect(args):
    """Re-run the detector over committed samples (deterministic; no ingestion)."""
    definition, manifest, _ = investigate.load(args.definition, args.data_root)
    with investigate.connect(args.dsn) as conn:
        detector = Detector(conn, investigate.client(conn), definition["detector"], definition["id"])
        streams = args.streams or sorted(investigate.stream_metadata(manifest))
        if args.reset:
            detector.reset(streams)
        started = time.perf_counter()
        detector.close_all(streams=streams)
        stored = conn.execute("SELECT stream,count(*) FROM seismic.triggers WHERE dataset=%s AND detector_version=%s"
                              " GROUP BY 1 ORDER BY 1", (definition["id"], detector.version)).fetchall()
    print(json.dumps({"command": "detect", "reset": args.reset, "streams": streams,
                      "provenance": provenance.collect(definition, manifest, args.data_root),
                      "elapsed_s": time.perf_counter() - started, "run": detector.stats,
                      "stored_triggers": dict(stored)}, indent=1))
    return 0


def live(args):
    if not args.enable_live:
        raise SystemExit("live mode connects to a public SeedLink server; pass --enable-live")
    definition, manifest, _ = investigate.load(args.definition, args.data_root)
    with investigate.connect(args.dsn) as conn:
        init_db(conn)
        ingestor, detector = build_pipeline(conn, definition, manifest, dataset_id=LIVE_DATASET,
                                            mode="live")
        source = SeedLinkSource(args.server, args.streams or sorted(investigate.stream_metadata(manifest)),
                                enabled=True)
        deadline = time.time() + args.duration_s
        for chunk in source.chunks():
            ingestor.handle(chunk)
            ingestor.flush()  # Live: commit each packet promptly; freshness over batching.
            if time.time() >= deadline:
                source.stop()
        print(json.dumps({"ingest": ingestor.stats, "source": source.status()}, indent=1))
    return 0


def status(args):
    definition = dataset.load_definition(args.definition)
    with investigate.connect(args.dsn) as conn:
        rows = conn.execute("SELECT * FROM seismic.progress ORDER BY dataset,stream").fetchall()
        names = [d.name for d in conn.execute("SELECT * FROM seismic.progress LIMIT 0").description]
        segs = investigate.segments(conn, args.dataset or definition["id"])
    print(json.dumps({"progress": [dict(zip(names, r)) for r in rows],
                      "segments": len(segs)}, indent=1, default=str))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="morlet_seismic", description=__doc__)
    parser.add_argument("--definition", type=Path, default=DEFAULT_DEFINITION)
    parser.add_argument("--data-root", type=Path, default=dataset.DEFAULT_DATA)
    parser.add_argument("--dsn", default=None, help="defaults to $DATABASE_URL or the local demo DB")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("download", help="fetch public data into the local cache")
    fetch.add_argument("--refresh", action="store_true", help="re-download cached files")
    commands.add_parser("verify", help="offline checksum verification of the cache")
    commands.add_parser("init-db", help="create the waveform collection and app tables")
    rep = commands.add_parser("replay", help="replay cached recordings through ingestion")
    rep.add_argument("--streams", nargs="*")
    rep.add_argument("--periods", nargs="*")
    rep.add_argument("--speed", type=float, default=0.0, help="x real time; 0 = unpaced")
    rep.add_argument("--chunk-samples", type=int)
    rep.add_argument("--max-chunks", type=int, help="stop early to demonstrate restart")
    rep.add_argument("--no-detect", action="store_true")
    rep.add_argument("--output", type=Path)
    rep.add_argument("--verbose", action="store_true")
    dt = commands.add_parser("detect", help="re-run STA/LTA over committed samples")
    dt.add_argument("--streams", nargs="*")
    dt.add_argument("--reset", action="store_true", help="delete this detector version's triggers first")
    lv = commands.add_parser("live", help="opt-in SeedLink ingestion (network access)")
    lv.add_argument("--enable-live", action="store_true")
    lv.add_argument("--server", default=LIVE_SERVER)
    lv.add_argument("--streams", nargs="*")
    lv.add_argument("--duration-s", type=float, default=600)
    st = commands.add_parser("status", help="committed progress per stream")
    st.add_argument("--dataset")
    ev = commands.add_parser("evaluate", help="real-data evaluation report")
    ev.add_argument("--output", type=Path, required=True)
    ev.add_argument("--background-every-s", type=float, default=300.0)
    ev.add_argument("--replay-reports", type=Path, help="directory of replay-*.json run reports")
    ev.add_argument("--max-unassociated", type=int, default=60,
                    help="evenly spaced unassociated held-out queries per stream")
    args = parser.parse_args(argv)
    if args.command == "download":
        print(dataset.download(args.definition, args.data_root, refresh=args.refresh))
    elif args.command == "verify":
        definition = dataset.load_definition(args.definition)
        manifest = dataset.load_manifest(definition, args.data_root)
        print(json.dumps({"status": "verified", "waveform_files": len(manifest["waveforms"]),
                          "station_files": len(manifest["stations"]),
                          "catalog_files": len(manifest["catalogs"])}))
    elif args.command == "init-db":
        with investigate.connect(args.dsn) as conn:
            init_db(conn)
        print("initialized")
    elif args.command == "replay":
        return replay(args)
    elif args.command == "detect":
        return detect(args)
    elif args.command == "live":
        return live(args)
    elif args.command == "status":
        return status(args)
    elif args.command == "evaluate":
        from .evaluate import evaluate
        return evaluate(args)
    return 0
