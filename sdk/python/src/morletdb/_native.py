"""The native decoder for compressed transfer (docs/compressed-transfer.md): MorletDB's Rust chunk decoder as a
small shared library (crates/morlet-ffi), loaded with ctypes. Without it the client reads raw float64 samples,
which gives the same values, only more slowly for long intervals.

The library is looked up at MORLETDB_NATIVE_LIB (a path), then next to this module in `_lib/`. MORLETDB_TRANSFER
chooses the read path: `auto` (default; encoded reads from ENCODED_MIN_SAMPLES when the library and the server
support them), `encoded` (always, an error where unsupported) or `raw` (never)."""
from __future__ import annotations

import ctypes
import os
import sys
from pathlib import Path

import numpy as np

from ._errors import DatabaseError, InvalidInputError, UnsupportedError

ABI_VERSION = 1
# Reads shorter than this go raw: one or two chunks gain nothing from transfer savings (eight 4,096-sample chunks).
ENCODED_MIN_SAMPLES = 32_768
_NAMES = {"darwin": "libmorlet_ffi.dylib", "win32": "morlet_ffi.dll"}
_ERRORS = {-1: "invalid arguments", -2: "corrupt transfer frame", -3: "corrupt chunk",
           -4: "transfer frame does not cover the interval exactly", -5: "internal decoder error"}


class _Native:
    def __init__(self, lib: ctypes.CDLL):
        self.lib = lib
        lib.morlet_ffi_abi_version.restype = ctypes.c_uint32
        lib.morlet_ffi_max_chunk_version.restype = ctypes.c_uint32
        lib.morlet_decode_frame.restype = ctypes.c_int32
        lib.morlet_decode_frame.argtypes = [ctypes.c_char_p, ctypes.c_size_t, ctypes.c_int64, ctypes.c_int64,
                                            ctypes.c_void_p, ctypes.c_size_t, ctypes.c_uint32,
                                            ctypes.POINTER(ctypes.c_int64)]
        if lib.morlet_ffi_abi_version() != ABI_VERSION:
            raise OSError("native decoder interface version differs")
        self.max_chunk_version = int(lib.morlet_ffi_max_chunk_version())
        self.threads = min(os.cpu_count() or 1, 4)

    def decode(self, frame: bytes, start: int, end: int) -> np.ndarray:
        out = np.empty(end - start, dtype=np.float64)
        at = ctypes.c_int64(-1)
        frame = bytes(frame)
        code = self.lib.morlet_decode_frame(frame, len(frame), start, end, out.ctypes.data, out.size,
                                            self.threads, ctypes.byref(at))
        if code == 0:
            return out
        if code == -1:
            raise InvalidInputError(f"native decoder: {_ERRORS[code]}")
        where = f" at sample {at.value}" if code == -3 else ""
        raise DatabaseError(f"stored data failed verification in the client: {_ERRORS.get(code, code)}{where}")


def _load() -> _Native | None:
    paths = []
    if os.environ.get("MORLETDB_NATIVE_LIB"):
        paths.append(Path(os.environ["MORLETDB_NATIVE_LIB"]))
    paths.append(Path(__file__).resolve().parent / "_lib" / _NAMES.get(sys.platform, "libmorlet_ffi.so"))
    for p in paths:
        if p.is_file():
            try:
                return _Native(ctypes.CDLL(str(p)))
            except (OSError, AttributeError):
                continue
    return None


_native: _Native | None = None
_loaded = False


def native() -> _Native | None:
    global _native, _loaded
    if not _loaded:
        _native, _loaded = _load(), True
    return _native


def mode() -> str:
    m = os.environ.get("MORLETDB_TRANSFER", "auto").strip().lower() or "auto"
    if m not in ("auto", "encoded", "raw"):
        raise InvalidInputError("MORLETDB_TRANSFER must be auto, encoded or raw")
    return m


def require() -> _Native:
    n = native()
    if n is None:
        raise UnsupportedError("MORLETDB_TRANSFER=encoded, but the native decoder library was not found "
                               "(set MORLETDB_NATIVE_LIB or install a platform wheel)")
    return n
