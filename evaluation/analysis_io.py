"""Read archived analysis inputs without requiring an extracted working directory."""
import gzip
import json
import os
from pathlib import Path


def directories(script):
    source = Path(os.environ.get("MORLET_ANALYSIS_INPUT", Path(script).parent / "results"))
    destination = Path(os.environ.get("MORLET_ANALYSIS_OUTPUT", source))
    return source, destination


def read_json(path):
    path = Path(path)
    if not path.exists():
        path = path.with_suffix(path.suffix + ".gz")
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, data):
    """Replace an output only after the complete result has been serialized."""
    text = json.dumps(data, indent=1)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text, encoding="utf-8")
    temporary.replace(path)


def spearman(a, b):
    """Pearson correlation of average ranks; constants have no defined correlation."""
    import numpy as np

    def ranks(values):
        _, inverse, counts = np.unique(values, return_inverse=True, return_counts=True)
        return (np.cumsum(counts) - (counts - 1) / 2.0)[inverse]

    ra, rb = ranks(a), ranks(b)
    if len(ra) != len(rb):
        raise ValueError("paired observations must have the same length")
    if len(ra) < 3 or np.std(ra) == 0 or np.std(rb) == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])
