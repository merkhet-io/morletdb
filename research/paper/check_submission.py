#!/usr/bin/env python3
"""Check the built SIGMOD PDF's basic format; requires Poppler tools.

This is a packaging check, not a scientific review or author approval.
"""
import re
from pathlib import Path
import subprocess

PDF = Path(__file__).resolve().parent / 'sigmod.pdf'
info = subprocess.check_output(['pdfinfo', str(PDF)], text=True)
text = subprocess.check_output(['pdftotext', '-layout', str(PDF), '-'], text=True)
pages = [p for p in text.split('\f') if p.strip()]
references = [i for i, p in enumerate(pages, 1) if re.search(r'\bReferences\s*\n', p)]
assert 'acmart 2026/11/01 v2.21' in info, 'rebuild using pinned ACM 2.21'
assert len(references) == 1 and references[0] <= 12, 'main text exceeds 12 pages or reference heading missing'
assert not re.search(r'Conference.?17|July 2017|Claim ledger', text), 'placeholder metadata or report appendix present'
assert all(name in text for name in ['Zin Nam', 'Semir Maslo', 'Sulzer-BDiM']), 'author block missing'
assert '612 x 792 pts (letter)' in info, 'unexpected paper size'
print(f'SIGMOD packaging OK: references begin on page {references[0]}, {len(pages)} total pages, ACM 2.21')
print('Still requires author contact details, final scientific review, and submission approval; see SUBMISSION.md.')
