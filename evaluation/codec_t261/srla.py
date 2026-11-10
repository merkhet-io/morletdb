#!/usr/bin/env python3
"""Codec comparison T1, third arm (development only): SRLA (lossless LPC audio codec) on the same integer channels.

  .venv-b3/bin/python -I evaluation/codec_t261/srla.py SRLA_BIN WORK_DIR evaluation/codec_t261/results/dev_srla.json

SRLA_BIN is the srla_codec tool built from the owner's clone (see README.md); WORK_DIR is prepare.py's output.
Each channel's integer indices k are written as a mono 16-bit PCM WAV (every channel here fits in 16 bits; a
wider one would be excluded) at its sample rate, then encoded and decoded with explicit arguments in WORK_DIR/srla/.
The decoded WAV's samples are compared exactly with k. Settings: default (-m 4: LPC order up to 64), max
(-m 6: order up to 255), and max+ (-m 6 -V 2 -L 8 -P 3: deeper variable-block search, longer lookahead,
long-term prediction). Speed: wall time of the tool process (file I/O and process start included), best of 3.
"""
import json
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

import numpy as np

NICE = ["nice", "-n", "10"]
REPS = 3
SETTINGS = {"default": [], "max": ["-m", "6"], "max+": ["-m", "6", "-V", "2", "-L", "8", "-P", "3"]}


def run(cmd):
    t = time.perf_counter()
    r = subprocess.run(NICE + cmd, capture_output=True, text=True)
    dt = time.perf_counter() - t
    if r.returncode != 0:
        raise RuntimeError(r.stdout[-300:] + r.stderr[-300:])
    return dt


def main():
    binp, work, outp = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), Path(sys.argv[3])
    spec = json.loads((work / "inputs.json").read_text())
    d = work / "srla"
    d.mkdir(exist_ok=True)
    rows, excluded = [], []
    for inp in spec["inputs"]:
        name, n, bits = inp["name"], inp["samples"], inp["bits"]
        if bits > 16:
            excluded.append({"name": name, "why": f"needs {bits} bits; this harness writes 16-bit WAV"})
            continue
        k = np.fromfile(work / f"{name}.k", dtype="<i2")
        wav = d / f"{name}.wav"
        with wave.open(str(wav), "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(int(inp["rate"]))
            w.writeframes(k.tobytes())
        b = wav.read_bytes()
        if len(b) < 34 * 1024:
            # The tool's WAV reader fails to open files shorter than its 32 KiB read buffer (seen on the
            # 16,001-sample force channels); a trailing 'junk' chunk, which it skips, works around that.
            pad = b"junk" + struct.pack("<I", 4096) + bytes(4096)
            wav.write_bytes(b[:4] + struct.pack("<I", len(b) - 8 + len(pad)) + b[8:] + pad)
        row = {"name": name, "samples": n, "bits": bits}
        for s, opts in SETTINGS.items():
            srl, dec = d / f"{name}.{s}.srl", d / f"{name}.{s}.dec.wav"
            te = min(run([str(binp), "-e", *opts, str(wav), str(srl)]) for _ in range(REPS))
            td = min(run([str(binp), "-d", str(srl), str(dec)]) for _ in range(REPS))
            with wave.open(str(dec), "rb") as w:
                y = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
            mism = int(np.count_nonzero(y != k)) if len(y) == len(k) else -1
            size = srl.stat().st_size
            row[s] = {"bytes": size, "bits_per_sample": 8.0 * size / n, "mismatched_samples": mism,
                      "decoded_equal": mism == 0, "enc_s": te, "dec_s": td, "enc_msps": n / te / 1e6,
                      "dec_msps": n / td / 1e6}
            srl.unlink()
            dec.unlink()
        wav.unlink()
        print(json.dumps({"name": name, **{s: round(row[s]["bits_per_sample"], 4) for s in SETTINGS},
                          "lossless": all(row[s]["decoded_equal"] for s in SETTINGS)}), flush=True)
        rows.append(row)
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps({"arm": "SRLA (owner's clone, commit 73cbb9a, codec version 18, format 10)",
                                "settings": SETTINGS, "excluded": excluded, "inputs": rows}, indent=1) + "\n")


if __name__ == "__main__":
    main()
