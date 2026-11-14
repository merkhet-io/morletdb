"""tools/license_check.py on small synthetic repositories (no git: file lists are passed in)."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import license_check  # noqa: E402

HEADER = "// Copyright 2026 Merkhet. Licensed under the MorletDB Enterprise License (LICENSE-ENTERPRISE.md);\n"


class LicenseCheck(unittest.TestCase):
    def repo(self, files):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        root = Path(d.name)
        files.setdefault("Cargo.toml", '[workspace]\nmembers = ["crates/core"]\n')
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return license_check.check(root, list(files))[0]

    def test_clean_repository(self):
        self.assertEqual(self.repo({
            "crates/core/Cargo.toml": '[package]\nname = "core"\n[dependencies]\nfleet = { path = "../../enterprise/crates/fleet", optional = true }\n[features]\nenterprise = ["dep:fleet"]\n',
            "crates/core/src/lib.rs": "// core\n",
            "enterprise/crates/fleet/src/lib.rs": HEADER + "pub fn f() {}\n",
            "enterprise/python/fleetagent/__init__.py": HEADER.replace("//", "#"),
            "tools/x.py": "import json\n",
        }), [])

    def test_missing_and_misplaced_headers(self):
        p = self.repo({"enterprise/crates/fleet/src/lib.rs": "pub fn f() {}\n", "crates/core/src/lib.rs": HEADER})
        self.assertEqual(len(p), 2)
        self.assertIn("without the enterprise header", p[0] + p[1])
        self.assertIn("outside enterprise/", p[0] + p[1])

    def test_dependencies_and_features(self):
        p = self.repo({
            "crates/core/Cargo.toml": '[package]\nname = "core"\n[dependencies]\nfleet = { path = "../../enterprise/crates/fleet" }\n[features]\ndefault = ["enterprise"]\nenterprise = []\nother = ["fleet"]\n',
            "enterprise/crates/fleet/Cargo.toml": HEADER.replace("//", "#") + '[package]\nname = "fleet"\n',
        })
        text = "\n".join(p)
        self.assertIn("is not optional", text)
        self.assertIn("feature other enables", text)
        self.assertIn("default features include enterprise", text)

    def test_workspace_and_imports(self):
        p = self.repo({
            "Cargo.toml": '[workspace]\nmembers = ["crates/core", "enterprise/crates/fleet"]\n',
            "enterprise/python/fleetagent/__init__.py": HEADER.replace("//", "#"),
            "tools/y.py": "from fleetagent import run\n",
        })
        text = "\n".join(p)
        self.assertIn("workspace members includes enterprise/crates/fleet", text)
        self.assertIn("tools/y.py: imports an enterprise package", text)


if __name__ == "__main__":
    unittest.main()
