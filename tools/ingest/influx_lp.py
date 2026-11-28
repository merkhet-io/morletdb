"""InfluxDB line protocol, read exactly (for importing InfluxDB exports, `influx_inspect export` / `influxd inspect
export-lp`). Standard library only.

Syntax: https://docs.influxdata.com/influxdb/v2/reference/syntax/line-protocol/
  measurement[,tag=value...] field=value[,field=value...] [timestamp]

A series is (measurement, tag set, field key). Values keep their type: float (`1.5`, `1e3`), integer (`3i`), unsigned
(`3u`), boolean and string. Each float is parsed from its text with correct rounding, the same float64 InfluxDB
stores; the text is kept so the importer can find the decimal grid. A line without a timestamp is refused: its time
was the server's clock, which the file does not contain.
"""
from dataclasses import dataclass, field

PRECISION_NS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}
BOOLEANS = {"t": True, "T": True, "true": True, "True": True, "TRUE": True,
            "f": False, "F": False, "false": False, "False": False, "FALSE": False}


class LineProtocolError(ValueError):
    pass


@dataclass
class Series:
    measurement: str
    tags: tuple                      # sorted ((key, value), ...)
    field: str
    kind: str                        # "float", "integer", "unsigned", "boolean", "string"
    times: list = field(default_factory=list)       # ns
    values: list = field(default_factory=list)      # float/int/bool/str
    texts: list = field(default_factory=list)       # numbers as written (float/integer/unsigned)

    @property
    def key(self):
        return (self.measurement, self.tags, self.field)


def _split(s, seps, start=0, quotes=False):
    """Split s at unescaped characters of `seps` from `start`; with quotes, separators inside "..." do not split.
    Returns (parts, end) where end is the index of the first unescaped space outside quotes (or len(s))."""
    parts, cur, i, inq = [], [], start, False
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            cur.append(s[i:i + 2])
            i += 2
            continue
        if quotes and c == '"':
            inq = not inq
        elif not inq and c == " ":
            break
        elif not inq and c in seps:
            parts.append("".join(cur))
            cur = []
            i += 1
            continue
        cur.append(c)
        i += 1
    if inq:
        raise LineProtocolError("unterminated string field")
    parts.append("".join(cur))
    return parts, i


def _unescape(s, chars):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in chars:
            out.append(s[i + 1])
            i += 2
        else:
            out.append(s[i])
            i += 1
    return "".join(out)


def _value(text):
    if text.startswith('"'):
        if len(text) < 2 or not text.endswith('"'):
            raise LineProtocolError(f"bad string field {text!r}")
        return "string", _unescape(text[1:-1], '"\\'), None
    if text in BOOLEANS:
        return "boolean", BOOLEANS[text], None
    if text.endswith("i") or text.endswith("u"):
        body, kind = text[:-1], "integer" if text.endswith("i") else "unsigned"
        if not body or not body.lstrip("-").isdigit() or (kind == "unsigned" and body.startswith("-")):
            raise LineProtocolError(f"bad {kind} field {text!r}")
        return kind, int(body), body
    try:
        v = float(text)
    except ValueError:
        raise LineProtocolError(f"bad field value {text!r}") from None
    if v != v or v in (float("inf"), float("-inf")) or text.lower() in ("nan", "inf", "-inf", "+inf", "infinity"):
        raise LineProtocolError(f"line protocol has no {text!r}")
    return "float", v, text


def parse_line(line, precision="ns"):
    """[(measurement, tags, field, kind, value, text, time_ns)] for one line; [] for a blank line or a comment."""
    s = line.rstrip("\r\n")
    if not s.strip() or s.lstrip().startswith("#"):
        return []
    head, i = _split(s, ",")
    measurement = _unescape(head[0], ", \\")
    if not measurement:
        raise LineProtocolError("missing measurement")
    tags = []
    for t in head[1:]:
        kv, _ = _split(t, "=")
        if len(kv) != 2 or not kv[0] or not kv[1]:
            raise LineProtocolError(f"bad tag {t!r}")
        tags.append((_unescape(kv[0], ",= \\"), _unescape(kv[1], ",= \\")))
    if i >= len(s):
        raise LineProtocolError("missing fields")
    fields, j = _split(s, ",", i + 1, quotes=True)
    parsed = []
    for f in fields:
        k = f.find("=")
        while k > 0 and f[k - 1] == "\\":
            k = f.find("=", k + 1)
        if k <= 0:
            raise LineProtocolError(f"bad field {f!r}")
        parsed.append((_unescape(f[:k], ",= \\"), *_value(f[k + 1:])))
    rest = s[j:].strip()
    if not rest:
        raise LineProtocolError("missing timestamp: the point's time was the server's clock, which is not in the file")
    try:
        ts = int(rest)
    except ValueError:
        raise LineProtocolError(f"bad timestamp {rest!r}") from None
    t_ns = ts * PRECISION_NS[precision]
    return [(measurement, tuple(sorted(tags)), fkey, kind, value, text, t_ns) for fkey, kind, value, text in parsed]


def read_series(lines, precision="ns", max_points=None):
    """{key: Series} from line-protocol lines, in file order. A field key whose type changes within a series is an
    error (InfluxDB refuses it as well). max_points bounds memory."""
    if precision not in PRECISION_NS:
        raise LineProtocolError(f"precision must be one of {', '.join(PRECISION_NS)}")
    series, points = {}, 0
    for n, line in enumerate(lines, 1):
        try:
            parsed = parse_line(line, precision)
        except LineProtocolError as exc:
            raise LineProtocolError(f"line {n}: {exc}") from None
        for measurement, tags, fkey, kind, value, text, t in parsed:
            key = (measurement, tags, fkey)
            s = series.get(key)
            if s is None:
                s = series[key] = Series(measurement, tags, fkey, kind)
            elif s.kind != kind:
                raise LineProtocolError(f"line {n}: field {fkey!r} of {measurement} changes type {s.kind} -> {kind}")
            s.times.append(t)
            s.values.append(value)
            s.texts.append(text)
            points += 1
            if max_points and points > max_points:
                raise LineProtocolError(f"more than {max_points} points; raise --max-points or split the export")
    return series
