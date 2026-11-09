"""The embedded edition (#351, experimental): a MorletDB file on SQLite, in this process, with no server.

    import morletdb
    with morletdb.open("line3.morlet") as db:
        db.ingest("pump-7/accel-x", samples, sample_rate_hz=25_600, channel="accel-x", unit="m/s^2")
        iv = db.read("pump-7/accel-x", 40_960, 45_056)          # the stored samples, bit for bit
        st = db.stats("pump-7/accel-x", 0, 1_000_000)           # as pg_morlet's morlet_interval_stats

The file holds the same chunks as pg_morlet (same bytes), so the statistics are the same bit for bit. The native
library (crates/morlet-sqlite, SQLite included) is looked up at MORLETDB_SQLITE_LIB, then in `_lib/`.
"""
from __future__ import annotations

import ctypes
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from ._client import _iso, _meta_start_ns, _samples, _start_ns
from ._errors import ConflictError, DatabaseError, InvalidInputError, NotFoundError, UnsupportedError
from ._types import Interval, IntervalRef, IntervalStats, Recording

ABI_VERSION = 1
CHUNK_SIZE = 4096
BATCH = 262_144
_NAMES = {"darwin": "libmorlet_sqlite.dylib", "win32": "morlet_sqlite.dll"}
_P, _C, _D, _I64 = ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double, ctypes.c_int64


def _load() -> ctypes.CDLL:
    paths = [Path(os.environ["MORLETDB_SQLITE_LIB"])] if os.environ.get("MORLETDB_SQLITE_LIB") else []
    paths.append(Path(__file__).resolve().parent / "_lib" / _NAMES.get(sys.platform, "libmorlet_sqlite.so"))
    for p in paths:
        if p.exists():
            lib = ctypes.CDLL(str(p))
            break
    else:
        raise UnsupportedError("the embedded edition needs the morlet-sqlite library (a platform wheel, or "
                               "MORLETDB_SQLITE_LIB pointing at a build of crates/morlet-sqlite)")
    lib.morlet_sqlite_abi_version.restype = ctypes.c_uint32
    if lib.morlet_sqlite_abi_version() != ABI_VERSION:
        raise UnsupportedError("morlet-sqlite library interface version differs")
    sig = {"open": [_C, ctypes.c_int32, ctypes.POINTER(_P)],
           "create_recording": [_P, _C, _D, _C, _C, _D, _C],
           "append": [_P, _C, _I64, _P, ctypes.c_size_t, ctypes.c_uint32, ctypes.POINTER(_I64)],
           "read": [_P, _C, _I64, _I64, _P, ctypes.c_size_t],
           "stats": [_P, _C, _I64, _I64, _P],
           "info_json": [_P, _P, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]}
    for name, args in sig.items():
        f = getattr(lib, "morlet_sqlite_" + name)
        f.argtypes, f.restype = args, ctypes.c_int32
    lib.morlet_sqlite_close.argtypes, lib.morlet_sqlite_close.restype = [_P], None
    lib.morlet_sqlite_last_error.argtypes = [_P, ctypes.c_size_t]
    lib.morlet_sqlite_last_error.restype = ctypes.c_size_t
    return lib


_LIB: ctypes.CDLL | None = None


def _lib() -> ctypes.CDLL:
    global _LIB
    if _LIB is None:
        _LIB = _load()
    return _LIB


def _check(code: int) -> None:
    if code == 0:
        return
    lib = _lib()
    buf = ctypes.create_string_buffer(lib.morlet_sqlite_last_error(None, 0) + 1)
    lib.morlet_sqlite_last_error(buf, len(buf))
    msg = buf.value.decode(errors="replace")
    if code == -1:
        if msg.startswith("no recording"):
            raise NotFoundError(msg)
        if "exists" in msg or "must start at the recording's end" in msg:
            raise ConflictError(msg)
        raise InvalidInputError(msg)
    raise DatabaseError(msg)


class LocalStore:
    """A MorletDB file (SQLite) opened in this process. Every append is one durable transaction
    (`durability="full"`, the default; `"normal"` may lose the newest appends on a power loss, never older
    ones). One writer at a time per file; readers may run alongside (WAL)."""

    def __init__(self, path: str | os.PathLike, durability: str = "full"):
        if durability not in ("full", "normal"):
            raise InvalidInputError("durability must be 'full' or 'normal'")
        self.path = str(path)
        self._h = _P()
        _check(_lib().morlet_sqlite_open(os.fsencode(self.path), 0 if durability == "full" else 1,
                                         ctypes.byref(self._h)))

    def close(self) -> None:
        if self._h:
            _lib().morlet_sqlite_close(self._h)
            self._h = _P()

    def __enter__(self) -> "LocalStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _handle(self) -> ctypes.c_void_p:
        if not self._h:
            raise InvalidInputError("the store is closed")
        return self._h

    def recordings(self) -> list[Recording]:
        n = ctypes.c_size_t(0)
        _check(_lib().morlet_sqlite_info_json(self._handle(), None, 0, ctypes.byref(n)))
        buf = ctypes.create_string_buffer(n.value + 1)
        _check(_lib().morlet_sqlite_info_json(self._handle(), buf, len(buf), ctypes.byref(n)))
        out = []
        for r in json.loads(buf.value.decode())["recordings"]:
            meta = r["metadata"]
            out.append(Recording(self.path, r["recording_id"], r["sample_rate_hz"], r["channel"], r["unit"],
                                 int(r["end_sample"]), _meta_start_ns(meta), meta))
        return out

    def recording(self, recording_id: str) -> Recording:
        for r in self.recordings():
            if r.recording_id == recording_id:
                return r
        raise NotFoundError(f"no recording {recording_id!r}")

    def ingest(self, recording_id: str, samples: Any, *, sample_rate_hz: float, channel: str, unit: str,
               start_time: datetime | int | None = None, metadata: Mapping[str, Any] | None = None,
               sample_quantum: float | None = None) -> Recording:
        """Store a complete waveform as a new recording, as Client.ingest does (without search envelopes)."""
        x = _samples(samples)
        meta = dict(metadata or {})
        ns = _start_ns(start_time)
        if ns is not None:
            for key in ("start", "start_ns"):
                if key in meta:
                    raise InvalidInputError(f"metadata[{key!r}] is set from start_time; do not pass both")
            meta.update({"start": _iso(ns), "start_ns": ns})
        quantum = 1.0 if sample_quantum is None else float(sample_quantum)
        _check(_lib().morlet_sqlite_create_recording(
            self._handle(), recording_id.encode(), float(sample_rate_hz), channel.encode(), unit.encode(), quantum,
            json.dumps(meta).encode()))
        self.append(recording_id, 0, x)
        return self.recording(recording_id)

    def append(self, recording_id: str, start_sample: int, samples: Any) -> int:
        """Append at the recording's end (an identical replay of stored samples is a no-op); one transaction per
        262,144 samples. Returns the recording's new end."""
        x = _samples(samples)
        end = ctypes.c_int64(-1)
        for s in range(0, x.size, BATCH):
            part = x[s:s + BATCH]
            _check(_lib().morlet_sqlite_append(self._handle(), recording_id.encode(), start_sample + s,
                                               part.ctypes.data, part.size, CHUNK_SIZE, ctypes.byref(end)))
        return int(end.value)

    def read(self, recording_id: str, start_sample: int, end_sample: int) -> Interval:
        if end_sample <= start_sample:
            raise InvalidInputError("end_sample must be greater than start_sample")
        out = np.empty(end_sample - start_sample, dtype=np.float64)
        _check(_lib().morlet_sqlite_read(self._handle(), recording_id.encode(), start_sample, end_sample,
                                         out.ctypes.data, out.size))
        return Interval(IntervalRef(self.path, recording_id, start_sample, end_sample), out,
                        self.recording(recording_id))

    def stats(self, recording_id: str, start_sample: int, end_sample: int) -> IntervalStats:
        out = (ctypes.c_double * 5)()
        _check(_lib().morlet_sqlite_stats(self._handle(), recording_id.encode(), start_sample, end_sample, out))
        return IntervalStats(int(out[0]), out[1], out[2], out[3], out[4])


def open(path: str | os.PathLike, durability: str = "full") -> LocalStore:  # noqa: A001
    """Open (or create) a MorletDB file: the embedded edition, no server needed."""
    return LocalStore(path, durability)
