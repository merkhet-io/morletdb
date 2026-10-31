# Offset grids: lossless storage for calibrated data whose zero is not on the step grid

This is an engineering change with measurements, not a frozen evaluation. It adds chunk
format version 3 ([chunk format](../../../docs/chunk-format.md)).

## The problem

EDF and similar formats store integer counts d plus a calibration. In Sleep-EDF:

    physical = (d - dmin) * (pmax - pmin) / (dmax - dmin) + pmin

- **Symmetric range** (pmin = −pmax, e.g. SC4001: −192 … 192): every value sits half a
  step off zero. A quantum of half the step therefore puts them on a grid through zero,
  which is what the sleep loader uses.
- **Asymmetric range** (SC4002: −208 … 209): the offset is 5.41 steps. No grid through
  zero fits, so the encoder fell back to coding float bits.

In the 153-night sleep example, **100 nights had such chunks**: 95,246 float and 1,622
raw chunks, against 46,582 grid chunks. Raw storage was **45.6 bits per sample**
(3.35 GB). Relative to their own offset, the samples sit on the step to within 3·10⁻¹³
of a step.

## The change

- **Format version 3:** a version-3 chunk is a grid with an `offset` field in its header,
  so x = offset + k·step.
- **When the encoder tries it:** only for a chunk with a sample off the plain grid. It
  takes the offset from the first sample, refits step and offset to within one ulp, and
  stores the remaining rounding as last-place corrections, as plain grids already do.
  It keeps the result if it is smaller than plain float64. Otherwise the chunk is coded
  as float bits or raw, as before.
- **Chunks on the plain grid** keep their version-2 encoding byte for byte.
- **Decoders** read versions 2 and 3.
- **No API, schema or SQL change:** the encoder needs nothing beyond `sample_quantum`.

## Results

### Re-encoding every stored chunk (`reencode.py`)

Each chunk is decoded, re-encoded with the recording's `sample_quantum`, and checked
bitwise against the original samples. `results/reencode-main.json` is `main` at df425a2;
`results/reencode-offset-grid.json` is this branch.

| Collection | Samples | `main` bits/sample | This branch | Chunks rewritten | Bitwise mismatches | Encoder speed (M samples/s) |
|---|---|---|---|---|---|---|
| Sleep-EDF, 153 nights | 587 M | 45.63 | **15.60** | 96,868 v3, 46,582 byte-identical | 0 | 7.6 → 5.7 |
| CX seismic counts (P2) | 4.2 M | 4.61 | 4.61 | 0 (all byte-identical) | 0 | 12.6 → 12.2 |
| IMS three-decimal values (P2) | 4.2 M | 8.81 | 8.81 | 0 | 0 | 12.3 → 11.8 |
| Paderborn counts (P2) | 4.2 M | 7.88 | 7.88 | 0 | 0 | 11.9 → 11.8 |
| Paderborn physical floats (P2) | 4.2 M | 9.17 | 9.17 | 0 | 0 | 6.5 → 6.4 |

- **Sleep-EDF:** the encoded sleep collection shrinks from 3.35 GB to 1.15 GB (2.9×).
  Every chunk is now a grid chunk.
- **The P2 datasets** encode exactly as before.
- **The encoder is slower only on chunks it now compresses:** an offset-grid chunk codes
  two streams, indices and corrections.

### End-to-end append (`append_bench.py`, `results/append.json`)

One asymmetric and one symmetric night are appended into a fresh raw-only collection,
in 4096-sample chunks with 16 chunks per transaction, as the loader does.

| Night | `main`: append time (5 runs) | `main`: bits/sample | This branch: append time (4 runs) | This branch: bits/sample |
|---|---|---|---|---|
| SC4002 (asymmetric, 3.38 M samples) | 5.85–6.10 s | 60.2 | **5.36–5.53 s** | **15.4** |
| SC4001 (symmetric, 2.52 M samples) | 3.99–4.18 s | 14.0 | 3.99–4.04 s | 14.0 |

- **Appends are 7–10% faster** for asymmetric nights. Storing 3.9× fewer bytes more than
  repays the slower encoder.
- **Symmetric nights are unchanged.**
- **Correction about runs:** two runs labelled as this branch while measuring were in
  fact `main`. A shared Cargo target directory had installed `main`'s library. They are
  counted under `main` (see `note` in the JSON).

## Checks

- **Codec unit tests:** asymmetric and half-step EDF ranges; plain grids keep version 2;
  offset grids in division mode; negative zero; ±1e9 extremes; lengths 1–257. Malformed
  version-3 headers are rejected (non-finite offset, float or raw kind under version 3,
  version-byte confusion), and every single-bit flip, overwrite and truncation of an
  offset-grid chunk is detected.
- `make test` (44 core tests) and `cargo pgrx test pg17` (74 tests) pass.
- `tests/upgrade_from_release.sh v0.3.0-alpha.1 0.3.0` passes: data written by the
  released 0.3.0 reads back unchanged.

## Limits

- **15.6 bits/sample is still more than a 12-bit EDF needs.** The likely cause, not yet
  measured, is last-place corrections. The readers' formula, (d − dmin)·R/D + pmin,
  rounds differently from offset + k·step. Modelling that formula exactly could remove
  them, but it would make the codec format-specific.
- **Downgrade:** pg_morlet 0.3.0 rejects a version-3 chunk ("unknown format version").
  Downgrading after appending such chunks needs them re-appended.
- **Existing chunks are not rewritten in place:** the gain applies to new appends. Copying
  a recording into a new collection re-encodes it.

## Reproduce

```sh
.venv-seismic/bin/python -I evaluation/compression/offset_grid/reencode.py OUT.json \
  morlet_sleep_demo:sleep_eeg morlet_p2_bench:m_cx morlet_p2_bench:m_ims \
  morlet_p2_bench:m_paderborn_counts morlet_p2_bench:m_paderborn_physical
.venv-seismic/bin/python -I evaluation/compression/offset_grid/append_bench.py OUT.json
```

Run on an Apple M5 Max with PostgreSQL 17.11 (pgrx) and a release build.
`morlet_sleep_demo` is built by `examples/sleep/load.py`; `morlet_p2_bench` by
`evaluation/plain_postgres/p2_bench.py`.
