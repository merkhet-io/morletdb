"""Usage: dump_chunks.py OUT_DIR CHUNK_SIZE (run with .venv-compress).

Dump held-out benchmark samples as the floats a user would insert, chunked, plus FLAC sizes per chunk."""
import json, struct, sys
from pathlib import Path
import numpy as np
EVAL = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(EVAL), str(EVAL.parent / "decision")]
import datasets as ds, blockcodecs as bc, bearings as b, paderborn_mat as pm
OUT = Path(sys.argv[1]); CH = int(sys.argv[2])

def chunks(x):
    for i in range(0, len(x), CH):
        yield x[i:i + CH]

def write(name, items):
    flac = n = 0
    with open(OUT / f"{name}-{CH}.bin", "wb") as f:
        for floats, q, ints in items:
            for fc, ic in zip(chunks(floats), chunks(ints)):
                f.write(struct.pack("<Id", len(fc), q)); f.write(np.asarray(fc, "<f8").tobytes())
                flac += len(bc.flac(ic)); n += len(fc)
    return {"samples": n, "flac_bytes": flac, "flac_bits_per_sample": 8 * flac / n}

res = {}
res["cx"] = write("cx", ((blk["x"].astype(float), 1.0, blk["x"]) for blk in ds.cx("heldout")))
res["ims"] = write("ims", ((blk["x"] / 1000.0, 0.001, blk["x"]) for blk in ds.ims("heldout")))
def pad():
    root = ds.DATA / "paderborn/zenodo-15845309/extracted"
    for blk in ds.paderborn("heldout"):
        _, _, _, vib, _ = pm.load_file(root / blk["unit"] / f"{blk['id']}.mat")
        yield np.asarray(vib, float)[:len(blk["x"])], blk["step"], blk["x"]
res["paderborn-physical"] = write("paderborn-physical", pad())
res["paderborn-counts"] = write("paderborn-counts", ((blk["x"].astype(float), 1.0, blk["x"]) for blk in ds.paderborn("heldout")))
(OUT / f"flac-{CH}.json").write_text(json.dumps(res, indent=1)); print(json.dumps(res))
