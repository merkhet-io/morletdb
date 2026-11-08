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
import uuid
from typing import Any, Iterable

STATE_VERSION = 2
SUMMARY_VALUES = ("duration_s", "std", "crest", "kurtosis")   # morlet_compare_summary's default score values


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
                   neighbors: int = 3, include_occurrence: bool = False, reliable_history: int = 10,
                   summary_quantile: bool = False) -> dict[str, Any]:
    """The decision state of one segment of `collection` (a regclass name such as 'public.milling').

    match_keys: context keys the history must share (empty: all of the segment's context keys).
    exclude_labels: history segments overlapping these labels are left out (for example known-bad parts).
    values: the simple-v1 value names to report (default: all); the summary score uses pg_morlet's defaults.
    neighbors: how many nearest earlier cases to include (0: none).
    reliable_history: below this many history segments the state says so (history.small, history.note): with few
    earlier segments one unusual segment, or a short transient in this one, can dominate the deviations.
    summary_quantile: also compute the summary score's quantile within the history (morlet_compare_summary; it
    repeats the history query and costs about twice as much). The summary score itself is derived from the
    per-value rows, equal to morlet_compare_summary's score and top value.
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

    scored = [(abs(r), n) for n, _v, _h, _m, r, _q, _s in rows if n in SUMMARY_VALUES and r is not None]
    summary = None
    if scored:
        top = max(scored)
        summary = {"score": _num(top[0]), "top_value": top[1],
                   "top_relative_deviation": _num(next(r for n, *_x, r, _q, _s in rows if n == top[1])),
                   "score_values": list(SUMMARY_VALUES)}
    if summary_quantile:
        summ = conn.execute(
            "SELECT score_quantile FROM morlet_compare_summary(%s::regclass, %s, %s::text[], "
            "exclude_labels => %s::text[], min_history => %s)",
            (collection, segment_id, match_keys, exclude_labels, min_history)).fetchone()
        if summary is not None and summ is not None:
            summary["score_quantile"] = _num(summ[0])

    near = []
    if neighbors > 0 and hist_n > 0:
        for nid, nrid, ns, ne, _occ, dist, _v, labels in conn.execute(
                "SELECT * FROM morlet_history_neighbors(%s::regclass, %s, %s::text[], "
                "exclude_labels => %s::text[], top_k => %s)",
                (collection, segment_id, match_keys, exclude_labels, neighbors)):
            near.append({"recording_id": nrid, "start_sample": ns, "end_sample": ne, "distance": _num(dist),
                         "labels": sorted({lab["label"] for lab in labels or []})})

    history = {"match_keys": match_keys or sorted(context), "size": hist_n, "excluded_labels": exclude_labels}
    if hist_n < reliable_history:
        history["small"] = True
        history["note"] = (f"only {hist_n} earlier comparable segments: one unusual segment, or a short transient in "
                           f"this one, can dominate the deviations")
    return {"v": STATE_VERSION, "segment": segment,
            "history": history,
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


DECISION_PREFIX = "s1:"     # labels written by the decision log: "s1:<answer>", e.g. "s1:replace_insert"
LOG_VERSION = 1


def log_decision(conn, collection: str, segments: Iterable[tuple[str, int, int]], answer: str, *, model: str,
                 question: str, probability: float | None = None, threshold: float | None = None,
                 state: dict | None = None, extra: dict | None = None) -> list[int]:
    """Store one decision as a label "s1:<answer>" on every (recording_id, start_sample, end_sample) it is about,
    for example the channels of one cut. The note holds the model, question, probability, threshold, the state the
    model saw and its hash, so the decision can be explained later from the same recordings; the database adds
    author and time. Needs a role that may insert labels (the MCP server's role cannot). Returns the label ids."""
    if not answer or any(ch.isspace() for ch in answer):
        raise ValueError("answer must be one word, such as replace_insert or continue")
    note = {"v": LOG_VERSION, "decision_id": uuid.uuid4().hex, "model": model, "question": question, "answer": answer,
            "probability": _num(probability), "threshold": _num(threshold),
            "state_sha256": state_hash(state) if state is not None else None, "state": state, **(extra or {})}
    text = json.dumps(note, sort_keys=True, separators=(",", ":"))
    return [conn.execute("SELECT morlet_add_label(%s::regclass, %s, %s, %s, %s, %s)",
                         (collection, rid, start, end, DECISION_PREFIX + answer, text)).fetchone()[0]
            for rid, start, end in segments]


def decisions(conn, collection: str, *, recording_prefix: str = "", answer: str = "", limit: int = 100,
              with_state: bool = False) -> list[dict[str, Any]]:
    """Logged decisions, newest first, grouped by the note they share (one decision on several recordings)."""
    labels = conn.execute("SELECT labels::text FROM morlet_context_relations(%s::regclass)",
                          (collection,)).fetchone()[0]
    rows = conn.execute(
        f"SELECT label_id, recording_id, start_sample, end_sample, label, note, author, created_at FROM {labels} "
        f"WHERE NOT retracted AND starts_with(label, %s) AND (%s = '' OR label = %s) AND starts_with(recording_id, %s) "
        f"ORDER BY created_at DESC, label_id DESC",
        (DECISION_PREFIX, answer, DECISION_PREFIX + answer, recording_prefix)).fetchall()
    out: dict[str, dict] = {}
    for lid, rid, start, end, label, note, author, created in rows:
        if note not in out:
            if len(out) >= limit:
                continue
            try:
                n = json.loads(note)
            except ValueError:
                n = {"note": note}
            if not with_state:
                n.pop("state", None)
            out[note] = {**n, "answer": label[len(DECISION_PREFIX):], "author": author,
                         "decided_at": created.isoformat(), "label_ids": [], "segments": []}
        out[note]["label_ids"].append(lid)
        out[note]["segments"].append({"recording_id": rid, "start_sample": start, "end_sample": end})
    return list(out.values())


def state_hash(state: dict) -> str:
    """A stable SHA-256 of a state (canonical JSON), to record in a decision log which state a decision used."""
    return hashlib.sha256(json.dumps(state, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
