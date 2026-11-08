"""Decision state: what a fast decision model ("System 1") needs to know about one context segment (#328).

`decision_state(conn, collection, segment_id)` returns compact JSON composed from pg_morlet's context API, with no
new SQL:
- the segment and its context (occurrence identifiers such as run or part numbers only on request, so a model
  cannot decide from them);
- per value, the deviation from the same-context history (`morlet_compare_to_history`);
- the summary score and its quantile (`morlet_compare_summary`);
- the nearest earlier cases with their labels (`morlet_history_neighbors`).

It only reads. The MCP server exposes it as the tool `decision_state`; a System 1 worker calls it directly, for
example after a commit notification, and sends the result as the model's state. Every number traces back to stored
samples: `segment` names the recording and sample range, each neighbour names its own.
"""
from __future__ import annotations

import hashlib
import json
import math
from typing import Any, Iterable

STATE_VERSION = 1


def _num(x, digits=6):
    """Round for a compact state; None and non-finite values become None."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return float(f"{x:.{digits}g}")


def decision_state(conn, collection: str, segment_id: int, *, match_keys: Iterable[str] = (),
                   exclude_labels: Iterable[str] = (), min_history: int = 3, values: Iterable[str] | None = None,
                   neighbors: int = 3, include_occurrence: bool = False) -> dict[str, Any]:
    """The decision state of one segment of `collection` (a regclass name such as 'public.milling').

    match_keys: context keys the history must share (empty: all of the segment's context keys).
    exclude_labels: history segments overlapping these labels are left out (for example known-bad parts).
    values: the simple-v1 value names to report (default: all); the summary score uses pg_morlet's defaults.
    neighbors: how many nearest earlier cases to include (0: none).
    """
    match_keys, exclude_labels = list(match_keys), list(exclude_labels)
    seg_table = conn.execute("SELECT segments::text FROM morlet_context_relations(%s::regclass)",
                             (collection,)).fetchone()[0]
    seg = conn.execute(f"SELECT s.recording_id, s.start_sample, s.end_sample, s.context, s.occurrence, s.stale "
                       f"FROM {seg_table} s WHERE s.segment_id = %s", (segment_id,)).fetchone()
    if seg is None:
        raise ValueError(f"segment {segment_id} not found in {collection} (or not visible)")
    rid, start, end, context, occurrence, stale = seg
    rec = conn.execute(f"SELECT sample_rate_hz, channel, unit FROM {collection} WHERE recording_id = %s",
                       (rid,)).fetchone()
    segment = {"collection": collection, "segment_id": segment_id, "recording_id": rid, "start_sample": start,
               "end_sample": end, "sample_rate_hz": rec[0], "channel": rec[1], "unit": rec[2], "context": context}
    if include_occurrence:
        segment["occurrence"] = occurrence
    if stale:
        segment["stale"] = True

    rows = conn.execute(
        "SELECT value_name, value, hist_n, hist_median, relative_deviation, quantile, status "
        "FROM morlet_compare_to_history(%s::regclass, %s, %s::text[], exclude_labels => %s::text[], "
        "min_history => %s)", (collection, segment_id, match_keys, exclude_labels, min_history)).fetchall()
    wanted = None if values is None else set(values)
    vals = [{"name": n, "value": _num(v), "history_median": _num(m), "relative_deviation": _num(r),
             "history_quantile": _num(q), "status": s}
            for n, v, h, m, r, q, s in rows if wanted is None or n in wanted]
    hist_n = max((h for _, _, h, *_ in rows if h is not None), default=0)

    summ = conn.execute(
        "SELECT score, score_quantile, top_value, top_relative_deviation, hist_n, status "
        "FROM morlet_compare_summary(%s::regclass, %s, %s::text[], exclude_labels => %s::text[], "
        "min_history => %s)", (collection, segment_id, match_keys, exclude_labels, min_history)).fetchone()
    summary = None if summ is None else {
        "score": _num(summ[0]), "score_quantile": _num(summ[1]), "top_value": summ[2],
        "top_relative_deviation": _num(summ[3]), "history_size": summ[4], "status": summ[5]}

    near = []
    if neighbors > 0 and hist_n > 0:
        for nid, nrid, ns, ne, _occ, dist, _v, labels in conn.execute(
                "SELECT * FROM morlet_history_neighbors(%s::regclass, %s, %s::text[], "
                "exclude_labels => %s::text[], top_k => %s)",
                (collection, segment_id, match_keys, exclude_labels, neighbors)):
            near.append({"recording_id": nrid, "start_sample": ns, "end_sample": ne, "distance": _num(dist),
                         "labels": sorted({lab["label"] for lab in labels or []})})

    return {"v": STATE_VERSION, "segment": segment,
            "history": {"match_keys": match_keys or sorted(context), "size": hist_n,
                        "excluded_labels": exclude_labels},
            "values": vals, "summary": summary, "neighbors": near}


def segment_at(conn, collection: str, recording_id: str, sample: int) -> int:
    """The id of the narrowest current (not stale) context segment of a recording that contains a sample."""
    seg_table = conn.execute("SELECT segments::text FROM morlet_context_relations(%s::regclass)",
                             (collection,)).fetchone()[0]
    row = conn.execute(f"SELECT segment_id FROM {seg_table} WHERE recording_id = %s AND start_sample <= %s "
                       f"AND %s < end_sample AND NOT stale ORDER BY end_sample - start_sample, segment_id LIMIT 1",
                       (recording_id, sample, sample)).fetchone()
    if row is None:
        raise ValueError(f"no context segment of {recording_id!r} contains sample {sample} "
                         f"(segments are built with morlet_build_context_segments)")
    return row[0]


def state_hash(state: dict) -> str:
    """A stable SHA-256 of a state (canonical JSON), to record in a decision log which state a decision used."""
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
