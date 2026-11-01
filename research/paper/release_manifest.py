#!/usr/bin/env python3
"""Write or check RELEASE_MANIFEST.json for the technical report (standard library only).

  python3 research/paper/release_manifest.py           # record HEAD and the PDF hashes (run on a clean tree)
  python3 research/paper/release_manifest.py --check   # verify hashes and that no report file changed since
"""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "research/paper/RELEASE_MANIFEST.json"
PDFS = ["research/paper/morletdb.pdf", "research/paper/supplement.pdf", "docs/morletdb.pdf", "docs/morletdb-supplement.pdf"]
WATCHED = ["research/paper", "docs/morletdb.pdf", "docs/morletdb-supplement.pdf"]
STATIC = {
    "title": "MorletDB: Recordings, Not Rows — Lag-Tolerant Waveform Retrieval in PostgreSQL",
    "version": "0.11",
    "date": "2026-11-01",
    "type": "technical report / preprint (not peer reviewed)",
    "author": "Zin Nam",
    "measured_commit": "68594d7",
    "measured_commit_scope": "E1 consolidated comparison and diagnostics D1-D2; earlier studies used earlier implementation states (STUDIES.md)",
    "measured_library_sha256": "3c786684421f3132e7a97b5ff38801e802948bf3e014aaae656b16bb109de7b7",
    "sources_first_merged": "cdf8b4b",
    "tag": "paper-v0.11",
    "funding": "none",
    "licence": {"code": "Apache-2.0", "report_text_and_figures": "CC-BY-4.0 (LICENSE.md)"},
}


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()


def sha(path):
    return hashlib.sha256((ROOT / path).read_bytes()).hexdigest()


def main():
    if "--check" in sys.argv[1:]:
        m = json.loads(OUT.read_text())
        bad = [p for p in PDFS if sha(p) != m["pdf_sha256"][p]]
        changed = [f for f in git("diff", "--name-only", m["report_source_commit"], "--", *WATCHED).splitlines()
                   if f != str(OUT.relative_to(ROOT))]
        for p in bad:
            print("hash differs:", p)
        for f in changed:
            print("changed since", m["report_source_commit"] + ":", f)
        print("manifest OK" if not (bad or changed) else "manifest STALE")
        sys.exit(1 if bad or changed else 0)
    if git("status", "--porcelain", "--", *WATCHED):
        sys.exit("commit the report files first; the manifest records a commit, not a working tree")
    m = dict(STATIC, report_source_commit=git("rev-parse", "HEAD"), pdf_sha256={p: sha(p) for p in PDFS})
    OUT.write_text(json.dumps(m, indent=1, ensure_ascii=False) + "\n")
    print("wrote", OUT.relative_to(ROOT), "for", m["report_source_commit"][:7])


if __name__ == "__main__":
    main()
