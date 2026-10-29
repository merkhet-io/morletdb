#!/usr/bin/env python3
"""S1 (PROTOCOL.md): explainable sleep staging by retrieval of similar epochs.

  s1.py split SUMS OUT_JSON       subject split from the file names in SHA256SUMS.txt (reads no recording)
  s1.py features DATA OUT         epochs, labels and Morlet/FFT features for every night (pg_morlet for Morlet)
  s1.py check OUT                 product check: 20 random epochs recomputed in separate SQL calls
  s1.py select OUT                k and C on development subjects only (leave-subjects-out CV)
  s1.py evaluate OUT              reads held-out subjects once; writes OUT/s1.json

Seismic environment (numpy, scipy, psycopg), python -I. DSN from MORLET_DSN.
"""
import json
import os
import re
import sys
from pathlib import Path

import numpy as np

SEED = 7
RATE = 100.0
EPOCH = 3000                      # 30 s at 100 Hz
CHANNEL = "EEG Fpz-Cz"
EXTRACTOR = "morlet-energy-lowrate-v1"
STAGES = ("W", "N1", "N2", "N3", "REM")
MAP = {"W": 0, "1": 1, "2": 2, "3": 3, "4": 3, "R": 4}   # M and ? dropped
TRIM_EPOCHS = 60                  # 30 min either side of the sleep period
KS = (5, 10, 20, 40)
CS = (0.01, 0.1, 1.0, 10.0)
FOLDS = 5
RESAMPLES = 2000
DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_sleep")
NAME = re.compile(r"sleep-cassette/SC4(\d\d)(\d)E\w-PSG\.edf$")


# -- split ---------------------------------------------------------------------------------
def cmd_split(sums, out):
    subjects = set()
    for line in Path(sums).read_text().splitlines():
        m = NAME.search(line)
        if m:
            subjects.add(int(m.group(1)))
    subjects = sorted(subjects)
    order = [int(s) for s in np.random.default_rng(SEED).permutation(subjects)]
    n_dev = len(order) // 2
    split = {"protocol": "S1", "seed": SEED, "subjects": len(order),
             "development": order[:n_dev], "held_out": order[n_dev:]}
    Path(out).write_text(json.dumps(split, indent=1) + "\n")
    print(len(order), "subjects:", n_dev, "development,", len(order) - n_dev, "held-out")


if __name__ == "__main__":
    {"split": lambda: cmd_split(*sys.argv[2:4])}[sys.argv[1]]()
