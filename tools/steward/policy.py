"""Decisions of the steward (docs/steward-design.md), as pure functions of what it observed: whether work may run,
how the disk stands, which job is next, how big its next batch is, and what the status says. No I/O here, so every
rule is tested without a database (test_policy.py)."""
from __future__ import annotations

from dataclasses import dataclass, field

NORMAL, ATTENTION, ACTION = "normal", "attention", "action_required"
LEVELS = (NORMAL, ATTENTION, ACTION)

# Jobs in priority order: what keeps search and statistics complete first, checks after.
JOBS = ("envelopes", "chunk_summaries", "rollups", "context_values", "verify", "analyze")


@dataclass(frozen=True)
class Config:
    tick_s: float = 30.0
    budget_s: float = 2.0             # database time per batch
    load_per_cpu: float = 0.7         # jobs run while the 1-minute load is below this times the CPUs
    reserve_fraction: float = 0.05    # disk kept free: the larger of this share and reserve_bytes
    reserve_bytes: int = 2 << 30
    attention_days: float = 14.0
    pause_days: float = 3.0
    drop_days: float = 1.0
    verify_every_days: float = 7.0
    breaker_failures: int = 3


@dataclass(frozen=True)
class Disk:
    """Filesystem holding the data directory, and the database's growth (bytes per day, None when not yet known)."""
    total_bytes: int | None
    free_bytes: int | None
    growth_per_day: float | None


@dataclass(frozen=True)
class DiskState:
    level: str
    days_left: float | None           # None: not growing, or unknown
    pause: bool                       # builders and probe work pause
    drop_derived: bool                # rebuildable derived data may be dropped (only if a policy allows it)
    hold_ingest: bool                 # new appends must stop
    unknown: bool
    cause: str


def reserve(cfg: Config, total: int) -> int:
    return max(int(cfg.reserve_fraction * total), cfg.reserve_bytes)


def disk_state(cfg: Config, d: Disk) -> DiskState:
    """The disk ladder: warn in time remaining, pause, drop rebuildable data (by policy), hold ingest."""
    if d.total_bytes is None or d.free_bytes is None or d.total_bytes <= 0 or d.free_bytes < 0:
        return DiskState(ACTION, None, True, False, False, True,
                         "the free disk space cannot be read, so automatic work is paused")
    usable = d.free_bytes - reserve(cfg, d.total_bytes)
    if usable <= 0:
        return DiskState(ACTION, 0.0, True, True, True, False,
                         "the disk reserve is reached: new recordings are refused until space is freed")
    if not d.growth_per_day or d.growth_per_day <= 0:
        return DiskState(NORMAL, None, False, False, False, False, "the database is not growing")
    days = usable / d.growth_per_day
    cause = f"disk full in {fmt_days(days)} at the current rate"
    if days <= cfg.drop_days:
        return DiskState(ACTION, days, True, True, False, False, cause)
    if days <= cfg.pause_days:
        return DiskState(ACTION, days, True, False, False, False, cause)
    if days <= cfg.attention_days:
        return DiskState(ATTENTION, days, False, False, False, False, cause)
    return DiskState(NORMAL, days, False, False, False, False, cause)


def fmt_days(days: float) -> str:
    if days < 1:
        return f"{max(1, round(days * 24))} h"
    return f"{days:.0f} days" if days >= 2 else f"{days:.1f} days"


def growth_per_day(samples: list[tuple[float, int]], min_span_s: float = 3600) -> float | None:
    """Bytes per day from (unix time, database bytes) samples: the slope between the oldest and newest sample over
    at least min_span_s. None until enough history exists. Shrinking (vacuum, removal) counts as no growth."""
    if len(samples) < 2:
        return None
    s = sorted(samples)
    (t0, b0), (t1, b1) = s[0], s[-1]
    if t1 - t0 < min_span_s:
        return None
    return max(0.0, (b1 - b0) / (t1 - t0) * 86400)


def may_work(cfg: Config, load1: float, cpus: int, disk: DiskState) -> tuple[bool, str]:
    """Whether a batch may run now, and why not."""
    if disk.unknown:
        return False, "disk state unknown"
    if disk.pause:
        return False, "disk pressure"
    if load1 >= cfg.load_per_cpu * max(1, cpus):
        return False, f"load {load1:.2f} on {cpus} CPUs"
    return True, ""


@dataclass
class Breaker:
    failures: int = 0
    open: bool = False
    last_error: str = ""

    def record(self, cfg: Config, ok: bool, error: str = "") -> None:
        if ok:
            self.failures, self.last_error = 0, ""
            return
        self.failures += 1
        self.last_error = error
        if self.failures >= cfg.breaker_failures:
            self.open = True


def next_job(backlog: dict[str, bool], breakers: dict[str, Breaker], verify_due: bool) -> str | None:
    """The highest-priority job with work and a closed breaker. verify runs when due; analyze when it has work."""
    for job in JOBS:
        if breakers.get(job, Breaker()).open:
            continue
        if job == "verify":
            if verify_due:
                return job
            continue
        if backlog.get(job):
            return job
    return None


def next_batch(cfg: Config, batch: int, elapsed_s: float, lo: int = 1, hi: int = 100000) -> int:
    """Batch size for the next call: halve after a batch over budget, grow by half while well under it."""
    if elapsed_s > cfg.budget_s:
        return max(lo, batch // 2)
    if elapsed_s < cfg.budget_s / 4:
        return min(hi, batch + max(1, batch // 2))
    return batch


@dataclass(frozen=True)
class Item:
    """One status line for people (ISA-101 / ISA-18.2 style): cause, consequence, one action and who takes it."""
    key: str
    level: str
    cause: str
    consequence: str
    action: str
    who: str


@dataclass
class Observed:
    disk: DiskState
    breakers: dict[str, Breaker] = field(default_factory=dict)
    verify_problems: dict[str, int] = field(default_factory=dict)   # collection -> recordings with problems
    verify_age_days: dict[str, float | None] = field(default_factory=dict)
    no_privileges: list[str] = field(default_factory=list)          # collections the steward cannot maintain


def status_items(cfg: Config, o: Observed) -> list[Item]:
    items: list[Item] = []
    d = o.disk
    if d.unknown:
        items.append(Item("disk", ACTION, d.cause, "upkeep stops; new recordings may fail when the disk fills",
                          "check that the data volume is mounted and readable by the steward", "controls engineer"))
    elif d.hold_ingest:
        items.append(Item("disk", ACTION, d.cause, "recording has stopped; collectors buffer what they can",
                          "free space: approve a retention plan or add storage", "data owner"))
    elif d.level != NORMAL:
        conseq = "upkeep is paused; search and statistics fall behind" if d.pause else "recording stops when it is full"
        items.append(Item("disk", d.level, d.cause, conseq,
                          "approve a retention plan or add storage", "data owner"))
    for job, b in sorted(o.breakers.items()):
        if b.open:
            items.append(Item(f"job:{job}", ACTION, f"{job} failed {b.failures} times: {b.last_error[:200]}",
                              f"{job} is stopped; its backlog grows",
                              f"fix the cause, then reset it: morlet-steward reset {job}", "controls engineer"))
    for c, n in sorted(o.verify_problems.items()):
        if n:
            items.append(Item(f"verify:{c}", ACTION, f"verification found problems in {n} recordings of {c}",
                              "stored samples or derived data may be damaged",
                              f"review morlet_verify('{c}') and restore from a backup if chunks are damaged",
                              "data owner"))
    for c, age in sorted(o.verify_age_days.items()):
        if age is None or age > 2 * cfg.verify_every_days:
            when = "never" if age is None else f"{age:.0f} days ago"
            items.append(Item(f"verify-age:{c}", ATTENTION, f"{c} was last verified {when}",
                              "damage would go unnoticed", "keep the steward running; verification is automatic",
                              "controls engineer"))
    for c in sorted(o.no_privileges):
        items.append(Item(f"privileges:{c}", ATTENTION, f"the steward has no privileges on {c}",
                          f"{c} is not maintained", f"run: morlet-steward grant (as the database owner)",
                          "controls engineer"))
    return items


def overall(items: list[Item]) -> str:
    return max((i.level for i in items), key=LEVELS.index, default=NORMAL)
