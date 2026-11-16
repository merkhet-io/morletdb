#!/usr/bin/env python3
"""Study B9 development inputs (PROTOCOL.md section 5, gates 6 and 7). Standard library only. DEVELOPMENT DATA.

  python3 -I dev_inputs.py OUT_DIR [KA09.f64]

Writes OUT_DIR/<name>.bin as the codec bench example's chunk file (per chunk: u32 LE sample count, f64 LE quantum
1.0, the samples as f64 LE; 4,096-sample chunks) and OUT_DIR/dev_inputs.json (samples, chunks, SHA-256 of samples
and chunk file, provenance). The synthetic inputs are generated here exactly as #394's development generated them
(the same code, seeds and arguments; their SHA-256 are checked against the files development used):

- syn_current, syn_resonance: pure formulas (sines and Gaussian noise, random.seed(394)), 2^20 samples each on a
  0.001 grid; no data file is read.
- apg_wide: scratchpad/prof394/gen_grid.py of #394's development, run as
  `gen_grid.py apg_wide.f64 98304 98.00005 0.0003 5 300 0.9 14`: a stand-in with an Elf-style decimal series'
  properties as B6 published them (a non-decimal resolution 0.0003 printed with 5 decimals, about 98,000 samples,
  values near 98), generated synthetically; the Air-pressure series itself was never read.
- syn_big: the size check of gate 6, 26,744,663 samples (B6's B3 64 kHz channels' sample count) as 104
  acquisitions, each starting a new chunk: a 50 Hz phase-current-like signal at 64 kHz with harmonics and noise on a
  0.001 grid (random.seed(9)); no data file is read.
- ka09 (if given): KA09 phase_current_1 of B3's development set (N15_M07_F04_KA09_19), a development input.
"""
import hashlib
import json
import math
import random
import struct
import sys
from pathlib import Path

CHUNK = 4096
WANT = {  # SHA-256 of the raw float64 files #394's development used
    "syn_current": "e75a5626217b8252ea93f4cd365a20ec7c20fc4db106156774bf64e73808f4a0",
    "syn_resonance": "11aaf9660cdb691e6ea7ef8ed5562910ff4133e6f27e9a362c1452ee25dec143",
    "apg_wide": "4bdaa14b37e2ada7d76511391f5a774c6591ef2954a2d6c4e084c0e17188b0cc",
    "ka09": "caf5384e220d78cd1dca098194188eebd1d057d7273bb9538b993dc4d7663ee2",
}


def syn_current_resonance():
    random.seed(394)
    n, q = 1 << 20, 0.001
    cur = []
    for i in range(n):
        t = i / 64000
        v = 3.0 * math.sin(2 * math.pi * 50 * t) + 0.4 * math.sin(2 * math.pi * 250 * t + 0.3) + \
            0.15 * math.sin(2 * math.pi * 350 * t) + random.gauss(0, 0.02)
        cur.append(round(v / q) * q)
    res = []
    for i in range(n):
        v = 1.5 * math.sin(0.0731 * i) + 0.8 * math.sin(0.4127 * i + 1) + 0.3 * math.sin(1.37 * i) + \
            random.gauss(0, 0.05)
        res.append(round(v / q) * q)
    return cur, res


def gen_grid(n, origin, q, dec, acc_sd, damp, seed):
    """scratchpad/prof394/gen_grid.py, unchanged: a sensor with a non-decimal resolution q, printed with `dec`
    decimals and parsed back, as a CSV loader would; index walk: a smooth AR(2)-like process in units of q."""
    r = random.Random(seed)
    v, k, out = 0.0, 0.0, []
    for _ in range(n):
        v = damp * v + r.gauss(0, acc_sd)
        k = 0.9995 * k + v
        out.append(float(f"{origin + round(k) * q:.{dec}f}"))
    return out


def syn_big():
    n, acq = 26_744_663, 104
    base = n // acq
    lengths = [base] * (acq - 1) + [n - base * (acq - 1)]
    r = random.Random(9)
    xs, q, i = [], 0.001, 0
    for a, m in enumerate(lengths):
        amp = 2.0 + 0.01 * a
        for _ in range(m):
            t = i / 64000
            v = amp * math.sin(2 * math.pi * 50 * t) + 0.3 * math.sin(2 * math.pi * 150 * t + 0.2) + \
                0.1 * math.sin(2 * math.pi * 1700 * t) + r.gauss(0, 0.03)
            xs.append(round(v / q) * q)
            i += 1
    return xs, lengths


def raw_sha(xs):
    return hashlib.sha256(struct.pack(f"<{len(xs)}d", *xs)).hexdigest()


def write(out, name, xs, acquisitions, source, rows):
    h = hashlib.sha256()
    chunks = 0
    with open(out / f"{name}.bin", "wb") as f:
        pos = 0
        for m in acquisitions:
            for s in range(0, m, CHUNK):
                c = xs[pos + s:pos + min(m, s + CHUNK)]
                b = struct.pack("<I", len(c)) + struct.pack("<d", 1.0) + struct.pack(f"<{len(c)}d", *c)
                f.write(b)
                h.update(b)
                chunks += 1
            pos += m
    rows.append({"input": name, "samples": len(xs), "chunks": chunks, "sha256_samples": raw_sha(xs),
                 "chunk_file": f"{name}.bin", "chunk_file_sha256": h.hexdigest(), "source": source})
    print(json.dumps({k: rows[-1][k] for k in ("input", "samples", "chunks")}), flush=True)


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    rows = []
    cur, res = syn_current_resonance()
    apg = gen_grid(98304, 98.00005, 0.0003, 5, 300, 0.9, 14)
    for name, xs in (("syn_current", cur), ("syn_resonance", res), ("apg_wide", apg)):
        assert raw_sha(xs) == WANT[name], f"{name}: not the development file"
    write(out, "syn_current", cur, [len(cur)], "synthetic: sines + Gaussian noise, random.seed(394), 0.001 grid", rows)
    write(out, "syn_resonance", res, [len(res)], "synthetic: sines + Gaussian noise, random.seed(394), 0.001 grid",
          rows)
    write(out, "apg_wide", apg, [len(apg)], "synthetic: gen_grid.py apg_wide.f64 98304 98.00005 0.0003 5 300 0.9 14 "
          "(an Elf-style decimal series' properties as B6 published them; no data read)", rows)
    if len(sys.argv) > 2:
        b = Path(sys.argv[2]).read_bytes()
        ka = list(struct.unpack(f"<{len(b) // 8}d", b))
        assert raw_sha(ka) == WANT["ka09"], "ka09: not the development file"
        write(out, "ka09", ka, [len(ka)], "B3 development set, N15_M07_F04_KA09_19 phase_current_1", rows)
    big, lengths = syn_big()
    write(out, "syn_big", big, lengths, "synthetic: 104 acquisitions, 50 Hz at 64 kHz + harmonics + Gaussian noise, "
          "random.seed(9), 0.001 grid; no data read", rows)
    (out / "dev_inputs.json").write_text(json.dumps({"study": "B9 development inputs (DEVELOPMENT DATA)",
                                                     "chunk": CHUNK, "quantum": 1.0, "inputs": rows}, indent=1) + "\n")


if __name__ == "__main__":
    main()
