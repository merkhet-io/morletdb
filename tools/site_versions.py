#!/usr/bin/env python3
"""Write the release and report versions into the website, or check them (standard library only).

Sources: VERSION (software release) and research/paper/RELEASE_MANIFEST.json (report). Targets: every
<span data-version="release"> and <span data-version="report"> in docs/*.html and README.md.

  python3 tools/site_versions.py           # update docs/*.html
  python3 tools/site_versions.py --check   # fail if a page shows another version (CI)
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSIONS = {
    "release": (ROOT / "VERSION").read_text().strip(),
    "report": json.loads((ROOT / "research/paper/RELEASE_MANIFEST.json").read_text())["version"],
}
SPAN = re.compile(r'(<span data-version="(release|report)">)([^<]*)(</span>)')


def main():
    check = "--check" in sys.argv[1:]
    stale = []
    for page in [*sorted((ROOT / "docs").glob("*.html")), ROOT / "README.md"]:
        text = page.read_text()
        new = SPAN.sub(lambda m: m.group(1) + VERSIONS[m.group(2)] + m.group(4), text)
        for m in SPAN.finditer(text):
            if m.group(3) != VERSIONS[m.group(2)]:
                stale.append(f"{page.relative_to(ROOT)}: {m.group(2)} {m.group(3)!r}, expected {VERSIONS[m.group(2)]!r}")
        if new != text and not check:
            page.write_text(new)
    for s in stale:
        print(("STALE " if check else "updated ") + s)
    if check and stale:
        sys.exit("run python3 tools/site_versions.py")
    print("site versions:", VERSIONS)


if __name__ == "__main__":
    main()
