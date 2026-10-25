# Seismic replay evaluation: cx-north-chile-2024-v1

Generated 2026-10-25T15:57:53.387884Z on macOS-27.0.1-arm64-arm-64bit (arm64), Python 3.12.13, ObsPy 1.4.2.

Code `0b41233378070150e6e9ca322c48d3d25392fa20`; representation `morlet-energy-lowrate-v1` (window 2048, stride 512, zscore); detector `stalta-baseline-v1`; definition sha256 `398311d6e0e76dae…`; manifest sha256 `c03f4126f016a102…`; database `seismic_eval_0b41233` (pg_morlet 0.1.0, stored extractor versions ['morlet-energy-lowrate-v1']).

Research diagnostics only. Distances are representation distances, not probabilities; catalog labels carry location and travel-time uncertainty; unmatched triggers are not automatically false positives.

## Data and ingestion

| Stream | Split | Segments | Station-hours | Monitored hours |
|---|---|---:|---:|---:|
| CX.PB01..HHZ | reference | 1 | 168.00 | 167.97 |
| CX.PB01..HHZ | heldout | 1 | 132.00 | 131.97 |
| CX.PB02..HHZ | reference | 1 | 168.00 | 167.97 |
| CX.PB02..HHZ | heldout | 1 | 132.00 | 131.97 |
| CX.PB08..HHZ | reference | 132 | 167.94 | 163.54 |
| CX.PB08..HHZ | heldout | 108 | 131.95 | 128.35 |

| Stream | Committed samples | Duplicate samples verified | Gaps | Conflicts | Errors |
|---|---:|---:|---:|---:|---:|
| CX.PB01..HHZ | 108000000 | 7165 | 1 | 0 | 0 |
| CX.PB02..HHZ | 108000000 | 5776 | 1 | 0 | 0 |
| CX.PB08..HHZ | 107959193 | 9258 | 239 | 0 | 0 |

Storage: chunks 0.92 GB, segment features 264.3 MB, 631865 windows. Raw miniSEED cache 311 MB.

Server-side extraction per 2048-sample window: p50 4.72 ms, p90 6.11 ms (n=200).
- Replay ['CX.PB01..HHZ']: 913 s, actions {'no_earlier_segment': 1, 'append': 36000, 'partial_duplicate': 11, 'gap': 1}, append batch p50/p90 0.333/0.389 s, emit-to-commit p90 0.390 s.
- Replay ['CX.PB02..HHZ']: 908 s, actions {'no_earlier_segment': 1, 'append': 36000, 'partial_duplicate': 11, 'gap': 1}, append batch p50/p90 0.330/0.386 s, emit-to-commit p90 0.387 s.
- Replay ['CX.PB08..HHZ']: 906 s, actions {'no_earlier_segment': 1, 'append': 35891, 'gap': 239, 'partial_duplicate': 68}, append batch p50/p90 0.325/0.377 s, emit-to-commit p90 0.384 s.

## Detection (STA/LTA baseline)

| Stream | Split | Monitored h | Triggers | Episodes | Unmatched episodes/h |
|---|---|---:|---:|---:|---:|
| CX.PB01..HHZ | reference | 168.0 | 3709 | 2644 | 15.65 |
| CX.PB01..HHZ | heldout | 132.0 | 2885 | 2090 | 15.73 |
| CX.PB02..HHZ | reference | 168.0 | 3090 | 2405 | 14.23 |
| CX.PB02..HHZ | heldout | 132.0 | 2334 | 1839 | 13.87 |
| CX.PB08..HHZ | reference | 163.5 | 1228 | 937 | 5.62 |
| CX.PB08..HHZ | heldout | 128.3 | 951 | 742 | 5.67 |

Catalog station-arrivals: 90 (86 monitored, 4 in gaps or warm-up). Detected 77, missed 9 (regional 77/77, teleseismic 0/9).

Onset minus predicted P (IASP91): median 0.48 s, p90 1.49 s, max 2.00 s (n=77).

## Retrieval

### CX.PB01..HHZ

Gallery: 4096 reference windows {'trigger': 3709, 'background': 387}, 29 overlap catalog arrival windows; 761856 posting entries.

catalog associated queries: 14

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.371 | 0.431 | 0.143 | 118122 | – | 0.547 |
| pooled-gallery | 0.471 | 0.729 | 0.286 | 4096 | – | 0.018 |
| temporal-exhaustive | 0.243 | 0.576 | 0.286 | 4096 | 0 | 0.085 |
| temporal-approx | 0.214 | 0.583 | 0.286 | 64 | 328876 | 0.024 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.286 | 0.286 | 328876 |
| 64 | 0.443 | 0.443 | 328876 |
| 256 | 0.629 | 0.629 | 328876 |
| 1024 | 0.671 | 0.671 | 328876 |

Queries where pooled-gallery and temporal precision are equal: 5

unassociated queries: 60

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.000 | 0.000 | – | 118122 | – | 0.516 |
| pooled-gallery | 0.003 | 0.003 | – | 4096 | – | 0.016 |
| temporal-exhaustive | 0.000 | 0.000 | – | 4096 | 0 | 0.079 |
| temporal-approx | 0.000 | 0.000 | – | 64 | 690514 | 0.035 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.113 | 0.113 | 690514 |
| 64 | 0.203 | 0.203 | 690514 |
| 256 | 0.317 | 0.317 | 690514 |
| 1024 | 0.537 | 0.537 | 690514 |

Queries where pooled-gallery and temporal precision are equal: 59

### CX.PB02..HHZ

Gallery: 4096 reference windows {'trigger': 3090, 'background': 1006}, 30 overlap catalog arrival windows; 761856 posting entries.

catalog associated queries: 9

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.244 | 0.454 | 0.111 | 118122 | – | 0.518 |
| pooled-gallery | 0.289 | 0.500 | 0.222 | 4096 | – | 0.019 |
| temporal-exhaustive | 0.111 | 0.100 | 0.111 | 4096 | 0 | 0.090 |
| temporal-approx | 0.044 | 0.139 | 0.000 | 64 | 268339 | 0.020 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.200 | 0.200 | 268339 |
| 64 | 0.511 | 0.511 | 268339 |
| 256 | 0.733 | 0.733 | 268339 |
| 1024 | 0.778 | 0.778 | 268339 |

Queries where pooled-gallery and temporal precision are equal: 4

unassociated queries: 60

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.003 | 0.017 | – | 118122 | – | 0.522 |
| pooled-gallery | 0.003 | 0.003 | – | 4096 | – | 0.020 |
| temporal-exhaustive | 0.000 | 0.000 | – | 4096 | 0 | 0.092 |
| temporal-approx | 0.003 | 0.006 | – | 64 | 655957 | 0.040 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.183 | 0.183 | 655957 |
| 64 | 0.273 | 0.273 | 655957 |
| 256 | 0.360 | 0.360 | 655957 |
| 1024 | 0.557 | 0.557 | 655957 |

Queries where pooled-gallery and temporal precision are equal: 59

### CX.PB08..HHZ

Gallery: 3241 reference windows {'trigger': 1221, 'background': 2020}, 30 overlap catalog arrival windows; 602826 posting entries.

catalog associated queries: 14

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.386 | 0.570 | 0.000 | 117616 | – | 0.559 |
| pooled-gallery | 0.214 | 0.310 | 0.143 | 3241 | – | 0.019 |
| temporal-exhaustive | 0.214 | 0.411 | 0.214 | 3241 | 0 | 0.077 |
| temporal-approx | 0.186 | 0.429 | 0.143 | 64 | 208218 | 0.018 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.429 | 0.429 | 208218 |
| 64 | 0.643 | 0.643 | 208218 |
| 256 | 0.786 | 0.786 | 208218 |
| 1024 | 0.814 | 0.814 | 208218 |

Queries where pooled-gallery and temporal precision are equal: 4

unassociated queries: 60

| Mode | Catalog P@5 | MRR | Same-source hit | Scored p50 | Postings p50 | Query p50 s |
|---|---:|---:|---:|---:|---:|---:|
| pooled-pg | 0.003 | 0.008 | – | 117616 | – | 0.528 |
| pooled-gallery | 0.003 | 0.006 | – | 3241 | – | 0.016 |
| temporal-exhaustive | 0.007 | 0.006 | – | 3241 | 0 | 0.072 |
| temporal-approx | 0.000 | 0.000 | – | 64 | 477906 | 0.030 |

| Candidate budget | Candidate recall (mean) | Top-5 recall vs exhaustive (mean) | Posting visits p50 |
|---:|---:|---:|---:|
| 16 | 0.293 | 0.293 | 477906 |
| 64 | 0.423 | 0.423 | 477906 |
| 256 | 0.483 | 0.483 | 477906 |
| 1024 | 0.633 | 0.633 | 477906 |

Queries where pooled-gallery and temporal precision are equal: 59

