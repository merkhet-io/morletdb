# L1: the operating envelope of MorletDB (DRAFT, not frozen)

**Question.** Where does MorletDB work, and where should another system be used instead? L1 measures how
correctness, speed, size and memory behave at the edges of each workload dimension, one dimension at a time,
and where MorletDB crosses over against a row-oriented time-series database. The output is a published
**operating envelope**: "tested up to X; beyond Y use something else", every boundary a measured knee or
crossover, not an opinion.

Status: draft. It freezes (commit recorded here) before any measurement on the benchmark sets below;
generators and harness are developed on the small development sizes only.

## Stages

- **L1-beta (closes #318, gate for the beta):** D1, D2, D4, D5 and D7 at the reduced points below, synthetic data
  only, no crossovers. Question: within which rates, lengths, value kinds, ingest patterns and resources is
  MorletDB correct and how fast, and what happens at and beyond each limit?
- **L1-1.0 (before 1.0):** everything else in this protocol: D3, D6, D8, D9, the TimescaleDB crossovers, the full
  point lists and the real data sets.

| # | L1-beta points |
|---|---|
| D1 | 1/60 Hz, ⅓ Hz, 1 Hz, 1 kHz, 44.1 kHz, 64 kHz, 1 MHz |
| D2 | 10⁴, 10⁶, 10⁸, 10⁹ samples in one recording |
| D4 | all value kinds of the table |
| D5 | chunk sizes 1, 64, 4,096, 65,536; 1, 4 and 16 concurrent writers; gaps, late, out-of-order and overlapping chunks |
| D7 | 2 GB, 512 MB, 256 MB memory; 1 CPU; a full disk |

Same rules, metrics, knee definition and three repeats as below. The machine is named at the freeze; the results
state that the envelope holds for that machine and scales with it where the curve says so.

## L1-beta, frozen (2026-11-08)

Frozen in the commit that adds this section and `l1.py`, `summarize.py` and `run_l1.sh`, before any reported
measurement. Development runs (`l1.py --dev`, a tenth of the samples, `results/dev/`) built and checked the harness
on five points and are not reported. Where this section differs from the draft above, this section applies.

- **System:** the release image `ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1` (SQL 0.3.7; image id recorded per
  run), data checksums, `max_wal_size=1GB`, otherwise image defaults (durable commits); a fresh container and volume
  per point and repeat; one collection with chunk summaries, no search structures (search limits are D6, for 1.0).
- **Machine:** the owner's Apple M5 Max, Docker Desktop (VM: 12 CPUs, 33.6 GB); the client on the same host.
  Start condition as in B3.16 (`run_l1.sh`: load average below 3, no foreign process above half a core, AC power).
- **Baseline:** 8 CPUs, 16 GB; 64 kHz; 4,096-sample chunks; calibrated ADC values on a grid of 100/32768; one writer
  sending batches of 64 chunks (262,144 samples) through `morlet_append_chunks_raw`, one durable commit per batch.
- **Points** (`l1.py list`), with the samples per recording:
  - **D1 rate:** 1/60, 1/3, 1, 1,000, 44,100, 64,000, 1,000,000 Hz, 1,048,576 samples each (a fixed count, not
    60 s: at 1/60 Hz a minute is one sample). Baseline point: 64,000 Hz.
  - **D2 length:** 10⁴, 10⁶, 10⁸, 10⁹ samples in one recording. Baseline point: 10⁶.
  - **D4 values:** integer counts, decimals (3 places), calibrated ADC values, full-entropy floats, a constant,
    denormals, values at ±1e9, NaN and ±Inf; 1,048,576 each. Baseline point: ADC values.
  - **D5 ingest:** chunk sizes 4,096, 64, 1 (65,536 samples) and 65,536; 1, 4 and 16 concurrent writers with
    1,048,576 samples each; the pattern point: a gap, an identical replay, a late chunk with one altered sample, an
    overlap of stored and new samples, and an out-of-order range, each after half of 262,144 samples.
  - **D7 resources:** the 10⁸-sample workload of D2 within 2 GB, 512 MB and 256 MB memory (8 CPUs), within 1 CPU
    (16 GB), and a full disk: a 400 MB data directory (tmpfs) and a 10⁹-sample target. Baseline point: D2's 10⁸.
- **Per point:** correctness first (every stored sample read back and compared with the generator by SHA-256; the
  stored end; at D1 1,002 derived times against an exact rational reference; at D5's pattern point each case
  refused or stored and the earlier samples unchanged; after a refused or failed write, the stored samples intact
  and readable, and whether the server restarted); then ingest samples/s, commit latency (p50, p95, first and last
  tenth), full read-back samples/s, reads of 4,096 samples and statistics over 10⁶ samples at the start, middle and
  end, an overview of the whole recording (100 buckets), storage (chunk table with TOAST and indexes, checkpoint),
  and container memory (cgroup peak, out-of-memory kills).
- **Repeats:** three per point; medians and ranges (`summarize.py`).
- **Knee** (`summarize.py`): a point is a knee if it shows an error, refusal, correctness problem or out-of-memory
  kill, or if ingest or read-back samples/s, the read latencies, the statistics or the overview are more than 2×
  worse than the dimension's baseline point. A refusal at a documented limit (NaN, ±Inf, beyond ±1e9, chunks above
  32,768) is reported as the limit, not as a defect.
- **Output:** `RESULTS.md` with every point, knee and refusal; the website and docs state the envelope; an issue for
  every knee that looks like an engineering fix rather than a design limit.

**Expectation (written before measuring):** D1: rates are stored as given, so all rates should store and time
exactly; 1 MHz may reach the ingest limit of one writer only in samples/s, not in correctness. D2: reads at the end
of a 10⁹-sample recording should stay near the start's; the commit latency may grow with the checkpoint. D4: NaN,
±Inf refused; random floats near 64 bits per sample, the constant near zero. D5: chunk size 1 is far slower per
sample (a knee); 65,536 refused; writers scale until the client or one CPU limits. D7: 256 MB may hold (the
memory-cap study M1 completed B3 within 256 MB); 1 CPU will be slower; the full disk must fail visibly with the
stored samples intact.

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
| D9 | Archive scale on one node | windows 10⁶, 10⁷, 10⁸ (10⁹ by extrapolation from the measured curve, labelled as such); recordings 10⁶, 10⁷, 10⁸ (short and long recordings); archive volume 10 GB, 100 GB, 1 TB (10 TB if hardware allows) | probe p95 and recall as the HNSW index outgrows memory (at a fixed 16 GB and a fixed 64 GB limit); screened-exact p95 unfiltered and at 10% / 1% / 0.1% filter selectivity; catalogue, listing and filter latency; storage per window and per recording; VACUUM, index build, backup and restore time | none; output = the tipping points below |


## D9: scaling questions and tipping points

D9 answers how far one node scales and where the current search design stops working. A **tipping point** is the
smallest scale at which a fixed criterion fails in all three repeats:

1. **Memory:** probe search p95 more than 2× its in-memory value, or recall@10 below 0.95, as the HNSW index
   exceeds the container's memory (measured at 16 GB and 64 GB, so the curve can be scaled to other machines).
2. **Interactive exact search:** screened exact search p95 above 1 s, unfiltered and per filter selectivity.
3. **Catalogue:** listing or filtering recordings by metadata above 100 ms p95, or ingest of a new recording
   slowed more than 2× by the catalogue's size.
4. **Operations:** VACUUM of the companion tables, index rebuild or a full restore longer than 8 h.

Expected from E1's sizes (about 3.1 KB of search structures per window; a model, to be replaced by the
measurement): the HNSW index leaves 16 GB of memory near 1.4 × 10⁷ windows and 128 GB near 10⁸; screened exact
search passes 1 s near 2 × 10⁶ unfiltered windows; 10⁹ windows need about 3 TB of search structures.

Beyond the tipping points, the study names what would have to change, as hypotheses for later studies, not
claims: time-partitioned companion tables (chunks, envelopes, probe rows and their indexes partitioned by collection and time, as TDengine partitions by vgroup and duration), so each search, retention step and index rebuild touches only the partitions it needs, with filter-first routing; disk-based or quantised vector
indexes (DiskANN-style, binary or product quantisation) with exact re-ranking; coarse-to-fine screening from
chunk summaries; sharding across nodes with a merged top-k (Citus or application-level).

Hardware: D9 at 1 TB and above needs a machine with enough fast storage; the run is planned once that machine
is chosen (named before the freeze). Scale points that cannot be run are reported as not measured, never
extrapolated silently.

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
