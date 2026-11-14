# Copyright 2026 MorletDB contributors. Licensed under the Apache License, Version 2.0 (LICENSE).
# SPDX-License-Identifier: Apache-2.0
"""The sidecar image (deploy/steward/Dockerfile) copies every steward module that steward.py can import (#489)."""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]


def local_imports(path: Path) -> set[str]:
    """Steward modules (files next to `path`) imported anywhere in it, including inside functions."""
    names = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            names |= {a.name.split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return {n for n in names if (HERE / f"{n}.py").exists()}


class DockerfileCopiesModules(unittest.TestCase):
    def test_every_imported_module_is_copied(self):
        needed, todo = set(), ["steward"]
        while todo:
            m = todo.pop()
            if m not in needed:
                needed.add(m)
                todo += sorted(local_imports(HERE / f"{m}.py"))
        text = (ROOT / "deploy" / "steward" / "Dockerfile").read_text()
        copied = set()
        for line in text.splitlines():
            if line.startswith("COPY "):
                copied |= set(re.findall(r"tools/steward/(\w+)\.py", line))
        self.assertEqual(needed - copied, set(), "modules steward.py imports but the image does not copy")


if __name__ == "__main__":
    unittest.main()
