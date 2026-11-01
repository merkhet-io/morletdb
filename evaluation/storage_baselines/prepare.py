#!/usr/bin/env python3
"""B1 inputs (PROTOCOL.md): the Sleep-EDF slice and PTB-XL lead I as OUT/<id>.npz with `floats`,
`ints`, `lengths` (chunk sizes) and `quanta` (per chunk). The four P2 inputs come from
evaluation/plain_postgres/p2_dump.py and are converted by `measure.py`.

  .venv-paper/bin/python -I evaluation/storage_baselines/prepare.py data/sleep-edfx/1.0.0/sleep-cassette \
      data/ptb-xl/1.0.3/records100 data/b1-runs/inputs
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "sleep"))
import s1  # noqa: E402

CHUNK = 4096
SLEEP_NIGHTS, SLEEP_CHUNKS, SLEEP_START = 32, 32, 360_000
ECG_RECORDS, ECG_LEAD = 4194, "I"


def edf_digital(path, label):
    """EDF digital values d, the physical floats s1.read_signal derives from them, and the range."""
    raw = Path(path).read_bytes()
    h = s1.edf_header(raw)
    j = h["label"].index(label)
    rec = sum(h["n"])
    data = np.frombuffer(raw, dtype="<i2", offset=h["header_bytes"], count=rec * h["records"]).reshape(h["records"], rec)
    start = sum(h["n"][:j])
    d = data[:, start:start + h["n"][j]].astype(np.int64).ravel()
    x, _, _, _ = s1.read_signal(path, label)
    pmin, pmax, dmin, dmax = (float(h[k][j]) for k in ("pmin", "pmax", "dmin", "dmax"))
    return d, x, (pmin, pmax, dmin, dmax)


def sleep(root):
    fl, it, q, sym = [], [], [], []
    for psg in sorted(Path(root).glob("SC4*-PSG.edf"))[:SLEEP_NIGHTS]:
        d, x, (pmin, pmax, dmin, dmax) = edf_digital(psg, s1.CHANNEL)
        a, b = SLEEP_START, SLEEP_START + SLEEP_CHUNKS * CHUNK
        assert len(x) >= b, psg.name
        fl.append(x[a:b])
        it.append(d[a:b])
        q += [(pmax - pmin) / (dmax - dmin) / 2] * SLEEP_CHUNKS     # examples/sleep/load.py quantum()
        sym.append(pmin == -pmax)
    return np.concatenate(fl), np.concatenate(it), np.full(len(q), CHUNK), np.array(q), {"symmetric_nights": int(sum(sym)), "nights": len(sym)}


def wfdb_lead(hea, lead):
    lines = hea.read_text().split("\n")
    nsig, n = int(lines[0].split()[1]), int(lines[0].split()[3])
    sigs = [ln.split() for ln in lines[1:1 + nsig]]
    j = [s[-1] for s in sigs].index(lead)
    fmt, gain = sigs[j][1], sigs[j][2]
    assert fmt == "16" and gain.startswith("1000.0(0)/"), (hea.name, fmt, gain)   # gain 1000, baseline 0
    d = np.fromfile(hea.with_suffix(".dat"), dtype="<i2").reshape(n, nsig)[:, j].astype(np.int64)
    return d


def ecg(root):
    heas = sorted(Path(root).glob("*/*_lr.hea"), key=lambda p: int(p.name.split("_")[0]))[:ECG_RECORDS]
    ints = [wfdb_lead(h, ECG_LEAD) for h in heas]
    lengths = np.array([len(d) for d in ints])
    d = np.concatenate(ints)
    return d.astype(np.float64) / 1000.0, d, lengths, np.full(len(lengths), 0.001), {"records": len(heas)}


def main():
    sleep_root, ecg_root, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
    out.mkdir(parents=True, exist_ok=True)
    for name, (fl, it, lengths, quanta, info) in (("sleep", sleep(sleep_root)), ("ecg", ecg(ecg_root))):
        np.savez(out / f"{name}.npz", floats=fl, ints=it, lengths=lengths, quanta=quanta)
        print(name, len(fl), "samples", len(lengths), "chunks", info, flush=True)


if __name__ == "__main__":
    main()
