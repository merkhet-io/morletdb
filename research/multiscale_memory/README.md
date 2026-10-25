# Research: multiscale external memory for retrieval-augmented signal models

Status: **research spike**, not a MorletDB feature. It does not change the extension, the
Rust core or the frozen Morlet extractor. The question, design and decision rules are in
[PROTOCOL.md](PROTOCOL.md); the paper outline is in [paper/outline.md](paper/outline.md).

## What is here

| Path | Contents |
|---|---|
| `msmem/dwt.py` | Orthonormal periodized DWT (db4, Haar), an independent NumPy implementation |
| `msmem/representation.py` | Immutable specs with content ids, provenance, level-major serialization, selective reads, full and partial reconstruction, coarse plus deterministic refinement |
| `tests/` | Checks against PyWavelets and analytical fixtures (no self-generated expectations) |
| `experiments/ptbxl_h2.py` | PTB-XL pilot for H1 and H2 (verify, prepare, retrieve, contexts, train, gate, evaluate) |
| `results/pilot-590445f/` | Pilot outcome: H1 not supported, H2 inconclusive; see its `NOTES.md` |

Pooled Morlet energies and localized cells (the existing extractor) are **not**
reconstructive and are not used here. The DWT is a separate research baseline.

## Run

```sh
uv venv --python 3.12 .venv-research
uv pip install --python .venv-research/bin/python -r research/multiscale_memory/requirements.txt
cd research/multiscale_memory
../../.venv-research/bin/python -m unittest discover -s tests

# PTB-XL 1.0.3 (CC BY 4.0) 100 Hz records and metadata from the PhysioNet open-data mirror:
aws s3 sync --no-sign-request s3://physionet-open/ptb-xl/1.0.3/records100/ ../../data/ptb-xl/1.0.3/records100/
for f in ptbxl_database.csv scp_statements.csv SHA256SUMS.txt LICENSE.txt; do
  aws s3 cp --no-sign-request s3://physionet-open/ptb-xl/1.0.3/$f ../../data/ptb-xl/1.0.3/$f; done

OUT=../../data/msmem-runs/pilot
PY="../../.venv-research/bin/python experiments/ptbxl_h2.py"
$PY verify --out $OUT && $PY prepare --out $OUT && $PY retrieve --out $OUT && $PY contexts --out $OUT
# Phase 1, then the validation-only gate, then phase 2 if gate.json passes (PROTOCOL.md):
for arm in A0 A1 A3 A3n R64n W64n C64n; do
  for seed in 0 1 2; do $PY train --out $OUT --arm $arm --seed $seed; done; done
$PY gate --out $OUT
for arm in A2 R32 R64 R128 W32 W64 W128 C32 C64 C128 R32n R128n W32n W128n C32n C128n; do
  for seed in 0 1 2; do $PY train --out $OUT --arm $arm --seed $seed; done; done
$PY evaluate --out $OUT       # reads the test fold once
../../.venv-research/bin/python experiments/ptbxl_h2_supplementary.py $OUT
```

Data stays under `data/` (git-ignored) and is never redistributed. Cite PTB-XL
(Wagner et al., 2020, *Scientific Data*) and PhysioNet when reporting results.
