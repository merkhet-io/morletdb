#!/usr/bin/env python3
"""Build with pinned ACM 2.21 sources, without changing the class or its spacing.

Requires tectonic and network access on the first build. CTAN URLs are mutable;
checksum verification fails closed if they change. --template-dir accepts an
already generated, unmodified acmart 2.21 directory. --all also builds the report
and supplement. Generated files go beside the manuscript.
"""
import argparse
import hashlib
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
VERSION = '2026/11/01 v2.21'
ARCHIVES = [
    ('https://mirrors.ctan.org/macros/latex/contrib/acmart.zip',
     '81977a834f5cfd5302c37181ede34d2663eeea60604d1d89230e3c5e74b9a4bb', 'acmart.zip'),
    ('https://mirrors.ctan.org/systems/texlive/tlnet/archive/latex.tar.xz',
     '1ae770eab0866a96186fe4156b7f5d61c17c784be40efd8d971508f5afc08d15', 'latex.tar.xz'),
]


def prepare(directory):
    for url, digest, name in ARCHIVES:
        data = urllib.request.urlopen(url, timeout=120).read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise SystemExit(f'{name}: upstream archive changed; review and update the pin before building')
        (directory / name).write_bytes(data)
    with zipfile.ZipFile(directory / 'acmart.zip') as archive:
        for name in ('acmart.dtx', 'acmart.ins'):
            (directory / name).write_bytes(archive.read('acmart/' + name))
    with tarfile.open(directory / 'latex.tar.xz') as archive:
        data = archive.extractfile('tex/latex/base/docstrip.tex').read()
        (directory / 'docstrip.tex').write_bytes(data)
    ins = directory / 'acmart.ins'
    ins.write_text(ins.read_text().replace(r'\input docstrip', r'\input docstrip.tex'))
    subprocess.run(['tectonic', '-X', 'compile', 'acmart.ins', '--outfmt', 'xdv'], cwd=directory, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template-dir', type=Path)
    parser.add_argument('--all', action='store_true')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='morlet-acmart-') as temporary:
        directory = args.template_dir.resolve() if args.template_dir else Path(temporary)
        if not args.template_dir:
            prepare(directory)
        if VERSION not in (directory / 'acmart.cls').read_text():
            raise SystemExit('expected ACM acmart ' + VERSION)
        for stem in (['sigmod', 'morletdb', 'supplement'] if args.all else ['sigmod']):
            subprocess.run(['tectonic', '-X', 'compile', '-Z', f'search-path={directory}',
                            '--keep-logs', stem + '.tex'], cwd=HERE, check=True)


if __name__ == '__main__':
    main()
