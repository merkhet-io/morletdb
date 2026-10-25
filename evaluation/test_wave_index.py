from dataclasses import replace
import json
import math
from pathlib import Path
import random
import tempfile
import unittest

import numpy as np

from wave_index import Spec, WaveIndex, Window, run_demo


BINARY = Path(__file__).resolve().parents[1]/'target/release/examples/features'


def layout():
    return tuple((band, start, min(start+128, 502), 10)
                 for band in range(12) for start in range(10, 502, 128))


def window(name, values=None, start=0, spec=None, metadata=()):
    spec = spec or Spec(window=512)
    return Window(name, start, spec.id, layout(),
                  tuple([.1]*48 if values is None else values), (0.,)*15,
                  '0'*64, metadata)


class WaveIndexContracts(unittest.TestCase):
    def test_exact_matches_independent_numpy_oracle_and_deterministic_ties(self):
        rng = random.Random(73)
        spec = Spec(window=512, bin_width=10)
        rows = [window(f'r{i:02d}', [rng.random()*2 for _ in range(48)], spec=spec)
                for i in range(30)]
        query = window('q', [rng.random()*2 for _ in range(48)], spec=spec)
        index = WaveIndex(rows, spec)
        durations = np.array([end-start for _, start, end, _ in layout()])
        # All bands in this control have 492 valid coefficient centers.
        weights = durations/(492*12)
        expected = np.sqrt(np.sum((np.array([r.values for r in rows])-query.values)**2*weights, axis=1))
        oracle = sorted(zip(expected, rows), key=lambda x: (x[0], x[1].recording))[:5]
        exact = index.search(query, top_k=5)
        self.assertEqual([r['id'] for r in exact['matches']], [r.recording for _, r in oracle])
        np.testing.assert_allclose([r['distance'] for r in exact['matches']], [d for d, _ in oracle])
        approximate = index.search(query, approximate=True, candidate_budget=30, top_k=5)
        self.assertEqual(approximate['matches'], exact['matches'])
        self.assertFalse(approximate['exhaustive'])
        reverse = WaveIndex(list(reversed(rows)), spec)
        self.assertEqual(reverse.generation, index.generation)
        self.assertEqual(reverse.search(query)['matches'], exact['matches'])

    def test_approximation_can_lose_true_nearest_and_budgets_fail_closed(self):
        spec = Spec(window=512, bin_width=10)
        query = window('q', [1.]*48, spec=spec)
        rows = [window('aaa', [2.]*48, spec=spec), window('zzz', [1.]*48, spec=spec)]
        index = WaveIndex(rows, spec)
        self.assertEqual(index.search(query, top_k=1)['matches'][0]['id'], 'zzz')
        result = index.search(query, approximate=True, candidate_budget=1, top_k=1)
        self.assertEqual(result['matches'][0]['id'], 'aaa')
        self.assertEqual(result['scored'], 1)
        self.assertEqual(result['mode'], 'approximate')
        with self.assertRaisesRegex(ValueError, 'posting budget'):
            index.search(query, approximate=True, posting_budget=1)
        with self.assertRaisesRegex(ValueError, 'query budget'):
            index.search(query, top_k=0)
        with self.assertRaisesRegex(ValueError, 'query budget'):
            index.search(query, candidate_budget=0)

    def test_source_exclusion_metadata_adjacency_and_overlap_before_topk(self):
        spec = Spec(window=512)
        query = window('source', spec=spec)
        rows = [query, window('source', start=256), window('source', start=512),
                window('other', metadata=(('asset', 'a'),)),
                window('other', start=256, metadata=(('asset', 'a'),)),
                window('other', start=512, metadata=(('asset', 'a'),))]
        index = WaveIndex(rows, spec)
        result = index.search(query, top_k=5)
        self.assertEqual([(m['id'], m['start']) for m in result['matches']],
                         [('other', 0), ('other', 512), ('source', 512)])
        filtered = index.search(query, metadata={'asset': 'a'}, top_k=2)
        self.assertEqual([m['start'] for m in filtered['matches']], [0, 512])
        no_suppression = index.search(query, metadata={'asset': 'a'}, suppress_overlaps=False)
        self.assertEqual(len(no_suppression['matches']), 3)
        self.assertEqual(index.search(query, metadata={'asset': 'absent'})['matches'], [])

    def test_compatibility_coverage_and_invalid_numbers_fail(self):
        spec = Spec(window=512)
        row = window('a')
        index = WaveIndex([row], spec)
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            index.search(replace(row, spec_id=Spec(window=512, unit='V').id))
        with self.assertRaisesRegex(ValueError, 'invalid indexed'):
            index.search(replace(row, values=(math.nan,)*48))
        with self.assertRaisesRegex(ValueError, 'coverage'):
            WaveIndex([replace(row, layout=row.layout[:-1], values=row.values[:-1])], spec)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            WaveIndex([row, row], spec)
        with self.assertRaises(ValueError):
            Spec(bin_width=0)
        with self.assertRaises(ValueError):
            WaveIndex([], spec)
        changed = replace(row, values=(.2,)*48)
        self.assertNotEqual(WaveIndex([changed], spec).generation, index.generation)

    def test_zero_cells_are_valid_and_no_shared_tokens_returns_no_candidates(self):
        spec = Spec(window=512, bin_width=.01)
        query = window('q', [0.]*48, spec=spec)
        index = WaveIndex([window('far', [10.]*48, spec=spec)], spec)
        result = index.search(query, approximate=True)
        self.assertEqual(result['matches'], [])
        self.assertEqual(result['scored'], 0)
        self.assertEqual(index.search(query)['eligible'], 1)

    @unittest.skipUnless(BINARY.exists(), 'build the Rust features adapter first')
    def test_actual_rust_cells_ordered_control_and_demo_report(self):
        report = run_demo(BINARY, size=12)
        self.assertGreater(report['temporal_reversal_distance'], .01)
        self.assertLess(report['pooled_reversal_distance'], 1e-8)
        self.assertEqual(report['exact']['matches'][0]['id'], 'same')
        self.assertGreater(report['posting_entries'], 0)
        for result in report['candidate_sweep']:
            self.assertLessEqual(result['scored'], result['candidate_budget'])
            self.assertGreaterEqual(result['candidate_recall_at_3'], result['recall_at_3_against_exact'])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'report.json'
            path.write_text(json.dumps(report, allow_nan=False))
            self.assertEqual(json.loads(path.read_text())['version'], report['version'])


if __name__ == '__main__':
    unittest.main()
