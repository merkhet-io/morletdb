"""Result types. All intervals are half-open sample ranges [start_sample, end_sample) of one recording."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

import numpy as np

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class Recording:
    """A stored recording: one uniformly sampled channel.

    `samples` is the number of samples stored so far. `start_ns` is the UTC time of sample 0 in integer
    nanoseconds since the Unix epoch, or None when the recording has no start time.
    """
    collection: str
    recording_id: str
    sample_rate_hz: float
    channel: str
    unit: str
    samples: int
    start_ns: int | None
    metadata: dict[str, Any] = field(repr=False)

    @property
    def start_time(self) -> datetime | None:
        """Start time as a UTC datetime (microsecond resolution; `start_ns` is exact)."""
        if self.start_ns is None:
            return None
        return _EPOCH + timedelta(microseconds=self.start_ns // 1000)

    @property
    def duration_s(self) -> float:
        return self.samples / self.sample_rate_hz


@dataclass(frozen=True)
class IntervalRef:
    """A stable reference to stored samples: recording and half-open sample range [start, end)."""
    collection: str
    recording_id: str
    start_sample: int
    end_sample: int

    @property
    def length(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True)
class Interval:
    """Original samples of an interval, read back bit for bit as float64."""
    ref: IntervalRef
    samples: np.ndarray = field(repr=False)
    recording: Recording = field(repr=False)

    def time_offsets_s(self) -> np.ndarray:
        """Seconds since the recording's sample 0 for each sample: (start_sample + i) / sample_rate_hz.
        float64, so the last digits are rounded; sample indices are the exact coordinates."""
        n = np.arange(self.ref.start_sample, self.ref.end_sample, dtype=np.float64)
        return n / self.recording.sample_rate_hz


@dataclass(frozen=True)
class Match:
    """One search result. `distance` is pg_morlet's envelope distance: about 0 (identical shape at some
    allowed time shift) to 2; smaller is more similar. It is not a probability or a confidence, and it is
    comparable only between results of the same window size."""
    rank: int
    ref: IntervalRef
    distance: float
    metadata: dict[str, Any] = field(repr=False)


@dataclass(frozen=True)
class SearchResult:
    """Matches ordered by (distance, recording_id, start_sample).

    `mode` is "exact" when every eligible stored window was scored, or "probes" when probe search was
    enabled for this window size: candidates then come from an approximate index (small galleries are still
    searched exhaustively), while the distances returned are always computed exactly.
    """
    query: IntervalRef
    matches: list[Match]
    mode: Literal["exact", "probes"]
    max_lag_fraction: float


@dataclass(frozen=True)
class IntervalStats:
    """Computed by the server from the stored samples: count and min/max exact; mean and RMS in float64
    with compensated summation."""
    sample_count: int
    min: float
    max: float
    mean: float
    rms: float


@dataclass(frozen=True)
class OverviewBucket:
    """One part of an interval overview: the half-open sample range it covers and its statistics, computed
    by the server like IntervalStats."""
    start_sample: int
    end_sample: int
    sample_count: int
    min: float
    max: float
    mean: float
    rms: float
