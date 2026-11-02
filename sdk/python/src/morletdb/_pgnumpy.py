"""Binary float8[] transfer between numpy arrays and PostgreSQL (psycopg 3).

psycopg's default path turns every sample into a Python float and formats arrays element by element, in Python.
These adapters write and read PostgreSQL's binary array format directly from and into numpy buffers. The values
travel as IEEE 754 float64 bit patterns, so they are reproduced exactly (including -0.0, subnormals, NaN payloads
and infinities), as with the default text path.

    import pg_numpy
    pg_numpy.register(conn)                      # numpy arrays as parameters; float8[] results as numpy arrays
"""
import struct

import numpy as np
from psycopg.adapt import Dumper, Loader
from psycopg.pq import Format

FLOAT8_OID = 701
FLOAT8_ARRAY_OID = 1022
_ELEM = np.dtype([("len", ">i4"), ("val", ">f8")])     # each element: length (always 8) and big-endian value
_HEADER = struct.Struct(">iii")                         # ndim, has_null, element oid
_DIM = struct.Struct(">ii")                             # size, lower bound


class Float8ArrayDumper(Dumper):
    """One-dimensional float64 numpy array -> binary float8[] (other float dtypes are widened exactly)."""
    format = Format.BINARY
    oid = FLOAT8_ARRAY_OID

    def dump(self, obj):
        a = np.asarray(obj)
        if a.ndim != 1 or a.dtype.kind != "f":
            raise TypeError(f"expected a one-dimensional float array, got {a.dtype} with {a.ndim} dimensions")
        if a.size == 0:
            return _HEADER.pack(0, 0, FLOAT8_OID)
        buf = np.empty(a.size, _ELEM)
        buf["len"] = 8
        buf["val"] = a
        return _HEADER.pack(1, 0, FLOAT8_OID) + _DIM.pack(a.size, 1) + buf.tobytes()


class Float8ArrayLoader(Loader):
    """Binary float8[] -> float64 numpy array; refuses NULL elements and more than one dimension."""
    format = Format.BINARY

    def load(self, data):
        mv = memoryview(data)
        ndim, has_null, oid = _HEADER.unpack_from(mv, 0)
        if ndim == 0:
            return np.empty(0, np.float64)
        if ndim != 1 or oid != FLOAT8_OID:
            raise ValueError(f"expected a one-dimensional float8 array (ndim {ndim}, element oid {oid})")
        if has_null:
            raise ValueError("float8[] with NULL elements cannot become a float64 array")
        n, _ = _DIM.unpack_from(mv, _HEADER.size)
        el = np.frombuffer(mv, _ELEM, count=n, offset=_HEADER.size + _DIM.size)
        return el["val"].astype("<f8")


def register(context):
    """Register both adapters on a connection or cursor (results need binary=True to use the loader)."""
    context.adapters.register_dumper(np.ndarray, Float8ArrayDumper)
    context.adapters.register_loader(FLOAT8_ARRAY_OID, Float8ArrayLoader)
