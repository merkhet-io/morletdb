"""The morlet-mqtt/1 payload contract (README.md) and the deterministic test signal the publisher sends."""
from datetime import datetime, timezone

import numpy as np

SCHEMA = "morlet-mqtt/1"
SAMPLE_KEYS = {"schema", "acquisition", "channel", "unit", "sample_rate_hz", "seq", "start_sample", "start_time",
               "samples"}
CONTEXT_KEYS = {"schema", "acquisition", "key", "value", "status", "source_time"}


def check_samples(p):
    """A sample batch, or ValueError with the reason."""
    if p.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    missing = SAMPLE_KEYS - set(p) - {"start_time"}
    if missing:
        raise ValueError(f"missing {sorted(missing)}")
    if not (isinstance(p["acquisition"], str) and p["acquisition"] and isinstance(p["channel"], str) and p["channel"]):
        raise ValueError("acquisition and channel must be nonempty strings")
    if not (isinstance(p["start_sample"], int) and p["start_sample"] >= 0 and isinstance(p["seq"], int)):
        raise ValueError("start_sample must be a nonnegative integer and seq an integer")
    rate = p["sample_rate_hz"]
    if not (isinstance(rate, (int, float)) and rate > 0):
        raise ValueError("sample_rate_hz must be positive")
    s = p["samples"]
    if not (isinstance(s, list) and 1 <= len(s) <= 32768 and all(isinstance(v, (int, float)) for v in s)):
        raise ValueError("samples must be a list of 1..32768 numbers")
    return p


def check_context(p):
    if p.get("schema") != SCHEMA:
        raise ValueError(f"schema must be {SCHEMA}")
    missing = CONTEXT_KEYS - set(p) - {"status"}
    if missing:
        raise ValueError(f"missing {sorted(missing)}")
    datetime.fromisoformat(p["source_time"].replace("Z", "+00:00"))
    return p


def signal(start, n, rate=5120.0):
    """Test samples start .. start+n-1 on a 0.001 grid: a tone with a burst every 4096 samples, seeded noise."""
    k = np.arange(start, start + n)
    x = 0.3 * np.sin(2 * np.pi * 120 * k / rate) + 0.5 * np.exp(-(((k % 4096) - 2048) / 60.0) ** 2) * np.sin(k * 0.9)
    x += np.random.default_rng(start).normal(0, 0.01, n)
    return np.round(x, 3)


def utc(t):
    return t.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
