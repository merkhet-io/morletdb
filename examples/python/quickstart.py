#!/usr/bin/env python3
"""MorletDB Python SDK quickstart: ingest two waveforms, read an exact interval, search similar intervals.

    DATABASE_URL=postgresql://morlet:...@127.0.0.1:54329/morlet python examples/python/quickstart.py [plot.png]

No download: the waveforms are generated deterministically. Two machines, two minutes each at 8 kHz, with
"impacts" (short 900 Hz bursts) and "electrical transients" (slower 120 Hz bursts) at known times. The
search starts from one impact on machine A and should find the other impacts, on both machines, before
the electrical transients. Rerunning is safe: identical ingests are no-ops.
"""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np

import morletdb

RATE = 8000                      # samples per second
SECONDS = 120
STEP = 1 / 1024                  # the value grid of a simulated 24-bit ADC (the server detects it)
COLLECTION = "public.quickstart"
START = datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc)
EVENTS = {                       # seconds after the start of each recording
    "machine-a": {"impact": [12.0, 47.5, 90.25], "electrical": [30.0, 75.0]},
    "machine-b": {"impact": [20.0, 70.0], "electrical": [55.0]},
}


def waveform(name, seed):
    rng = np.random.default_rng(seed)
    x = 0.05 * rng.standard_normal(RATE * SECONDS)
    t = np.arange(4096) / RATE
    shapes = {"impact": np.exp(-40 * t) * np.sin(2 * np.pi * 900 * t),
              "electrical": 0.8 * np.exp(-8 * t) * np.sin(2 * np.pi * 120 * t)}
    for kind, times in EVENTS[name].items():
        for s in times:
            i = int(s * RATE)
            x[i:i + 4096] += shapes[kind] * (0.9 + 0.2 * rng.random())
    return np.round(x / STEP) * STEP


def label(rec_id, start_sample):
    """Which generated event (if any) a sample interval overlaps, for the printout."""
    for kind, times in EVENTS[rec_id].items():
        for s in times:
            if abs(start_sample - s * RATE) < 4096:
                return f"{kind} at {s:g} s"
    return "background"


def main():
    with morletdb.Client.connect(os.environ.get("DATABASE_URL", "")) as db:
        # 1. A searchable collection and two recordings with timing and metadata.
        db.create_collection(COLLECTION)
        for seed, name in enumerate(EVENTS):
            # Search windows of 4,096 samples start every 512 samples: search tolerates shifts of about
            # ±300 samples, so an event at any offset lies close enough to a window start to be found.
            # (The default stride, 2,048, stores 4x fewer windows but misses events between window starts.)
            rec = db.ingest(COLLECTION, name, waveform(name, seed), sample_rate_hz=RATE, channel="accel-x",
                            unit="g", start_time=START, metadata={"asset": name, "site": "demo"},
                            search_window=4096, search_stride=512)
            print(f"stored {rec.recording_id}: {rec.samples:,} samples at {rec.sample_rate_hz:g} Hz, "
                  f"sample 0 at {rec.start_time.isoformat()}")
        db.connection.commit()

        # 2. An exact interval by time: the first impact on machine A, 0.5 s of it.
        a = db.recording(COLLECTION, "machine-a")
        t0 = a.start_ns + 12 * 10 ** 9
        start, end = db.samples_for_time(a, t0, t0 + 500_000_000)
        iv = db.read(COLLECTION, "machine-a", start, end)
        print(f"\nread samples [{start}, {end}) of machine-a: {iv.samples.size} original values, "
              f"peak {np.abs(iv.samples).max():.3f} g")

        # 3. Similar intervals to a 4,096-sample window at that impact (the stored search window size).
        result = db.search(COLLECTION, "machine-a", start, top_k=6)
        print(f"\nsearch ({result.mode}): intervals most similar to machine-a [{start}, {start + 4096})")
        print("  rank  distance  recording   samples                time                  generated as")
        matches = []
        for m in result.matches:
            rec = db.recording(COLLECTION, m.ref.recording_id)
            when = rec.start_time + timedelta(seconds=m.ref.start_sample / rec.sample_rate_hz)
            print(f"  {m.rank:>4}  {m.distance:8.4f}  {m.ref.recording_id:<10}  "
                  f"[{m.ref.start_sample}, {m.ref.end_sample})  {when:%H:%M:%S.%f}  "
                  f"{label(m.ref.recording_id, m.ref.start_sample)}")
            matches.append(db.read_ref(m.ref))          # the original samples behind each match
        print("\nDistance is 1 minus the best correlation of the two intervals' envelopes over small time "
              "shifts:\n0 is the same shape, smaller is more similar; it is not a probability. Each result "
              "is a recording\nand a half-open sample range [start, end) that reads back the stored samples "
              "exactly.")
        query = db.read(COLLECTION, "machine-a", start, start + 4096)

    plot(query, matches[:3], sys.argv[1] if len(sys.argv) > 1 else "quickstart.png")


def plot(query, matches, path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("\n(install matplotlib to plot the query and its matches: pip install 'morletdb[plot]')")
        return
    fig, axes = plt.subplots(1 + len(matches), 1, figsize=(9, 1.8 * (1 + len(matches))), sharex=True)
    t_ms = np.arange(4096) / RATE * 1000
    for ax, iv, title in zip(axes, [query] + matches,
                             ["query"] + [f"match {i + 1}" for i in range(len(matches))]):
        ax.plot(t_ms, iv.samples, lw=0.8)
        ax.set_title(f"{title}: {iv.ref.recording_id} [{iv.ref.start_sample}, {iv.ref.end_sample})",
                     fontsize=9, loc="left")
        ax.set_ylabel(iv.recording.unit)
    axes[-1].set_xlabel("ms within the interval")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    print(f"\nplot: {path}")


if __name__ == "__main__":
    main()
