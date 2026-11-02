# B3: time-series databases on dense waveforms — protocol (FROZEN)

Frozen 2026-11-02 in the commit that adds this file, before any B3 adapter was run or any B3 number measured.
Deviations are appended below. One database per stage, in this order: QuestDB, Apache IoTDB, TDengine, then
InfluxDB 3 if the first three have been reviewed and the budget allows. Each stage is reported before the next
starts. This file fixes everything common to all stages and the QuestDB stage; later stages add a section here,
frozen before their own measurements.

## Question

Where does MorletDB's recording model offer advantages or costs against general time-series databases, for
**dense waveform storage, ingestion and exact interval retrieval** of real, synchronously acquired vibration and
current signals? The outcome is a map of trade-offs, not a winner. Similarity search is a separate capability
and is not compared here (see E1–E6).

## Machine, resources, budget

- Apple M5 Max (18 cores, 128 GB), macOS 27, Docker Desktop 29.1.3 (Linux VM, aarch64; 12 CPUs, 33.6 GB).
- Every database runs in its own container, alone, limited to `--cpus 8 --memory 16g`, with data on a named
  Docker volume. The Python client runs on the host, single process (Python 3.13, numpy 2.x).
- Native arm64 images only (no emulation).
- Budget per database stage: at most 6 hours of measurement runtime and 50 GB of disk. A stage that would exceed
  it, or that hits a documented access, correctness or resource barrier, stops and reports why.

## Dataset (fixed now)

Paderborn bearing dataset (Lessmeier et al. 2016, KAt, Paderborn University; CC BY-NC 4.0), the Zenodo copy
already on disk (`data/paderborn/zenodo-15845309/extracted`, archives checksummed in `SHA256SUMS.archives`).
Earlier evaluations consumed it; B3 only measures storage and speed, nothing is selected on it.

- **Acquisitions:** the 2,480 `.mat` files sorted by path; every 20th, starting with the first (124 files). Each
  file is one synchronous acquisition of about 4 s; sample counts vary slightly per file.
- **Signals per acquisition:**

  | Group | Channels | Rate | Samples per file | Values |
  |---|---|---|---|---|
  | high | `vibration_1`, `phase_current_1`, `phase_current_2` | 64 kHz (period 15,625 ns) | about 256,000 each | float64; vibration on a 100/32768 grid, currents on a 0.000688781… grid |
  | mid | `force`, `speed`, `torque` | 4 kHz (period 250,000 ns) | about 16,000 each | float64, not on a clean grid |
  | process | `temp_2_bearing_module` | 1 Hz | 5 | float64 |

- **Time:** the files carry no absolute start time. Acquisition *k* (0-based, in the order above) is given the
  start time 2026-01-01T00:00:00Z + *k* × 60 s. Sample *n* of a channel is at start + *n* × period, exact in
  integer nanoseconds for all three rates. The sample index is recoverable from the timestamp exactly; no
  rounding is involved.
- **Manifest:** `results/manifest.json` records every file's path, its archive SHA-256, and per channel the
  sample count and the SHA-256 of its float64 little-endian samples. It is written before any database is
  loaded and checked by every adapter.
- About 95.4 M high-rate samples, 6.0 M mid-rate samples and 620 process samples; canonical size = samples × 8
  bytes (float64), reported per group.
- Synthetic signals appear only in adapter self-tests.

## Systems and configurations

**MorletDB** (`ghcr.io/merkhet-io/morletdb:0.3.3-alpha.1`, linux/arm64, PostgreSQL 17.6 with pg_morlet SQL
0.3.2, image defaults otherwise):
- One stream collection; one recording per (acquisition, channel), id `<file stem>.<channel>`, metadata with the
  acquisition, channel group and start time. Sample quantum: the measured grid for vibration and the currents,
  1 (no grid) for the others; the quantum only steers compression, samples are stored bit for bit either way.
- **M-base:** extractor `none` (raw samples only; no segment features, no envelopes): the archive configuration.
- **M-search:** the same data with the default features and search envelopes (window 4,096); reported
  separately, as the cost of search structures, not compared with other systems' storage.
- Bulk path: `tools/ingest/parquet_import.py`'s `import_files` (the supported importer core: `morlet_ensure_recording`
  and batched `morlet_append_chunks`), chunk size 4,096 and 16 chunks per transaction (the importer defaults).

**QuestDB** (`questdb/questdb:10.1.0`, linux/arm64; Python client `questdb` 6.0.1 with pandas and pyarrow):
- Idiomatic schema, one table per group, one row per sample instant, synchronous channels as columns:
  - `b3_high(ts TIMESTAMP_NS, acquisition SYMBOL, vibration_1 DOUBLE, phase_current_1 DOUBLE, phase_current_2 DOUBLE)`
  - `b3_mid(ts TIMESTAMP_NS, acquisition SYMBOL, force DOUBLE, speed DOUBLE, torque DOUBLE)`
  - `b3_process(ts TIMESTAMP_NS, acquisition SYMBOL, temp_2_bearing_module DOUBLE)`
  - `timestamp(ts) PARTITION BY HOUR WAL DEDUP UPSERT KEYS(ts, acquisition)`. Deduplication makes retries safe,
    as MorletDB's replay check does.
- Bulk path: `questdb.connect('ws::addr=…')` and `db.dataframe(df, table_name=…, symbols=['acquisition'],
  at='ts')`, one call per acquisition and table (the client's recommended bulk path; it returns after every
  row is committed to the WAL).
- **Q-native:** native column files (QuestDB OSS stores them uncompressed).
- **Q-parquet-lz4** and **Q-parquet-zstd9:** after loading, every partition is converted in place with
  `ALTER TABLE … CONVERT PARTITION TO PARQUET`, codec LZ4_RAW (the default) or ZSTD level 9 (documented
  configuration `cairo.partition.encoder.parquet.compression.*`). The active (newest) partition is never
  converted, so one sentinel row is appended in a later hour first; it is excluded from all measures.
- ZFS compression, the other documented lossless option, needs a ZFS file system and is not reproducible in
  this setup; it is reported as not tested.

**Packed-recording baseline:** the same samples as a MorletDB exchange file (`morlet_export.py`, Parquet, zstd,
one row per chunk of up to 32,768 samples), and as one Parquet file per group with the same columns as the
QuestDB tables (pyarrow 21, zstd level 9, one row group per acquisition). Storage only; they separate codec
effects from database overhead.

## Durability (reported, never mixed)

| Class | MorletDB | QuestDB |
|---|---|---|
| D1 durable on acknowledgement | `synchronous_commit=on`, `fsync=on` (PostgreSQL defaults) | `cairo.commit.mode=sync` |
| D2 OS-buffered | `synchronous_commit=off` | `cairo.commit.mode` default (no fsync per commit) |

Ingestion rates are compared only within a class. Each class is a separate fresh load.

## Correctness (before any performance run, and again after every measured load)

On a 3-acquisition subset first, then on the full load: sample counts per channel; ordering; every sample read
back equal in float64 bit patterns to the manifest's samples (by SHA-256 of the read-back array); timestamps
equal to start + n × period exactly; channel, unit and acquisition identity; reads that cross ingestion batches
(MorletDB chunks, QuestDB client batches); retry behaviour: loading one acquisition twice leaves one copy
(MorletDB: identical replay; QuestDB: dedup), loading it with one altered sample is refused (MorletDB) or
replaces the row (QuestDB, last write wins), reported as a semantic difference. A system that cannot pass these
is reported as such and not measured further.

## Measurements

**A. Storage** (after a settled state: load complete; QuestDB WAL applied, i.e. `wal_tables()` shows no
pending transactions, then the server stopped cleanly; MorletDB `CHECKPOINT` then `VACUUM (ANALYZE)` on the
collection). Measured from the volume, by file:
- data (column files, Parquet partitions; MorletDB chunk table heap and TOAST),
- indexes and metadata (symbol files, `_txn`/`_meta`, partition metadata; PostgreSQL indexes, recordings table),
- WAL / journal and transient files (QuestDB `wal*` directories, `seq`; PostgreSQL `pg_wal`), reported separately,
- bytes per sample and ratio to the canonical float64 size, per group.
Parquet conversion time is reported with the Q-parquet arms.

**B. Ingestion** (per durability class, three fresh loads each): end-to-end samples/s for the whole dataset from arrays
in memory (file parsing excluded and timed separately); client-side conversion/serialization time; per-call
acknowledgement latency (p50, p95 over the calls); time from the last acknowledgement until the last row is
queryable (count query polled every 50 ms); server CPU and peak memory (`docker stats`, 1 s samples); failures,
rejected writes and retries.

**C. Exact interval reads** (D2 load; warm cache: one untimed run of each query first; 50 timed repetitions
each; p50, p95; end to end in the client: query, transfer, decode and reconstruction into a float64 array per
channel, checked against the manifest's samples). Fixed now, with seed 20261102 choosing acquisitions and
offsets:
- R1 short: 4,096 samples of `vibration_1` (64 ms), 20 random (acquisition, offset) pairs.
- R2 long: 128,000 samples of `vibration_1` (2 s), crossing MorletDB chunk and QuestDB client-batch boundaries, 20 pairs.
- R3 multi-channel: 64,000 samples of all three high-rate channels together, 20 pairs.
- R4 recent vs older: R1 on the last-loaded 5 acquisitions vs the first-loaded 5.
- R5 concurrency: R1 with 4 client threads, 200 queries in total.
Cold-cache reads are not claimed. Server-only timings are reported where the system exposes them.

**D. Interval analytics** (small): min, max, mean and RMS = sqrt(mean(x²)) of `vibration_1` over the R2 intervals,
computed in the database in float64; checked against numpy on the manifest samples (relative difference
≤ 1e-12); 20 repetitions each.

## Decision rules (fixed now)

Descriptive. Ingestion is repeated with three fresh loads per durability class and system; storage is measured
after the first D2 load (deterministic up to file-system rounding, checked on a second load). A statement "A is
smaller/faster than B on this workload" is made only when the difference is at least 10% and holds in all three
loads (ingestion), in both p50 and p95 (reads and analytics), or in the single storage measure. Each advantage is attributed to codec, representation (implicit timing, chunking),
indexing or another implementation choice where the measurements allow, and otherwise marked unexplained. No
statement extends beyond this dataset, these configurations and this machine.

## Not in B3

Similarity search; lossy encodings; float32 coercion; cold-cache claims; cross-machine comparisons with earlier
numbers; tuning beyond the configurations listed (at most one documented setting change per system if a load
fails, reported as a deviation).

## Deviations

Recorded 2026-11-02 during and after the QuestDB stage's measurements.

1. **MorletDB bulk path wording.** The protocol says "batched `morlet_append_chunks`"; the importer's default
   (`rpc_chunks=1`) sends one `morlet_append_chunk` per 4,096-sample chunk, 16 chunks per transaction. The
   frozen arm uses the importer defaults as stated ("the importer defaults"); the batched mode (`rpc_chunks=16`)
   is reported only as an attribution run, outside the comparison. `import_files` is called once per
   acquisition with an in-memory reader (no Parquet file); its own SHA-256 of the source file (preflight) is
   excluded from the timings and reported.
2. **Environment.** scipy 1.16.3 (the 1.15.3 wheel does not load on macOS 27). Six unrelated, idle containers
   of the user's own stack (about 8% CPU in total) ran alongside; the MorletDB demo containers were stopped.
3. **Failed attempt.** The first QuestDB D2 load 1 completed its load, correctness check and part of the
   reads, then stopped on a wrong column name in the client script (`table_partitions()`). It was rerun from a
   fresh volume; none of its numbers are used.
4. **Storage accounting.** QuestDB storage is the allocated size of the files under each table directory; the
   per-row `acquisition` key file is counted as data ("identity"), the symbol dictionary as metadata; the
   sentinel's partition directory is reported apart and not counted. For load 1 the key file was split out
   arithmetically (4 bytes per row; load 2 measured it directly: equal to within 4 KiB page rounding per partition). MorletDB per-group bytes are the
   encoded chunk payloads, measured on the batched attribution load (identical chunks).
5. **Additional measurements, not in the frozen list:** reads and analytics on Q-parquet-zstd9 (after the
   conversion on D2 load 2); two MorletDB attribution loads (batched RPC; no sample grid). They are labelled
   as such and no comparison statement rests on them.
6. **Reads, MorletDB.** One SQL statement per channel fetches the interval as pieces of at most 32,768
   samples (`generate_series` with `LATERAL morlet_read_interval`, binary transfer), assembled in the client.

## Amendment B3.1: MorletDB client path and SQL 0.3.3 (FROZEN before its confirmation run)

Frozen 2026-11-02 in the commit that adds this section, after development on the development set
(`B3_SET=dev`: every 20th file starting at the 11th, disjoint from the benchmark set; results in
`results/dev/`) and before any B3.1 measurement on the benchmark set. Stage-1 results stay as published.

**What changed (all lossless; existing SQL functions unchanged):** the importer core sends numpy chunks in
binary (`tools/ingest/pg_numpy.py`) and, on SQL 0.3.3, as raw float64 bytes (`morlet_append_chunk_raw`, which
calls `morlet_append_chunk`); reads use `morlet_read_interval_raw` (one call per interval and channel, mapped
with `numpy.frombuffer`); interval analytics use `morlet_interval_stats`; the raw codec decodes with a
word-based bit reader and encodes with a faster search that writes byte-identical chunks.

**Image:** `morletdb:b31-dev`, built locally with `deploy/Dockerfile` from the commit that adds this section
(PostgreSQL 17.6, pg_morlet SQL 0.3.3); its image id is recorded in the results.

**Arms (benchmark set, same harness, same rules as stage 1):**
- **M-031** (recommended configuration): chunk 4,096, 16 chunks per transaction (importer defaults). Three
  fresh loads per durability class; D2 load 1 measures storage, reads (R1–R5) and analytics; full correctness
  after every load.
- **M-031c16** (documented bulk option): the same with 16,384-sample chunks; one D2 load with storage, reads
  and analytics. Reported as a trade-off, not as the recommended configuration.
- **QuestDB** is rerun unchanged in the same session (three loads per class; reads and analytics on D2 load
  1; storage on D2 load 1), so that both systems' numbers come from the same machine state.

**Acceptance criteria for adopting the change (all on the benchmark set, M-031):**
1. Every correctness check passes; analytics within 1e-12 relative of numpy.
2. Sample-data storage equal to stage 1 to within 0.1% (the encoder writes byte-identical chunks).
3. Ingestion (D2, median of three) at least 2× stage-1 MorletDB (≥ 1.06 M samples/s).
4. R2 p50 at most 12 ms.
5. Analytics p50 at most 2× QuestDB-native p50 of the same session.

Comparative statements keep the stage-1 rule (≥ 10%, all loads, or both p50 and p95).
