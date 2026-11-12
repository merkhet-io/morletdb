#!/usr/bin/env python3
"""IU4 H2: load the runs into the Pi's MorletDB through an ssh pipe (PROTOCOL.md, Latency).

  Mac:  pi_load4.py send NPZ_DIR MACHINE | ssh dana@PI 'MORLET_DSN=... ~/venv-x2/bin/python -I ~/iu4/pi_load4.py recv MACHINE'

send writes, per run in tool and run order, a JSON header line (machine, tool, run, wear, per-signal length, SHA-256 of
the float32 bytes) followed by the 9 signals as float32 little-endian (the source values are float32, so nothing
is lost). recv checks every signal's SHA-256, widens to float64 exactly and stores the run with iu4.store_run
(gallery envelopes for runs 1-10); after a tool's last run it labels the tool's life (iu4.label_life). No signal value
is printed.
"""
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from features import SIGNALS  # noqa: E402

TOOLS = {1: (1, 2, 3), 2: (4, 5, 6), 3: (7, 8, 9)}


def send(npz_dir, machine):
    import re
    out = sys.stdout.buffer
    for tool in TOOLS[machine]:
        runs = sorted((int(re.match(r"M\d+T\d+R(\d+)\.npz", p.name).group(1)), p)
                      for p in Path(npz_dir).glob(f"M{machine}T{tool}R*.npz"))
        for i, (run, p) in enumerate(runs):
            z = np.load(p)
            blobs = [z[s].astype("<f4").tobytes() for s in SIGNALS]           # exact: the source was float32
            labels = json.loads(str(z["labels"]))
            fields = json.loads(str(z["fields"])) if "fields" in z.files else {}
            head = {"machine": machine, "tool": tool, "run": run, "wear": labels["wear"], "last": i == len(runs) - 1,
                    "lengths": [len(b) // 4 for b in blobs], "sha256": [hashlib.sha256(b).hexdigest() for b in blobs],
                    "fields": fields}
            out.write((json.dumps(head) + "\n").encode())
            for b in blobs:
                out.write(b)
    out.flush()


def recv(machine):
    import iu4
    inp = sys.stdin.buffer
    t0, n, wear = time.time(), 0, {}
    with iu4.connect() as c:
        while True:
            line = inp.readline()
            if not line:
                break
            h = json.loads(line)
            arrays = {}
            for sig, length, sha in zip(SIGNALS, h["lengths"], h["sha256"]):
                b = inp.read(4 * length)
                if hashlib.sha256(b).hexdigest() != sha:
                    raise ValueError(f"m{h['machine']}t{h['tool']}r{h['run']} {sig}: checksum mismatch")
                arrays[sig] = np.frombuffer(b, dtype="<f4").astype(np.float64)
            iu4.store_run(c, h["machine"], h["tool"], h["run"], arrays, gallery=h["run"] <= iu4.N_BASELINE,
                          fields=h["fields"])
            wear.setdefault(h["tool"], {})[h["run"]] = h["wear"]
            n += 1
            if h["last"]:
                iu4.label_life(c, h["machine"], h["tool"], wear[h["tool"]])
                print(f"m{h['machine']}t{h['tool']} stored and labelled: {len(wear[h['tool']])} runs, "
                      f"{time.time() - t0:.0f}s", flush=True)
            elif n % 100 == 0:
                print(f"{n} runs {time.time() - t0:.0f}s", flush=True)
    print(f"machine {machine}: {n} runs in {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "send":
        send(sys.argv[2], int(sys.argv[3]))
    else:
        recv(int(sys.argv[2]))
