"""Recount must reject corrupted evidence even with Python optimization enabled."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


from measure import source_state


SCRIPT = Path(__file__).with_name('measure.py')


class MeasurementCLI(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-O', str(SCRIPT), *map(str, args)],
                              capture_output=True, text=True, timeout=30)

    def test_source_state_distinguishes_clean_staged_unstaged_and_untracked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                return subprocess.check_output(['git', *args], cwd=root, text=True).strip()
            git('init', '-q')
            tracked = root / 'source.py'
            tracked.write_text('original\n', encoding='utf-8')
            git('add', 'source.py')
            git('-c', 'user.name=D1 test', '-c', 'user.email=d1@example.invalid',
                '-c', 'commit.gpgsign=false', 'commit', '-qm', 'fixture')
            commit = git('rev-parse', 'HEAD')
            self.assertEqual(source_state(root), {'source_commit': commit, 'source_dirty': False})
            tracked.write_text('modified\n', encoding='utf-8')
            self.assertEqual(source_state(root), {'source_commit': commit, 'source_dirty': True})
            git('add', 'source.py')
            self.assertTrue(source_state(root)['source_dirty'])
            git('restore', '--staged', '--worktree', 'source.py')
            self.assertFalse(source_state(root)['source_dirty'])
            (root / 'untracked.py').write_text('new\n', encoding='utf-8')
            self.assertTrue(source_state(root)['source_dirty'])

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
