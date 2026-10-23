#!/usr/bin/env python3
"""Prepare an explicitly scoped public bearing-recording evaluation.

Downloads stay outside version control. No raw dataset is redistributed.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import urllib.request

import numpy as np
from scipy.io import loadmat

SOURCE = "https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data"
FILES = [(105, "inner", "query"), (106, "inner", "gallery"),
         (118, "ball", "query"), (119, "ball", "gallery"),
         (130, "outer6", "query"), (131, "outer6", "gallery")]

SOURCE_SHA256 = {'105': 'f80b0ea04fd06b372a0eaec7c056543ea37e4bb4727a5b173d2a5bacd2aa9cab', '106': 'e5cec7cdd138e6cd1deb9ed8634e5aaa9bc1bd7094ddc075bff606580eb6e883', '118': 'b00628f8dd8d1d930af77fa465d1e5cdb385fe259489053f91f3680bda7f640e', '119': 'bc72d9df7668219e004a0f711232b23ec0aed393eb9bd8f9f5483426f6b51330', '130': '35a095307d0971477049b343a1b5981dde465a58fb7f233ad89b035068c1717d', '131': '7883f7b83beadc54b2f301767f9145ce07055707182fb0278fdf0a6bb53ce3cb'}

def prepare(output):
    output.mkdir(parents=True, exist_ok=True)

    def one(spec):
        file_id, label, split = spec
        url = f"https://engineering.case.edu/sites/default/files/{file_id}.mat"
        path = output / f"{file_id}.mat"
        if not path.exists():
            with urllib.request.urlopen(url, timeout=60) as response:
                content = response.read()
            path.write_bytes(content)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != SOURCE_SHA256[str(file_id)]:
            raise ValueError(f"upstream source checksum changed: {file_id}")
        data = loadmat(path)
        keys = [key for key in data if key.endswith("_DE_time")]
        if len(keys) != 1:
            raise ValueError(f"ambiguous drive-end channel in {path}")
        values = np.asarray(data[keys[0]], dtype=np.float64).reshape(-1)
        sample_path = output / f"{file_id}.npy"
        np.save(sample_path, values, allow_pickle=False)
        return {"id": str(file_id), "path": sample_path.name, "label": label,
                "split": split, "run_id": str(file_id), "sample_rate_hz": 12000,
                "channel": "drive-end", "unit": "source-units",
                "sha256": hashlib.sha256(sample_path.read_bytes()).hexdigest(),
                "source_url": url, "source_sha256": digest,
                "load_hp": 0 if split == "query" else 1,
                "fault_diameter_in": 0.007, "source_variable": keys[0]}

    with ThreadPoolExecutor(max_workers=6) as pool:
        recordings = list(pool.map(one, FILES))
    manifest = {"name": "cwru-007-cross-load-v1", "synthetic": False,
                "source": SOURCE, "label_semantics": "seeded fault location",
                "limitations": ["Six recordings from one laboratory apparatus.",
                    "Query load 0 hp; gallery load 1 hp. No healthy class.",
                    "Run-separated, not independent-bearing or machine-held-out.",
                    "Coarse fault labels are a relevance proxy, not reviewed event matches.",
                    "No threshold calibration or defense-domain validation."],
                "recordings": recordings}
    path = output / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2) + "\n")
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    print(prepare(parser.parse_args().output))
