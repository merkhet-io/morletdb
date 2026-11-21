"""From an asset and an incident time to recording intervals (#580): existing metadata and time mapping only.

Nothing is guessed: every way the mapping can be uncertain is a candidate field or an entry in `states`, recordings are
never concatenated or resampled, and a recording the caller cannot see is indistinguishable from one that does not exist.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from fractions import Fraction
import json
import math
import time

from psycopg import sql
from psycopg.types.json import Jsonb

from ._client import _start_ns
from ._errors import InvalidInputError, InvestigationLimitError, UnsupportedError
from ._investigation import read_snapshot
from ._types import IncidentCandidate, IncidentResolution, IntervalRef

MAX_WINDOW_S = 31 * 86400
MIN_SQL = (0, 3, 15)      # morlet_time_pieces fails before it on collections without a time-anchor table (#562)


def _key(name, value):
    if not isinstance(value, str) or not value:
        raise InvalidInputError(f"{name} must be a non-empty string")
    return value


def _seconds_ns(name, value):
    """Whole nanoseconds, rounded up, so the requested window is never narrower than asked. From the shortest decimal
    of the number, as written: the binary value of 0.05 is a little above it and would add a nanosecond, and with it
    a sample whose time is exactly on the window's end."""
    # the range test also refuses NaN and infinities, and integers too large for a float
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= MAX_WINDOW_S:
        raise InvalidInputError(f"{name} must be a finite number of seconds in 0..{MAX_WINDOW_S}")
    exact = Fraction(value) if isinstance(value, int) else Fraction(repr(float(value)))
    return math.ceil(exact * 1_000_000_000)


def _incident_ns(value):
    if value is None or isinstance(value, bool):
        raise InvalidInputError("incident_time must be a timezone-aware datetime or integer UTC nanoseconds")
    if isinstance(value, datetime) and (value.tzinfo is None or value.utcoffset() is None):
        raise InvalidInputError("incident_time must be timezone-aware (naive datetimes are ambiguous)")
    return _start_ns(value)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _references(claimed, keys, metadata):
    """Each claimed reference against what the recording stores: a join is verified only by an equal stored value."""
    out = {}
    for name, value in claimed.items():
        recorded = metadata.get(keys[name])
        # the stored JSON value itself: the number 42 is not the string "42", and a stored null is not an absent key;
        # JSON has one number type, so 42 and 42.0 are the same value
        same = recorded == value if _number(recorded) and _number(value) else \
            json.dumps(recorded, sort_keys=True) == json.dumps(value, sort_keys=True)
        status = "not_recorded" if keys[name] not in metadata else "verified" if same else "different"
        out[name] = {"claimed": value, "metadata_key": keys[name], "recorded": recorded, "status": status}
    return out


def _covers(reader, collection, rid, axis, rate, a, b, start, end):
    """Whether [a, b) covers the window on the recording's own time axis. By the mapped times, not the nominal sample
    count: a drifting clock has more or fewer samples per second than declared. The first and last samples must lie
    within one local sample period of the window's ends (the start is inclusive: a first sample a whole period after it
    leaves a sample position uncovered), and no clock break inside may skip more than half a period."""
    nominal = Fraction(1_000_000_000) / rate
    points = sorted({a, min(a + 1, b - 1), max(b - 2, a), b - 1})
    t = {int(n): Fraction(u) for n, u in reader._execute(
        "SELECT p.n, s.utc_ns FROM unnest(%s::bigint[]) p(n), LATERAL public.morlet_sample_utc(%s::regclass,%s,p.n) s",
        (points, collection, rid)).fetchall()}
    first = max(nominal, t[min(a + 1, b - 1)] - t[a])
    last = max(nominal, t[b - 1] - t[max(b - 2, a)])
    if t[a] - start >= first or end - t[b - 1] > last:
        return False
    if not axis.startswith("anchors"):
        return True
    # one scan of the pieces: a lookup per break would rebuild them all each time
    return not reader._execute(
        "SELECT EXISTS(SELECT 1 FROM (SELECT segment, lo, t_ref+(lo-n_ref)*dt/dn AS t, lag(segment) OVER w AS s0, "
        "lag(t_ref) OVER w AS t0, lag(n_ref) OVER w AS n0, lag(dt) OVER w AS dt0, lag(dn) OVER w AS dn0 "
        "FROM public.morlet_time_pieces(%s::regclass,%s) WINDOW w AS (ORDER BY lo)) x "
        "WHERE x.segment<>x.s0 AND x.lo>%s AND x.lo<%s AND x.t-(x.t0+(x.lo-1-x.n0)*x.dt0/x.dn0) > %s::numeric)",
        (collection, rid, a, b, str(float(nominal * 3 / 2)))).fetchone()[0]


def _clipped(reader, collection, rid, r, a, b):
    """Extrapolation and end uncertainties of the stored part [a, b) of a range that reaches past the stored samples."""
    if (a, b) == (r.start_sample, r.end_sample):
        return r.extrapolated, r.start_uncertainty_ns, r.end_uncertainty_ns
    extrapolated = reader._execute(
        "SELECT coalesce(bool_or(kind IN ('before','after') AND NOT (greatest(lo,%s)=n_ref AND least(coalesce(hi,%s),%s)-1=n_ref)),"
        "false) FROM public.morlet_time_pieces(%s::regclass,%s) WHERE lo<%s AND (hi IS NULL OR hi>%s)",
        (a, b, b, collection, rid, b, a)).fetchone()[0]
    bounds = [reader._one("SELECT uncertainty_ns FROM public.morlet_sample_utc(%s::regclass,%s,%s)", (collection, rid, n))
              for n in (a, b - 1)]
    return bool(extrapolated), *(None if u is None else math.ceil(u) for u in bounds)


def _raw_samples(reader, collection, rid, a, b):
    """Samples of [a, b) whose raw chunks are stored. Not from the search states: a retained envelope window keeps a
    range searchable after a memory level has removed part of its chunks."""
    chunks, count = reader._execute("SELECT r.chunks::text, r.count_expr FROM public.morlet_stream_relations(%s::regclass) r",
                                    (collection,)).fetchone()
    return int(reader._execute(sql.SQL(
        "SELECT coalesce(sum(least(c.start_sample+{1},%s)-greatest(c.start_sample,%s)),0) FROM {0} c "
        "WHERE c.recording_id=%s AND c.start_sample<%s AND c.start_sample+{1}>%s").format(sql.SQL(chunks), sql.SQL(count)),
        (b, a, rid, b, a)).fetchone()[0])


def resolve_incident(client, collection, asset, incident_time, *, before_s, after_s, sensor, metadata_filter, asset_key,
                     sensor_key, identity_keys, references, reference_keys, timeout_ms, max_recordings):
    _key("asset", asset)
    _key("asset_key", asset_key)
    if sensor is not None:
        _key("sensor", sensor)
    if sensor_key is not None:
        _key("sensor_key", sensor_key)
    t = _incident_ns(incident_time)
    before, after = _seconds_ns("before_s", before_s), _seconds_ns("after_s", after_s)
    if before + after == 0:
        raise InvalidInputError("before_s and after_s must not both be zero")
    for name, value, high in (("timeout_ms", timeout_ms, 300_000), ("max_recordings", max_recordings, 100_000)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= high:
            raise InvalidInputError(f"{name} must be an integer in 1..{high}")
    if not isinstance(identity_keys, (tuple, list)) or any(not isinstance(k, str) or not k for k in identity_keys):
        raise InvalidInputError("identity_keys must be a sequence of non-empty strings")
    if not isinstance(reference_keys, Mapping) or any(not isinstance(k, str) or not isinstance(v, str) or not k or not v
                                                      for k, v in reference_keys.items()):
        raise InvalidInputError("reference_keys must map non-empty strings to non-empty strings")
    if not isinstance(references, Mapping) or any(
            not isinstance(k, str) or not k or v == "" or not isinstance(v, (str, int, float, bool))
            or (isinstance(v, float) and not math.isfinite(v)) for k, v in references.items()):
        raise InvalidInputError("references must map non-empty strings to JSON scalars")
    unmapped = sorted(set(references) - set(reference_keys))
    if unmapped:
        raise InvalidInputError("references without a metadata key in reference_keys: " + ", ".join(unmapped))
    if metadata_filter is not None and not isinstance(metadata_filter, Mapping):
        raise InvalidInputError("metadata_filter must be a JSON object")
    flt = dict(metadata_filter or {})
    for key, value in ((asset_key, asset), (sensor_key, sensor if sensor_key else None)):
        if value is None:
            continue
        if key in flt and flt[key] != value:
            raise InvalidInputError(f"metadata_filter sets {key} to another value")
        flt[key] = value
    try:
        json.dumps(flt, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise InvalidInputError("metadata_filter must be JSON-compatible") from exc
    start, end = t - before, t + after
    mapping = {"asset_key": asset_key, "sensor_key": sensor_key, "identity_keys": list(identity_keys),
               "reference_keys": dict(reference_keys), "metadata_filter": flt,
               "boundary": "samples whose UTC time t satisfies window_start_ns <= t < window_end_ns"}
    with read_snapshot(client.connection, time.monotonic() + timeout_ms / 1000) as reader:
        if not reader._has("public.morlet_utc_to_samples(regclass,text,numeric,numeric,double precision)"):
            raise UnsupportedError("resolving an incident needs pg_morlet SQL 0.3.15 or later")
        version = reader._one("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'")
        if tuple(int(p) for p in version.split("-")[0].split(".")) < MIN_SQL:
            raise UnsupportedError(f"resolving an incident needs pg_morlet SQL 0.3.15 or later (this server: {version})")
        rel = reader._one("SELECT %s::regclass::text", (collection,))
        query = "SELECT recording_id FROM {} WHERE metadata @> %s"
        args = [Jsonb(flt)]
        if sensor is not None and sensor_key is None:
            query += " AND channel = %s"
            args.append(sensor)
        ids = [r[0] for r in reader._execute(sql.SQL(query + " ORDER BY recording_id LIMIT %s").format(sql.SQL(rel)),
                                             (*args, max_recordings + 1)).fetchall()]
        if len(ids) > max_recordings:
            raise InvestigationLimitError("incident matches more than max_recordings recordings; narrow metadata_filter")
        candidates, no_time, outside, identities = [], [], [], set()
        for rid in ids:
            rec = reader.recording(collection, rid)
            identities.add(tuple(json.dumps(rec.metadata.get(k), sort_keys=True) for k in identity_keys))
            axis = reader._one("SELECT public.morlet_time_axis_version(%s::regclass,%s)", (collection, rid))
            # the server's reading of the start time, since the server does the mapping
            server_start = reader._one("SELECT public.morlet_recording_start_ns(%s)", (Jsonb(rec.metadata),))
            if server_start is None and not axis.startswith("anchors"):
                no_time.append(rid)
                continue
            if rec.start_ns is None and server_start is not None:
                rec = replace(rec, start_ns=math.floor(server_start))
            found = []
            for r in reader.time_ranges(rec, start, end):                # offsets on the time axis, not clipped
                a, b = max(r.start_sample, 0), min(r.end_sample, rec.samples)
                if a < b:
                    found.append((r, a, b))
            if not found:
                outside.append(rid)
                continue
            rate = Fraction(repr(float(rec.sample_rate_hz)))
            expected = math.ceil(Fraction(end - start, 1_000_000_000) * rate)
            for r, a, b in found:
                states = {s: int(n) for s, n in reader._execute(
                    "SELECT state, sum(least(end_sample,%s)-greatest(start_sample,%s)) "
                    "FROM public.morlet_coverage(%s::regclass,ARRAY[%s]::text[],NULL) "
                    "WHERE start_sample<%s AND end_sample>%s GROUP BY state", (b, a, collection, rid, b, a)).fetchall()}
                extrapolated, start_u, end_u = _clipped(reader, collection, rid, r, a, b)
                candidates.append(IncidentCandidate(
                    IntervalRef(collection, rid, a, b), rec.sample_rate_hz, rec.channel, rec.unit,
                    rec.metadata.get(sensor_key) if sensor_key else rec.channel, r.basis, r.ambiguous, extrapolated,
                    start_u, end_u, r.assumes_linear_clock, axis,
                    expected, b - a, _covers(reader, collection, rid, axis, rate, a, b, start, end), states,
                    b - a - _raw_samples(reader, collection, rid, a, b),
                    _references(references, reference_keys, rec.metadata)))
        result_states = _states(ids, no_time, candidates, identities, identity_keys, sensor_key)
        provenance = reader._execute(
            "SELECT current_database(), current_user, transaction_timestamp(), "
            "(SELECT extversion FROM pg_extension WHERE extname='pg_morlet')").fetchone()
    return IncidentResolution(1, asset, t, start, end, mapping, candidates, no_time, outside,
                              [dict(zip(identity_keys, (json.loads(v) for v in i))) for i in sorted(identities)]
                              if identity_keys else [], result_states,
                              dict(zip(("database", "role", "transaction_started_at", "extension_version"), provenance)))


def _states(ids, no_time, candidates, identities, identity_keys, sensor_key=None):
    """Machine-readable: what an agent must not gloss over before it treats a candidate as the incident's interval."""
    states = []
    if not ids:
        return ["asset_not_found"]
    # a configured key without a value on some recording does not scope that recording
    if not identity_keys or any("null" in identity for identity in identities):
        states.append("asset_identity_not_scoped")
    if len(identities) > 1:
        states.append("ambiguous_asset")
    if no_time:
        states.append("recordings_without_time_axis")
    if not candidates:
        states.append("no_recording_at_time")
    by_sensor = {}
    for c in candidates:
        # the sensor is any stored JSON value, lists and objects included; without one, recordings are not the same sensor
        unknown = sensor_key is not None and c.sensor is None
        key = ["unknown", c.ref.recording_id] if unknown else c.sensor
        by_sensor.setdefault(json.dumps(key, sort_keys=True), set()).add(c.ref.recording_id)
    if sensor_key is not None and any(c.sensor is None for c in candidates):
        states.append("sensor_not_recorded")
    if len(by_sensor) > 1:
        states.append("multiple_sensors")
    if any(len(r) > 1 for r in by_sensor.values()):
        states.append("multiple_recordings_per_sensor")
    for state, test in (("partial_window", lambda c: not c.covers_window),
                        ("unreadable_samples", lambda c: c.unreadable_samples > 0),
                        ("ambiguous_time", lambda c: c.ambiguous),
                        ("extrapolated_time", lambda c: c.extrapolated),
                        ("time_uncertainty_unknown", lambda c: c.start_uncertainty_ns is None),
                        ("assumes_linear_clock", lambda c: c.assumes_linear_clock),
                        ("reference_different", lambda c: any(r["status"] == "different" for r in c.references.values())),
                        ("reference_not_recorded", lambda c: any(r["status"] == "not_recorded"
                                                                 for r in c.references.values()))):
        if any(test(c) for c in candidates):
            states.append(state)
    return states
