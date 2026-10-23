import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import numpy as np

from retrieval import baseline_features, evaluate, load_windows, rank

ROOT = Path(__file__).resolve().parents[1]
BINARY = ROOT / 'target/release/examples/features'


class RetrievalContracts(unittest.TestCase):
    def test_run_leakage_and_checksums_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            np.save(root/'signal.npy', np.ones(2048), allow_pickle=False)
            entry = {'id': 'q', 'path': 'signal.npy', 'split': 'query', 'run_id': 'run',
                     'label': 'event', 'sample_rate_hz': 4096, 'channel': 'x', 'unit': 'g',
                     'sha256': hashlib.sha256((root/'signal.npy').read_bytes()).hexdigest()}
            manifest = root/'manifest.json'
            gallery = {**entry, 'id': 'g', 'split': 'gallery'}
            manifest.write_text(json.dumps({'recordings': [entry, gallery]}))
            with self.assertRaisesRegex(ValueError, 'disjoint'):
                load_windows(manifest, 512, 2, 2)
            gallery['run_id'] = 'other'
            manifest.write_text(json.dumps({'recordings': [entry, gallery]}))
            _, windows = load_windows(manifest, 512, 2, 2)
            self.assertEqual([(r['start'], r['end']) for r in windows[:2]], [(0, 512), (1536, 2048)])
            gallery['sha256'] = 'changed'
            manifest.write_text(json.dumps({'recordings': [entry, gallery]}))
            with self.assertRaisesRegex(ValueError, 'checksum'):
                load_windows(manifest, 512, 2, 2)

    def test_overlap_before_topk_negative_queries_and_compatibility(self):
        row = {'id': 'g', 'run_id': 'gallery', 'label': 'other',
               'sample_rate_hz': 4096, 'channel': 'x', 'unit': 'g'}
        gallery = [{**row, 'start': start, 'end': start+512} for start in (0, 256, 512)]
        query = {**row, 'run_id': 'query', 'label': 'absent', 'features': {'test': np.array([0.])}}
        result = rank(query, gallery, {'test': [np.array([i]) for i in range(3)]}, 'test', 2)
        self.assertEqual([m['start'] for m in result['matches']], [0, 512])
        self.assertEqual(result['precision_at_k'], 0)
        self.assertIsNone(result['recall_at_k'])
        query['channel'] = 'incompatible'
        self.assertEqual(rank(query, gallery, {'test': []}, 'test', 2)['returned'], 0)

    def test_baselines_are_finite_for_silence(self):
        for vector in baseline_features(np.zeros(1024), 4096, 'zscore').values():
            self.assertTrue(np.isfinite(vector).all())

    @unittest.skipUnless(BINARY.exists(), 'build the real Rust example adapter first')
    def test_adapter_matches_production_golden_features(self):
        fixture = json.loads((ROOT/'testdata/morlet-energy-v1/tone.json').read_text())
        result = subprocess.run([str(BINARY)], input=json.dumps({
            'samples': fixture['capture']['samples'], 'sample_rate_hz': 4096,
            'normalization': 'none'})+'\n', text=True, capture_output=True, check=True)
        np.testing.assert_allclose(json.loads(result.stdout)['morlet'], fixture['features'], rtol=1e-9, atol=1e-10)

    @unittest.skipUnless(BINARY.exists(), 'build the real Rust example adapter first')
    def test_temporal_experiment_preserves_information_pooling_loses(self):
        x = np.zeros(2048)
        i = np.arange(300)
        x[450:750] = np.sin(0.2*i)*np.sin(np.pi*i/300)**2
        payload = ''.join(json.dumps({'samples': a.tolist(), 'sample_rate_hz': 4096,
                                     'normalization': 'zscore'})+'\n' for a in (x, x[::-1]))
        result = subprocess.run([str(BINARY)], input=payload, text=True, capture_output=True, check=True)
        left, right = [json.loads(line) for line in result.stdout.splitlines()]
        np.testing.assert_allclose(left['morlet'], right['morlet'], rtol=1e-9, atol=1e-10)
        self.assertGreater(np.linalg.norm(np.array(left['morlet_temporal_experimental'])-
                                         right['morlet_temporal_experimental']), 0.1)

    @unittest.skipUnless(BINARY.exists(), 'build the real Rust example adapter first')
    def test_complete_evaluator_without_network_or_database(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            recordings = []
            for split in ('query', 'gallery'):
                for label, frequency in [('low', 0.04), ('high', 0.3)]:
                    name = split+label
                    path = root/(name+'.npy')
                    np.save(path, np.sin(np.arange(4096)*frequency), allow_pickle=False)
                    recordings.append({'id': name, 'run_id': name, 'split': split, 'label': label,
                        'path': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'sample_rate_hz': 4096, 'channel': 'x', 'unit': 'g'})
            manifest = root/'manifest.json'
            manifest.write_text(json.dumps({'name': 'synthetic-control', 'synthetic': True, 'recordings': recordings}))
            report = evaluate(manifest, BINARY, query_windows=2, gallery_windows=3, top_k=2)
            self.assertEqual(report['query_count'], 4)
            self.assertEqual(report['gallery_count'], 6)
            self.assertEqual(report['summary']['morlet']['macro_recording_precision_at_k'], 1.)


if __name__ == '__main__':
    unittest.main()
