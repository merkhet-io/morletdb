#!/usr/bin/env python3
"""Every public SQL function of pg_morlet (stable or experimental in extensions/pg_morlet/api/functions.tsv) is named
on the website (docs/docs.html), and the website names no internal or unknown function. Standard library only; run from
the repository root."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
classes = {}
for line in (ROOT / "extensions/pg_morlet/api/functions.tsv").read_text().splitlines():
    if line.strip() and not line.startswith("#"):
        name, cls = line.split("\t")[:2]
        classes[name] = cls
public = sorted(n for n, c in classes.items() if c in ("stable", "experimental"))
html = (ROOT / "docs/docs.html").read_text()
named = set(re.findall(r"\bmorlet_[a-z0-9_]+\b(?!\.py)", html))  # morlet_ship.py etc. are scripts
ROLES = {"morlet_approver", "morlet_steward", "morlet_mcp"}  # role and tool names, not functions
problems = [f"{n} ({classes[n]}) is not named in docs/docs.html" for n in public if n not in named]
problems += [f"docs/docs.html names {n}, which is {classes[n]}" for n in sorted(named) if classes.get(n) in ("internal", "deprecated")]
problems += [f"docs/docs.html names {n}, which is not in functions.tsv" for n in sorted(named - set(classes) - ROLES)]
if not public:
    problems.append("no public functions found in extensions/pg_morlet/api/functions.tsv")
print("\n".join(problems) or f"SQL docs: all {len(public)} public functions named in docs/docs.html")
sys.exit(1 if problems else 0)
