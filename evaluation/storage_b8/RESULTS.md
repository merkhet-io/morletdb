# Study B8 results: float32 codings (chunk format version 6)

Data: IU4's milling runs (Stiehl, Denkena et al., *Multivariate time series data of milling processes with varying
tool wear and machine tools*, Data in Brief 2023; Mendeley Data doi:10.17632/zpxs87bjt8.3, CC BY 4.0), force and
controller channels as float32 values, and B6's inputs.

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen at `4325312`, build record §7.1 (build `4ed5e90`, image
`morletdb:b8-4ed5e90`, `sha256:7f074a1f…`). Benchmark run 2026-11-14, 11:51–11:57 local, on the inputs no
development step read: IU4 runs[15::20] (321 runs) and B6's 30 benchmark inputs. Files: `results/bench/`.

## Verdict: all six criteria hold

| criterion | limit | measured | |
|---|---|---|---|
| S1 exact | every chunk bit-exact | all 36 inputs, all arms | pass |
| S2 encoded size, G1+6 | force ≤ 3.3, ctrl ≤ 2.6 B/sample | 3.022, 2.293 | pass |
| S3 table bytes | force < 3.65, ctrl < 3.27 B/sample | 3.363, 3.026; 0 series not exact | pass |
| S4 speed, G1+6 | decode ≥ 50, encode ≥ 10 M samples/s | slowest decode 69.0 (ctrl), slowest encode 12.68 (pad-c) | pass |
| S5 no regression | no larger chunk; others byte-identical | 0 larger chunks, G1+6 and G21+6, all 30 inputs | pass |
| S6 platforms | same bytes on 3 platforms | macOS arm64, Linux arm64, Linux amd64 (build record) | pass |

By the decision recorded with the protocol, version 6 can now become the default (`morlet.float32_chunks = on`).

## Figures

Bytes per sample (`results/bench/storage.json`, `table.json`, `pcodec.json`):

| input | samples | G1 | G1+6 | G21 | G21+6 | table, G1+6 | pcodec 1.0.4 |
|---|---:|---:|---:|---:|---:|---:|---:|
| force | 107,492,700 | 6.598 | 3.022 | 6.579 | 3.018 | 3.363 | 2.874 |
| ctrl | 4,301,484 | 6.522 | 2.293 | 6.236 | 2.201 | 3.026 | 1.581 |

Version 6 roughly halves the IU4 float32 channels (force −54 %, ctrl −65 %). pcodec stays smaller, by 5 % on force
and 31 % on ctrl in encoded bytes. In the table the server kept version 6 for 26,503 of 26,682 force chunks and
1,911 of 1,926 ctrl chunks.

On B6's inputs version 6 changes little: it is kept in 986 of 1,024 cx chunks (−4.1 % against G1; −0.05 % against G21, where Grizzbit 2.1 already codes them well), 528 of 1,024 Paderborn
count chunks (−1.1 %) and a few Stocks-UK chunks (−0.3 %). Every other B6 input is byte-identical.

Speed, single thread, best of 3, every pass under the start condition (load below 3, no busy process;
`results/bench/speed.json`), M samples/s:

| input | G1 encode | G1+6 encode | G1 decode | G1+6 decode |
|---|---:|---:|---:|---:|
| force | 23.55 | 22.56 | 62.95 | 89.92 |
| ctrl | 21.20 | 13.01 | 74.54 | 69.04 |
| cx | 63.82 | 14.53 | 121.23 | 104.40 |
| pad-c | 61.67 | 12.68 | 142.23 | 120.50 |
| other 10 inputs | — | within 1.2 % of G1 | — | within 1.2 % of G1 |

## Limits and costs, stated plainly

- **Encode cost on integer counts.** Counts that fit in float32 (cx, Paderborn counts) take the version 6 attempt, and
  encode drops from about 62 to 13–15 M samples/s for a 1–4 % size gain. That still passes S4, but it is the price
  of turning the setting on by default. Making the attempt cheaper is a candidate for later work, not part of this
  claim.
- **ctrl encode** with version 6 is 13.0 M samples/s, 30 % above the floor.
- **Workload.** The size gain is measured on IU4 force and ctrl (float32 sensor values stored as float64) and on B6's
  inputs. It is not a claim for other float32 sources.
- **S6** ran locally in Docker, not in CI, because the branch is not pushed.
- **pcodec** is a reference without a criterion; it remains smaller.
