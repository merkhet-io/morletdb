"""Bounded composition of existing SQL operations; no new numerical or search semantics."""
from __future__ import annotations

import contextlib
from dataclasses import asdict
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal
import json
import math
import re
import time

import psycopg
from psycopg import sql
from psycopg.pq import TransactionStatus
from psycopg.types.json import Jsonb

from ._client import Client, SEARCH_WINDOW
from ._errors import InvalidInputError, InvestigationLimitError, NotFoundError, UnsupportedError, translate
from ._types import CoverageRange, IntervalRef, InvestigationContext, InvestigationResult


def result_dict(result):
    def clean(value):
        if isinstance(value, dict):
            return {k: clean(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [clean(v) for v in value]
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, Decimal):
            return str(value)
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    return clean(asdict(result))


# Server budget errors: compare_to_history's history, value spreads and stream search ("... exceeds N segments"),
# and envelope search's candidate cap. They narrow scope; they are not failures of the data (review of #576).
_BUDGET = re.compile(r"history exceeds|more than max_candidates|exceeds \d+ segments")
# The only envelope representation pg_morlet defines; recorded next to the extension version that defines it.
ENVELOPE_REPRESENTATION = "xc-envelope-f16-v1"


def _cancel(exc, deadline):
    """A cancelled statement is a limit when the deadline or a statement timeout ended it; otherwise (a cancel
    request from another session or thread, or an administrator) it is reported as the database error it is."""
    if time.monotonic() >= deadline or "statement timeout" in str(exc):
        return InvestigationLimitError("investigation deadline or statement timeout exceeded")
    return translate(exc)


def _integer(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise InvalidInputError(f"{name} must be an integer in {low}..{high}")


class _Reader(Client):
    """A per-call client: no shared capability/history cache, and a shrinking statement deadline."""

    def __init__(self, connection, deadline):
        super().__init__(connection)
        self.deadline = deadline
        self.statement_limit = 0
        self.timeout_set = None       # the statement_timeout last sent, in ms

    def remaining(self):
        ms = math.floor((self.deadline - time.monotonic()) * 1000)
        if ms <= 0:
            raise InvestigationLimitError("investigation deadline exceeded")
        return ms

    def _execute(self, query, args=(), **kw):
        remaining = self.remaining()
        limit = min(remaining, self.statement_limit) if self.statement_limit else remaining
        # Resend the timeout only when the deadline has moved closer than a tenth of it (at least 50 ms): one
        # round trip per statement doubled the cost on remote and edge databases. A statement can then overrun
        # the deadline by at most that slack; the deadline is checked again after every statement.
        if self.timeout_set is None or self.timeout_set - limit > max(50, limit // 10):
            self.connection.execute("SELECT set_config('statement_timeout', %s, true)", (str(limit),))
            self.timeout_set = limit
        try:
            cursor = super()._execute(query, args, **kw)
        except Exception as exc:
            cause = exc.__cause__ or exc
            if isinstance(cause, psycopg.errors.QueryCanceled):
                raise _cancel(cause, self.deadline) from exc
            if isinstance(cause, psycopg.Error) and _BUDGET.search(str(cause)):
                raise InvestigationLimitError(str(cause)) from exc
            raise
        self.remaining()
        return cursor


def _rows(reader, query, args=()):
    cursor = reader._execute(query, args)
    names = [c.name for c in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def _segment(reader, collection, rid, start, end, segment_id):
    """The context segment row, checked before any search runs: a wrong or stale segment, a collection without
    context or an older server fails at once instead of after the search has used the deadline."""
    for signature in ("public.morlet_label_definitions(regclass)", "public.morlet_value_rules(regclass)"):
        if not reader._has(signature):
            raise UnsupportedError("investigation context needs pg_morlet SQL 0.3.9 or later")
    # Relation text is produced by regclass, including identifier quoting; never interpolate user names.
    table = reader._one("SELECT segments::text FROM public.morlet_context_relations(%s::regclass)",
                        (collection,))
    row = reader._execute(sql.SQL(
        "SELECT recording_id,start_sample,end_sample,context,occurrence,spec,stale FROM {} "
        "WHERE segment_id=%s").format(sql.SQL(table)), (segment_id,)).fetchone()
    if row is None:
        raise NotFoundError("context segment does not exist or is not visible")
    if row[0] != rid or row[1] > start or row[2] < end:
        raise InvalidInputError("context segment must contain the entire query interval in the same recording")
    if row[6]:
        raise InvalidInputError("context segment is stale; rebuild context segments")
    return row


def _context(reader, collection, rid, row, segment_id, settings):
    common = (collection, segment_id, list(settings["match_keys"]), Jsonb(settings["metadata_filter"]),
              list(settings["exclude_labels"]))
    comparisons = _rows(reader,
        "SELECT * FROM public.morlet_compare_to_history(%s::regclass,%s,%s,%s,%s,%s,%s,false)",
        common + (settings["min_history"], settings["max_history"]))
    neighbors = _rows(reader,
        "SELECT * FROM public.morlet_history_neighbors(%s::regclass,%s,%s,%s,%s,%s,%s,false)",
        common + (settings["top_k"], settings["max_history"]))
    shown = sorted({label["label"] for neighbor in neighbors for label in neighbor["labels"]})
    definitions = _rows(reader,
        "SELECT * FROM public.morlet_label_definitions(%s::regclass) WHERE label=ANY(%s::text[])",
        (collection, shown)) if shown else []
    return InvestigationContext(segment_id, IntervalRef(collection, rid, row[1], row[2]), row[3], row[4],
                                row[5], reader._one("SELECT public.morlet_value_rules(%s::regclass)",
                                                    (collection,)), comparisons, neighbors, definitions)


def investigate(client, collection, recording_id, start_sample, end_sample, **settings):
    end = start_sample + SEARCH_WINDOW if end_sample is None and isinstance(start_sample, int) else end_sample
    _integer("start_sample", start_sample, 0, 2**63 - 1)
    _integer("end_sample", end, start_sample + 1, 2**63 - 1)
    window = end - start_sample
    if window < 512 or window > 32768 or window & (window - 1):
        raise InvalidInputError("investigation window must be a power of two in 512..32768 samples")
    for key, high in (("top_k", 100), ("max_candidates", 1_000_000), ("max_history", 1_000_000),
                      ("min_history", 1_000_000), ("timeout_ms", 300_000), ("max_recordings", 100_000),
                      ("max_coverage_ranges", 1_000_000), ("max_output_bytes", 100_000_000)):
        _integer(key, settings[key], 1, high)
    lag = settings["max_lag_fraction"]
    if isinstance(lag, bool) or not isinstance(lag, (int, float)) or not math.isfinite(lag) or not 0 <= lag <= 0.5:
        raise InvalidInputError("max_lag_fraction must be finite and in [0,0.5]")
    if not isinstance(settings["coverage_ranges"], bool):
        raise InvalidInputError("coverage_ranges must be true or false")
    segment_id = settings.pop("segment_id")
    if segment_id is not None:
        _integer("segment_id", segment_id, 1, 2**63 - 1)
    for key in ("match_keys", "exclude_labels"):
        value = settings[key]
        if not isinstance(value, (tuple, list)) or any(not isinstance(k, str) for k in value):
            raise InvalidInputError(f"{key} must be a sequence of strings")
        settings[key] = list(value)
    if segment_id is None and (settings["match_keys"] or settings["exclude_labels"]):
        raise InvalidInputError("match_keys and exclude_labels require segment_id; they do not filter waveform search")
    if settings["metadata_filter"] is not None and not isinstance(settings["metadata_filter"], Mapping):
        raise InvalidInputError("metadata_filter must be a JSON object")
    try:
        settings["metadata_filter"] = dict(settings["metadata_filter"] or {})
        # Copy caller-owned nested input, and reject non-JSON values before beginning a transaction.
        settings = json.loads(json.dumps(settings, allow_nan=False))
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("investigation settings must be JSON-compatible") from exc
    settings.update(segment_id=segment_id, exclude_source=True, suppress_overlaps=True, before_only=False)
    deadline = time.monotonic() + settings["timeout_ms"] / 1000
    with read_snapshot(client.connection, deadline) as reader:
        result = _collect(reader, collection, recording_id, start_sample, end, settings)
        encoded = json.dumps(result.to_dict(), ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        if len(encoded.encode("utf-8")) > settings["max_output_bytes"]:
            raise InvestigationLimitError("investigation exceeds max_output_bytes; narrow scope or reduce top_k")
    return result


@contextlib.contextmanager
def read_snapshot(connection, deadline):
    """A _Reader in one read-only snapshot under one deadline: its own REPEATABLE READ READ ONLY transaction on an
    idle connection, or a savepoint in a compatible caller transaction, whose work and timeout are left as found."""
    status = connection.info.transaction_status
    if status not in (TransactionStatus.IDLE, TransactionStatus.INTRANS):
        raise InvalidInputError("investigation requires an idle connection or a healthy read-only snapshot")
    reader = _Reader(connection, deadline)
    try:
        # transaction() is a savepoint in an existing transaction, so errors cannot roll back caller work.
        with connection.transaction():
            if status == TransactionStatus.IDLE:
                connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            else:
                isolation, readonly = connection.execute(
                    "SELECT current_setting('transaction_isolation'), current_setting('transaction_read_only')"
                ).fetchone()
                if isolation not in ("repeatable read", "serializable") or readonly != "on":
                    raise InvalidInputError("existing transaction must be read-only and REPEATABLE READ or SERIALIZABLE; "
                                            "commit/rollback it yourself or use a separate connection")
            old_timeout, timeout_ms = connection.execute(
                "SELECT current_setting('statement_timeout'), setting::int FROM pg_settings "
                "WHERE name='statement_timeout'").fetchone()
            reader.statement_limit = timeout_ms
            yield reader
            reader.remaining()
            connection.execute("SELECT set_config('statement_timeout', %s, true)", (old_timeout,))
    except psycopg.errors.InsufficientPrivilege:
        raise
    except psycopg.errors.QueryCanceled as exc:
        raise _cancel(exc, deadline) from exc
    except psycopg.Error as exc:
        raise translate(exc) from exc


def _collect(reader, collection, rid, start, end, settings):
    recording = reader.recording(collection, rid)
    if end > recording.samples:
        # as read(), search() and stats() report it: not stored (yet)
        raise NotFoundError("query interval exceeds recording length")
    segment = (_segment(reader, collection, rid, start, end, settings["segment_id"])
               if settings["segment_id"] is not None else None)
    provenance = _rows(reader,
        "SELECT current_database() AS database, current_user AS role, "
        "current_setting('transaction_isolation') AS isolation, transaction_timestamp() AS transaction_started_at, "
        "pg_current_snapshot()::text AS snapshot, "
        "(SELECT extversion FROM pg_extension WHERE extname='pg_morlet') AS extension_version")[0]
    provenance["envelope_representation"] = ENVELOPE_REPRESENTATION   # defined by that extension version
    provenance.update(_rows(reader,
        "SELECT c.oid::bigint AS collection_oid, n.nspname AS collection_schema, c.relname AS collection_name "
        "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE c.oid=%s::regclass", (collection,))[0])
    # Resolve names server-side rather than deriving companion names from caller spelling.
    rel = reader._one("SELECT %s::regclass::text", (collection,))
    ids = [r[0] for r in reader._execute(sql.SQL(
        "SELECT recording_id FROM {} WHERE sample_rate_hz=%s AND channel=%s AND unit=%s AND metadata @> %s "
        "ORDER BY recording_id LIMIT %s").format(sql.SQL(rel)),
        (recording.sample_rate_hz, recording.channel, recording.unit, Jsonb(settings["metadata_filter"]),
         settings["max_recordings"] + 1)).fetchall()]
    if len(ids) > settings["max_recordings"]:
        raise InvestigationLimitError("investigation exceeds max_recordings; narrow metadata_filter")
    provenance["compatible_recordings"] = len(ids)
    # max_coverage_ranges bounds the ranges returned, not the server's work: morlet_coverage computes every
    # range of the listed recordings before the LIMIT (max_recordings bounds those).
    coverage = []
    coverage_status = "unknown"
    if reader._has("public.morlet_coverage(regclass,text[],integer)"):
        coverage = [CoverageRange(*r) for r in reader._execute(
            "SELECT recording_id,start_sample,end_sample,state,windows FROM public.morlet_coverage("
            "%s::regclass,%s::text[],%s) ORDER BY recording_id,start_sample LIMIT %s",
            (collection, ids, end - start, settings["max_coverage_ranges"] + 1)).fetchall()] if ids else []
        if len(coverage) > settings["max_coverage_ranges"]:
            raise InvestigationLimitError("investigation exceeds max_coverage_ranges; narrow metadata_filter")
        coverage_status = "available"
    summary = {"samples_by_state": {}, "range_count": len(coverage), "ranges_included": settings["coverage_ranges"]}
    for r in coverage:
        summary["samples_by_state"][r.state] = summary["samples_by_state"].get(r.state, 0) + r.end_sample - r.start_sample
    query_stats = reader.stats(collection, rid, start, end)
    search = reader.search(collection, rid, start, end, metadata_filter=settings["metadata_filter"],
                           top_k=settings["top_k"], max_candidates=settings["max_candidates"],
                           max_lag_fraction=settings["max_lag_fraction"])
    context = (_context(reader, collection, rid, segment, settings["segment_id"], settings)
               if segment is not None else None)
    if context is not None:
        provenance["context_representation"] = context.value_rules   # read from the server
    # Machine-readable counterparts of the notes, for agents and reports (#577 review): one rule set, here.
    states = ["no_temporal_cutoff"]
    notes = ["Coverage describes compatible recordings before source exclusion; it is not a count of scored candidates.",
             "Search considers stored windows on a grid, not every sample offset. A missing match is not proof of absence.",
             "No temporal cutoff: history may include later occurrences and labels added after the event."]
    if not search.matches:
        states.append("no_matches_returned")
    if search.mode == "probes":
        states.append("approximate_candidates_possible")
        notes.append("Probe search is enabled: candidate selection can be approximate; small galleries may route to exact. "
                     "Recording coverage does not establish probe-index completeness or recall.")
    if coverage_status == "unknown":
        states.append("coverage_unknown")
        notes.append("Server cannot report recording coverage; unsearched periods are unknown.")
    elif any(r.state != "searchable" for r in coverage):
        states.append("unsearched_ranges")
        notes.append("Some compatible recording ranges are not directly searchable; inspect coverage states.")
    if context is None:
        states.append("context_not_requested")
        notes.append("Context comparison was not requested (supply segment_id).")
    else:
        states.append("labelled_history_not_exhaustively_searched")
        notes.append("Context neighbors use simple-value distance, not envelope distance. Their labels describe those "
                     "occurrences, not the query; this is not an exhaustive search of labelled cases.")
        if any(r["hist_n"] is None or r["hist_n"] < settings["min_history"] for r in context.comparisons):
            states.append("insufficient_history")
            notes.append("Insufficient comparable history for one or more context values.")
        defined = {d["label"] for d in context.label_definitions}
        undefined = sorted({l["label"] for n in context.neighbors for l in n["labels"]} - defined)
        if undefined:
            states.append("undefined_labels")
            notes.append("Labels without glossary definitions: " + ", ".join(undefined))
    return InvestigationResult(1, recording, search, query_stats, context, coverage_status,
                               coverage if settings["coverage_ranges"] else [], settings, provenance, notes,
                               states, summary)
