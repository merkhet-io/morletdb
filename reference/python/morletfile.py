#!/usr/bin/env python3
"""Reference reader for MorletDB files (file format 1, docs/file-format.md), written from the specification alone.

Python standard library only (sqlite3, struct): no MorletDB code, no numpy. It is slow by design (a few hundred
thousand samples per second) and exists to show that the format can be read without MorletDB and to check other
implementations. Usage:

    python morletfile.py FILE                       list the recordings
    python morletfile.py FILE RECORDING START END   print the samples of [START, END), one per line (repr)

As a module: recordings(path), read(path, recording_id, start, end), decode_chunk(encoded).
Apache-2.0.
"""
import json
import sqlite3
import struct
import sys
from contextlib import closing

APPLICATION_ID = 0x4D524C54          # "MRLT"
FORMAT_VERSION = 1
MAX_SAMPLES = 32768
MAX_AMPLITUDE = 1e9


class FormatError(ValueError):
    pass


# --- CRC-32C (Castagnoli), reflected polynomial 0x82F63B78, initial value and final xor 0xFFFFFFFF ------------
def _crc_table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0x82F63B78 if c & 1 else c >> 1
        t.append(c)
    return t


_CRC = _crc_table()


def crc32c(data):
    c = 0xFFFFFFFF
    for b in data:
        c = (c >> 8) ^ _CRC[(c ^ b) & 0xFF]
    return c ^ 0xFFFFFFFF


# --- integer mappings ----------------------------------------------------------------------------------------
def _wrap64(v):
    """Two's-complement wrap to a signed 64-bit integer (the codec's integer arithmetic wraps)."""
    v &= (1 << 64) - 1
    return v - (1 << 64) if v >> 63 else v


def unzigzag(u):
    return (u >> 1) ^ -(u & 1)


def float_from_ordered(o):
    """Inverse of the order-preserving map: o >= 0 is the bit pattern itself; o < 0 is -(o + 1) with the sign set."""
    bits = o if o >= 0 else (-(o + 1)) | (1 << 63)
    return struct.unpack("<d", struct.pack("<Q", bits))[0]


def ordered_bits(x):
    bits = struct.unpack("<Q", struct.pack("<d", x))[0]
    return -(bits & ~(1 << 63)) - 1 if bits >> 63 else bits


def varint(b, pos):
    v, shift = 0, 0
    while True:
        if pos >= len(b) or shift > 63:
            raise FormatError("truncated or overlong varint")
        byte = b[pos]
        pos += 1
        v |= (byte & 0x7F) << shift
        if byte < 0x80:
            return v, pos
        shift += 7


# --- bit stream (MSB first) ----------------------------------------------------------------------------------
class Bits:
    def __init__(self, data):
        self.data, self.pos = data, 0

    def get(self, n):
        if self.pos + n > 8 * len(self.data):
            raise FormatError("truncated bitstream")
        v = 0
        for _ in range(n):
            v = (v << 1) | ((self.data[self.pos >> 3] >> (7 - (self.pos & 7))) & 1)
            self.pos += 1
        return v

    def rice(self, k):
        q = 0
        while self.get(1) == 0:          # unary quotient: q zero bits, then a one bit
            q += 1
        return (q << k) | self.get(k)

    def rest_is_padding(self):
        used = (self.pos + 7) // 8
        if used != len(self.data):
            return False
        return self.pos % 8 == 0 or self.data[used - 1] & (0xFF >> (self.pos % 8)) == 0


def decode_ints(data, n):
    """An integer stream: predictor order (3 bits), partition order (4 bits), then per partition a Rice parameter
    (6 bits; 63 = escape, followed by a 7-bit width and the values in that many bits) and its zigzag residuals."""
    r = Bits(data)
    order, porder = r.get(3), r.get(4)
    if order > 4 or porder > 8 or (n >> porder) == 0:
        raise FormatError("predictor header")
    res = []
    for p in range(1 << porder):
        a, b = (p * n) >> porder, ((p + 1) * n) >> porder
        k = r.get(6)
        if k == 63:
            width = r.get(7)
            if width > 64:
                raise FormatError("escape width")
            res.extend(unzigzag(r.get(width)) for _ in range(a, b))
        else:
            res.extend(unzigzag(r.rice(k)) for _ in range(a, b))
    if not r.rest_is_padding():
        raise FormatError("trailing data in stream")
    x = []
    for i, e in enumerate(res):          # fixed polynomial predictors; the first `order` values use order i
        o = min(order, i)
        pred = (0 if o == 0 else x[i - 1] if o == 1 else 2 * x[i - 1] - x[i - 2] if o == 2
                else 3 * (x[i - 1] - x[i - 2]) + x[i - 3] if o == 3
                else 4 * (x[i - 1] + x[i - 3]) - 6 * x[i - 2] - x[i - 4])
        x.append(_wrap64(e + pred))
    return x


def decode_chunk(enc):
    """The float64 samples of one encoded chunk (docs/chunk-format.md), checksum verified first."""
    enc = bytes(enc)
    if len(enc) < 7:
        raise FormatError("truncated chunk")
    version, kind = enc[0], enc[1]
    if version not in (2, 3):
        raise FormatError(f"unsupported chunk version {version}")
    body, crc = enc[:-4], struct.unpack("<I", enc[-4:])[0]
    if crc32c(body) != crc:
        raise FormatError("checksum mismatch")
    n, pos = varint(body, 2)
    if not 1 <= n <= MAX_SAMPLES:
        raise FormatError("sample count")
    if kind == 0 and version == 2:                       # raw float64
        if len(body) - pos != 8 * n:
            raise FormatError("raw length")
        out = list(struct.unpack(f"<{n}d", body[pos:]))
    elif kind == 2 and version == 2:                     # float bits, order-preserving integers
        out = [float_from_ordered(v) for v in decode_ints(body[pos:], n)]
    elif kind == 1:                                      # grid: integer indices times (or over) a scale
        mode = body[pos]
        scale = struct.unpack("<d", body[pos + 1:pos + 9])[0]
        pos += 9
        offset = None
        if version == 3:
            offset = struct.unpack("<d", body[pos:pos + 8])[0]
            pos += 8
        if mode > 1 or not (scale > 0 and scale != float("inf")) or (offset is not None and not abs(offset) < float("inf")):
            raise FormatError("grid header")
        has_fix = body[pos]
        pos += 1
        if has_fix > 1:
            raise FormatError("grid header")
        length, pos = varint(body, pos)
        if pos + length > len(body):
            raise FormatError("truncated")
        ks = decode_ints(body[pos:pos + length], n)
        rest = body[pos + length:]

        def value(k):
            v = k / scale if mode == 1 else k * scale    # float(k) is exact: |k| <= 2^53
            return v if offset is None else offset + v
        if has_fix:                                      # last-place corrections on the order-preserving integers
            fix = decode_ints(rest, n)
            out = [float_from_ordered(_wrap64(ordered_bits(value(float(k))) + c)) for k, c in zip(ks, fix)]
        else:
            if rest:
                raise FormatError("trailing data")
            out = [value(float(k)) for k in ks]
    else:
        raise FormatError(f"unknown kind {kind} for version {version}")
    if any(not abs(v) <= MAX_AMPLITUDE for v in out):
        raise FormatError("sample not finite or outside ±1e9")
    return out


# --- the file ------------------------------------------------------------------------------------------------
def _open(path):
    c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    app = c.execute("PRAGMA application_id").fetchone()[0]
    ver = c.execute("PRAGMA user_version").fetchone()[0]
    if app != APPLICATION_ID:
        c.close()
        raise FormatError("not a MorletDB file")
    if ver != FORMAT_VERSION:
        c.close()
        raise FormatError(f"file format {ver}; this reader reads format {FORMAT_VERSION}")
    return c


def recordings(path):
    with closing(_open(path)) as c:
        rows = c.execute("SELECT recording_id, sample_rate_hz, channel, unit, sample_quantum, metadata, end_sample "
                         "FROM recordings ORDER BY recording_id").fetchall()
    keys = ("recording_id", "sample_rate_hz", "channel", "unit", "sample_quantum", "metadata", "end_sample")
    return [dict(zip(keys, r[:5] + (json.loads(r[5]), r[6]))) for r in rows]


def read(path, recording_id, start, end):
    """The samples of [start, end) as a list of floats, checking that the chunks cover it without gaps."""
    with closing(_open(path)) as c:
        row = c.execute("SELECT end_sample FROM recordings WHERE recording_id=?", (recording_id,)).fetchone()
        if row is None:
            raise FormatError(f"no recording {recording_id!r}")
        if not 0 <= start < end <= row[0]:
            raise FormatError("interval outside the recording")
        out, at = [], None
        for s, n, enc in c.execute("SELECT start_sample, sample_count, encoded FROM chunks WHERE recording_id=? "
                                   "AND start_sample < ? AND start_sample + sample_count > ? ORDER BY start_sample",
                                   (recording_id, end, start)):
            if at is not None and s != at:
                raise FormatError("chunks do not cover the interval")
            x = decode_chunk(enc)
            if len(x) != n:
                raise FormatError("chunk does not match its sample count")
            out.extend(x[max(start, s) - s:min(end, s + n) - s])
            at = s + n
    if len(out) != end - start:
        raise FormatError("chunks do not cover the interval")
    return out


if __name__ == "__main__":
    if len(sys.argv) == 2:
        for r in recordings(sys.argv[1]):
            print(json.dumps(r))
    elif len(sys.argv) == 5:
        for v in read(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])):
            print(repr(v))
    else:
        sys.exit(__doc__)
