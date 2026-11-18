#!/usr/bin/env python3
"""B6 inputs (PROTOCOL.md section 3): builds every input as a chunk file and writes a manifest for the build record.

  MORLET_DATA=/path/to/data .venv-b3/bin/python -I evaluation/storage_b6/prepare_b6.py SET WORK

  SET: benchmark  the 30 benchmark inputs and the development series measured in the same run (reported apart);
                  started by run_b6.sh, never in development.
       dev        the harness test (gate 7 development inputs only): B4's four development Elf series, FEMTO and
                  XJTU-SY, the Paderborn (B6) builder run on B3's development-set files only, and the six B3 machine
                  channels from B3's development set; written under results/dev/.

WORK/inputs/<name>.bin is the codec bench example's chunk file (per stored chunk: u32 LE sample count, f64 LE quantum
1.0, the samples as f64 LE); WORK/inputs.json lists every input with its set, samples, chunk lengths, sha256 of the
samples and of the chunk file, and its sources (file lists, acquisition counts).

Inputs (benchmark set):
- waveform (6, S2): CX counts, IMS decimals, Sleep-EDF and ECG as B4 parses them (measure_b4.load_wave, unchanged;
  data/b1-runs), and Paderborn counts (B6) and Paderborn physical (B6): B1's loader and rules
  (evaluation/compression/datasets.py `paderborn("heldout")`, the first 2 accepted vibration files per bearing and
  setting) as B1's P2 dump turns them into inputs (evaluation/plain_postgres/p2_dump.py: the first 1,024 chunks of
  4,096 samples in the loader's order; counts as float64, or the physical float64 of the same samples), minus every
  file of the five development bearings (DEV_BEARINGS);
- elf (18): B4's Elf series minus its four development series (measure_b4.load_elf, unchanged), 4,096-sample chunks;
- b3 (6, S3 and S4): B3's benchmark set (evaluation/tsdb_b3/workload.py files(): every 20th of the 2,480 files,
  starting with the first) minus every file of the five development bearings; per channel (vibration_1,
  phase_current_1, phase_current_2 at 64 kHz; force, speed, torque at 4 kHz) one input, its acquisitions concatenated
  in B3's file order, every acquisition starting a new chunk (4,096-sample chunks, the last of each acquisition
  partial), as B3 stores them.
Measured in the same run, reported as development (not counted in any criterion): B4's four development Elf series
(elf-dev) and B4's unfiltered Paderborn inputs (waveform-b4: pad-c, pad-p).

Every chunk is stored with quantum 1 (section 3: "all at 4,096-sample chunks and quantum 1").
"""
import hashlib
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DATA = Path(os.environ.get("MORLET_DATA", ROOT / "data"))
CHUNK = 4096
P2_CHUNKS = 1024                                     # p2_dump.py's N_CHUNKS
# B4's Paderborn inputs are p2_dump.py's first 1,024 chunks; B6_PADERBORN_CHUNKS=all builds every held-out block
# instead (an owner's decision, recorded in the manifest; the default is B4's construction).
PADERBORN_CHUNKS = None if os.environ.get("B6_PADERBORN_CHUNKS") == "all" else \
    int(os.environ.get("B6_PADERBORN_CHUNKS", P2_CHUNKS))
DEV_BEARINGS = ("K001", "KA01", "KA09", "KB24", "KI08")   # section 3, "Development bearings"
ELF_DEV = ["Blockchain-tr", "POI-lat", "Stocks-USA", "electric_vehicle_charging"]
ELF_BENCH = ["Air-pressure", "Air-sensor", "Basel-temp", "Basel-wind", "Bird-migration", "Bitcoin-price",
             "City-lat", "City-lon", "City-temp", "Dew-point-temp", "Food-price", "IR-bio-temp",
             "PM10-dust", "POI-lon", "SSD-bench", "Stocks-DE", "Stocks-UK", "Wind-Speed"]
B4_WAVES = ["cx", "ims", "sleep", "ecg"]             # B4's waveforms kept unchanged in B6
B3_CHANNELS = ["vibration_1", "phase_current_1", "phase_current_2", "force", "speed", "torque"]


# ---------------------------------------------------------------- pure parts (unit-tested in test_prepare_b6.py)
def keep_unit(unit):
    """Section 3: every file of the five development bearings is excluded from every benchmark input."""
    return unit not in DEV_BEARINGS


def first_chunks(items, n_chunks=P2_CHUNKS, ch=CHUNK):
    """p2_dump.py's first_chunks: the first n_chunks chunks of ch samples over items in order, each item cut into
    chunks of ch samples (its last chunk partial). items: (floats, id). Returns (chunks, ids of the items used)."""
    out, used = [], []
    for floats, ident in items:
        took = False
        for i in range(0, len(floats), ch):
            out.append(np.asarray(floats[i:i + ch], dtype="<f8"))
            took = True
            if n_chunks is not None and len(out) == n_chunks:
                used.append(ident)
                return out, used
        if took:
            used.append(ident)
    return out, used


def b3_keep(paths):
    """B3's benchmark files minus every file of the development bearings (the bearing is the file's directory)."""
    return [p for p in paths if keep_unit(Path(p).parent.name)]


def acquisition_chunks(acquisitions, channel, ch=CHUNK):
    """One B3 channel: the acquisitions' samples concatenated in order, every acquisition starting a new chunk.
    acquisitions: [{channel: array}] in B3's file order. Returns (samples, chunk lengths)."""
    xs, lengths = [], []
    for data in acquisitions:
        x = np.ascontiguousarray(data[channel], dtype="<f8")
        xs.append(x)
        lengths += [min(ch, len(x) - i) for i in range(0, len(x), ch)]
    return (np.concatenate(xs) if xs else np.zeros(0, "<f8")), np.array(lengths, dtype=np.int64)


def write_chunks(x, lengths, path):
    """The bench example's chunk file, quantum 1 for every chunk. Returns its sha256."""
    assert int(np.sum(lengths)) == x.size and all(0 < n <= 32768 for n in lengths)
    h = hashlib.sha256()
    with open(path, "wb") as f:
        at = 0
        for n in lengths:
            n = int(n)
            rec = np.uint32(n).tobytes() + np.float64(1.0).tobytes() + np.ascontiguousarray(
                x[at:at + n], dtype="<f8").tobytes()
            f.write(rec)
            h.update(rec)
            at += n
    return h.hexdigest()


# ---------------------------------------------------------------- loaders (B4, B1, B3 code, unchanged)
def b4():
    """B4's measure_b4 module with its data directory at DATA. Its loaders use numpy only; its module-level
    `import pcodec` (used by B4's own PCO arm, not by the loaders) is satisfied by a stub when pcodec is absent."""
    if "pcodec" not in sys.modules:
        try:
            import pcodec  # noqa: F401
        except ImportError:
            stub = types.ModuleType("pcodec")
            stub.standalone = types.ModuleType("pcodec.standalone")
            sys.modules["pcodec"], sys.modules["pcodec.standalone"] = stub, stub.standalone
    sys.path.insert(0, str(ROOT / "evaluation/storage_b4"))
    import measure_b4
    measure_b4.DATA = DATA
    return measure_b4


def b1():
    sys.path[:0] = [str(ROOT / "evaluation/compression"), str(ROOT / "evaluation/decision")]
    import datasets
    import paderborn_mat
    datasets.DATA = DATA
    return datasets, paderborn_mat


def paderborn_b6(root=None, modules=None):
    """Paderborn counts (B6) and Paderborn physical (B6): p2_dump.py's construction from B1's loader, with every block
    of the development bearings skipped before chunking. root: B1's Paderborn directory (default: the data's);
    modules: (datasets, paderborn_mat), B1's by default (tests pass synthetic ones)."""
    ds, pm = modules or b1()
    root = Path(root) if root else DATA / "paderborn/zenodo-15845309/extracted"
    blocks, skipped = [], []

    def counts():
        for blk in ds.paderborn("heldout", root=root):
            if not keep_unit(blk["unit"]):
                skipped.append(f"{blk['unit']}/{blk['id']}.mat")
                continue
            blocks.append(blk)
            yield blk["x"].astype(float), f"{blk['unit']}/{blk['id']}.mat"
    c_chunks, used = first_chunks(counts(), n_chunks=PADERBORN_CHUNKS)
    by_id = {f"{b['unit']}/{b['id']}.mat": b for b in blocks}

    def physical():
        for ident in used:
            blk = by_id[ident]
            _, _, _, vib, _ = pm.load_file(root / blk["unit"] / f"{blk['id']}.mat")
            yield np.asarray(vib, float)[:len(blk["x"])], ident
    p_chunks, used_p = first_chunks(physical(), n_chunks=PADERBORN_CHUNKS)
    assert used_p == used and [len(c) for c in c_chunks] == [len(c) for c in p_chunks]
    src = {"rule": ("p2_dump.py first_chunks (1,024 chunks of 4,096 samples) over B1's paderborn('heldout'), "
                    "development bearings skipped") if PADERBORN_CHUNKS == P2_CHUNKS else
           f"every block of B1's paderborn('heldout') (chunk cap: {PADERBORN_CHUNKS or 'none'}), development "
           "bearings skipped", "chunks_cap": PADERBORN_CHUNKS,
           "files_used": used, "units_used": sorted({u.split("/")[0] for u in used}),
           "files_skipped_development_bearings": sorted(set(skipped)), "root": str(root)}
    return (np.concatenate(c_chunks), np.array([len(c) for c in c_chunks])), \
        (np.concatenate(p_chunks), np.array([len(c) for c in p_chunks])), src


def b3_channels(b3_set):
    """The six B3 machine channels from B3's file list (bench or dev) minus the development bearings."""
    os.environ["B3_SET"] = "dev" if b3_set == "dev" else "bench"
    sys.path.insert(0, str(ROOT / "evaluation/tsdb_b3"))
    import workload as wl
    wl.SET = os.environ["B3_SET"]
    wl.DATA = DATA / "paderborn/zenodo-15845309/extracted"
    allp = wl.files()
    kept = b3_keep(allp)
    acqs = [wl.load(p) for p in kept]
    src = {"b3_set": os.environ["B3_SET"], "acquisitions_in_set": len(allp), "acquisitions_kept": len(kept),
           "acquisitions_excluded": len(allp) - len(kept),
           "excluded": [str(Path(p).relative_to(wl.DATA)) for p in allp if p not in kept],
           "files": [str(Path(p).relative_to(wl.DATA)) for p in kept]}
    return {c: acquisition_chunks(acqs, c) for c in B3_CHANNELS}, src


def csv_column(paths, col):
    vals = []
    for p in paths:
        for line in p.read_text().splitlines():
            parts = line.split(",")
            if len(parts) > col:
                try:
                    vals.append(float(parts[col].strip()))
                except ValueError:
                    pass
    return np.array(vals, dtype="<f8")


def dev_paderborn_root(tmp):
    """For the harness test only: a Paderborn directory holding B3's development-set files (gate 7) under their
    bearings, so B1's loader and the exclusion run on development files only (the development bearings among them)."""
    os.environ["B3_SET"] = "dev"
    sys.path.insert(0, str(ROOT / "evaluation/tsdb_b3"))
    import workload as wl
    wl.SET = "dev"
    wl.DATA = DATA / "paderborn/zenodo-15845309/extracted"
    root = Path(tmp) / "paderborn-dev"
    for p in wl.files():
        d = root / p.parent.name
        d.mkdir(parents=True, exist_ok=True)
        (d / p.name).symlink_to(p)
    return root


def chunked(x, ch=CHUNK):
    return np.array([min(ch, x.size - i) for i in range(0, x.size, ch)], dtype=np.int64)


# ---------------------------------------------------------------- build
def build(which, work):
    work = Path(work)
    (work / "inputs").mkdir(parents=True, exist_ok=True)
    rows = []

    def add(name, set_, x, lengths, source):
        x = np.ascontiguousarray(x, dtype="<f8")
        lengths = np.asarray(lengths, dtype=np.int64)
        sizes = {}
        for n in lengths:
            sizes[int(n)] = sizes.get(int(n), 0) + 1
        file_sha = write_chunks(x, lengths, work / "inputs" / f"{name}.bin")
        row = {"input": name, "set": set_, "samples": int(x.size), "chunks": int(lengths.size),
               "chunk_lengths": {str(k): v for k, v in sorted(sizes.items())},
               "sha256": hashlib.sha256(x.tobytes()).hexdigest(), "chunk_file": f"inputs/{name}.bin",
               "chunk_file_sha256": file_sha, "source": source}
        rows.append(row)
        print(json.dumps({k: row[k] for k in ("input", "set", "samples", "chunks")}), flush=True)

    m = b4()
    if which == "benchmark":
        for w in B4_WAVES:
            x, lengths, _ = m.load_wave(w)
            add(w, "waveform", x, lengths, {"loader": f"measure_b4.load_wave('{w}')", "file": m.WAVE[w]})
        (cx, cl), (px, pl), src = paderborn_b6()
        add("pad-c-b6", "waveform", cx, cl, src)
        add("pad-p-b6", "waveform", px, pl, src)
        for e in ELF_BENCH:
            x, lengths, _ = m.load_elf(e)
            add(e, "elf", x, lengths, {"loader": f"measure_b4.load_elf('{e}')"})
        chans, src = b3_channels("bench")
        for c in B3_CHANNELS:
            add(f"b3-{c}", "b3", *chans[c], src)
        for e in ELF_DEV:
            x, lengths, _ = m.load_elf(e)
            add(e, "elf-dev", x, lengths, {"loader": f"measure_b4.load_elf('{e}')"})
        for w in ("pad-c", "pad-p"):
            x, lengths, _ = m.load_wave(w)
            add(w, "waveform-b4", x, lengths, {"loader": f"measure_b4.load_wave('{w}')", "file": m.WAVE[w],
                                               "note": "B4's unfiltered Paderborn input; development (section 3)"})
    elif which == "dev":
        with tempfile.TemporaryDirectory() as tmp:
            (cx, cl), (px, pl), src = paderborn_b6(dev_paderborn_root(tmp))
        src["note"] = "harness test: B1's loader on B3's development-set files only"
        add("pad-c-b6", "waveform", cx, cl, src)
        add("pad-p-b6", "waveform", px, pl, src)
        femto = DATA / "femto/extracted/Learning_set/Bearing1_1"
        x = csv_column([femto / f"acc_{i:05d}.csv" for i in range(1, 1639)], 4)
        add("femto-dev", "waveform", x, chunked(x), {"input": "FEMTO Bearing1_1 horizontal, files 1-1638"})
        xjtu = DATA / "xjtu-sy/extracted/XJTU-SY_Bearing_Datasets/35Hz12kN/Bearing1_1"
        x = csv_column([xjtu / f"{i}.csv" for i in range(1, 124)], 0)
        add("xjtu-dev", "waveform", x, chunked(x), {"input": "XJTU-SY Bearing1_1 horizontal, files 1-123"})
        for e in ELF_DEV:
            x, lengths, _ = m.load_elf(e)
            add(e, "elf", x, lengths, {"loader": f"measure_b4.load_elf('{e}')"})
        chans, src = b3_channels("dev")
        for c in B3_CHANNELS:
            add(f"b3-{c}", "b3", *chans[c], src)
    else:
        raise SystemExit(f"unknown set {which}")
    out = {"study": "B6 inputs (PROTOCOL.md section 3)", "set": which, "written": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "data": str(DATA), "chunk": CHUNK, "quantum": 1.0, "development_bearings_excluded": list(DEV_BEARINGS),
           "inputs": rows}
    (work / "inputs.json").write_text(json.dumps(out, indent=1) + "\n")
    print(json.dumps({"inputs": len(rows), "sets": {s: sum(r["set"] == s for r in rows)
                                                    for s in sorted({r["set"] for r in rows})}}))


if __name__ == "__main__":
    build(sys.argv[1], sys.argv[2])
