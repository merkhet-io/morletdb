# L1: the operating envelope of MorletDB (DRAFT, not frozen)

**Question.** Where does MorletDB work, and where should another system be used instead? L1 measures how
correctness, speed, size and memory behave at the edges of each workload dimension, one dimension at a time,
and where MorletDB crosses over against a row-oriented time-series database. The output is a published
**operating envelope**: "tested up to X; beyond Y use something else", every boundary a measured knee or
crossover, not an opinion.

Status: draft. It freezes (commit recorded here) before any measurement on the benchmark sets below;
generators and harness are developed on the small development sizes only.

## Rules

- **Correctness is a hard gate everywhere:** every stored sample read back bit for bit (SHA-256 per
  recording), every derived time equal to the reference, every refused write refused with an error. A
  correctness failure is a finding to report, not something to tune away.
- **One dimension at a time:** all other parameters at the baseline (64 kHz, one recording of 60 s per
  channel, 4,096-sample chunks, calibrated ADC values, one writer, one reader, durable commits, 8 CPUs and
  16 GB container, PostgreSQL 17, release image of the frozen commit).
- **Knee:** the smallest value at which a metric is worse than 2× its baseline, or a hard error, out-of-memory
  event or refusal occurs. Points are spaced by factors of 2–10; the curve is reported in full.
- **Crossover** (only where a rule below names one): against TimescaleDB 2.x on PostgreSQL 17, rows with one
  timestamp and value column per channel and its documented compression, same container limits. A crossover
  is the smallest dimension value at which TimescaleDB is ≥ 10% better on the named metric in all three
  repeats.
- Three repeats per point; medians and ranges; no tuning between repeats; deviations appended.

## Dimensions

| # | Dimension | Points | Metrics | Crossover against TimescaleDB |
|---|---|---|---|---|
| D1 | Sample rate | 1/3600 Hz, 1/60, 1/3, 1, 100, 1 k, 25.6 k, 44.1 k, 64 k, 1 M, 10 M Hz | exact times (including non-integer periods: 44.1 kHz, ⅓ Hz), ingest samples/s of one stream, time from sample to queryable | none |
| D2 | Recording length | 10⁴ … 10¹⁰ samples in one recording | read, statistics and overview latency at the start, middle and end; checkpoint and TOAST size; append latency as the recording grows | none |
| D3 | Number of recordings | 1, 10², 10⁴, 10⁵, 10⁶ recordings of 1 Hz for one day | storage per recording, catalogue and filtered-listing latency, ingest of all channels for one interval, a dashboard query (mean per hour across all channels) | **yes**: storage and dashboard-query latency |
| D4 | Value content | integer counts, decimals, calibrated ADC values, full-entropy random floats, constants, NaN/±Inf, denormals, extreme ranges | bits per sample vs raw float64, exactness, encode and decode speed | **yes**: storage on full-entropy floats |
| D5 | Ingest pattern | chunk sizes 1 … 65,536 samples; 1 … 64 concurrent writers; gaps; late and out-of-order chunks; overlapping resends | samples/s, commit latency, WAL bytes per sample, behaviour of late data (must be refused or stored, never silently merged) | **yes**: one-sample appends |
| D6 | Query extremes | intervals up to 2³⁰ samples; overviews of a year; search galleries of 10⁵ … 10⁸ windows with filters selecting 100% … 0.01% | latency, memory, refusals at the documented budgets | none |
| D7 | Resources | 2 GB, 512 MB, 256 MB memory; 2 / 1 CPUs; a slow card; a full disk | out-of-memory events, behaviour at disk full (must fail visibly, data intact) | none |
| D8 | Irregular data | event series with random timestamps | documented as out of scope; L1 shows the cost of forcing them into recordings | **yes**: storage and range queries |

## Data

Synthetic generators with fixed seeds (sine bursts plus noise on an ADC grid, as used in the walkthrough), and
real data at the edges: a high-rate set (acoustic emission or ultrasound in the MHz range, to be chosen and
approved before the freeze), B3's Paderborn set at 64 kHz, and a low-rate set (10-minute weather or SCADA data).
Downloads are named with exact versions and sizes and approved before they happen.

## Output

1. `RESULTS.md` with every curve, knee and crossover, and every correctness finding.
2. A website table "Tested limits" (dimension, tested range, knee, what to use beyond it).
3. The "where another choice fits better" list rewritten from the crossovers.
4. Issues for every knee that looks like an engineering fix rather than a design limit.

## Not in L1

Clusters and replication (production-grade work, separate), power loss and multi-day soak (separate, need
hardware), retrieval quality (E-studies).
