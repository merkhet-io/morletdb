#!/usr/bin/env python3
"""Convert MillingVibes' pickled .npy files to a plain npz without general unpickling.

The published files are object arrays saved with pickle. Loading them with
np.load(allow_pickle=True) would execute arbitrary pickle code. This loader allows only
the three globals the files use (checked with pickletools): numpy.core.multiarray._reconstruct,
numpy.ndarray and numpy.dtype. Anything else raises.

Usage: convert.py EXTRACTED_DIR OUT.npz
"""
import io
import pickle
import struct
import sys

import numpy as np
from numpy._core import multiarray

ALLOWED = {("numpy.core.multiarray", "_reconstruct"): multiarray._reconstruct,
           ("numpy", "ndarray"): np.ndarray, ("numpy", "dtype"): np.dtype}


class Restricted(pickle.Unpickler):
    def find_class(self, module, name):
        if (module, name) in ALLOWED:
            return ALLOWED[(module, name)]
        raise pickle.UnpicklingError(f"blocked global {module}.{name}")


def load(path):
    b = open(path, "rb").read()
    if b[:6] != b"\x93NUMPY" or b[6] != 1:
        raise ValueError("expected an .npy version 1 file")
    header_len = struct.unpack("<H", b[8:10])[0]
    return Restricted(io.BytesIO(b[10 + header_len:])).load()


def main():
    src, out = sys.argv[1].rstrip("/") + "/", sys.argv[2]
    arrays = {}
    for part in ("train", "test"):
        rows = load(src + f"small_segments_8000_{part}__good+bad.npy")
        arrays[f"x_{part}"] = np.stack([np.asarray(r[0], float) for r in rows])
        arrays[f"y_{part}"] = np.array([int(r[1]) for r in rows])
    np.savez(out, **arrays)


if __name__ == "__main__":
    main()
