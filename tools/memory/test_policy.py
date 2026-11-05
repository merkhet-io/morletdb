"""Unit tests of the policy engine against an in-memory store (no database)."""
import unittest
from dataclasses import replace

from policy import (BudgetSelector, Episode, ExemptFilter, LadderPicker, ManualSelector, MinAgeFilter, Move,
                    ProtectContextFilter, cluster_moves, kmedoids, novelty_importance, novelty_policy, recency,
                    run_task)

# Bytes per sample at each level, as measured (L0 samples + search representations, L1 samples, L2/L3 summaries).
PER_SAMPLE = {0: 3.2, 1: 1.6, 2: 0.016, 3: 0.016, 4: 0.0}


class MemoryStore:
    def __init__(self, episodes):
        self.eps = {e.episode_id: e for e in episodes}
        self.runs, self.ledger = {}, []

    def episodes(self):
        return [self.eps[i] for i in sorted(self.eps)]

    def size(self, e, level):
        return int(e.samples * PER_SAMPLE[level])

    def bytes_at(self):
        return sum(self.size(e, e.level) for e in self.eps.values())

    def freed(self, moves):
        return [self.size(self.eps[m.episode_id], self.eps[m.episode_id].level)
                - self.size(self.eps[m.episode_id], m.level) for m in moves]

    def apply(self, run_id, moves):
        out = []
        for m in moves:
            e = self.eps[m.episode_id]
            if m.level <= e.level:
                raise ValueError("levels only move down")
            if e.exempt and m.level > 1:
                raise ValueError("exempt")
            out.append(self.size(e, e.level) - self.size(e, m.level))
            self.ledger.append((run_id, m.episode_id, e.level, m.level, m.reason))
            self.eps[m.episode_id] = replace(e, level=m.level)
        return out

    def start_run(self, policy, params, dry_run):
        rid = len(self.runs) + 1
        self.runs[rid] = {"policy": policy, "params": params, "dry_run": dry_run, "status": "running"}
        return rid

    def finish_run(self, run_id, status, summary):
        self.runs[run_id].update(status=status, summary=summary)


def gallery():
    # 10 episodes of 1000 samples; ids 1..10; older first; episode 3 is exempt, 7 is important, 9 has a rare context.
    eps = []
    for i in range(1, 11):
        eps.append(Episode(i, "A", (i - 1) * 1000, i * 1000, age_s=(11 - i) * 100.0,
                           exempt=(i == 3), importance=1.0 if i == 7 else 0.0,
                           context={"program": "P9" if i == 9 else "P1"}))
    return eps


# Novelty from a 1-D "signal feature": distance to the nearest other kept episode, scaled to [0, 1].
FEATURE = {1: 0.0, 2: 0.01, 3: 0.02, 4: 0.5, 5: 0.51, 6: 0.52, 7: 0.53, 8: 0.9, 9: 0.95, 10: 1.0}


def novelty(ep, kept):
    others = [abs(FEATURE[ep.episode_id] - FEATURE[k.episode_id]) for k in kept if k.episode_id != ep.episode_id]
    return min(others) if others else 1.0


class PolicyTests(unittest.TestCase):
    def test_filters_veto(self):
        e = gallery()[2]
        self.assertEqual(ExemptFilter().veto(e, 3), "exempt")
        self.assertEqual(ExemptFilter().veto(e, 1), "exempt")      # the library stays searchable
        self.assertEqual(MinAgeFilter(1000).veto(gallery()[9], 1), "too young")
        self.assertEqual(ProtectContextFilter({"program": "P9"}).veto(gallery()[8], 3), "protected context")
        self.assertIsNone(ProtectContextFilter({"program": "P9"}).veto(gallery()[8], 1))

    def test_recency_moves_oldest_first_and_frees_enough(self):
        store = MemoryStore(gallery())
        picker = LadderPicker(recency(), [ExemptFilter()])
        moves = picker.pick(store, ManualSelector(4000).select(store))
        # value = −age: the oldest episodes have the lowest value and move first; the exempt episode 3 stays;
        # each 0→1 move frees 1,600 bytes here.
        self.assertEqual([m.episode_id for m in moves], [1, 2, 4])

    def test_novelty_moves_redundant_episodes_first(self):
        store = MemoryStore(gallery())
        moves = LadderPicker(novelty_policy(novelty), [ExemptFilter()]).pick(store, ManualSelector(3000).select(store))
        self.assertTrue({m.episode_id for m in moves} <= {1, 2, 4, 5, 6, 7})
        self.assertNotIn(10, [m.episode_id for m in moves])
        self.assertEqual(moves[0].reason["policy"], "novelty")
        self.assertIn("novelty", moves[0].reason["terms"])

    def test_importance_protects_labelled_episodes(self):
        store = MemoryStore(gallery())
        moves = LadderPicker(novelty_importance(novelty), [ExemptFilter()]).pick(store, ManualSelector(5000).select(store))
        self.assertNotIn(7, [m.episode_id for m in moves][:3])

    def test_random_is_seeded(self):
        a = LadderPicker(recency(), [ExemptFilter()], seed=7).pick(MemoryStore(gallery()), ManualSelector(5000).select(None))
        b = LadderPicker(recency(), [ExemptFilter()], seed=7).pick(MemoryStore(gallery()), ManualSelector(5000).select(None))
        self.assertEqual([m.episode_id for m in a], [m.episode_id for m in b])
        self.assertEqual(a[0].reason["policy"], "random")

    def test_exempt_is_never_moved_past_level_one(self):
        store = MemoryStore(gallery())
        picker = LadderPicker(recency(), [ExemptFilter()])
        for _ in range(4):
            run_task(store, ManualSelector(10 ** 9), picker, dry_run=False)
        self.assertEqual(store.eps[3].level, 0)
        self.assertEqual({e.level for i, e in store.eps.items() if i != 3}, {4})

    def test_budget_selector_and_dry_run(self):
        store = MemoryStore(gallery())
        before = store.bytes_at()
        sel = BudgetSelector(before - 5000)
        res = run_task(store, sel, LadderPicker(recency(), [ExemptFilter()]), dry_run=True)
        self.assertGreaterEqual(res.bytes_freed, 5000)
        self.assertEqual(store.bytes_at(), before)                          # nothing changed
        self.assertEqual(store.runs[res.run_id]["status"], "finished")
        res = run_task(store, sel, LadderPicker(recency(), [ExemptFilter()]), dry_run=False)
        self.assertLessEqual(store.bytes_at(), before - 5000)
        self.assertIsNone(run_task(store, BudgetSelector(10 ** 12), LadderPicker(recency()), dry_run=False))

    def test_kmedoids_keeps_real_members_and_outliers(self):
        ids = list(range(1, 11))
        dist = lambda a, b: abs(FEATURE[a] - FEATURE[b])
        assign = kmedoids(ids, dist, 3, seed=1)
        medoids = set(assign.values())
        self.assertTrue(medoids <= set(ids))
        self.assertEqual(len(medoids), 3)
        self.assertEqual(assign[1], assign[2])
        self.assertEqual(assign[4], assign[6])
        eps = {e.episode_id: e for e in gallery()}
        moves = cluster_moves(assign, dist, keep_outliers=0.05, filters=[ExemptFilter()], episodes=eps)
        self.assertTrue(all(isinstance(m, Move) and m.level == 2 and m.representative in medoids for m in moves))
        self.assertNotIn(3, [m.episode_id for m in moves])                  # exempt
        self.assertEqual(kmedoids(ids, dist, 3, seed=1), assign)            # deterministic


if __name__ == "__main__":
    unittest.main()
