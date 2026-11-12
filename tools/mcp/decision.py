"""Decision state: what a fast decision model ("System 1") needs to know about one context segment (#328).

`decision_state(conn, collection, segment_id)` returns compact JSON about one segment; `decision_states` returns
the states of several segments at once, for example all channels of one cut, which is how a System 1 worker
should call it (#403):
- the segment and its context (occurrence identifiers such as run or part numbers only on request, so a model
  cannot decide from them);
- per value, the deviation from the same-context history (as morlet_compare_to_history computes it);
- a summary score (as morlet_compare_summary's score and top value; its quantile on request);
- the nearest earlier cases with their labels (as morlet_history_neighbors ranks them);
- optionally the drift from the asset's own reference segments, and labelled earlier cases as examples.

The history of all requested segments that share a context is read in one query, and medians, quantiles and
distances are computed here with the same rules as the SQL functions (PostgreSQL's percentile_cont, the same
floors, filters and order); tools/mcp/test_decision_db.py checks them against those functions. Reading the
history once per cut instead of three SQL calls per channel, and leaving out the pooled spreads the state does not
use, is what makes a decision fast on a small device: the SQL path re-reads the collection for every channel.

It only reads. The MCP server exposes it as the tool `decision_state`; a System 1 worker calls it directly, for
example after a commit notification, and sends the result as the model's state. Every number traces back to stored
samples: `segment` names the recording and sample range, each neighbour and example names its own.
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
import uuid
import weakref
from typing import Any, Iterable, Mapping

import numpy as np

STATE_VERSION = 4
SUMMARY_VALUES = ("duration_s", "std", "crest", "kurtosis")   # morlet_compare_summary's default score values


def _num(x, digits=6):
    """Round for a compact state; None and non-finite values become None."""
    if x is None:
        return None
    x = float(x)
    if not math.isfinite(x):
        return None
    return float(f"{x:.{digits}g}")


def _median(xs):
    """PostgreSQL's percentile_cont(0.5) over the non-NULL values: lo + 0.5 * (hi - lo) of the middle pair."""
    return _median_sorted(sorted(x for x in xs if x is not None))


def _median_sorted(v):
    if not v:
        return None
    pos = 0.5 * (len(v) - 1)
    lo, hi = math.floor(pos), math.ceil(pos)
    return v[lo] if lo == hi else v[lo] + (pos - lo) * (v[hi] - v[lo])


IN_CHANNEL_UNITS = ("mean", "std", "rms", "p75", "peak_abs")    # morlet_core::values::IN_CHANNEL_UNITS


def _negligible_floors(level, names):
    """Mirror of morlet_core::values::negligible_floors: 1e-9 x |level| for values in channel units, else 0."""
    f = abs(level) * 1e-9 if math.isfinite(level) else 0.0
    return [f if n in IN_CHANNEL_UNITS else 0.0 for n in names]


class _History:
    """The history of one segment: its rows and their values as a matrix (NULL as NaN), medians computed once."""

    def __init__(self, rows, width):
        self.rows = rows
        try:                                              # no NULL value: the fast path
            self.V = np.array([r[4] for r in rows], dtype=np.float64).reshape(len(rows), width)
        except (TypeError, ValueError):
            self.V = np.array([[np.nan if v is None else v for v in r[4]] for r in rows], dtype=np.float64
                              ).reshape(len(rows), width)
        self._med = {}

    def med(self, i):
        """PostgreSQL's percentile_cont(0.5) over the non-NULL values of column i."""
        if i not in self._med:
            col = np.sort(self.V[:, i])                   # NaN (NULL) sorts last and is dropped
            self._med[i] = _median_sorted(col[:np.count_nonzero(~np.isnan(col))].tolist())
        return self._med[i]

    def without(self, exclude):                           # the history's label exclusion
        if not exclude:
            return self
        return _History([r for r in self.rows if not any(lab in exclude for _lid, lab in r[5])], self.V.shape[1])


def decision_state(conn, collection: str, segment_id: int, *, baseline: Iterable[int] = (),
                   **options) -> dict[str, Any]:
    """The decision state of one segment of `collection` (a regclass name such as 'public.milling').
    Takes the options of decision_states; baseline is this segment's list of reference segments."""
    return decision_states(conn, collection, [segment_id], baselines={segment_id: list(baseline)}, **options)[0]


def decision_states(conn, collection: str, segment_ids: Iterable[int], *, match_keys: Iterable[str] = (),
                    exclude_labels: Iterable[str] = (), min_history: int = 3, values: Iterable[str] | None = None,
                    neighbors: int = 3, include_occurrence: bool = False, reliable_history: int = 10,
                    summary_quantile: bool = False, baselines: Mapping[int, Iterable[int]] | None = None,
                    examples: int = 0, example_labels: Iterable[str] | None = None,
                    example_match_keys: Iterable[str] | None = None) -> list[dict[str, Any]]:
    """The decision states of several segments of `collection`, in the order given.

    match_keys: context keys the history must share (empty: all of the segment's context keys).
    exclude_labels: history segments overlapping these labels are left out (for example known-bad parts).
    values: the simple-v1 value names to report (default: all); the summary score uses pg_morlet's defaults.
    neighbors: how many nearest earlier cases to include (0: none).
    reliable_history: below this many history segments the state says so (history.small, history.note): with few
    earlier segments one unusual segment, or a short transient in this one, can dominate the deviations.
    baselines: per segment id, the segment ids of this asset's own reference segments, such as a tool's first cuts
    after it was changed. The state then also reports each value's deviation from their median (own_baseline):
    wear shows as a drift away from the tool's own start, which a comparison with other tools can miss.
    examples: how many labelled cases to include as worked examples (examples): the nearest segments that carry a
    label (decision labels "s1:..." do not count), with their labels, notes and values, so a model can see what
    labelled cases looked like. The whole history is searched for them, so an example is missing only if no
    labelled case exists, and then examples_note says so (#404). Unlike the history, examples may come from
    segments that exclude_labels keeps out of the history (for example known worn tools). example_labels limits
    which labels count. example_match_keys: the context keys examples must share (default match_keys); fewer keys
    widen the search, for example to other tools of the same process when this tool has no labelled cuts yet.
    summary_quantile: also compute the summary score's quantile within the history (morlet_compare_summary, one
    SQL call per segment; it reads the history again and costs more than the rest of the state).
    """
    ids = [int(s) for s in segment_ids]
    match_keys, exclude_list = list(match_keys), list(dict.fromkeys(exclude_labels))
    exclude_labels = set(exclude_list)
    ex_keys = match_keys if example_match_keys is None else list(example_match_keys)
    wanted_ex = None if example_labels is None else set(example_labels)
    wanted = None if values is None else set(values)
    baselines = {int(k): [int(b) for b in v if int(b) != int(k)] for k, v in (baselines or {}).items()}
    seg_t, vals_t, labels_t, names = _relations(conn, collection)
    rms_i = names.index("rms")
    score_i = [i for i, n in enumerate(names) if n in SUMMARY_VALUES]

    queries = {r[0]: dict(zip(("id", "rid", "start", "end", "context", "occ", "stale", "vals", "rate", "channel",
                               "unit"), r)) for r in conn.execute(
        f"SELECT s.segment_id, s.recording_id, s.start_sample, s.end_sample, s.context, s.occurrence, s.stale, "
        f"v.vals, r.sample_rate_hz, r.channel, r.unit FROM {seg_t} s "
        f"LEFT JOIN {vals_t} v ON v.segment_id = s.segment_id AND v.representation = 'simple-v1' "
        f"JOIN {collection} r ON r.recording_id = s.recording_id WHERE s.segment_id = ANY(%s)", (ids,))}
    for sid in ids:                                       # morlet_comparable_segment's checks
        q = queries.get(sid)
        if q is None:
            raise ValueError(f"segment {sid} not found in {collection} (or not visible)")
        if q["stale"]:
            raise ValueError(f"segment {sid} is stale; rebuild context segments")
        if q["vals"] is None:
            raise ValueError(f"segment {sid} has no simple-v1 values (morlet_build_context_values)")

    def match(q, keys):                                   # morlet_context_match
        if not keys:
            return q["context"]
        if any(k not in q["context"] for k in keys):
            raise ValueError(f"match_keys must be keys of the segment context {q['context']}")
        return {k: q["context"][k] for k in keys}

    def histories(keys):
        """Per query segment, its history before label exclusion: morlet_history_sql's filters (population false,
        no metadata filter, no cutoff), evaluated in SQL for all query segments that share a context match in one
        query. Rows are [segment_id, recording_id, start, end, vals, [(label_id, label), ...]]."""
        groups: dict[str, list[int]] = {}
        for sid in ids:
            groups.setdefault(json.dumps(match(queries[sid], keys), sort_keys=True), []).append(sid)
        out: dict[int, list] = {sid: [] for sid in ids}
        for m, qs in groups.items():
            for row in conn.execute(
                    f"WITH h AS (SELECT q.segment_id AS qid, s.segment_id, s.recording_id, s.start_sample, "
                    f"s.end_sample, v.vals FROM {seg_t} q JOIN {collection} qr ON qr.recording_id = q.recording_id "
                    f"JOIN {seg_t} s ON s.context @> %s::jsonb AND NOT s.stale "
                    f"JOIN {collection} r ON r.recording_id = s.recording_id AND r.sample_rate_hz = qr.sample_rate_hz "
                    f"AND r.channel = qr.channel AND r.unit = qr.unit "
                    f"JOIN {vals_t} v ON v.segment_id = s.segment_id AND v.representation = 'simple-v1' "
                    f"WHERE q.segment_id = ANY(%s) AND s.segment_id <> q.segment_id AND (s.spec = q.spec) IS TRUE "
                    f"AND NOT (s.recording_id = q.recording_id AND s.start_sample < q.end_sample "
                    f"AND q.start_sample < s.end_sample) "
                    f"AND (q.occurrence = '{{}}'::jsonb OR s.occurrence <> q.occurrence)), "
                    # active labels per recording, hash-joined once (the labels table has no recording index)
                    f"l AS (SELECT recording_id, array_agg(label_id ORDER BY label_id) AS ids, "
                    f"array_agg(start_sample ORDER BY label_id) AS starts, array_agg(end_sample ORDER BY label_id) "
                    f"AS ends, array_agg(label ORDER BY label_id) AS labels FROM {labels_t} "
                    f"WHERE NOT retracted AND recording_id IN (SELECT recording_id FROM h) GROUP BY recording_id) "
                    f"SELECT h.*, l.ids, l.starts, l.ends, l.labels FROM h LEFT JOIN l USING (recording_id)",
                    (m, qs), binary=True):
                qid, sid_, rid, start, end_, vals, lids, ls, le, labs = row
                labels = () if lids is None else tuple(
                    (lid, lab) for lid, a, b, lab in zip(lids, ls, le, labs) if a < end_ and start < b)
                out[qid].append([sid_, rid, start, end_, vals, labels])
        return {sid: _History(hs, len(names)) for sid, hs in out.items()}

    main = {sid: h.without(exclude_labels) for sid, h in histories(match_keys).items()}
    ex_hist = histories(ex_keys) if examples > 0 else {}
    levels = sorted({h.med(rms_i) for h in [*main.values(), *ex_hist.values()]} - {None})
    floors_of = {lv: _negligible_floors(lv, names) for lv in levels}    # morlet_simple_value_floors, mirrored

    def floors(hist):                                     # morlet_simple_value_floors of the history's median rms
        return floors_of.get(hist.med(rms_i)) or [None] * len(names)

    def ranked(q, hist, k=None, keep=None):               # morlet_history_neighbors' distance and order
        fl = floors(hist)
        meds = {i: hist.med(i) for i in score_i}
        use = [i for i in score_i if meds[i] is not None and fl[i] is not None and abs(meds[i]) > fl[i]]
        if not use or not hist.rows:
            return []
        qv = np.array([np.nan if q["vals"][i] is None else q["vals"][i] for i in use])
        terms = ((qv - hist.V[:, use]) / np.abs(np.array([meds[i] for i in use]))) ** 2
        valid = ~np.isnan(terms)
        cnt = valid.sum(axis=1)
        total = np.zeros(len(hist.rows))
        for j in range(len(use)):                         # summed in value order, as avg() accumulates
            total = total + np.where(valid[:, j], terms[:, j], 0.0)
        dist = [math.sqrt(t / c) if c else None for t, c in zip(total.tolist(), cnt.tolist())]
        rows = hist.rows
        js = range(len(dist)) if keep is None else [j for j in range(len(dist)) if keep(rows[j])]
        key = lambda j: (dist[j] is None, dist[j] or 0.0, rows[j][1], rows[j][2])
        order = sorted(js, key=key) if k is None else heapq.nsmallest(k, js, key=key)
        return [(dist[j], rows[j]) for j in order]

    all_base = sorted({b for bs in baselines.values() for b in bs})
    base_rows = {r[0]: {"vals": r[1], "rate": r[2], "channel": r[3], "unit": r[4]} for r in conn.execute(
        f"SELECT s.segment_id, v.vals, r.sample_rate_hz, r.channel, r.unit FROM {seg_t} s "
        f"JOIN {vals_t} v ON v.segment_id = s.segment_id AND v.representation = 'simple-v1' "
        f"JOIN {collection} r ON r.recording_id = s.recording_id WHERE s.segment_id = ANY(%s)",
        (all_base,))} if all_base else {}

    states = []
    for sid in ids:
        q = queries[sid]
        hist = main[sid]
        n = len(hist.rows)
        fl = floors(hist)
        cmp = []                                          # morlet_compare_to_history, without the pooled spreads
        for i, name in enumerate(names):
            x = q["vals"][i]
            col = hist.V[:, i]
            m = hist.med(i)
            ok = n >= min_history
            nonzero = m is not None and fl[i] is not None and abs(m) > fl[i]
            below = int(np.count_nonzero(col < x)) if x is not None else 0
            equal = int(np.count_nonzero(col == x)) if x is not None else 0
            status = ("insufficient_history" if not ok else
                      "zero_median" if m is not None and fl[i] is not None and abs(m) <= fl[i] else "ok")
            cmp.append((name, x, m if ok else None,
                        (x - m) / abs(m) if ok and nonzero and x is not None else None,
                        float(below + 0.5 * equal) / n if ok else None, status))
        vals = [{"name": nm, "value": _num(v), "history_median": _num(m), "relative_deviation": _num(r),
                 "history_quantile": _num(qq), "status": s}
                for nm, v, m, r, qq, s in cmp if wanted is None or nm in wanted]

        scored = [(abs(r), nm) for nm, _v, _m, r, _q, _s in cmp if nm in SUMMARY_VALUES and r is not None]
        summary = None
        if scored:
            top = max(scored)
            summary = {"score": _num(top[0]), "top_value": top[1],
                       "top_relative_deviation": _num(next(r for nm, _v, _m, r, _q, _s in cmp if nm == top[1])),
                       "score_values": list(SUMMARY_VALUES)}
        if summary_quantile:
            summ = conn.execute(
                "SELECT score_quantile FROM morlet_compare_summary(%s::regclass, %s, %s::text[], "
                "exclude_labels => %s::text[], min_history => %s)",
                (collection, sid, match_keys, exclude_list, min_history)).fetchone()
            if summary is not None and summ is not None:
                summary["score_quantile"] = _num(summ[0])

        near = []
        if neighbors > 0 and n > 0:
            for dist, h in ranked(q, hist, k=neighbors):
                near.append({"recording_id": h[1], "start_sample": h[2], "end_sample": h[3],
                             "distance": _num(dist), "labels": sorted({lab for _lid, lab in h[5]})})

        segment = {"collection": collection, "segment_id": sid, "recording_id": q["rid"], "start_sample": q["start"],
                   "end_sample": q["end"], "sample_rate_hz": q["rate"], "channel": q["channel"], "unit": q["unit"],
                   "context": q["context"]}
        if include_occurrence:
            segment["occurrence"] = q["occ"]
        out_extra = {}
        if baselines.get(sid):
            out_extra["own_baseline"] = _own_baseline(names, q, [base_rows.get(b) for b in baselines[sid]],
                                                      baselines[sid])
        if examples > 0:
            labelled = lambda h: any(not lab.startswith(DECISION_PREFIX) and (wanted_ex is None or lab in wanted_ex)
                                     for _lid, lab in h[5])
            out_extra.update(_examples(conn, labels_t, names, ranked(q, ex_hist[sid], k=examples, keep=labelled),
                                       examples, wanted_ex))
        hist_info = {"match_keys": match_keys or sorted(q["context"]), "size": n,
                     "excluded_labels": exclude_list}
        if n < reliable_history:
            hist_info["small"] = True
            hist_info["note"] = (f"only {n} earlier comparable segments: one unusual segment, or a short transient "
                                 f"in this one, can dominate the deviations")
        states.append({"v": STATE_VERSION, "segment": segment, "history": hist_info, "values": vals,
                       "summary": summary, "neighbors": near, **out_extra})
    return states


def _own_baseline(names, q, base, ids):
    """Each simple-v1 value's deviation from the median of the given reference segments of the same channel."""
    missing = sorted(b for b, r in zip(ids, base) if r is None or r["vals"] is None)
    if missing:
        raise ValueError(f"baseline segments without values or not visible: {missing}")
    bad = sorted(b for b, r in zip(ids, base) if (r["rate"], r["channel"], r["unit"]) != (q["rate"], q["channel"],
                                                                                         q["unit"]))
    if bad:
        raise ValueError(f"baseline segments of another rate, channel or unit: {bad}")
    rms_i = names.index("rms")
    level = sorted(b["vals"][rms_i] for b in base)[len(base) // 2]
    out = []
    for i, n in enumerate(names):
        col = sorted(b["vals"][i] for b in base)
        med = (col[(len(col) - 1) // 2] + col[len(col) // 2]) / 2
        unit_valued = n in ("mean", "std", "rms", "p75", "peak_abs")
        zero = med == 0 or (unit_valued and abs(med) <= 1e-9 * abs(level))
        out.append({"name": n, "value": _num(q["vals"][i]), "baseline_median": _num(med),
                    "relative_deviation": None if zero else _num((q["vals"][i] - med) / abs(med)),
                    "status": "zero_median" if zero else "ok"})
    return {"segments": len(base), "values": out}


def _examples(conn, labels_t, names, ranked, k, wanted):
    """The k nearest segments of the history (no labels excluded) that carry a label other than a logged decision.
    Searches the whole history, so none are returned only when no labelled case exists; examples_note then says
    so."""
    picked = []
    for dist, h in ranked:
        lids = [int(lid) for lid, lab in h[5]
                if not lab.startswith(DECISION_PREFIX) and (wanted is None or lab in wanted)]
        if lids:
            picked.append((dist, h, lids))
            if len(picked) == k:
                break
    if not picked:
        what = "with the labels " + ", ".join(sorted(wanted)) if wanted else "with a label other than a decision"
        return {"examples": [], "examples_note": f"no segment of this history is {what}"}
    notes = dict(conn.execute(f"SELECT label_id, note FROM {labels_t} WHERE label_id = ANY(%s)",
                              ([lid for _d, _h, ls in picked for lid in ls],)).fetchall())
    labels = {int(lid): lab for _d, h, _ls in picked for lid, lab in h[5]}
    return {"examples": [
        {"recording_id": h[1], "start_sample": h[2], "end_sample": h[3], "distance": _num(dist),
         "labels": [{"label": labels[lid], "note": notes.get(lid) or None} for lid in lids],
         "values": {n: _num(v) for n, v in zip(names, h[4])}} for dist, h, lids in picked]}


_RELATIONS: "weakref.WeakKeyDictionary" = weakref.WeakKeyDictionary()


def _relations(conn, collection):
    """The collection's context tables and the simple-v1 value names, looked up once per connection: the names
    follow from the collection's name, and a System 1 worker asks for them on every cut (#403)."""
    try:
        per_conn = _RELATIONS.setdefault(conn, {})
    except TypeError:                                     # a connection that takes no weak reference: no cache
        per_conn = {}
    if collection not in per_conn:
        per_conn[collection] = tuple(conn.execute(
            "SELECT segments::text, vals::text, labels::text, morlet_simple_value_names() "
            "FROM morlet_context_relations(%s::regclass)", (collection,)).fetchone())
    return per_conn[collection]


def segment_at(conn, collection: str, recording_id: str, sample: int) -> int:
    """The id of the narrowest current (not stale) context segment of a recording that contains a sample."""
    seg_table = _relations(conn, collection)[0]
    row = conn.execute(f"SELECT segment_id FROM {seg_table} WHERE recording_id = %s AND start_sample <= %s "
                       f"AND %s < end_sample AND NOT stale ORDER BY end_sample - start_sample, segment_id LIMIT 1",
                       (recording_id, sample, sample)).fetchone()
    if row is None:
        raise ValueError(f"no context segment of {recording_id!r} contains sample {sample} "
                         f"(segments are built with morlet_build_context_segments)")
    return row[0]


def segments_at(conn, collection: str, points: Iterable[tuple[str, int]]) -> list[int]:
    """segment_at for several (recording_id, sample) points in one query, for example the channels of one cut."""
    points = [(str(r), int(s)) for r, s in points]
    seg_table = _relations(conn, collection)[0]
    found = {(r, s): sid for r, s, sid in conn.execute(
        f"SELECT p.rid, p.sample, (SELECT g.segment_id FROM {seg_table} g WHERE g.recording_id = p.rid "
        f"AND g.start_sample <= p.sample AND p.sample < g.end_sample AND NOT g.stale "
        f"ORDER BY g.end_sample - g.start_sample, g.segment_id LIMIT 1) "
        f"FROM unnest(%s::text[], %s::bigint[]) AS p(rid, sample)", ([r for r, _ in points], [s for _, s in points]))}
    missing = [p for p in points if found.get(p) is None]
    if missing:
        raise ValueError(f"no context segment contains {missing[0][1]} of {missing[0][0]!r} "
                         f"(segments are built with morlet_build_context_segments)")
    return [found[p] for p in points]


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
