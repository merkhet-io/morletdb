# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""A deterministic synthetic machine for the WinCC OA replay: a test rig like examples/machine-demo (a shaft on
bearings with a housing accelerometer), but generated, so every sample, operating context and event is known.

- Channels: `vib` (acceleration, 8,192 Hz, the DAQ clock, offset 0) and `current` (motor current, 1,000 Hz, a
  separate logger on the wall clock, offset +2 ms with 1 ms uncertainty). Each keeps its native rate.
- Operating contexts: `speed` (rpm: 1200 or 1800, the shaft harmonics follow it) and `load` (low or high: vibration
  level and current follow it), changing at known times.
- Events with known synthetic labels, deliberately repeated: `impact` (a train of decaying 2.1 kHz impulses, the same
  template every time) and `rub` (a slowly modulated 600 Hz tone). Labels annotate intervals; they are not
  diagnoses, and the replay never presents a match's label as the cause of anything.
- Values lie on a 2^-12 grid (like ADC counts), so they are exactly representable and compress well.

Nothing here is random at run time: every recording comes from a fixed seed.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

VIB_RATE = 8192
CURRENT_RATE = 1000
CURRENT_OFFSET_NS = 2_000_000
CURRENT_UNCERTAINTY_NS = 1_000_000
STEP = 2.0 ** -12
WINDOW = 4096          # search window of vib (0.5 s)
STRIDE = WINDOW // 2   # the SDK's default stride
MACHINE = "rig-1"


@dataclass(frozen=True)
class Context:
    t_start_ns: int        # acquisition time
    t_end_ns: int
    speed: int             # rpm
    load: str              # "low" | "high"


@dataclass(frozen=True)
class Event:
    label: str             # "impact" | "rub"
    vib_start: int         # sample of vib where the template starts
    length: int


@dataclass(frozen=True)
class Plan:
    acquisition_id: str
    seed: int
    seconds: int
    contexts: tuple[Context, ...]
    events: tuple[Event, ...]
    metadata: dict = field(default_factory=dict, compare=False)

    @property
    def vib_samples(self) -> int:
        return self.seconds * VIB_RATE

    @property
    def current_samples(self) -> int:
        return self.seconds * CURRENT_RATE

    @property
    def end_ns(self) -> int:
        """End of the acquisition in acquisition time: the end of the vib channel."""
        return self.seconds * 1_000_000_000


def _impact_template() -> np.ndarray:
    n = 3072
    t = np.arange(n) / VIB_RATE
    x = np.zeros(n)
    for k in range(36):                                  # 36 impulses every 76 samples (about 108 Hz)
        t0 = k * 76 / VIB_RATE
        tt = t - t0
        on = tt >= 0
        x[on] += 1.2 * (1 - k / 48) * np.exp(-tt[on] * 300) * np.sin(2 * np.pi * 2100 * tt[on])
    return x


def _rub_template() -> np.ndarray:
    n = 3584
    t = np.arange(n) / VIB_RATE
    return 0.9 * (0.5 - 0.5 * np.cos(2 * np.pi * t * VIB_RATE / n)) * np.sin(2 * np.pi * 600 * t) \
        * (1 + 0.6 * np.sin(2 * np.pi * 7 * t))


TEMPLATES = {"impact": _impact_template(), "rub": _rub_template()}


def at_grid(k: int, jitter: int = 0) -> int:
    """A vib sample near the k-th window start of the default stride (within the search's lag tolerance)."""
    return k * STRIDE + jitter


def _ctx(seconds_list: list[tuple[float, float, int, str]]) -> tuple[Context, ...]:
    return tuple(Context(int(Fraction(a) * 1_000_000_000), int(Fraction(b) * 1_000_000_000), s, ld)
                 for a, b, s, ld in seconds_list)


def history() -> list[Plan]:
    """Four historical acquisitions, 24 s each: two impacts and one rub each, in different operating contexts."""
    plans = []
    layouts = [
        ([(0, 10, 1200, "low"), (10, 24, 1800, "high")], [("impact", 9, 40), ("rub", 52, 0), ("impact", 70, -30)]),
        ([(0, 8, 1800, "low"), (8, 24, 1200, "high")], [("rub", 11, 20), ("impact", 30, 0), ("impact", 88, 60)]),
        ([(0, 12, 1800, "high"), (12, 24, 1800, "low")], [("impact", 20, -50), ("impact", 61, 10), ("rub", 80, 0)]),
        ([(0, 6, 1200, "high"), (6, 24, 1200, "low")], [("impact", 14, 0), ("rub", 40, -40), ("impact", 77, 25)]),
    ]
    for i, (ctx, evs) in enumerate(layouts):
        events = tuple(Event(lb, at_grid(k, j), len(TEMPLATES[lb])) for lb, k, j in evs)
        plans.append(Plan(f"hist-{i + 1:02d}", 100 + i, 24, _ctx(ctx), events))
    return plans


def current() -> Plan:
    """The current acquisition, 16 s: one impact (the investigation's source) and one rub, three context changes."""
    ctx = _ctx([(0, 6.5, 1800, "high"), (6.5, 11.25, 1200, "high"), (11.25, 16, 1200, "low")])
    events = (Event("impact", at_grid(36, 0), len(TEMPLATES["impact"])),
              Event("rub", at_grid(48, 12), len(TEMPLATES["rub"])))
    return Plan("current-01", 7, 16, ctx, events)


def _context_at(plan: Plan, t_ns: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    speed = np.zeros(t_ns.size)
    high = np.zeros(t_ns.size, dtype=bool)
    for c in plan.contexts:
        on = (t_ns >= c.t_start_ns) & (t_ns < c.t_end_ns)
        speed[on] = c.speed
        high[on] = c.load == "high"
    return speed, high


def synthesize(plan: Plan) -> dict[str, np.ndarray]:
    """The samples of every channel, float64 on the 2^-12 grid."""
    rng = np.random.default_rng(plan.seed)
    n = plan.vib_samples
    t_ns = np.arange(n, dtype=np.int64) * 1_000_000_000 // VIB_RATE
    speed, high = _context_at(plan, t_ns)
    f = speed / 60.0
    phase = 2 * np.pi * np.cumsum(f) / VIB_RATE
    level = np.where(high, 0.12, 0.06)
    vib = level * (np.sin(phase) + 0.4 * np.sin(2 * phase + 0.3) + 0.15 * np.sin(3 * phase + 1.1))
    vib += 0.02 * rng.standard_normal(n)
    for e in plan.events:
        tpl = TEMPLATES[e.label]
        vib[e.vib_start:e.vib_start + tpl.size] += tpl[: max(0, min(tpl.size, n - e.vib_start))]
    m = plan.current_samples
    tc = CURRENT_OFFSET_NS + np.arange(m, dtype=np.int64) * 1_000_000_000 // CURRENT_RATE
    _, chigh = _context_at(plan, tc)
    cur = np.where(chigh, 7.0, 3.0) + 0.05 * rng.standard_normal(m)
    return {"vib": np.round(vib / STEP) * STEP, "current": np.round(cur / STEP) * STEP}


CHANNELS = {
    # role: (rate, channel name, unit, offset_ns, clock_source, offset_uncertainty_ns)
    "current": (CURRENT_RATE, "motor_current", "A", CURRENT_OFFSET_NS, "wallclock", CURRENT_UNCERTAINTY_NS),
    "vib": (VIB_RATE, "housing_accel", "g", 0, "daq", None),
}
