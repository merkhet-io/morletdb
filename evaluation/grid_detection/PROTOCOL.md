# G1: automatic calibration-grid detection (FROZEN)

Frozen 2026-11-02 in the commit that adds this file, before G1 was run. The detector was developed on
synthetic grids and on chunks of the B3 *development* set (Paderborn vibration, current and force).

## Question

MorletDB's storage advantage comes from coding samples as integers on their value grid. Until now the grid
had to be declared (`sample_quantum`) or come from the file format; floats sent without it were coded as
float bit patterns. Does detecting the step from each chunk's own values recover the declared-quantum
size when no quantum is given, without ever making a chunk meaningfully larger?

## Change under test

The encoder (`morlet_core::rawcodec::encode`), for a chunk off its declared grid, estimates a step from the
chunk (smallest gap between distinct values, or that gap divided by 2–8), accepts it only if every distinct
value is within 1/1000 of a step of a multiple, encodes on that grid (plain or offset) with the existing
step refinement and last-place corrections, and keeps the result if it needs no corrections or codes in at
most half of plain float64; otherwise it keeps the smaller of it and the previous encoding. The decoder and
chunk format are unchanged.

## Inputs (fixed now)

B1's six inputs, as prepared by `evaluation/storage_baselines/prepare.py` (`data/b1-runs/inputs`, `data/b1-runs/p2`):
CX seismic counts, IMS three-decimal values, Paderborn counts, Paderborn physical floats, Sleep-EDF Fpz-Cz,
PTB-XL ECG lead I; 4,096-sample chunks as in B1; and the B3 benchmark set (all channels, 101.6 M samples).

## Arms

- **D:** the declared quanta (as B1 and B3).
- **A:** quantum 1 for every chunk, this encoder (detection).
- **P:** quantum 1 for every chunk, the previous encoder (`main` at the commit before this change).

## Measures and acceptance criteria

1. Every chunk of every arm round-trips bitwise (the bench asserts it).
2. **Recovery:** on each of the six B1 inputs, A is within 5% of D in bits per sample.
3. **No meaningful loss:** on each input, A is at most 0.1% larger than P.
4. Reported, not judged: encode and decode ns/sample for D, A and P (one machine, single thread).
5. **B3:** one D2 load of the benchmark set with quantum 1 for every channel (`morletdb-auto`), full
   correctness; sample-data storage reported next to the declared-quantum load (156.75 MiB) and QuestDB's
   Parquet zstd 9 (232.7 MiB); ingestion reported.
