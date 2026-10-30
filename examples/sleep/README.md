# Example: sleep staging by example

A small local application on top of MorletDB and pgvector. Pick a held-out night
from Sleep-EDF, click any 30-second epoch of its EEG, and see:

- the stage given by the **40 most similar expert-scored epochs from other people**;
- how those 40 voted;
- who each one comes from (subject, night, clock time), its distance and its waveform;
- the exact `morlet_read_interval` call that reads it back from MorletDB.

The setup is the frozen [S1 evaluation](../../evaluation/sleep/RESULTS.md) (its
M-KNN arm), made browsable. S1 found that this kind of retrieval stages sleep as
well as a simple classifier, with an explanation attached, and that Morlet features
do no better than FFT band powers. The demo shows the first part; it is not
evidence for the wavelet.

> **Research demo, not a medical device.** One EEG channel (Fpz-Cz) from healthy
> subjects recorded in 1987–1991. Agreement with the expert is moderate (mean kappa
> 0.58). The neighbour share is not a probability. Nothing here supports a diagnosis.
> The data stays on your machine; the page loads nothing from the network.

## How it is built

| Piece | What it does |
|---|---|
| MorletDB stream collection `sleep_eeg` | One raw-only recording per night (`extractor => 'none'`), from 30 min before sleep onset to 30 min after the last sleep epoch. Samples are the EDF physical values in µV, stored losslessly. |
| `sleep_epochs` | One row per scored epoch: recording, sample interval, subject, night, split and expert stage. |
| `features` | `morlet_features(samples, 100, 'morlet-energy-lowrate-v1')`, computed from the samples **read back out of the recording**. |
| `emb vector(15)` | Features standardized with development-subject statistics (`sleep_scaler`). |
| `sleep_gallery_hnsw` | Partial pgvector HNSW index over development epochs only. |

Queries are held-out subjects; the gallery is development subjects (the split in
[`evaluation/sleep/split.json`](../../evaluation/sleep/split.json)), so a query is
never matched against the same person. The vote uses S1's rule: majority, with ties
going to the nearest tied neighbour.

## Run it

Needs PostgreSQL 17 with pg_morlet (SQL 0.3.1) and pgvector 0.8.0 (see
[VR1](../../evaluation/vector/README.md)), and Sleep-EDF Expanded 1.0.0
(sleep-cassette, 7.1 GB) from PhysioNet under `data/sleep-edfx/1.0.0`.

```sh
createdb -h localhost -p 28817 morlet_sleep_demo
export MORLET_DSN="host=localhost port=28817 dbname=morlet_sleep_demo"
P=.venv-seismic/bin/python   # numpy, psycopg
$P -I examples/sleep/load.py data/sleep-edfx/1.0.0/sleep-cassette     # about 40 min; resumable
$P -I examples/sleep/app.py                                           # http://127.0.0.1:8766
```

The server binds to 127.0.0.1, answers only requests addressed to it, and uses
read-only sessions. Arrow keys step through epochs.

Optional checks (they decide nothing) and tests:

```sh
$P -I examples/sleep/check.py data/sleep-edfx/1.0.0/sleep-cassette data/sleep-edfx/run examples/sleep/results
MORLET_SLEEP_DEMO_DSN="$MORLET_DSN" $P -I examples/sleep/test_app.py
```

`check.py` needs S1's stored features (`data/sleep-edfx/run`, from `evaluation/sleep/s1.py`).

## Checks

From [`results/check.json`](results/check.json): 153 nights, 195,469 epochs,
587 M samples.

| Check | Result |
|---|---|
| Samples read back vs EDF (300 random epochs) | 300 bitwise equal |
| Features from stored samples vs S1's features | 195,469 of 195,469 identical (max difference 0.0) |
| HNSW top 40 vs exact scan (500 queries, `ef_search` 200, tie-aware) | recall 1.000; the plan uses the index |
| Demo staging of all 98,159 held-out epochs vs S1 M-KNN | 99.997% identical (3 epochs differ); mean kappa 0.5828 vs 0.5828 |

The three differing epochs come from pgvector storing float32, against S1's float64.

**Storage finding.** The raw chunks take 3.2 GB, about 43 bits per sample. Most
chunks fall back from grid to float encoding. Sleep-EDF stores
`physical = digital × step + offset`, and in most files the offset is not a multiple
of the step, so samples are not on the `k × quantum` grid the encoder looks for. The
values stay exact; only the space is lost. Supporting an offset in the grid would
fix it; that is a chunk-format change and is not part of this example.

## Data and citation

Sleep-EDF Expanded 1.0.0, sleep-cassette study (Kemp et al., 2000), via PhysioNet
(Goldberger et al., 2000). Open Data Commons Attribution License v1.0.
