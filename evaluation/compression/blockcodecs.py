"""Block codecs for the compression benchmark. Every codec returns real bytes and decodes them.

A block is a 1-D integer array of ADC counts (`step` gives physical units). Lossless codecs
must reproduce it exactly. Lossy codecs take an absolute error bound `eps` in counts and
must decode to within it; the benchmark checks this against the original samples
(an **empirical** check, not a mathematical guarantee).
"""
from __future__ import annotations

import io
import struct

import numpy as np
import zstandard

ZSTD_LEVEL = 19


def zc(b: bytes, level: int = ZSTD_LEVEL) -> bytes:
    return zstandard.ZstdCompressor(level=level).compress(b)


def zd(b: bytes) -> bytes:
    return zstandard.ZstdDecompressor().decompress(b)


def shuffle(a: np.ndarray) -> bytes:
    """Byte-plane shuffle (as Blosc/Parquet BYTE_STREAM_SPLIT) for better entropy coding."""
    a = np.ascontiguousarray(a)
    return a.view(np.uint8).reshape(-1, a.itemsize).T.tobytes()


def unshuffle(b: bytes, dtype, n: int) -> np.ndarray:
    item = np.dtype(dtype).itemsize
    return np.frombuffer(b, np.uint8).reshape(item, n).T.copy().view(dtype).ravel()


def smallest_int(a: np.ndarray):
    lo, hi = int(a.min()) if a.size else 0, int(a.max()) if a.size else 0
    for t in (np.int8, np.int16, np.int32, np.int64):
        if np.iinfo(t).min <= lo and hi <= np.iinfo(t).max:
            return t
    raise ValueError("out of range")


HEADER = struct.Struct("<4sBIQ")  # codec tag, dtype code, n, extra (codec-specific)
DTYPES = {1: np.int8, 2: np.int16, 4: np.int32, 8: np.int64}


def pack_ints(a: np.ndarray, delta: bool = False) -> bytes:
    a = np.asarray(a, np.int64)
    v = np.diff(a, prepend=0) if delta else a
    t = smallest_int(v)
    return bytes([np.dtype(t).itemsize, int(delta)]) + struct.pack("<I", len(a)) + zc(shuffle(v.astype(t)))


def unpack_ints(b: bytes) -> tuple[np.ndarray, int]:
    item, delta, n = b[0], b[1], struct.unpack("<I", b[2:6])[0]
    v = unshuffle(zd(b[6:]), DTYPES[item], n).astype(np.int64)
    return (np.cumsum(v) if delta else v), n


# -- lossless -------------------------------------------------------------------------
def int_zstd(x):
    return pack_ints(x)


def int_zstd_dec(b):
    return unpack_ints(b)[0]


def delta_zstd(x):
    return pack_ints(x, delta=True)


def delta_zstd_dec(b):
    return unpack_ints(b)[0]


def flac(x):
    """FLAC (linear prediction + Rice residuals). Needs |x| < 2^23 (24-bit PCM)."""
    import soundfile as sf
    x = np.asarray(x, np.int64)
    bits = 16 if smallest_int(x) in (np.int8, np.int16) else 24
    if bits == 24 and (x.min() < -(1 << 23) or x.max() >= (1 << 23)):
        raise ValueError("unsupported: exceeds 24-bit")
    buf = io.BytesIO()
    data = (x << 8).astype(np.int32) if bits == 24 else x.astype(np.int16)
    sf.write(buf, data, 48000, format="FLAC", subtype=f"PCM_{bits}", compression_level=1.0)
    return bytes([bits]) + buf.getvalue()


def flac_dec(b):
    import soundfile as sf
    bits = b[0]
    data, _ = sf.read(io.BytesIO(b[1:]), dtype="int32" if bits == 24 else "int16")
    data = data.astype(np.int64)
    return data >> 8 if bits == 24 else data


def lift53(x, levels=6):
    """Reversible integer CDF 5/3 lifting (JPEG 2000), symmetric extension; lossless."""
    x = np.asarray(x, np.int64)
    bands, a = [], x
    for _ in range(levels):
        if len(a) < 4:
            break
        e, o = a[0::2].copy(), a[1::2].copy()
        e_next = np.r_[e[1:], e[-1]] if len(e) > len(o) else np.r_[e[1:], e[-1]][:len(o)]
        d = o - ((e[:len(o)] + e_next[:len(o)]) >> 1)
        d_prev = np.r_[d[0], d[:-1]]
        d_cur = np.r_[d, d[-1]][:len(e)] if len(e) > len(d) else d
        d_prev = np.r_[d_prev, d_prev[-1]][:len(e)] if len(e) > len(d_prev) else d_prev
        s = e + ((d_prev + d_cur + 2) >> 2)
        bands.append(d)
        a = s
    bands.append(a)
    return bands


def unlift53(bands):
    a = bands[-1]
    for d in reversed(bands[:-1]):
        s = a
        n_e = len(s)
        d_prev = np.r_[d[0], d[:-1]]
        d_cur = np.r_[d, d[-1]][:n_e] if n_e > len(d) else d
        d_prev = np.r_[d_prev, d_prev[-1]][:n_e] if n_e > len(d_prev) else d_prev
        e = s - ((d_prev + d_cur + 2) >> 2)
        e_next = np.r_[e[1:], e[-1]][:len(d)]
        o = d + ((e[:len(d)] + e_next) >> 1)
        out = np.empty(len(e) + len(o), np.int64)
        out[0::2], out[1::2] = e, o
        a = out
    return a


def lift53_zstd(x):
    bands = lift53(x)
    parts = [pack_ints(b) for b in bands]
    return struct.pack("<B", len(parts)) + b"".join(struct.pack("<I", len(p)) + p for p in parts)


def split_parts(b):
    k, pos, parts = b[0], 1, []
    for _ in range(k):
        n = struct.unpack("<I", b[pos:pos + 4])[0]
        parts.append(b[pos + 4:pos + 4 + n])
        pos += 4 + n
    return parts


def lift53_zstd_dec(b):
    return unlift53([unpack_ints(p)[0] for p in split_parts(b)])


# -- lossy, error-bounded (eps in counts) -----------------------------------------------
def quantize(x, eps):
    """Uniform mid-tread quantizer with step 2*eps: |x - xhat| <= eps (eps >= 0.5 -> integer grid)."""
    step = 2 * eps
    return np.round(np.asarray(x, np.float64) / step).astype(np.int64), step


def quant_zstd(x, eps):
    q, step = quantize(x, eps)
    return struct.pack("<d", step) + pack_ints(q)


def quant_zstd_dec(b):
    step = struct.unpack("<d", b[:8])[0]
    return unpack_ints(b[8:])[0] * step


def quant_flac(x, eps):
    """Predictive (FLAC LPC) coding of the quantized signal: near-lossless predictive codec."""
    q, step = quantize(x, eps)
    return struct.pack("<d", step) + flac(q)


def quant_flac_dec(b):
    return flac_dec(b[8:]) * struct.unpack("<d", b[:8])[0]


def _h5(x, filt, shape=None):
    import h5py
    data = np.asarray(x, np.float64)
    if shape is not None:
        data = data.reshape(shape)
    buf = io.BytesIO()
    with h5py.File(buf, "w") as f:
        f.create_dataset("x", data=data, chunks=data.shape, **filt)
    with h5py.File(io.BytesIO(buf.getvalue()), "r") as f:
        dsid = f["x"].id
        size = dsid.get_storage_size()  # compressed chunk payload, excluding HDF5 container
    return size, buf.getvalue()


def sz3(x, eps):
    import hdf5plugin
    size, blob = _h5(x, hdf5plugin.SZ3(absolute=eps))
    return blob, size


def zfp(x, eps):
    import zfpy
    b = zfpy.compress_numpy(np.asarray(x, np.float64), tolerance=eps)
    return b, len(b)


def zfp_dec(b):
    import zfpy
    return zfpy.decompress_numpy(b)


def sperr(x, eps, row=4096):
    """SPERR is a 2-D/3-D compressor: 1-D blocks are stacked as rows (labelled as such)."""
    import hdf5plugin
    n = len(x)
    rows = n // row
    if rows < 2 or n % row:
        raise ValueError("unsupported: SPERR needs >= 2 full rows")
    size, blob = _h5(x, hdf5plugin.Sperr(absolute=eps), shape=(rows, row))
    return blob, size


def h5_dec(blob):
    import h5py
    import hdf5plugin  # noqa: F401  (registers filters)
    with h5py.File(io.BytesIO(blob), "r") as f:
        return f["x"][:].ravel()


def dwt_q(x, eps, wavelet="db4", levels=6, coef_step_factor=2.0):
    """Wavelet codec: orthonormal periodized DWT, uniform coefficient quantization (step
    coef_step_factor*eps), zstd per subband, then sparse sample corrections so that the
    decoded block is within eps of the original (outlier correction, as in SPERR/SZ)."""
    import pywt
    x = np.asarray(x, np.float64)
    coeffs = pywt.wavedec(x, wavelet, mode="periodization", level=levels)
    cstep = coef_step_factor * eps
    qs = [np.round(c / cstep).astype(np.int64) for c in coeffs]
    xhat = pywt.waverec([q * cstep for q in qs], wavelet, mode="periodization")[:len(x)]
    corr, _ = quantize(x - xhat, eps)
    idx = np.flatnonzero(corr)
    parts = [pack_ints(q) for q in qs] + [pack_ints(idx, delta=True), pack_ints(corr[idx])]
    head = struct.pack("<ddI", eps, cstep, len(x)) + wavelet.encode().ljust(8, b"\0") + bytes([levels])
    return head + struct.pack("<B", len(parts)) + b"".join(struct.pack("<I", len(p)) + p for p in parts)


DWT_HEAD = 8 + 8 + 4 + 8 + 1


def dwt_q_dec(b, keep_levels=None):
    """keep_levels: number of coarse subbands to use (progressive partial decode); None = all,
    including the sample corrections that enforce the error bound."""
    import pywt
    eps, cstep, n = struct.unpack("<ddI", b[:20])
    wavelet = b[20:28].rstrip(b"\0").decode()
    parts = split_parts(b[DWT_HEAD:])
    qs = [unpack_ints(p)[0] for p in parts[:-2]]
    coeffs = [q * cstep for q in qs]
    if keep_levels is not None:
        coeffs = [c if i < keep_levels else np.zeros_like(c) for i, c in enumerate(coeffs)]
    xhat = pywt.waverec(coeffs, wavelet, mode="periodization")[:n]
    if keep_levels is None:
        idx = unpack_ints(parts[-2])[0]
        corr = unpack_ints(parts[-1])[0]
        xhat[idx] += corr * (2 * eps)
    return xhat


def harmonic_residual(x, eps, k=8):
    """Decomposition ablation: K strongest spectral lines (bin, amplitude, phase as float32)
    as a predictor, residual coded by quant_flac. Parameters count toward the size."""
    x = np.asarray(x, np.float64)
    n = len(x)
    spec = np.fft.rfft(x - x.mean())
    bins = np.sort(np.argsort(np.abs(spec[1:]))[-k:] + 1)
    amp = (2 * np.abs(spec[bins]) / n).astype(np.float32)
    ph = np.angle(spec[bins]).astype(np.float32)
    mean = np.float32(x.mean())
    pred = _harmonic(n, bins, amp, ph, mean)
    params = struct.pack("<If", n, mean) + bins.astype(np.uint32).tobytes() + amp.tobytes() + ph.tobytes()
    return struct.pack("<H", k) + params + quant_flac(x - pred, eps)


def _harmonic(n, bins, amp, ph, mean):
    t = np.arange(n)
    pred = np.full(n, float(mean))
    for b, a, p in zip(bins, amp.astype(np.float64), ph.astype(np.float64)):
        pred += a * np.cos(2 * np.pi * b * t / n + p)
    return pred


def harmonic_residual_dec(b):
    k = struct.unpack("<H", b[:2])[0]
    n, mean = struct.unpack("<If", b[2:10])
    pos = 10
    bins = np.frombuffer(b[pos:pos + 4 * k], np.uint32); pos += 4 * k
    amp = np.frombuffer(b[pos:pos + 4 * k], np.float32); pos += 4 * k
    ph = np.frombuffer(b[pos:pos + 4 * k], np.float32); pos += 4 * k
    return _harmonic(n, bins, amp, ph, np.float32(mean)) + quant_flac_dec(b[pos:])


def tsa_residual(x, eps, period):
    """Shaft-synchronous template (time-synchronous average over phase bins at a known
    period in samples, float32) as predictor; residual coded by quant_flac."""
    x = np.asarray(x, np.float64)
    m = int(round(period))
    phase = ((np.arange(len(x)) / period) % 1.0 * m).astype(np.int64) % m
    template = (np.bincount(phase, x, m) / np.maximum(np.bincount(phase, None, m), 1)).astype(np.float32)
    pred = template[phase].astype(np.float64)
    return struct.pack("<dII", period, m, len(x)) + template.tobytes() + quant_flac(x - pred, eps)


def tsa_residual_dec(b):
    period, m, n = struct.unpack("<dII", b[:16])
    template = np.frombuffer(b[16:16 + 4 * m], np.float32)
    phase = ((np.arange(n) / period) % 1.0 * m).astype(np.int64) % m
    return template[phase].astype(np.float64) + quant_flac_dec(b[16 + 4 * m:])
