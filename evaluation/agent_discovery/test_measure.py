"""Recount must reject corrupted evidence even with Python optimization enabled."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name('measure.py')


class MeasurementCLI(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-O', str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=30)

    def test_requires_exactly_one_mode(self):
        for args in ((), ('--output', 'unused.json', '--recount', 'unused.json')):
            result = self.run_cli(*args)
            self.assertEqual(result.returncode, 2)
            self.assertIn('error:', result.stderr)

    def test_optimized_recount_checks_each_archived_field(self):
        # Empty text has zero bytes/tokens independently of tokenizer implementation.
        case = {'name': 'empty', 'serialized_context': '', 'utf8_bytes': 0,
                'tokens': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.json'
            path.write_text(json.dumps({'scenarios': [case]}), encoding='utf-8')
            result = self.run_cli('--recount', path)
            self.assertEqual(result.returncode, 0, result.stderr)
            for field, bad, message in (('sha256', 'bad', 'hash'),
                                        ('utf8_bytes', 1, 'byte count'), ('tokens', 1, 'token count')):
                with self.subTest(field=field):
                    path.write_text(json.dumps({'scenarios': [{**case, field: bad}]}), encoding='utf-8')
                    result = self.run_cli('--recount', path)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn(f'Context {message} mismatch for empty', result.stderr)
                    self.assertNotIn('Verified', result.stdout)


if __name__ == '__main__':
    unittest.main()
