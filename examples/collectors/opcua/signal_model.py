"""The test server's waveform: block i is a deterministic function of i, so stored samples can be checked."""
from datetime import datetime, timedelta, timezone

import numpy as np

NAMESPACE = "urn:morletdb:example-machine"
RATE = 5120.0
BLOCK = 1024
UNIT = "g"
EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


def block(i):
    """Samples of block i on a 0.001 grid: a 160 Hz tone with a burst every 7th block, plus seeded noise."""
    n = np.arange(i * BLOCK, (i + 1) * BLOCK)
    x = 0.2 * np.sin(2 * np.pi * 160 * n / RATE)
    if i % 7 == 3:
        t = (n - (i * BLOCK + BLOCK // 2)) / RATE
        x += 0.8 * np.exp(-(t / 0.01) ** 2) * np.sin(2 * np.pi * 1800 * t)
    x += np.random.default_rng(i).normal(0, 0.02, BLOCK)
    return np.round(x, 3)


def block_start(i):
    return EPOCH + timedelta(seconds=i * BLOCK / RATE)


def block_at(t):
    """Index of the block containing time t."""
    return int((t - EPOCH).total_seconds() * RATE // BLOCK)
