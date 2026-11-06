"""Policy engine for episodic memory (docs/memory-design.md, phase 2).

The database applies moves (morlet_set_level); this module decides them. Its structure follows the compaction
design of RisingWave's Hummock storage engine (Apache-2.0, https://github.com/risingwavelabs/risingwave): selectors
decide *why* a run happens, pickers decide *what* moves, filters veto moves, and a task carries a run from a dry run
to resumable execution. The design is adopted; the code is our own.

Everything here is deterministic for a given input and seed, so policies can be compared in experiments (study F1).
The store is abstract: `PgStore` (store_pg.py) talks to the database, `MemoryStore` (tests) keeps state in memory.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Protocol, Sequence

POLICY_VERSION = "1"


@dataclass(frozen=True)
class Episode:
    episode_id: int
    recording_id: str
    start_sample: int
    end_sample: int
    level: int = 0
    context: dict = field(default_factory=dict)
    importance: float = 0.0
    exempt: bool = False
    age_s: float = 0.0            # seconds since the episode was recorded
    uses: int = 0                 # retrievals and citations

    @property
    def samples(self) -> int:
        return self.end_sample - self.start_sample


@dataclass(frozen=True)
class Move:
    episode_id: int
    level: int
    representative: Optional[int] = None
    distance: Optional[float] = None
    reason: dict = field(default_factory=dict)

    def as_json(self) -> dict:
        d = {"episode_id": self.episode_id, "level": self.level, "reason": self.reason}
        if self.representative is not None:
            d["representative"] = self.representative
        if self.distance is not None:
            d["distance"] = self.distance
        return d


class Store(Protocol):
    """What the engine needs from the memory: episodes, the size each level frees, and applying moves."""

    def episodes(self) -> list[Episode]: ...
    def bytes_at(self) -> int: ...                                             # bytes the episodes occupy now
    def freed(self, moves: Sequence[Move]) -> list[int]: ...                   # dry run: bytes each move frees
    def apply(self, run_id: int, moves: Sequence[Move]) -> list[int]: ...      # bytes each move freed
    def start_run(self, policy: str, params: dict, dry_run: bool) -> int: ...
    def finish_run(self, run_id: int, status: str, summary: dict) -> None: ...


# ---- filters: composable vetoes (Hummock's CompactionFilter counterpart) -------------------------------------

class Filter(Protocol):
    def veto(self, ep: Episode, target: int) -> Optional[str]: ...


class ExemptFilter:
    """Event-library episodes stay searchable: a policy never moves them (the library exists for recall). The
    database still allows an operator to move one to level 1 by hand; never below."""
    def veto(self, ep: Episode, target: int) -> Optional[str]:
        return "exempt" if ep.exempt else None


class MinAgeFilter:
    """Nothing younger than min_age_s moves (fresh data is often still under investigation)."""
    def __init__(self, min_age_s: float):
        self.min_age_s = min_age_s

    def veto(self, ep: Episode, target: int) -> Optional[str]:
        return "too young" if ep.age_s < self.min_age_s else None


class ProtectContextFilter:
    """Episodes whose context matches any of the given key/value pairs stay at or above max_level."""
    def __init__(self, protected: dict, max_level: int = 1):
        self.protected, self.max_level = protected, max_level

    def veto(self, ep: Episode, target: int) -> Optional[str]:
        hit = any(ep.context.get(k) == v for k, v in self.protected.items())
        return "protected context" if hit and target > self.max_level else None


def first_veto(filters: Iterable[Filter], ep: Episode, target: int) -> Optional[str]:
    for f in filters:
        why = f.veto(ep, target)
        if why:
            return why
    return None


# ---- scorers: the keep-value (higher = keep longer) -----------------------------------------------------------

Novelty = Callable[[Episode, Sequence[Episode]], float]   # distance to the nearest other kept episode, in [0, 1]


@dataclass
class KeepValue:
    """v = w_n·novelty + w_i·importance + w_u·use + w_c·context_rarity − w_a·age, every term in [0, 1].

    The weights and the version are part of the policy and are written to the ledger with every move."""
    w_novelty: float = 0.0
    w_importance: float = 0.0
    w_use: float = 0.0
    w_rarity: float = 0.0
    w_age: float = 0.0
    novelty: Optional[Novelty] = None
    name: str = "keep-value"

    def terms(self, ep: Episode, kept: Sequence[Episode], context_freq: dict, max_age: float, max_uses: int) -> dict:
        t = {"importance": ep.importance,
             "use": (math.log1p(ep.uses) / math.log1p(max_uses)) if max_uses else 0.0,
             "age": (ep.age_s / max_age) if max_age else 0.0,
             "rarity": _rarity(ep, context_freq)}
        t["novelty"] = self.novelty(ep, kept) if (self.novelty and self.w_novelty) else 0.0
        return t

    def value(self, t: dict) -> float:
        return (self.w_novelty * t["novelty"] + self.w_importance * t["importance"] + self.w_use * t["use"]
                + self.w_rarity * t["rarity"] - self.w_age * t["age"])

    def params(self) -> dict:
        return {"w_novelty": self.w_novelty, "w_importance": self.w_importance, "w_use": self.w_use,
                "w_rarity": self.w_rarity, "w_age": self.w_age}


def _rarity(ep: Episode, freq: dict) -> float:
    """1 − (share of episodes with the same context values); 0 when no context."""
    if not ep.context or not freq:
        return 0.0
    key = tuple(sorted(ep.context.items()))
    total = sum(freq.values())
    return 1.0 - freq.get(key, 0) / total


# The four policies of study F1 (docs/memory-design.md). Recency is the retention baseline.
def recency() -> KeepValue:
    return KeepValue(w_age=1.0, name="recency")


def novelty_policy(novelty: Novelty) -> KeepValue:
    return KeepValue(w_novelty=1.0, novelty=novelty, name="novelty")


def novelty_importance(novelty: Novelty, w_importance: float = 1.0) -> KeepValue:
    return KeepValue(w_novelty=1.0, w_importance=w_importance, novelty=novelty, name="novelty+importance")


# ---- selectors: why a run happens ------------------------------------------------------------------------------

@dataclass(frozen=True)
class Trigger:
    reason: str
    bytes_to_free: int


class BudgetSelector:
    """Fires when the episodes occupy more than budget_bytes; asks to free the excess."""
    def __init__(self, budget_bytes: int):
        self.budget = budget_bytes

    def select(self, store: Store) -> Optional[Trigger]:
        used = store.bytes_at()
        return Trigger("budget", used - self.budget) if used > self.budget else None


class ManualSelector:
    def __init__(self, bytes_to_free: int):
        self.n = bytes_to_free

    def select(self, store: Store) -> Optional[Trigger]:
        return Trigger("manual", self.n)


# ---- pickers: what moves ---------------------------------------------------------------------------------------

class LadderPicker:
    """Moves the lowest-valued episodes one level step at a time (0→1→3→4 by default, never 2: representation needs
    clusters, see ClusterPicker) until the trigger's bytes are freed. Every move carries its terms and value."""

    def __init__(self, scorer: KeepValue, filters: Sequence[Filter] = (), steps: Sequence[int] = (1, 3, 4),
                 seed: Optional[int] = None):
        self.scorer, self.filters, self.steps, self.seed = scorer, list(filters), list(steps), seed

    def next_level(self, level: int) -> Optional[int]:
        later = [s for s in self.steps if s > level]
        return later[0] if later else None

    def pick(self, store: Store, trigger: Trigger) -> list[Move]:
        eps = store.episodes()
        kept = [e for e in eps if e.level <= 1]
        freq: dict = {}
        for e in eps:
            if e.context:
                k = tuple(sorted(e.context.items()))
                freq[k] = freq.get(k, 0) + 1
        max_age = max((e.age_s for e in eps), default=0.0)
        max_uses = max((e.uses for e in eps), default=0)
        rng = random.Random(self.seed) if self.seed is not None else None
        scored = []
        for e in eps:
            target = self.next_level(e.level)
            if target is None or first_veto(self.filters, e, target):
                continue
            t = self.scorer.terms(e, kept, freq, max_age, max_uses)
            v = rng.random() if rng else self.scorer.value(t)       # random policy: a seeded uniform value
            scored.append((v, e.episode_id, e, target, t))
        scored.sort(key=lambda x: (x[0], x[1]))
        self.last_candidates = [Move(e.episode_id, target, reason={"policy": self.scorer.name if not rng else "random",
                                                          "version": POLICY_VERSION, "value": round(v, 6),
                                                          "terms": {k: round(x, 6) for k, x in t.items()},
                                                          "trigger": trigger.reason})
                      for v, _, e, target, t in scored]
        candidates = self.last_candidates
        freed = store.freed(candidates) if candidates else []
        self.last_freed = freed
        moves, total = [], 0
        for m, b in zip(candidates, freed):
            if total >= trigger.bytes_to_free:
                break
            if b > 0:
                moves.append(m)
                total += b
        return moves


class DepthPicker(LadderPicker):
    """Depth first: the lowest-valued episodes go straight to `first` (default level 3, summaries only) until the
    budget is met, so the most valuable stay fully searchable; only when every movable episode is at `first` do the
    lowest-valued move on to `last` (default 4). A step-by-step ladder takes nearly the whole archive out of search
    before any sample is removed, because search representations are a large share of the bytes (F1 development)."""

    def __init__(self, scorer: KeepValue, filters: Sequence[Filter] = (), first: int = 3, last: int = 4,
                 seed: Optional[int] = None):
        super().__init__(scorer, filters, steps=(first, last), seed=seed)
        self.first, self.last = first, last

    def next_level(self, level: int) -> Optional[int]:
        return self.first if level < self.first else (self.last if level < self.last else None)

    def pick(self, store: Store, trigger: Trigger) -> list[Move]:
        eps = store.episodes()
        movable = [e for e in eps if e.level < self.first and not first_veto(self.filters, e, self.first)]
        if movable:   # phase 1: only episodes not yet at `first` are candidates
            saved = self.steps
            self.steps = [self.first]
            try:
                return super().pick(store, trigger)
            finally:
                self.steps = saved
        return super().pick(store, trigger)


def random_policy(seed: int) -> tuple[KeepValue, int]:
    """The reservoir-style baseline: a seeded uniform order (the scorer is unused)."""
    return KeepValue(name="random"), seed


# ---- clustering for level 2: real representatives -------------------------------------------------------------

def kmedoids(ids: Sequence[int], dist: Callable[[int, int], float], k: int, iters: int = 20,
             seed: int = 0) -> dict[int, int]:
    """Alternating k-medoids under an arbitrary distance; returns {member: medoid}. Medoids are real members, never
    averages (level 2 keeps evidence). Deterministic for a given seed. O(n² ) distance calls: for galleries of a few
    thousand episodes, as in study F1."""
    ids = list(ids)
    if k <= 0 or not ids:
        return {}
    k = min(k, len(ids))
    rng = random.Random(seed)
    medoids = rng.sample(ids, k)
    cache: dict = {}

    def d(a: int, b: int) -> float:
        if a == b:
            return 0.0
        key = (a, b) if a < b else (b, a)
        if key not in cache:
            cache[key] = dist(*key)
        return cache[key]

    assign: dict[int, int] = {}
    for _ in range(iters):
        assign = {i: min(medoids, key=lambda m: (d(i, m), m)) for i in ids}
        new = []
        for m in medoids:
            members = [i for i, a in assign.items() if a == m]
            new.append(min(members, key=lambda c: (sum(d(c, x) for x in members), c)))
        if sorted(new) == sorted(medoids):
            break
        medoids = new
    return {i: min(medoids, key=lambda m: (d(i, m), m)) for i in ids}


def cluster_moves(assign: dict[int, int], dist: Callable[[int, int], float], keep_outliers: float,
                  filters: Sequence[Filter], episodes: dict[int, Episode]) -> list[Move]:
    """Level-2 moves for every member that is not its medoid and lies within keep_outliers of it; members further
    away (the unusual ones) stay as they are."""
    moves = []
    for member, medoid in sorted(assign.items()):
        if member == medoid:
            continue
        e = episodes[member]
        if e.level >= 2 or episodes[medoid].level > 1 or first_veto(filters, e, 2):
            continue
        dd = dist(member, medoid) if member < medoid else dist(medoid, member)
        if dd <= keep_outliers:
            moves.append(Move(member, 2, representative=medoid, distance=round(dd, 6),
                              reason={"policy": "cluster", "version": POLICY_VERSION, "distance": round(dd, 6)}))
    return moves


# ---- task: dry run, then resumable execution ------------------------------------------------------------------

@dataclass
class TaskResult:
    run_id: int
    moves: list
    bytes_freed: int
    dry_run: bool


def run_task(store: Store, selector, picker: LadderPicker, dry_run: bool, batch: int = 50) -> Optional[TaskResult]:
    """One policy run: the selector decides whether and how much, the picker what. With dry_run the picked moves and
    their estimated sizes are reported and nothing changes. Otherwise the candidates are applied in order of value, in
    batches (each one transaction), and after every batch the actual size is measured; the run stops once the bytes
    are freed. Per-move estimates are dry runs of each move alone; moves of neighbouring episodes free more together
    (shared search windows), so execution measures instead of trusting the estimates (F1 development)."""
    trigger = selector.select(store)
    if trigger is None:
        return None
    params = {"selector": trigger.reason, "bytes_to_free": trigger.bytes_to_free, **picker.scorer.params(),
              "seed": picker.seed, "steps": picker.steps}
    run_id = store.start_run(picker.scorer.name if picker.seed is None else "random", params, dry_run)
    try:
        moves = picker.pick(store, trigger)
        freed = 0
        if dry_run:
            freed = sum(store.freed(moves)) if moves else 0
        else:
            ordered = getattr(picker, "last_candidates", moves)
            estimates = getattr(picker, "last_freed", [1] * len(ordered))
            start = store.bytes_at()
            target = start - trigger.bytes_to_free
            applied, i, now = [], 0, start
            while i < len(ordered) and now > target:
                # take as many moves as the estimates say are still needed (at most `batch`), then measure
                need, j, est = now - target, i, 0
                while j < len(ordered) and j - i < batch and est < need:
                    est += max(estimates[j], 0)
                    j += 1
                store.apply(run_id, ordered[i:j])
                applied += ordered[i:j]
                i, now = j, store.bytes_at()
            moves, freed = applied, start - now
        store.finish_run(run_id, "finished", {"moves": len(moves), "bytes_freed": freed})
        return TaskResult(run_id, moves, freed, dry_run)
    except Exception:
        store.finish_run(run_id, "failed", {})
        raise
