"""Synthetic fixtures for tests only; never used by the real-data demonstration."""
import os
import sys
import uuid
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))

from morlet_seismic.sources import Chunk  # noqa: E402

T0 = 1_709_510_400 * 10**9  # 2024-03-04T00:00:00Z in ns
RATE = 100.0
REP = {"extractor": "morlet-energy-lowrate-v1", "window_samples": 2048, "stride_samples": 512,
       "normalization": "zscore", "units": "counts"}
DETECTOR = {"version": "stalta-test", "filter": {"type": "bandpass", "freqmin": 2.0, "freqmax": 10.0,
                                                 "corners": 4, "zerophase": False},
            "method": "recursive_sta_lta", "sta_s": 1.0, "lta_s": 30.0, "on": 4.0, "off": 1.5,
            "min_duration_s": 1.0, "warmup_s": 120.0, "post_s": 60.0, "block_s": 600.0,
            "query_pre_s": 2.0}
META = {"SY.TEST..HHZ": {"network": "SY", "station": "TEST", "location": "", "channel": "HHZ",
                         "synthetic": "true"}}


def signal(n, seed=7, bursts=(), amplitude=2000.0):
    """Integer-valued noise with damped 6 Hz bursts at the given sample offsets."""
    rng = np.random.default_rng(seed)
    x = rng.normal(0, 50, n)
    t = np.arange(600) / RATE
    burst = amplitude * np.sin(2 * np.pi * 6 * t) * np.exp(-t / 1.5)
    for at in bursts:
        stop = min(n, at + len(burst))
        x[at:stop] += burst[:stop - at]
    return np.round(x).astype(np.int32)


def chunk(start_sample, samples, *, stream="SY.TEST..HHZ", t0=T0, shift_ns=0, sequence=0):
    return Chunk(stream, t0 + round(start_sample * 1e9 / RATE) + shift_ns, RATE,
                 np.asarray(samples), "D", "synthetic", sequence)


def split_chunks(x, size, start=0):
    return [chunk(start + i, x[i:i + size], sequence=i) for i in range(0, len(x), size)]


class TempDatabase:
    """Creates and drops an isolated database on the local MorletDB server."""

    def __init__(self):
        import psycopg
        self.admin = os.environ.get("MORLET_SEISMIC_TEST_DSN",
                                    "postgresql://morlet:morlet-demo-only@localhost:54339/morlet")
        self.name = f"seismic_test_{uuid.uuid4().hex[:10]}"
        with psycopg.connect(self.admin, autocommit=True, connect_timeout=3) as conn:
            conn.execute(f'CREATE DATABASE "{self.name}"')
        base, _, _ = self.admin.rpartition("/")
        self.dsn = f"{base}/{self.name}"

    def drop(self):
        import psycopg
        with psycopg.connect(self.admin, autocommit=True) as conn:
            conn.execute(f'DROP DATABASE IF EXISTS "{self.name}" WITH (FORCE)')


def database_available():
    try:
        TempDatabase().drop()
        return True
    except Exception:  # noqa: BLE001 - tests skip without a local server
        return False


def require_database():
    """Availability for skipUnless; with MORLET_SEISMIC_REQUIRE_DB=1 a missing server fails."""
    available = database_available()
    if not available and os.environ.get("MORLET_SEISMIC_REQUIRE_DB") == "1":
        raise RuntimeError("MORLET_SEISMIC_REQUIRE_DB=1 but no MorletDB server is reachable at "
                           + os.environ.get("MORLET_SEISMIC_TEST_DSN", "the default DSN"))
    return available


def require_features():
    """The Rust features adapter for temporal tests; required when MORLET_SEISMIC_REQUIRE_FEATURES=1."""
    from morlet_seismic import retrieval
    present = retrieval.FEATURES_BIN.exists()
    if not present and os.environ.get("MORLET_SEISMIC_REQUIRE_FEATURES") == "1":
        raise RuntimeError(f"MORLET_SEISMIC_REQUIRE_FEATURES=1 but {retrieval.FEATURES_BIN} is missing")
    return present
