#!/usr/bin/env python3
"""Evaluate fixed-window retrieval using run-separated, checksummed recordings."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import platform
import subprocess
import time

import numpy as np


METHODS = ("statistics", "fft_bands", "morlet", "morlet_temporal_experimental", "aligned_samples")


def normalize(values, mode):
    values = np.asarray(values, dtype=np.float64)
    if mode == "none":
        return values.copy()
    centered = (values-values[0]) - np.mean(values-values[0])
    if mode == "demean":
        return centered
    scale = np.sqrt(np.mean(centered**2))
    return centered/scale if scale > 0 else np.zeros_like(centered)


def baseline_features(samples, rate, normalization):
    x = normalize(samples, normalization)
    ac = x - np.mean(x)
    rms = np.sqrt(np.mean(ac**2))
    crest = np.max(np.abs(ac))/rms if rms > 0 else 0
    kurtosis = np.mean(ac**4)/rms**4 if rms > 0 else 0
    statistics = np.log1p([rms, crest, kurtosis])
    frequencies = np.fft.rfftfreq(len(ac), 1/rate)
    power = np.abs(np.fft.rfft(ac))**2 / len(ac)**2
    centers = np.geomspace(rate/64, rate*0.4, 12)
    edges = np.r_[centers[0]/np.sqrt(centers[1]/centers[0]),
                  np.sqrt(centers[:-1]*centers[1:]),
                  centers[-1]*np.sqrt(centers[-1]/centers[-2])]
    fft = np.log1p([power[(frequencies >= a) & (frequencies < b)].sum()
                    for a, b in zip(edges[:-1], edges[1:])])
    return {"statistics": statistics, "fft_bands": fft, "aligned_samples": x}


def load_windows(manifest_path, window, query_windows, gallery_windows):
    if not 512 <= window <= 4096 or min(query_windows, gallery_windows) < 1:
        raise ValueError("window must be 512..4096 and window counts positive")
    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    recordings = manifest["recordings"]
    ids = [r["id"] for r in recordings]
    if len(ids) != len(set(ids)):
        raise ValueError("recording IDs must be unique")
    groups = {split: {r["run_id"] for r in recordings if r["split"] == split}
              for split in ("query", "gallery")}
    if not groups["query"] or not groups["gallery"] or groups["query"] & groups["gallery"]:
        raise ValueError("query/gallery must contain disjoint acquisition run IDs")
    windows = []
    for recording in recordings:
        if recording["split"] not in groups:
            raise ValueError("split must be query or gallery")
        path = manifest_path.parent / recording["path"]
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != recording["sha256"]:
            raise ValueError(f"source checksum mismatch: {recording['id']}")
        values = np.load(io.BytesIO(content), allow_pickle=False)
        rate = recording["sample_rate_hz"]
        if values.ndim != 1 or not np.isfinite(values).all() or np.any(np.abs(values)>1e9):
            raise ValueError("source must be a finite bounded single channel")
        if not isinstance(rate, (int, float)) or not np.isfinite(rate) or not 128 <= rate <= 200000:
            raise ValueError("sample rate outside current representation contract")
        count = len(values)//window
        if count < 1:
            raise ValueError("recording shorter than window")
        limit = query_windows if recording["split"] == "query" else gallery_windows
        starts = np.linspace(0, count-1, min(count, limit), dtype=int)*window
        for start in starts:
            windows.append({**recording, "start": int(start), "end": int(start+window),
                            "samples": values[start:start+window]})
    return manifest, windows


def rank(query, gallery, vectors, method, top_k):
    eligible = [i for i, row in enumerate(gallery) if
                (row["sample_rate_hz"], row["channel"], row["unit"]) ==
                (query["sample_rate_hz"], query["channel"], query["unit"]) and
                row["run_id"] != query["run_id"]]
    ranked = sorted(((float(np.sqrt(np.mean((vectors[method][i]-query["features"][method])**2))), i)
                     for i in eligible),
                    key=lambda item: (item[0], gallery[item[1]]["id"], gallery[item[1]]["start"]))
    selected = []
    for distance, index in ranked:
        row = gallery[index]
        if any(other["id"] == row["id"] and other["start"] < row["end"] and other["end"] > row["start"]
               for _, other in selected):
            continue
        selected.append((distance, row))
        if len(selected) == top_k:
            break
    relevant = sum(gallery[i]["label"] == query["label"] for i in eligible)
    hits = sum(row["label"] == query["label"] for _, row in selected)
    return {"precision_at_k": hits/top_k, "recall_at_k": hits/relevant if relevant else None,
            "relevant_candidates": relevant, "returned": len(selected),
            "nearest_distance": selected[0][0] if selected else None,
            "matches": [{"id": row["id"], "start": row["start"], "end": row["end"],
                         "label": row["label"], "distance": distance} for distance, row in selected]}


def evaluate(manifest_path, features_bin, *, window=1024, query_windows=6,
             gallery_windows=24, top_k=10, normalization="zscore"):
    if not 1 <= top_k <= 100 or normalization not in ("none", "demean", "zscore"):
        raise ValueError("invalid top_k or normalization")
    manifest, rows = load_windows(manifest_path, window, query_windows, gallery_windows)
    payload = "".join(json.dumps({"samples": r["samples"].tolist(),
                        "sample_rate_hz": r["sample_rate_hz"], "normalization": normalization})+"\n" for r in rows)
    started = time.perf_counter()
    output = subprocess.run([str(Path(features_bin).resolve())], input=payload,
                            text=True, capture_output=True, check=True)
    features = [json.loads(line) for line in output.stdout.splitlines()]
    if len(features) != len(rows) or {f["version"] for f in features} != {"morlet-energy-v1"}:
        raise ValueError("unexpected extractor output count/version")
    extraction_s = time.perf_counter()-started
    for row, feature in zip(rows, features):
        row["features"] = {**baseline_features(row["samples"], row["sample_rate_hz"], normalization),
                           **{k: np.asarray(feature[k]) for k in METHODS if k in feature}}
        if any(not np.isfinite(v).all() for v in row["features"].values()):
            raise ValueError("non-finite evaluation features")
    queries = [r for r in rows if r["split"] == "query"]
    gallery = [r for r in rows if r["split"] == "gallery"]
    vectors = {method: [r["features"][method] for r in gallery] for method in METHODS}
    results, summary = {}, {}
    for method in METHODS:
        method_results = [{"query_id": q["id"], "start": q["start"], "label": q["label"],
                           **rank(q, gallery, vectors, method, top_k)} for q in queries]
        # Aggregate within recordings first; adjacent windows are not independent trials.
        per_recording = []
        for recording_id in sorted({r["query_id"] for r in method_results}):
            group = [r for r in method_results if r["query_id"] == recording_id]
            recalls = [r["recall_at_k"] for r in group if r["recall_at_k"] is not None]
            per_recording.append({"id": recording_id,
                "precision_at_k": float(np.mean([r["precision_at_k"] for r in group])),
                "recall_at_k": float(np.mean(recalls)) if recalls else None})
        recalls = [r["recall_at_k"] for r in per_recording if r["recall_at_k"] is not None]
        summary[method] = {"macro_recording_precision_at_k": float(np.mean([r["precision_at_k"] for r in per_recording])),
            "macro_recording_recall_at_k": float(np.mean(recalls)) if recalls else None,
            "per_recording": per_recording,
            "negative_queries": sum(r["relevant_candidates"] == 0 for r in method_results)}
        results[method] = method_results
    binary = Path(features_bin)
    return {"report_version": 1, "dataset": {k: v for k, v in manifest.items() if k != "recordings"},
        "sources": [{k: v for k, v in r.items() if k != "path"} for r in manifest["recordings"]],
        "manifest_sha256": hashlib.sha256(Path(manifest_path).read_bytes()).hexdigest(),
        "features_binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "extractor_version": "morlet-energy-v1", "python": platform.python_version(),
        "numpy": np.__version__, "platform": platform.platform(),
        "config": {"window": window, "top_k": top_k, "normalization": normalization,
            "query_windows_per_recording": query_windows, "gallery_windows_per_recording": gallery_windows,
            "window_selection": "evenly spaced disjoint windows; no learned scaling or threshold"},
        "query_count": len(queries), "gallery_count": len(gallery),
        "rust_extraction_including_adapter_s": extraction_s, "summary": summary, "queries": results}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--features-bin", type=Path, default=Path("target/release/examples/features"))
    parser.add_argument("--window", type=int, default=1024)
    parser.add_argument("--query-windows", type=int, default=6)
    parser.add_argument("--gallery-windows", type=int, default=24)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--normalization", choices=["none", "demean", "zscore"], default="zscore")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise ValueError("choose a new output path; reports are not overwritten")
    report = evaluate(args.manifest, args.features_bin, window=args.window,
        query_windows=args.query_windows, gallery_windows=args.gallery_windows,
        top_k=args.top_k, normalization=args.normalization)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    print(json.dumps(report["summary"], indent=2))


if __name__ == "__main__":
    main()
