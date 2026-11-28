"""Read one measurement from a running InfluxDB 1.x or 2.x over HTTP, exactly, into a line-protocol file.

For trying MorletDB without an export (no shell on the device, no access to InfluxDB's data directory). Read-only:
1.x needs a user with READ on the database, 2.x a read token. Credentials come from the environment only, never from
the command line: INFLUX_USERNAME and INFLUX_PASSWORD (1.x), INFLUX_TOKEN (2.x).

Gentle on the source: the time window is read in slices (default 60 s) with a pause between them (default 0.5 s), and
reading stops at a point limit. Values arrive exactly: floats as InfluxDB formats them (shortest round-trip form) and
parsed with correct rounding, typed by the field's InfluxDB type (1.x: SHOW FIELD KEYS; 2.x: the CSV's datatype
annotation), so `-0` stays a negative zero and an integer-valued float stays a float. Standard library only.
"""
import csv
import io
import json
import math
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from influx_lp import PRECISION_NS  # noqa: F401  (same package; keeps the reader and writer together)

SKIP_V2 = {"", "result", "table", "_start", "_stop", "_time", "_value", "_field", "_measurement"}


class SourceError(RuntimeError):
    pass


# ---- line protocol writing (the inverse of influx_lp)
def _esc(s, chars):
    s = s.replace("\\", "\\\\")
    for c in chars:
        s = s.replace(c, "\\" + c)
    return s


def lp_value(kind, v):
    if kind == "float":
        f = float(v)
        if not math.isfinite(f):
            raise SourceError(f"non-finite float {v!r} cannot be written as line protocol")
        return repr(f)
    if kind == "integer":
        return f"{int(v)}i"
    if kind == "unsigned":
        return f"{int(v)}u"
    if kind == "boolean":
        return "true" if v else "false"
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def lp_line(measurement, tags, fields, t_ns):
    """fields: [(key, kind, value)]."""
    head = _esc(measurement, ", ") + "".join(f",{_esc(k, ',= ')}={_esc(v, ',= ')}" for k, v in sorted(tags.items()) if v != "")
    body = ",".join(f"{_esc(k, ',= ')}={lp_value(kind, v)}" for k, kind, v in fields)
    return f"{head} {body} {t_ns}"


# ---- HTTP
def _request(url, data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as exc:
        body = exc.read()[:300].decode("utf-8", "replace")
        raise SourceError(f"InfluxDB answered {exc.code}: {body}") from None
    except urllib.error.URLError as exc:
        raise SourceError(f"cannot reach InfluxDB at {url.split('?')[0]}: {exc.reason}") from None


def version(base):
    _, headers, _ = _request(base.rstrip("/") + "/ping")
    v = {k.lower(): x for k, x in headers.items()}.get("x-influxdb-version", "")
    if not v:
        raise SourceError("no X-Influxdb-Version header from /ping: is this InfluxDB?")
    return v.lstrip("v")


def _v1_auth():
    user, pw = os.environ.get("INFLUX_USERNAME"), os.environ.get("INFLUX_PASSWORD")
    if not user:
        return {}
    import base64
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pw or ''}".encode()).decode()}


def _v2_auth():
    token = os.environ.get("INFLUX_TOKEN")
    if not token:
        raise SourceError("InfluxDB 2.x needs a read token in INFLUX_TOKEN")
    return {"Authorization": f"Token {token}"}


def _ident(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _flux_string(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"').replace("${", "\\${") + '"'


def _rfc3339(ns):
    sec, frac = divmod(ns, 1_000_000_000)
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec)) + f".{frac:09d}Z"


def _v1_json(raw):
    # "-0" must stay a negative zero: json gives ints by default; field types are applied afterwards
    return json.loads(raw, parse_int=lambda s: -0.0 if s == "-0" else int(s))


def _v1_query(base, db, q):
    url = base.rstrip("/") + "/query?" + urllib.parse.urlencode({"db": db, "epoch": "ns", "q": q})
    _, _, raw = _request(url, headers=_v1_auth())
    out = _v1_json(raw)
    for res in out.get("results", []):
        if "error" in res:
            raise SourceError(f"InfluxDB: {res['error']}")
    return out


V1_KINDS = {"float": "float", "integer": "integer", "unsigned": "unsigned", "boolean": "boolean", "string": "string"}


def _v1_slices(base, db, measurement, start_ns, end_ns, slice_ns):
    keys = _v1_query(base, db, f"SHOW FIELD KEYS FROM {_ident(measurement)}")
    kinds = {}
    for res in keys["results"]:
        for s in res.get("series", []):
            for k, t in s["values"]:
                kinds[k] = V1_KINDS[t]
    if not kinds:
        raise SourceError(f"measurement {measurement!r} not found in database {db!r} (or no fields readable)")
    a = start_ns
    while a < end_ns:
        b = min(end_ns, a + slice_ns)
        q = f"SELECT * FROM {_ident(measurement)} WHERE time >= {a} AND time < {b} GROUP BY *"
        lines = []
        for res in _v1_query(base, db, q)["results"]:
            for s in res.get("series", []):
                cols = s["columns"]
                for row in s["values"]:
                    fields = [(c, kinds[c], v) for c, v in zip(cols[1:], row[1:]) if v is not None and c in kinds]
                    if fields:
                        lines.append(lp_line(s["name"], s.get("tags", {}), fields, int(row[0])))
        yield lines
        a = b


V2_KINDS = {"double": "float", "long": "integer", "unsignedLong": "unsigned", "boolean": "boolean", "string": "string"}


def _v2_value(kind, text):
    if kind == "float":
        return float(text)
    if kind in ("integer", "unsigned"):
        return int(text)
    if kind == "boolean":
        return text == "true"
    return text


def _v2_slices(base, org, bucket, measurement, start_ns, end_ns, slice_ns, iso_ns):
    url = base.rstrip("/") + "/api/v2/query?" + urllib.parse.urlencode({"org": org})
    headers = {**_v2_auth(), "Accept": "application/csv", "Content-Type": "application/json"}
    a = start_ns
    while a < end_ns:
        b = min(end_ns, a + slice_ns)
        flux = (f"from(bucket: {_flux_string(bucket)}) |> range(start: {_rfc3339(a)}, stop: {_rfc3339(b)}) "
                f"|> filter(fn: (r) => r._measurement == {_flux_string(measurement)})")
        body = json.dumps({"query": flux, "dialect": {"annotations": ["datatype"], "header": True}}).encode()
        _, _, raw = _request(url, data=body, headers=headers)
        lines, types, header = [], None, None
        for row in csv.reader(io.StringIO(raw.decode("utf-8"))):
            if not row or not any(row):
                types = header = None                    # a blank line ends a table
                continue
            if row[0] == "#datatype":
                types, header = row, None
                continue
            if row[0].startswith("#"):
                continue
            if header is None:
                header = row
                continue
            r = dict(zip(header, row))
            if r.get("result") == "error" or "error" in header:
                raise SourceError(f"InfluxDB: {r.get('error', row)}")
            kind = V2_KINDS.get(types[header.index("_value")]) if types else None
            if kind is None:
                raise SourceError(f"unsupported value type {types and types[header.index('_value')]!r}")
            tags = {k: v for k, v in r.items() if k not in SKIP_V2}
            lines.append(lp_line(r["_measurement"], tags, [(r["_field"], kind, _v2_value(kind, r["_value"]))],
                                 iso_ns(r["_time"])[0]))
        yield lines
        a = b


def fetch(base, out_path, measurement, start_ns, end_ns, *, db=None, org=None, bucket=None, slice_s=60.0,
          pause_s=0.5, max_points=10_000_000, iso_ns=None, log=None):
    """Write the measurement's points in [start_ns, end_ns) to out_path as line protocol. Returns provenance (no
    credentials). 2.x writes one line per field; the importer joins fields of one series by time either way."""
    if end_ns <= start_ns:
        raise SourceError("--end must be after --start")
    if not (slice_s > 0 and pause_s >= 0):
        raise SourceError("slices must be positive and pauses zero or more")
    ver = version(base)
    major = ver.split(".")[0]
    slice_ns = int(slice_s * 1e9)
    if major == "1":
        if not db:
            raise SourceError("InfluxDB 1.x: give --influx-db")
        slices = _v1_slices(base, db, measurement, start_ns, end_ns, slice_ns)
    elif major == "2":
        if not (org and bucket):
            raise SourceError("InfluxDB 2.x: give --influx-org and --influx-bucket")
        slices = _v2_slices(base, org, bucket, measurement, start_ns, end_ns, slice_ns, iso_ns)
    else:
        raise SourceError(f"InfluxDB {ver} is not supported here (1.x and 2.x are); export line protocol instead")
    points, n = 0, 0
    with open(out_path, "w", encoding="utf-8") as f:
        for lines in slices:
            n += 1
            points += len(lines)
            if points > max_points:
                raise SourceError(f"more than {max_points} points; shorten the window or raise --max-points")
            f.write("".join(x + "\n" for x in lines))
            if log:
                log(f"slice {n}: {len(lines)} lines")
            time.sleep(pause_s)
    return {"influxdb_version": ver, "url": urllib.parse.urlsplit(base)._replace(netloc=urllib.parse.urlsplit(base).hostname
            + (f":{urllib.parse.urlsplit(base).port}" if urllib.parse.urlsplit(base).port else "")).geturl(),
            "database": db, "org": org, "bucket": bucket, "measurement": measurement, "start": _rfc3339(start_ns),
            "end": _rfc3339(end_ns), "slices": n, "lines": points, "slice_s": slice_s, "pause_s": pause_s}
