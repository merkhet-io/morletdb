#!/usr/bin/env python3
"""The licence boundary of docs/licensing.md, checked over the repository's tracked files.

  python3 tools/license_check.py [ROOT]

1. Every source file under enterprise/ starts with the enterprise header (within its first 5 lines).
2. No source file outside enterprise/ carries it there.
3. The root workspace does not build enterprise/ by default (no member or default member under it).
4. A Cargo manifest outside enterprise/ uses a crate under enterprise/ only as an optional dependency, enabled only by
   a feature named `enterprise`.
5. Python code outside enterprise/ never imports a package from enterprise/python/.
Exit status 1 with one line per violation; 0 and a summary otherwise.
"""
import re
import subprocess
import sys
import tomllib
from pathlib import Path, PurePosixPath

MARKER = "Licensed under the MorletDB Enterprise License"
SOURCE = {".rs", ".py", ".sh", ".toml", ".sql", ".ts", ".js", ".mjs", ".cjs", ".go", ".c", ".h"}
HEAD_LINES = 5


def tracked(root):
    out = subprocess.run(["git", "ls-files", "-z"], cwd=root, capture_output=True, check=True).stdout
    return [PurePosixPath(p) for p in out.decode().split("\0") if p]


def in_enterprise(p):
    return p.parts[:1] == ("enterprise",)


def has_header(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            head = [next(f, "") for _ in range(HEAD_LINES)]
    except OSError:
        return False
    return any(MARKER in line for line in head)


def check(root, files=None):
    root = Path(root)
    files = tracked(root) if files is None else [PurePosixPath(f) for f in files]
    problems = []
    sources = [p for p in files if p.suffix in SOURCE]
    for p in sources:
        header = has_header(root / p)
        if in_enterprise(p) and not header:
            problems.append(f"{p}: enterprise file without the enterprise header")
        if not in_enterprise(p) and header:
            problems.append(f"{p}: enterprise header outside enterprise/")
    # 3. the default build
    ws = tomllib.loads((root / "Cargo.toml").read_text()).get("workspace", {})
    for key in ("members", "default-members"):
        for m in ws.get(key, []):
            if PurePosixPath(m).parts[:1] == ("enterprise",):
                problems.append(f"Cargo.toml: workspace {key} includes {m}")
    # 4. Cargo dependencies on enterprise crates
    for p in files:
        if p.name != "Cargo.toml" or in_enterprise(p):
            continue
        man = tomllib.loads((root / p).read_text())
        sections = [man.get(k, {}) for k in ("dependencies", "dev-dependencies", "build-dependencies")]
        for t in man.get("target", {}).values():
            sections += [t.get(k, {}) for k in ("dependencies", "dev-dependencies", "build-dependencies")]
        enterprise_deps = set()
        for sec in sections:
            for name, spec in sec.items():
                if not isinstance(spec, dict) or "path" not in spec:
                    continue
                target = (root / p.parent / spec["path"]).resolve()
                try:
                    rel = PurePosixPath(target.relative_to(root.resolve()).as_posix())
                except ValueError:
                    continue
                if in_enterprise(rel):
                    enterprise_deps.add(name)
                    if not spec.get("optional"):
                        problems.append(f"{p}: dependency {name} on enterprise/ is not optional")
        for feat, enables in man.get("features", {}).items():
            for e in enables:
                dep = e.removeprefix("dep:").split("/")[0].rstrip("?")
                if dep in enterprise_deps and feat != "enterprise":
                    problems.append(f"{p}: feature {feat} enables enterprise dependency {dep}")
        if "enterprise" in man.get("features", {}).get("default", []):
            problems.append(f"{p}: the default features include enterprise")
    # 5. Python imports of enterprise packages
    packages = {q.parts[2] for q in files if q.parts[:2] == ("enterprise", "python") and len(q.parts) > 3}
    if packages:
        pattern = re.compile(r"^\s*(from|import)\s+(" + "|".join(map(re.escape, sorted(packages))) + r")\b", re.M)
        for p in files:
            if p.suffix == ".py" and not in_enterprise(p) and pattern.search((root / p).read_text(errors="replace")):
                problems.append(f"{p}: imports an enterprise package")
    return problems, len(sources)


def main():
    root = Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[1])
    problems, n = check(root)
    for line in problems:
        print(line)
    if problems:
        return 1
    print(f"licence boundary ok: {n} source files checked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
