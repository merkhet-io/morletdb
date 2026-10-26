"""Usage: prepare_read_data.py OUT.npy N_SAMPLES (run with .venv-compress).

Concatenates Paderborn *development* bearings (int16 ADC counts, the frozen loader)
into one recording for the read benchmark. Development data only: the held-out
units were consumed by the stage-1 benchmark and are not reused here."""
import sys
from pathlib import Path

import numpy as np

EVAL = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(EVAL), str(EVAL.parent / "decision")]
import datasets as ds  # noqa: E402

out, n = Path(sys.argv[1]), int(sys.argv[2])
parts, total = [], 0
for blk in ds.paderborn("dev"):
    parts.append(blk["x"])
    total += len(blk["x"])
    if total >= n:
        break
x = np.concatenate(parts)[:n].astype(np.int16)
np.save(out, x)
print(out, len(x), "samples from", len(parts), "files")
