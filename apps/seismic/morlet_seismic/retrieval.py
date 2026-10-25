"""Retrieval modes over compatible recordings of ONE station/channel.

- pooled-pg: MorletDB's exact pooled-feature search over every stored segment
  window of the reference split (morlet_search_interval), with an explicit
  candidate budget. This is the database baseline.
- pooled-gallery / temporal-exhaustive / temporal-approx: the offline wave-index
  PoC over a bounded in-memory gallery of reference windows reconstructed from
  MorletDB. Exhaustive temporal scoring is the oracle for the approximate plan.

The gallery is NOT a persistent PostgreSQL index: it is rebuilt from committed
samples, holds at most 4096 windows and lists exactly which windows it contains.
Distances are representation distances, never probabilities or labels.
"""
from dataclasses import dataclass
import math
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "evaluation"))
import wave_index  # noqa: E402

FEATURES_BIN = REPO / "target" / "release" / "examples" / "features"

MODES = {
    "pooled-pg": "MorletDB exact pooled-feature search (all reference segments)",
    "pooled-gallery": "Pooled features, exhaustive over the bounded gallery",
    "temporal-exhaustive": "Wave-index PoC temporal score, exhaustive over the gallery (oracle)",
    "temporal-approx": "Wave-index PoC posting candidates, reranked with the temporal score",
}
UNAVAILABLE = {
    "cross-station": "Not implemented: matches are restricted to the query's station/channel.",
    "multi-station-association": "Not implemented: no synchronized multi-station search.",
    "event-classification": "Not implemented: similarity is not an event type or origin label.",
}


def stream_filter(dataset, stream, split="reference"):
    return {"dataset": dataset, "stream": stream, "split": split}


def eligible_count(conn, collection, metadata):
    """Read-only count of segment windows the pooled search would score."""
    segments = conn.execute("SELECT segments FROM morlet_stream_relations(%s::regclass)",
                            (collection,)).fetchone()[0]
    from psycopg import sql
    from psycopg.types.json import Jsonb
    query = sql.SQL("SELECT count(*) FROM {} s JOIN {} r USING(recording_id) WHERE r.metadata @> %s").format(
        sql.Identifier(*str(segments).split(".")), sql.Identifier(*collection.split(".")))
    return conn.execute(query, (Jsonb(metadata),)).fetchone()[0]


def pooled_pg(client, recording_id, start, end, metadata, *, top_k=10, max_candidates=200_000):
    started = time.perf_counter()
    eligible = eligible_count(client.connection, client.collection, metadata)
    if eligible > max_candidates:
        return {"mode": "pooled-pg", "status": "budget_exceeded", "eligible": eligible,
                "candidate_budget": max_candidates, "matches": [],
                "message": f"{eligible} eligible windows exceed the candidate budget; no partial top-k"}
    hits = client.search_interval(recording_id, start, end, metadata=metadata, top_k=top_k,
                                  exclude_source=True, suppress_overlaps=True,
                                  max_candidates=max_candidates)
    return {"mode": "pooled-pg", "status": "ok", "exhaustive": True, "eligible": eligible,
            "scored": eligible, "candidate_budget": max_candidates, "posting_visits": None,
            "query_s": time.perf_counter() - started,
            "matches": [{"recording_id": h["recording_id"], "start": h["start_sample"],
                         "end": h["end_sample"], "distance": h["distance"],
                         "extractor_version": h["extractor_version"]} for h in hits]}


@dataclass
class Gallery:
    spec: object
    index: object
    composition: dict
    build: dict


def window_spec(rep, channel, unit, rate, bin_width=0.25):
    return wave_index.Spec(window=rep["window_samples"], rate=float(rate), channel=channel,
                           unit=unit, normalization=rep["normalization"], bin_width=bin_width,
                           extractor=rep["extractor"])


def extract_rows(rows, spec):
    if not FEATURES_BIN.exists():
        raise FileNotFoundError(f"build the Rust adapter first: cargo build --locked --release "
                                f"-p morlet-core --example features ({FEATURES_BIN})")
    return wave_index.extract_windows(rows, spec, FEATURES_BIN)


def build_gallery(client, spec, windows, *, max_windows=wave_index.MAX_WINDOWS):
    """windows: [(recording_id, start, kind)], read from MorletDB original samples."""
    if len(windows) > max_windows:
        raise ValueError(f"gallery of {len(windows)} windows exceeds the PoC limit {max_windows}")
    started = time.perf_counter()
    rows = []
    for recording_id, start, kind in windows:
        data = client.read_interval(recording_id, start, start + spec.window)
        rows.append({"id": recording_id, "start": int(start), "samples": list(data["samples"]),
                     "metadata": {"kind": kind}})
    read_s = time.perf_counter() - started
    started = time.perf_counter()
    extracted = extract_rows(rows, spec)
    extract_s = time.perf_counter() - started
    started = time.perf_counter()
    index = wave_index.WaveIndex(extracted, spec)
    build_s = time.perf_counter() - started
    kinds = {}
    for _, _, kind in windows:
        kinds[kind] = kinds.get(kind, 0) + 1
    return Gallery(spec, index, kinds, {"read_s": read_s, "extract_s": extract_s,
                                        "index_build_s": build_s, "windows": len(rows),
                                        "posting_keys": len(index.postings),
                                        "posting_entries": sum(map(len, index.postings.values())),
                                        "generation_id": index.generation, "spec_id": spec.id})


def query_window(client, spec, recording_id, start):
    data = client.read_interval(recording_id, start, start + spec.window)
    return extract_rows([{"id": recording_id, "start": int(start),
                          "samples": list(data["samples"])}], spec)[0]


def pooled_gallery(gallery, query, *, top_k=10):
    """Same 15-feature RMS metric as MorletDB, over exactly the gallery windows."""
    started = time.perf_counter()
    window = gallery.spec.window
    ranked = []
    for row in gallery.index.windows:
        if wave_index.overlap(query, row, window):
            continue  # Source exclusion, same rule as the temporal plans.
        d = math.sqrt(sum((a - b) ** 2 for a, b in zip(query.pooled, row.pooled)) / 15)
        ranked.append((d, row.recording, row.start))
    ranked.sort()
    chosen = []
    for d, recording, start in ranked:
        if any(recording == r and start < s + window and s < start + window for _, r, s in chosen):
            continue
        chosen.append((d, recording, start))
        if len(chosen) == top_k:
            break
    return {"mode": "pooled-gallery", "status": "ok", "exhaustive": True,
            "eligible": len(ranked), "scored": len(ranked), "posting_visits": None,
            "query_s": time.perf_counter() - started,
            "matches": [{"recording_id": r, "start": s, "end": s + window, "distance": d}
                        for d, r, s in chosen]}


def temporal(gallery, query, *, approximate=False, top_k=10, candidate_budget=128,
             posting_budget=1_000_000):
    started = time.perf_counter()
    try:
        result = gallery.index.search(query, approximate=approximate, top_k=top_k,
                                      candidate_budget=candidate_budget, posting_budget=posting_budget)
    except ValueError as exc:  # Budget exhaustion returns no partial top-k.
        return {"mode": "temporal-approx" if approximate else "temporal-exhaustive",
                "status": "budget_exceeded", "message": str(exc), "matches": [],
                "candidate_budget": candidate_budget, "posting_budget": posting_budget}
    return {"mode": "temporal-approx" if approximate else "temporal-exhaustive", "status": "ok",
            "exhaustive": result["exhaustive"], "eligible": result["eligible"],
            "scored": result["scored"], "posting_visits": result["posting_visits"],
            "candidate_budget": candidate_budget, "posting_budget": posting_budget,
            "candidate_intervals": result["candidate_intervals"],
            "query_s": time.perf_counter() - started, "spec_id": result["spec_id"],
            "generation_id": result["generation_id"],
            "matches": [{"recording_id": m["id"], "start": m["start"], "end": m["end"],
                         "distance": m["distance"], "source_sha256": m["source_sha256"]}
                        for m in result["matches"]]}


def recall(found, oracle):
    """Fraction of the exhaustive top-k intervals also returned."""
    reference = {(m["recording_id"], m["start"]) for m in oracle}
    if not reference:
        return None
    return len(reference & {(m["recording_id"], m["start"]) for m in found}) / len(reference)
