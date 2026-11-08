#!/usr/bin/env python3
"""Fetch PLAID 2018 submetered records (figshare 10084619 v2, CC BY 4.0) for study N1 and check pinned hashes.

Downloads metadata_submetered.json and submetered.zip into --out, verifies the figshare MD5 and the SHA-256
pinned in PROTOCOL.md, and extracts the CSV files. Nothing is redistributed by this repository; cite
PLAID (De Baets et al.) and follow its licence when you use the data.
"""
import argparse
import hashlib
import urllib.request
import zipfile
from pathlib import Path

FILES = {
    "metadata_submetered.json": ("https://ndownloader.figshare.com/files/19812581",
                                 "d9ff7b76fd5ea837f9694b360bb200d3",
                                 "f639453a7644321dc520c49983555d1629b442bf2987463fa4340d818311f768"),
    "submetered.zip": ("https://ndownloader.figshare.com/files/18183113",
                       "698ff4d11650aa162afdbf4b35fbc6fc",
                       "7950ae288071025f0a32f095fd4fe477f28b64250a4cc8bb11cd373e68802c95"),
}
RECORDS = 1876


def digests(path):
    md5, sha = hashlib.md5(), hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            md5.update(block)
            sha.update(block)
    return md5.hexdigest(), sha.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    for name, (url, md5, sha) in FILES.items():
        path = a.out / name
        if not path.exists():
            print("downloading", name, flush=True)
            urllib.request.urlretrieve(url, path)
        got = digests(path)
        if got != (md5, sha):
            raise SystemExit(f"{name}: hash mismatch {got}, expected {(md5, sha)}")
        print("ok", name, sha)
    target = a.out / "extracted"
    if not (target / "submetered_new").is_dir():
        with zipfile.ZipFile(a.out / "submetered.zip") as z:
            z.extractall(target)
    n = len(list((target / "submetered_new").glob("*.csv")))
    if n != RECORDS:
        raise SystemExit(f"expected {RECORDS} CSV files, found {n}")
    print("ok", n, "records in", target / "submetered_new")


if __name__ == "__main__":
    main()
