"""Real-data evaluation: detection, retrieval and catalog association kept separate.

Labels: a match is "catalog-relevant" when its interval overlaps the predicted
arrival window [P - pre, S + post] of a USGS-cataloged regional earthquake at the
same station; "same-source" additionally requires that event to lie within 50 km
epicentral and 30 km depth of the query's associated event. These are derived
reference labels with location and travel-time uncertainty, not ground truth.
Uncataloged earthquakes exist in this region, so a non-relevant match can still
be a real earthquake; precision here is a lower-bound style diagnostic.
"""
from datetime import datetime, timezone
import json
import math
import platform
import statistics
import time
from pathlib import Path

from obspy.geodetics import gps2dist_azimuth

from . import catalog, dataset, investigate, provenance, retrieval
from .ingest import iso_ns

EPISODE_GAP_S = 25.0   # catalog-independent grouping of P/S/coda triggers
TOP_K = 5
BUDGETS = (16, 64, 256, 1024)


def distribution(values):
    values = [v for v in values if v is not None]
    if not values:
        return None
    ordered = sorted(values)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))]  # noqa: E731
    return {"n": len(values), "mean": statistics.fmean(values), "p50": pick(0.5),
            "p90": pick(0.9), "max": ordered[-1]}


def episodes(trigs):
    """First trigger of each run whose onsets are < EPISODE_GAP_S apart (per stream)."""
    firsts, last = [], {}
    for t in sorted(trigs, key=lambda t: (t["stream"], t["on_ns"])):
        previous = last.get(t["stream"])
        if previous is None or t["on_ns"] - previous >= EPISODE_GAP_S * 1e9:
            firsts.append(t)
        last[t["stream"]] = t["on_ns"]
    return firsts


def hours(segs, warmup_s=0.0):
    return sum(max(0.0, (s["end_ns"] - s["start_ns"]) / 1e9 - warmup_s) for s in segs) / 3600


def evaluate(args):
    definition, manifest, base = investigate.load(args.definition, args.data_root)
    rep, det, assoc_cfg = definition["representation"], definition["detector"], definition["association"]
    dataset_id = definition["id"]
    arrivals = catalog.predicted_arrivals(manifest, assoc_cfg)
    regional = [a for a in arrivals if a["regional"]]
    report = {"version": "seismic-evaluation-v1", "dataset": dataset_id,
              "created_utc": dataset.iso(datetime.now(timezone.utc)),
              "definition_sha256": manifest["definition_sha256"],
              "label_rules": __doc__.strip(), "episode_gap_s": EPISODE_GAP_S,
              "host": {"platform": platform.platform(), "machine": platform.machine(),
                       "python": platform.python_version(), **manifest["software"]},
              "dataset_files": {"waveform_files": len(manifest["waveforms"]),
                                "waveform_bytes": sum(w["bytes"] for w in manifest["waveforms"]),
                                "station_files": len(manifest["stations"]),
                                "catalog_files": len(manifest["catalogs"]),
                                "catalog_events": len(catalog.catalog_events(manifest))}}
    with investigate.connect(args.dsn) as conn:
        client = investigate.client(conn)
        report["provenance"] = provenance.collect(definition, manifest, args.data_root, conn)
        segs = investigate.segments(conn, dataset_id)
        trigs = investigate.triggers(conn, dataset_id, det["version"])
        progress = conn.execute("SELECT stream,committed_samples,committed_chunks,duplicate_samples,"
                                "conflicts,gaps,errors FROM seismic.progress WHERE dataset=%s ORDER BY stream",
                                (dataset_id,)).fetchall()
        event_counts = dict(conn.execute("SELECT kind,count(*) FROM seismic.events WHERE dataset=%s GROUP BY kind",
                                         (dataset_id,)).fetchall())
        size = conn.execute("SELECT pg_total_relation_size('public.seismic_waveforms_chunks'),"
                            "pg_total_relation_size('public.seismic_waveforms_segments'),"
                            "(SELECT count(*) FROM public.seismic_waveforms_segments)").fetchone()
        report["storage"] = {"chunk_table_bytes": size[0], "segment_table_bytes": size[1],
                             "segment_windows": size[2]}
        report["ingestion"] = ingestion_section(segs, progress, event_counts, det, args)
        report["detection"] = detection_section(segs, trigs, arrivals, det, assoc_cfg)
        report["extraction_latency_s"] = extraction_latency(conn, segs, rep)
        report["retrieval"] = retrieval_section(conn, client, segs, trigs, regional, rep, det,
                                                dataset_id, args.background_every_s, args.max_unassociated)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=1, default=str) + "\n")
    markdown = args.output.with_suffix(".md")
    markdown.write_text(render_markdown(report))
    print(json.dumps({"output": str(args.output), "markdown": str(markdown)}))
    return 0


def ingestion_section(segs, progress, event_counts, det, args):
    per_stream = {}
    for stream in sorted({s["stream"] for s in segs}):
        mine = [s for s in segs if s["stream"] == stream]
        per_stream[stream] = {
            split: {"segments": len([s for s in mine if s["split"] == split]),
                    "station_hours": hours([s for s in mine if s["split"] == split]),
                    "monitored_hours": hours([s for s in mine if s["split"] == split], det["warmup_s"]),
                    "segments_shorter_than_window": len([s for s in mine if s["split"] == split
                                                         and s["committed"] < 2048])}
            for split in ("reference", "heldout")}
    replays = []
    for path in sorted(Path(args.replay_reports).glob("replay-*.json")) if args.replay_reports else []:
        replays.append(json.loads(path.read_text()))
    return {"per_stream": per_stream,
            "progress": [dict(zip(["stream", "committed_samples", "committed_chunks", "duplicate_samples",
                                   "conflicts", "gaps", "errors"], row)) for row in progress],
            "event_counts": event_counts,
            "replay_runs": [{k: r.get(k) for k in ("streams", "interrupted_after_chunks", "elapsed_s",
                                                   "actions", "ingest", "detector",
                                                   "append_batch_latency_s", "chunk_emit_to_commit_s")}
                            for r in replays],
            "queue_backlog": "replay ingestion is synchronous (no queue); live backlog is reported by the "
                             "SeedLink source status and was not exercised in this evaluation",
            "reconnects": "not applicable to replay; live mode was not run for this report"}


def detection_section(segs, trigs, arrivals, det, assoc_cfg):
    associations = catalog.associate(trigs, arrivals)
    firsts = episodes(trigs)
    out = {"detector": det, "association": assoc_cfg, "per_stream": {}, "events": []}
    for stream in sorted({s["stream"] for s in segs}):
        for split in ("reference", "heldout"):
            mine = [s for s in segs if s["stream"] == stream and s["split"] == split]
            t = [x for x in trigs if x["stream"] == stream and x["split"] == split]
            e = [x for x in firsts if x["stream"] == stream and x["split"] == split]
            monitored = hours(mine, det["warmup_s"])
            unmatched = [x for x in e if not associations[(x["recording_id"], x["on_sample"])]]
            out["per_stream"].setdefault(stream, {})[split] = {
                "monitored_station_hours": monitored, "triggers": len(t), "episodes": len(e),
                "triggers_per_hour": len(t) / monitored if monitored else None,
                "unmatched_episodes": len(unmatched),
                "unmatched_episodes_per_hour": len(unmatched) / monitored if monitored else None}
    residuals = []
    for a in arrivals:
        mine = [s for s in segs if s["stream"] == a["stream"]]
        split = next((s["split"] for s in mine if s["start_ns"] <= a["p_ns"] < s["end_ns"]), None)
        covered = catalog.monitored(mine, a["stream"], a["p_ns"], det["warmup_s"])
        hits = [x for x in trigs if x["stream"] == a["stream"]
                and a["window_ns"][0] <= x["on_ns"] <= a["window_ns"][1]]
        first = min(hits, key=lambda x: abs(x["on_ns"] - a["p_ns"]), default=None)
        if first is not None:
            residuals.append((first["on_ns"] - a["p_ns"]) / 1e9)
        out["events"].append({"event_id": a["event_id"], "stream": a["stream"], "split": split,
                              "magnitude": a["magnitude"], "depth_km": a["depth_km"],
                              "distance_deg": round(a["distance_deg"], 3), "regional": a["regional"],
                              "predicted_p_utc": iso_ns(a["p_ns"]), "monitored": covered,
                              "detected": bool(hits) if covered else None,
                              "associated_triggers": len(hits),
                              "nearest_p_residual_s": None if first is None else (first["on_ns"] - a["p_ns"]) / 1e9})
    covered = [e for e in out["events"] if e["monitored"]]
    out["summary"] = {
        "catalog_station_arrivals": len(out["events"]),
        "monitored_arrivals": len(covered),
        "unmonitored_arrivals": len(out["events"]) - len(covered),
        "detected": sum(1 for e in covered if e["detected"]),
        "missed": sum(1 for e in covered if not e["detected"]),
        "regional_detected": sum(1 for e in covered if e["detected"] and e["regional"]),
        "regional_monitored": sum(1 for e in covered if e["regional"]),
        "teleseismic_detected": sum(1 for e in covered if e["detected"] and not e["regional"]),
        "teleseismic_monitored": sum(1 for e in covered if not e["regional"]),
        "p_residual_s": distribution(residuals)}
    return out


def extraction_latency(conn, segs, rep, samples=200):
    """Per-window server-side Rust extraction (normalize + features), timed from Python."""
    candidates = [s for s in segs if s["committed"] >= rep["window_samples"]]
    if not candidates:
        return None
    timings, client = [], investigate.client(conn)
    for i in range(samples):
        segment = candidates[i % len(candidates)]
        start = (i * 7919 * rep["stride_samples"]) % (segment["committed"] - rep["window_samples"] + 1)
        data = client.read_interval(segment["recording_id"], start, start + rep["window_samples"])["samples"]
        started = time.perf_counter()
        conn.execute("SELECT morlet_features(morlet_normalize(%s::float8[],%s),%s,%s)",
                     (data, rep["normalization"], segment["rate"], rep["extractor"])).fetchone()
        timings.append(time.perf_counter() - started)
    return distribution(timings)


def relevant_events(match, stream, regional):
    """Regional catalog arrivals whose association window overlaps the match interval."""
    return [a for a in regional if a["stream"] == stream
            and match["start_ns"] < a["window_ns"][1] and a["window_ns"][0] < match["end_ns"]]


def same_source(a, b):
    meters, _, _ = gps2dist_azimuth(a["latitude"], a["longitude"], b["latitude"], b["longitude"])
    return meters <= 50_000 and abs((a["depth_km"] or 0) - (b["depth_km"] or 0)) <= 30


def retrieval_section(conn, client, segs, trigs, regional, rep, det, dataset_id, background_every_s,
                      max_unassociated=60):
    by_id = {s["recording_id"]: s for s in segs}
    associations = catalog.associate(trigs, regional)
    out = {"top_k": TOP_K, "streams": {}, "notes": [
        "pooled-pg scores every reference segment window (stride 512); gallery modes score only the "
        "bounded gallery (detector-trigger windows plus a background grid).",
        "Queries are held-out episode-first triggers; galleries contain reference data only.",
        "catalog-relevant precision undercounts real earthquakes: the region has many uncataloged events."]}
    for stream in sorted({s["stream"] for s in segs}):
        sseg = [s for s in segs if s["stream"] == stream]
        strig = [t for t in trigs if t["stream"] == stream]
        windows = investigate.gallery_windows(sseg, strig, rep, det["query_pre_s"],
                                              background_every_s=background_every_s)
        windows, cap = investigate.cap_gallery(windows)
        spec = retrieval.window_spec(rep, stream.split(".")[-1], rep["units"], sseg[0]["rate"])
        gallery = retrieval.build_gallery(client, spec, windows)
        gallery_relevant = 0
        for recording_id, start, _ in windows:
            seg = by_id[recording_id]
            m = {"start_ns": seg["start_ns"] + round(start * 1e9 / seg["rate"]),
                 "end_ns": seg["start_ns"] + round((start + rep["window_samples"]) * 1e9 / seg["rate"])}
            gallery_relevant += bool(relevant_events(m, stream, regional))
        queries = []
        for t in episodes([t for t in strig if t["split"] == "heldout"]):
            seg = by_id[t["recording_id"]]
            start = investigate.query_start(t, seg, rep, det["query_pre_s"])
            if start is None:
                continue
            assoc = associations[(t["recording_id"], t["on_sample"])]
            queries.append((t, seg, start, assoc[0] if assoc else None))
        # All catalog-associated queries; an evenly spaced subset of the rest.
        associated = [q for q in queries if q[3] is not None]
        others = investigate.evenly([q for q in queries if q[3] is None], max_unassociated)
        query_selection = {"heldout_episode_queries": len(queries), "catalog_associated": len(associated),
                           "unassociated_total": len(queries) - len(associated),
                           "unassociated_evaluated": len(others),
                           "rule": "all associated; unassociated evenly spaced in time"}
        queries = sorted(associated + others, key=lambda q: q[0]["on_ns"])
        leakage = {"gallery_splits": sorted({by_id[r]["split"] for r, _, _ in windows}),
                   "query_splits": sorted({seg["split"] for _, seg, _, _ in queries}),
                   "gallery_kinds": gallery.composition}
        assert leakage["gallery_splits"] == ["reference"] and set(leakage["query_splits"]) <= {"heldout"}
        rows, modes = [], ("pooled-pg", "pooled-gallery", "temporal-exhaustive", "temporal-approx")
        for t, seg, start, event in queries:
            q = retrieval.query_window(client, spec, seg["recording_id"], start)
            meta = retrieval.stream_filter(dataset_id, stream)
            results = {"pooled-pg": retrieval.pooled_pg(client, seg["recording_id"], start,
                                                        start + rep["window_samples"], meta, top_k=TOP_K),
                       "pooled-gallery": retrieval.pooled_gallery(gallery, q, top_k=TOP_K),
                       "temporal-exhaustive": retrieval.temporal(gallery, q, top_k=TOP_K)}
            results["temporal-approx"] = retrieval.temporal(gallery, q, approximate=True, top_k=TOP_K,
                                                            candidate_budget=64)
            sweep = []
            for budget in BUDGETS:
                approx = retrieval.temporal(gallery, q, approximate=True, top_k=TOP_K, candidate_budget=budget)
                exact_ids = {(m["recording_id"], m["start"]) for m in results["temporal-exhaustive"]["matches"]}
                cand = {tuple(c) for c in approx.get("candidate_intervals", [])}
                sweep.append({"candidate_budget": budget, "status": approx["status"],
                              "scored": approx.get("scored"), "posting_visits": approx.get("posting_visits"),
                              "candidate_recall": len(cand & exact_ids) / len(exact_ids) if exact_ids else None,
                              "result_recall": retrieval.recall(approx["matches"],
                                                                results["temporal-exhaustive"]["matches"]),
                              "query_s": approx.get("query_s")})
            row = {"query": {"recording_id": seg["recording_id"], "start": start,
                             "onset_utc": iso_ns(t["on_ns"]), "peak_ratio": t["peak_ratio"],
                             "catalog_event": None if event is None else event["event_id"],
                             "catalog_magnitude": None if event is None else event["magnitude"]},
                   "sweep": sweep}
            for mode in modes:
                r = results[mode]
                matches = []
                for m in r["matches"]:
                    mseg = by_id[m["recording_id"]]
                    span = {"start_ns": mseg["start_ns"] + round(m["start"] * 1e9 / mseg["rate"]),
                            "end_ns": mseg["start_ns"] + round(m["end"] * 1e9 / mseg["rate"])}
                    rel = relevant_events(span, stream, regional)
                    matches.append({"recording_id": m["recording_id"], "start": m["start"],
                                    "utc": iso_ns(span["start_ns"]), "distance": m["distance"],
                                    "split": mseg["split"], "catalog_relevant": bool(rel),
                                    "same_source": bool(event) and any(same_source(event, a) for a in rel)})
                row[mode] = {"status": r["status"], "eligible": r.get("eligible"), "scored": r.get("scored"),
                             "posting_visits": r.get("posting_visits"), "query_s": r.get("query_s"),
                             "matches": matches}
            rows.append(row)
        out["streams"][stream] = {"gallery": {**gallery.build, "composition": gallery.composition,
                                              "cap": cap, "catalog_relevant_windows": gallery_relevant},
                                  "query_selection": query_selection,
                                  "leakage_checks": leakage, "queries": rows,
                                  "summary": summarize_retrieval(rows, modes)}
    return out


def summarize_retrieval(rows, modes):
    summary = {}
    for subset, chosen in (("catalog_associated_queries", [r for r in rows if r["query"]["catalog_event"]]),
                           ("unassociated_queries", [r for r in rows if not r["query"]["catalog_event"]])):
        block = {"queries": len(chosen)}
        for mode in modes:
            ok = [r[mode] for r in chosen if r[mode]["status"] == "ok"]
            p = [sum(m["catalog_relevant"] for m in r["matches"]) / TOP_K for r in ok]
            same = [any(m["same_source"] for m in r["matches"]) for r in ok]
            rr = []
            for r in ok:
                rank = next((i + 1 for i, m in enumerate(r["matches"]) if m["catalog_relevant"]), None)
                rr.append(0 if rank is None else 1 / rank)
            block[mode] = {"ok": len(ok), "empty_results": sum(1 for r in ok if not r["matches"]),
                           "catalog_relevant_precision_at_k": statistics.fmean(p) if p else None,
                           "catalog_relevant_mrr": statistics.fmean(rr) if rr else None,
                           "same_source_hit_rate": (sum(same) / len(same)) if same and subset.startswith("catalog") else None,
                           "query_s": distribution([r["query_s"] for r in ok]),
                           "scored": distribution([r["scored"] for r in ok]),
                           "posting_visits": distribution([r["posting_visits"] for r in ok])}
        sweeps = {}
        for r in chosen:
            for s in r["sweep"]:
                sweeps.setdefault(s["candidate_budget"], []).append(s)
        block["approximate_sweep"] = {
            budget: {"candidate_recall": distribution([s["candidate_recall"] for s in items]),
                     "result_recall": distribution([s["result_recall"] for s in items]),
                     "posting_visits": distribution([s["posting_visits"] for s in items]),
                     "scored": distribution([s["scored"] for s in items]),
                     "query_s": distribution([s["query_s"] for s in items])}
            for budget, items in sorted(sweeps.items())}
        ties = 0
        for r in chosen:
            a, b = r["pooled-gallery"], r["temporal-exhaustive"]
            if a["status"] == b["status"] == "ok":
                pa = sum(m["catalog_relevant"] for m in a["matches"])
                pb = sum(m["catalog_relevant"] for m in b["matches"])
                ties += pa == pb
        block["pooled_vs_temporal_equal_precision_queries"] = ties
        summary[subset] = block
    return summary


def fmt(value, digits=3):
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}" if math.isfinite(value) else str(value)
    return str(value)


def render_markdown(r):
    pv = r["provenance"]
    code = pv["code"]
    lines = [f"# Seismic replay evaluation: {r['dataset']}", "",
             f"Generated {r['created_utc']} on {r['host']['platform']} ({r['host']['machine']}), "
             f"Python {r['host']['python']}, ObsPy {r['host']['obspy']}.", "",
             f"Code `{code['commit']}`" + (" **with uncommitted changes**" if code["dirty"] else "")
             + f"; representation `{pv['representation']['extractor']}` (window "
             f"{pv['representation']['window_samples']}, stride {pv['representation']['stride_samples']}, "
             f"{pv['representation']['normalization']}); detector `{pv['detector_version']}`; "
             f"definition sha256 `{pv['definition_sha256'][:16]}…`; manifest sha256 "
             f"`{pv['manifest_sha256'][:16]}…`; database `{pv['database']}` "
             f"(pg_morlet {pv['pg_morlet_extversion']}, stored extractor versions {pv['stored_extractor_versions']}).", "",
             "Research diagnostics only. Distances are representation distances, not probabilities; "
             "catalog labels carry location and travel-time uncertainty; unmatched triggers are not "
             "automatically false positives.", "", "## Data and ingestion", "",
             "| Stream | Split | Segments | Station-hours | Monitored hours |", "|---|---|---:|---:|---:|"]
    for stream, splits in r["ingestion"]["per_stream"].items():
        for split, v in splits.items():
            lines.append(f"| {stream} | {split} | {v['segments']} | {fmt(v['station_hours'], 2)} | "
                         f"{fmt(v['monitored_hours'], 2)} |")
    lines += ["", "| Stream | Committed samples | Duplicate samples verified | Gaps | Conflicts | Errors |",
              "|---|---:|---:|---:|---:|---:|"]
    for p in r["ingestion"]["progress"]:
        lines.append(f"| {p['stream']} | {p['committed_samples']} | {p['duplicate_samples']} | {p['gaps']} | "
                     f"{p['conflicts']} | {p['errors']} |")
    s = r["storage"]
    lines += ["", f"Storage: chunks {s['chunk_table_bytes'] / 1e9:.2f} GB, segment features "
                  f"{s['segment_table_bytes'] / 1e6:.1f} MB, {s['segment_windows']} windows. Raw miniSEED "
                  f"cache {r['dataset_files']['waveform_bytes'] / 1e6:.0f} MB.", ""]
    ex = r["extraction_latency_s"]
    if ex:
        lines.append(f"Server-side extraction per 2048-sample window: p50 {ex['p50'] * 1e3:.2f} ms, "
                     f"p90 {ex['p90'] * 1e3:.2f} ms (n={ex['n']}).")
    for run in r["ingestion"]["replay_runs"]:
        b, c = run["append_batch_latency_s"], run["chunk_emit_to_commit_s"]
        lines.append(f"- Replay {run['streams']}: {fmt(run['elapsed_s'], 0)} s, actions {run['actions']}, "
                     f"append batch p50/p90 {fmt(b and b['p50'])}/{fmt(b and b['p90'])} s, "
                     f"emit-to-commit p90 {fmt(c and c['p90'])} s.")
    d = r["detection"]
    lines += ["", "## Detection (STA/LTA baseline)", "",
              "| Stream | Split | Monitored h | Triggers | Episodes | Unmatched episodes/h |",
              "|---|---|---:|---:|---:|---:|"]
    for stream, splits in d["per_stream"].items():
        for split, v in splits.items():
            lines.append(f"| {stream} | {split} | {fmt(v['monitored_station_hours'], 1)} | {v['triggers']} | "
                         f"{v['episodes']} | {fmt(v['unmatched_episodes_per_hour'], 2)} |")
    sm = d["summary"]
    lines += ["", f"Catalog station-arrivals: {sm['catalog_station_arrivals']} "
                  f"({sm['monitored_arrivals']} monitored, {sm['unmonitored_arrivals']} in gaps or warm-up). "
                  f"Detected {sm['detected']}, missed {sm['missed']} "
                  f"(regional {sm['regional_detected']}/{sm['regional_monitored']}, "
                  f"teleseismic {sm['teleseismic_detected']}/{sm['teleseismic_monitored']}).", ""]
    if sm["p_residual_s"]:
        pr = sm["p_residual_s"]
        lines.append(f"Onset minus predicted P (IASP91): median {pr['p50']:.2f} s, p90 {pr['p90']:.2f} s, "
                     f"max {pr['max']:.2f} s (n={pr['n']}).")
    lines += ["", "## Retrieval", ""]
    for stream, block in r["retrieval"]["streams"].items():
        g = block["gallery"]
        lines += [f"### {stream}", "",
                  f"Gallery: {g['windows']} reference windows {g['composition']}, "
                  f"{g['catalog_relevant_windows']} overlap catalog arrival windows; "
                  f"{g['posting_entries']} posting entries.", ""]
        for subset, sm in block["summary"].items():
            lines += [f"{subset.replace('_', ' ')}: {sm['queries']}", "",
                      "| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |",
                      "|---|---:|---:|---:|---:|---:|---:|"]
            for mode in ("pooled-pg", "pooled-gallery", "temporal-exhaustive", "temporal-approx"):
                m = sm[mode]
                lines.append(f"| {mode} | {fmt(m['catalog_relevant_precision_at_k'])} | "
                             f"{fmt(m['catalog_relevant_mrr'])} | {fmt(m['same_source_hit_rate'])} | "
                             f"{fmt(m['scored'] and m['scored']['p50'])} | "
                             f"{fmt(m['posting_visits'] and m['posting_visits']['p50'])} | "
                             f"{fmt(m['query_s'] and m['query_s']['p50'])} |")
            lines += ["", "| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |",
                      "|---:|---:|---:|---:|"]
            for budget, v in sm["approximate_sweep"].items():
                lines.append(f"| {budget} | {fmt(v['candidate_recall'] and v['candidate_recall']['mean'])} | "
                             f"{fmt(v['result_recall'] and v['result_recall']['mean'])} | "
                             f"{fmt(v['posting_visits'] and v['posting_visits']['p50'])} |")
            lines += ["", f"Queries where pooled-gallery and temporal precision are equal: "
                          f"{sm['pooled_vs_temporal_equal_precision_queries']}", ""]
    return "\n".join(lines) + "\n"
