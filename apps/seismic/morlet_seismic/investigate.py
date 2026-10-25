"""Shared read-side helpers for the UI, CLI and evaluation (no writes)."""
import os

import psycopg

from . import dataset
from .dataset import parse_time, stream_id
from .ingest import COLLECTION, RecordingClient

DEFAULT_DSN = "postgresql://morlet:morlet-demo-only@localhost:54339/morlet"
PREPROCESSING = {
    "version": "seismic-preprocess-v1",
    "stored_samples": "raw digitizer counts from miniSEED; no filtering, resampling, gap filling "
                      "or instrument-response removal",
    "retrieval": "per-window zscore inside the MorletDB extractor (morlet-energy-lowrate-v1)",
    "detector": "copy of committed samples: demean, causal 4-pole Butterworth 2-10 Hz, recursive STA/LTA",
    "timing": "segment start from the miniSEED header; sample i at start + i/rate; header timing "
              "jitter up to 0.5 sample is absorbed into a segment and logged",
}


def connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("DATABASE_URL", DEFAULT_DSN))


def labels_for(definition):
    periods = [(int(parse_time(p["start"]).timestamp()) * 10**9,
                int(parse_time(p["end"]).timestamp()) * 10**9, p) for p in definition["periods"]]

    def split_of(stream, start_ns):
        for lo, hi, period in periods:
            if lo <= start_ns < hi:
                return {"split": period["split"], "period": period["name"]}
        raise ValueError(f"chunk at {start_ns} lies outside every dataset period")
    return split_of


def stream_metadata(manifest):
    definition = manifest["definition"]
    result = {}
    for item in manifest["stations"]:
        for channel in item["channels"]:
            result[channel["id"]] = {
                "network": channel["id"].split(".")[0], "station": channel["id"].split(".")[1],
                "location": channel["id"].split(".")[2], "channel": channel["id"].split(".")[3],
                "latitude": str(channel["latitude"]), "longitude": str(channel["longitude"]),
                "elevation_m": str(channel["elevation_m"]), "azimuth": str(channel["azimuth"]),
                "dip": str(channel["dip"]), "sensor": str(channel["sensor"]),
                "sensitivity": str(channel["sensitivity"]),
                "sensitivity_units": f"{channel['input_units']}->{channel['output_units']}",
                "provider": definition["provider"]["name"],
                "network_doi": definition["network_citation"]["doi"],
                "preprocessing": PREPROCESSING["version"]}
    missing = {stream_id(s) for s in definition["streams"]} - set(result)
    if missing:
        raise ValueError(f"no station metadata for {sorted(missing)}")
    return result


def segments(conn, dataset_id, stream=None):
    rows = conn.execute(
        "SELECT g.recording_id,g.stream,g.split,g.start_ns,g.sample_rate_hz,g.opened_reason,"
        " (r.checkpoint->>'end')::bigint, r.metadata FROM seismic.segments g"
        f" JOIN {COLLECTION} r USING(recording_id) WHERE g.dataset=%s"
        + (" AND g.stream=%s" if stream else "") + " ORDER BY g.stream,g.start_ns",
        (dataset_id, stream) if stream else (dataset_id,)).fetchall()
    return [{"recording_id": r[0], "stream": r[1], "split": r[2], "start_ns": r[3], "rate": r[4],
             "opened_reason": r[5], "committed": r[6],
             "end_ns": r[3] + round(r[6] * 1e9 / r[4]), "metadata": r[7]} for r in rows]


def triggers(conn, dataset_id, detector_version, stream=None):
    rows = conn.execute(
        "SELECT t.recording_id,t.stream,t.on_sample,t.off_sample,t.on_ns,t.off_ns,t.peak_ratio,"
        " t.off_truncated,g.split FROM seismic.triggers t JOIN seismic.segments g USING(recording_id)"
        " WHERE t.dataset=%s AND t.detector_version=%s" + (" AND t.stream=%s" if stream else "")
        + " ORDER BY t.on_ns", (dataset_id, detector_version, stream) if stream
        else (dataset_id, detector_version)).fetchall()
    keys = ["recording_id", "stream", "on_sample", "off_sample", "on_ns", "off_ns", "peak_ratio",
            "off_truncated", "split"]
    return [dict(zip(keys, row)) for row in rows]


def query_start(trigger, segment, rep, pre_s):
    """Event-window rule: window starts pre_s before the onset; None if it does not fit."""
    start = trigger["on_sample"] - round(pre_s * segment["rate"])
    if start < 0 or start + rep["window_samples"] > segment["committed"]:
        return None
    return start


def gallery_windows(segs, trigs, rep, pre_s, *, background_every_s=300.0, split="reference"):
    """Reference gallery: one window per detector trigger plus a fixed background grid.

    Built only from detector output and a time grid, never from catalog times.
    """
    by_id = {s["recording_id"]: s for s in segs}
    windows, seen = [], set()
    for trigger in trigs:
        segment = by_id.get(trigger["recording_id"])
        if segment is None or segment["split"] != split:
            continue
        start = query_start(trigger, segment, rep, pre_s)
        if start is not None and (segment["recording_id"], start) not in seen:
            seen.add((segment["recording_id"], start))
            windows.append((segment["recording_id"], start, "trigger"))
    for segment in segs:
        if segment["split"] != split:
            continue
        step = round(background_every_s * segment["rate"])
        # Stride-aligned background windows, matching MorletDB segment starts.
        stride = rep["stride_samples"]
        for start in range(0, segment["committed"] - rep["window_samples"] + 1, step):
            start -= start % stride
            if (segment["recording_id"], start) not in seen:
                seen.add((segment["recording_id"], start))
                windows.append((segment["recording_id"], start, "background"))
    return windows


def load(definition_path, data_root=dataset.DEFAULT_DATA, *, verify=True):
    definition = dataset.load_definition(definition_path)
    manifest = dataset.load_manifest(definition, data_root, verify=verify)
    return definition, manifest, dataset.dataset_dir(definition, data_root)


def client(conn):
    return RecordingClient(conn, COLLECTION)

