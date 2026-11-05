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

## Amendment B3.2: pipelined appends, zero-copy array I/O, faster codec, parallel track (FROZEN before its confirmation run)

Frozen 2026-11-02 in the commit that adds this section, after development on the development set only
(`results/dev/`). B3.1 and stage-1 results stay as published.

**What changed (all lossless; SQL signatures unchanged; chunks byte-identical):** the importer core sends its
statements in psycopg pipeline mode; pg_morlet's append path borrows `float8[]` input and builds `float8[]`
results in one call; the raw codec decodes with a one-window Rice path, a second prediction pass and a
slicing-by-8 CRC, and encodes with per-order residual loops. Multi-channel reads fetch all channels of an
interval in one statement.

**Image:** `morletdb:b32-dev`, built with `deploy/Dockerfile` from the commit that adds this section; its
image id is recorded in every MorletDB result.

**Track A, recommended configuration (single client connection, as stage 1 and B3.1):**
- **M-032:** three fresh loads per durability class; D2 load 1 measures storage, reads (R1–R5) and analytics.
- **M-032w** (storage option): as M-032 with `min_wal_size=32MB`, `max_wal_size=48MB`; one D2 load,
  storage and ingestion only. Reported as a documented option with its trade-off (more frequent checkpoints).
- **QuestDB** rerun unchanged in the same session (three loads per class; reads and analytics on D2 load 1).

**Track B, maximum throughput (`parallel.py`):** N writer processes per system, each with its own connection
(MorletDB: the importer core per acquisition; QuestDB: its client's `dataframe()` per acquisition and
table), acquisitions dealt round-robin, files parsed before a common start barrier, wall time from the
barrier to the last acknowledgement. Both systems get the same treatment and the same container limits.
Three loads per system for N = 4 and N = 8 in D2, and N = 8 in D1. Full correctness after every load.
Track B results are reported separately and never compared with Track A results.

**Acceptance criteria for adopting the change (benchmark set, M-032):**
1. Every correctness check passes; analytics within 1e-12 relative of numpy.
2. Sample-data storage equal to stage 1 within 0.1%.
3. Single-connection ingestion (D2, median of three) at least 1.5× B3.1's 2.27 M samples/s (≥ 3.4 M).
4. No read or analytics regression: p50 of R1–R5 and analytics each at most 1.1× B3.1's confirmed value.

Comparative statements keep the stage-1 rule (≥ 10%, all loads, or both p50 and p95).

## Amendment B3.3: acquisition reads, and the cost of search at ingest (FROZEN before its confirmation run)

Frozen 2026-11-03 in the commit that adds this section, after development on the development set only
(`results/dev/b33*`). Earlier stages stay as published.

**Questions.**
- **A: acquisition reads.** B3 read each channel as its own recording. How fast is reading one time interval
  of an acquisition from all 7 channels (64 kHz, 4 kHz and 1 Hz)? Measured through MorletDB's acquisition
  API, through the client's own one-statement read, and with QuestDB.
- **B: search at ingest.** Stage 1 measured search structures once, on the old client path (0.30 M
  samples/s). What do search features and envelopes cost on the B3.2 path, and where does the time go?

**What changed (development findings, included in the image under test):**
1. **Feature extractor** (`morlet-energy-v1`, `-lowrate-v1`; `morlet_core::morlet`):
   - The twelve kernels are built once per sample rate instead of once per 512-sample window.
   - Eight coefficients are computed side by side, each accumulated in the same order.
   - Features are bitwise unchanged:
     - a unit test compares every bit with the previous code;
     - on the development set, all 395,316 stored feature vectors had the same digest under both images.
2. **`morlet_read_acquisition_raw`** (new, SQL 0.3.3): the rows of `morlet_read_acquisition`, with samples
   as little-endian float64 bytes, read in one statement.

**Image:** `morletdb:b33`, built with `deploy/Dockerfile` from the commit that adds this section. The
reference image for the extractor is `morletdb:b33-dev`, built from main at `ed855cb`: the same code without
change 1 or 2. Image ids are recorded in every result.

**Part A, acquisition reads** (on M-033's D2 load 1, after storage, R1–R5 and analytics):
- **Setup:** every acquisition is registered with its 7 channels: offset 0, clock `daq`, origin = the
  acquisition's start. The registration time is reported.
- **Windows:**
  - AQ1: 64 ms, about 4,096 + 3 × 256 samples, plus the 1 Hz channel when a sample falls inside.
  - AQ2: 0.5 s, 3 × 32,000 + 3 × 2,000 samples.
  - For each, 20 intervals are fixed by seed 20261103: a random acquisition, and a random start inside it.
- **Timing:** each interval is timed 50 times after one checked warm-up, end to end into numpy arrays.
- **Arms:**
  - `morlet_read_acquisition` (as shipped since SQL 0.3.0);
  - `morlet_read_acquisition_raw`;
  - `client_raw`: the client maps time to samples itself and reads every non-empty channel with
    `morlet_read_interval_raw` in one statement;
  - QuestDB: one time-range query per group table. It runs on QuestDB's D2 load 1 in the same session,
    after its R1–R5 and analytics.
- **Correctness:** every result is compared bit for bit with the manifest arrays. This includes each
  channel's first sample index and the exact time of that sample (QuestDB: every timestamp). Any mismatch
  voids the arm.
- **Statements:** stage-1 rule, ≥ 10% at both p50 and p95.

**Part B, search at ingest** (single connection, B3.2 client path, D2):
- **Arms, three fresh loads each:**
  - **M-033:** base, no features.
  - **M-033-feat:** search features at ingest, `morlet-energy-v1` for 64 and 4 kHz, `-lowrate-v1` for 1 Hz.
  - **M-033-search:** features plus envelopes enabled, built after the load with `morlet_build_envelopes`
    (window 4,096). The build time is reported separately.
  - **M-033-feat on the reference image:** the extractor before change 1.
- **Order:** loads alternate by round: base, feat, search, feat-reference; then repeat.
- **Recorded:**
  - full correctness after every load;
  - the stored-features digest for every feat and search load;
  - storage on each arm's load 1.
- **Attribution:** one more load per arm with `track_functions=all`, reporting server time per function.
  These loads are outside every statement.

**Acceptance criteria for change 1 (benchmark set):**
1. Every load passes full correctness.
2. Every feat and search load on `morletdb:b33` has the same features digest as every feat-reference load.
3. M-033-feat median ingestion is at least 1.5× the feat-reference median.

Change 2 is adopted if its arm has no mismatch. Its speed is reported against the other arms by the
statement rule above.

**Development pilots (development set; not results):**

| Measure | Value |
|---|---|
| Ingestion: base / features (old extractor) / features (new) | 4.91 / 0.72 / 1.46 M samples/s |
| Features: server time in `morlet_chunk_step` (old → new) | 108 s → 41 s of 140 s → 69 s |
| AQ1 p50: `morlet_read_acquisition` / `client_raw` / QuestDB | 3.0 / 1.9 / 2.6 ms |
| AQ2 p50: the same arms | 12.7 / 6.2 / 8.1 ms |
| Attribution, AQ2 (`morlet_read_acquisition` vs `client_raw`) | server 5.7 vs 4.0 ms; the rest is returning `float8[]` |

### B3.3 deviations

1. **First attempt aborted and restarted from scratch.** The first confirmation run (started 2026-11-03,
   about 10:55) was stopped at 11:17, in round 2, because the laptop had to be moved; sleep would have frozen
   the containers during timed loads. Its partial results were moved unread to `results/b33-aborted-1/` and
   enter no statement. The run was restarted unchanged (same commit, image, script and order) from round 1.
2. **Profile result of the feat arm overwritten, recovered from the log.** `run_b33.sh` wrote the profile load
   of M-033-feat on the reference image to the same file as the one on `morletdb:b33`
   (`results/b33/profile/load_morletdb-033-feat_D2_r4.json`). The overwritten result was recovered unchanged
   from the run log (`results/b33/run.log`, which holds every result as printed) and saved under the original
   name; the reference profile moved to `results/b33/profile/reference/`. Profile loads are outside every
   statement; no measurement was repeated.

## Amendment B3.4: CrateDB stage (FROZEN before any load of the benchmark set)

Added 2026-11-04. CrateDB is sold to the same industrial users as MorletDB, so the question "why not store the
waveforms in CrateDB?" needs measured answers. The stage runs before IoTDB; the order of the remaining stages is
unchanged. Everything common to B3 applies (machine, container limits, budget of 6 h and 50 GB, dataset,
manifest, correctness checks, measurements A–D, decision rules, "Not in B3"). MorletDB is not re-run: its arms
are B3.3's (`results/b33/`, SQL 0.3.3 = release 0.3.4-alpha.1), compared with CrateDB on the same machine.

**CrateDB** (`crate:6.4.6`, linux/arm64, digest `sha256:865ed64fd4e7ffc31dccd79ec96d374f7da458e23e65520699e31ad4f21775ef`;
Python client `crate` 2.3.0, HTTP endpoint; image defaults otherwise):
- Single node: `discovery.type=single-node`, `CRATE_HEAP_SIZE=8g` (half the container's memory, as documented),
  tables with `number_of_replicas = 0`.
- **Time:** CrateDB timestamps have millisecond resolution; the 64 kHz period (15,625 ns) is not representable.
  Every arm therefore stores the sample index `n` (BIGINT) and derives time as start + n × period, as MorletDB
  does; `ts` (TIMESTAMP, milliseconds, start + floor(n × period)) is stored in the row arms for time-range use
  and is not part of the exactness check.
- **C-row** (idiomatic): one table per group, one row per sample instant, synchronous channels as columns, e.g.
  `b3_high(acquisition TEXT, n BIGINT, ts TIMESTAMP, part TIMESTAMP GENERATED ALWAYS AS date_trunc('day', ts),
  vibration_1 DOUBLE, phase_current_1 DOUBLE, phase_current_2 DOUBLE, PRIMARY KEY (acquisition, n, part))
  PARTITIONED BY (part)`; default codec (LZ4). Retries: `INSERT … ON CONFLICT DO NOTHING`.
- **C-row-best:** the same with `WITH (codec = 'best_compression')`.
- **C-array** (CrateDB's documented pattern for dense series): one row per (acquisition, channel, block) with
  `samples DOUBLE ARRAY INDEX OFF`, blocks of 4,096 samples (MorletDB's chunk size; the community example uses
  2,880), `codec = 'best_compression'`, primary key (acquisition, channel, block).
- Bulk path: the client's bulk inserts (`cursor.executemany` → HTTP `bulk_args`), one call per acquisition and
  table, batches of at most 10,000 rows (C-row) or all blocks of the acquisition (C-array).
- **Durability:** D1 `translog.durability = 'REQUEST'` (default: fsync before acknowledgement); D2
  `translog.durability = 'ASYNC'`. Rows become queryable after a refresh: "time until queryable" is measured
  with the default `refresh_interval`.
- **Settled state for storage (A):** `REFRESH TABLE`, then `OPTIMIZE TABLE` (default arguments), then the server
  stopped cleanly; sizes from the volume by file (Lucene segments per table, translog separately, cluster
  state and metadata). A second figure after `OPTIMIZE TABLE … WITH (max_num_segments = 1)` is reported
  separately and not used for statements.
- **Reads (C):** C-row by `WHERE acquisition = ? AND n >= ? AND n < ? ORDER BY n`; C-array by fetching the
  overlapping blocks and slicing in the client (time counted). **Analytics (D):** `min`, `max`, `avg` and
  `sqrt(avg(x * x))` in SQL on C-row; C-array computed in the client after the read, reported as such.
- **Retry semantics:** loading an acquisition twice leaves one copy (primary key); an altered retry is silently
  ignored (`DO NOTHING`), reported as a semantic difference (MorletDB refuses it).

**Runs (fixed now):** C-array and C-row: three fresh loads per durability class (D1 r1–r3, D2 r1–r3); storage
(after `OPTIMIZE`, and after `OPTIMIZE` to one segment), reads (R1–R5) and analytics on D2 r1. C-row-best: one D2
load (r1), with storage, reads and analytics; its ingestion is reported but makes no statement (one load). Order:
C-array, C-row, C-row-best (`run_b34.sh`; a completed load is not repeated, an interrupted one is rerun from a
fresh volume). Client: `.venv-b3c` (Python 3.13, the B3 requirements and `crate` 2.3.0). Ingestion time includes
building the client's parameter rows, reported separately as conversion (as QuestDB's `dataframe` conversion).

**Development (not results).** On B3's development set (`B3_SET=dev`, `results/dev/b34/`): every arm passed the
3-acquisition subset (bit-exact reads through the HTTP JSON interface; identical retry leaves one copy; an altered
retry is accepted and silently ignored); full loads of C-row (ingest 565 s, 180 k samples/s, correct) and C-array
(223 s, 455 k samples/s, correct). What development changed: `restart` waits until every shard has recovered
(the first C-row run failed reading during shard recovery after the restart).

**Not in this stage:** CrateDB's vector search (similarity search is not part of B3), clusters of more than
one node, CrateDB Cloud.

**Downloads (to be approved before the stage):** `crate:6.4.6` (arm64 image, 244 MB compressed) and `crate` 2.3.0
(PyPI, pure Python) into a separate venv.

### B3.4 deviations

1. **C-row D2 r1 interrupted by system sleep; rerun under the stage's own rule.** The Mac entered clamshell
   sleep at 16:12:16 (`pmset` log) while this load was in its storage, restart and read phases (load started
   15:57:55; ingestion 738.8 s, against 415–440 s for the other C-row loads, cause unknown); it woke at
   17:58:55 and the script continued (R1 and R2 maxima 1.9 s and 4.0 s). `caffeinate -i` does not prevent
   clamshell sleep. Decided at 19:15, before the rerun: the load counts as interrupted and is rerun from a
   fresh volume ("an interrupted one is rerun from a fresh volume"); the interrupted result is kept in
   `results/b34/interrupted/` and used for nothing. No other load overlapped a sleep (all others started after
   17:58:55 or ended before 16:12).

## Amendment B3.5: Apache IoTDB stage (FROZEN before any load of the benchmark set)

Added 2026-11-04. The stage named in this protocol after QuestDB (CrateDB, B3.4, was inserted before it).
Everything common to B3 applies (machine, container limits of 8 CPUs and 16 GB, budget of 6 h and 50 GB,
dataset, manifest, correctness checks, measurements A–D, decision rules, "Not in B3"). MorletDB is not re-run:
its numbers are B3.3's (`results/b33/`, SQL 0.3.3), compared with IoTDB on the same machine.

**IoTDB** (`apache/iotdb:2.0.11-standalone`, released 2026-09-14; linux/arm64 digest
`sha256:a04c66a9f5bb72ed93fe9cc1a35160da2e398f75539afb1bd7b99cc0da891e1c`, 357 MB compressed; index
`sha256:5ace0f3eceff892dd349d02288224c966f3fdeb11d08c4f575b642a110b82338`). Python client `apache-iotdb` 2.0.11
(PyPI, wheel sha256 `199a329e5b093805…`; requires thrift, numpy, pandas, SQLAlchemy, tzlocal), native RPC
port 6667, user `root` with the image's default password (container bound to localhost only).
- Standalone node (ConfigNode and DataNode in one container), image defaults apart from the settings below,
  passed as lowercase environment variables, which the image writes into `iotdb-system.properties`.
  Memory: the image's start script sizes the JVM from the container limit; the value used is recorded.
- **Time:** `timestamp_precision=ns` (set at first start, cannot change later). Every period of the dataset
  (15,625 ns at 64 kHz, 250,000 ns at 4 kHz, 1 s) is then exact, so IoTDB stores the true time of every sample
  and the read check covers times as well as values; no sample index is stored.
- **Model:** IoTDB 2.0's table model (recommended for new applications), one database `b3`, one table per
  group with `acquisition STRING TAG` and the group's channels as `DOUBLE FIELD` columns, e.g.
  `b3_high(acquisition STRING TAG, vibration_1 DOUBLE FIELD, phase_current_1 DOUBLE FIELD,
  phase_current_2 DOUBLE FIELD)`; the time column is implicit.
- **Arms:**
  - **I-table** (idiomatic): image defaults, i.e. DOUBLE encoded with GORILLA (`default_double_encoding`)
    and compressed with LZ4 (`compressor`).
  - **I-table-zstd**: the same with `compressor=ZSTD`, IoTDB's documented higher-ratio compressor (as
    CrateDB's `best_compression` arm).
  - A per-column encoding (for example CHIMP) is not accepted by the table model's `CREATE TABLE` (checked in
    development), so there is no third arm.
- Write path: `TableSession.insert(NumpyTablet)` with big-endian `int64` timestamps and `float64` columns, one
  tablet per acquisition and table, at most 100,000 rows per tablet. The server listens on all interfaces
  inside the container (`dn_rpc_address=0.0.0.0`; the image default 127.0.0.1 is unreachable through the
  port mapping), published on localhost only. Client `fetch_size` 65,536 rows. Conversion to tablets is timed and reported apart, as for QuestDB and CrateDB.
- **Durability:** D1 `wal_mode=SYNC` (fsync before acknowledgement); D2 `wal_mode=ASYNC`, the image default
  (fsync within `wal_async_mode_fsync_delay_in_ms`, 1,000 ms). "Time until queryable" is measured after the
  last acknowledgement, as for the other systems.
- **Retry semantics:** IoTDB has no primary-key conflict check; a second write of the same time and tag
  overwrites the value. Loading an acquisition twice is checked for one copy; an altered retry is expected
  to replace the stored value silently, reported as a semantic difference (MorletDB refuses it).
- **Settled state for storage (A):** `FLUSH`, then wait until the data directory has not changed for 60 s
  (background compaction idle), then the server stopped cleanly; sizes from the volume by file: TsFiles
  (sequence and unsequence) per database, WAL, ConfigNode and system files.
- **Reads (C):** `SELECT CAST(time AS INT64) AS t, <channels> FROM b3_<group> WHERE acquisition = ? AND time >= ?
  AND time < ? ORDER BY time`, times from the manifest; the returned times must equal start + n × period
  exactly. **Analytics (D):** `min`, `max`, `avg` and `sqrt(avg(x * x))` in SQL (supported, checked in
  development).

**Runs (fixed at the freeze):** each arm three fresh loads per durability class (D1 r1–r3, D2 r1–r3); storage,
reads (R1–R5) and analytics on D2 r1. Order: I-table, I-table-zstd (`run_b35.sh`; a completed load is not
repeated, an interrupted one, including one that overlaps a system sleep, is rerun from a fresh volume).
Client: `.venv-b3i` (Python 3.13, the B3 requirements and `apache-iotdb` 2.0.11). The Mac stays awake with the
lid open for the whole run (`caffeinate -i` does not prevent clamshell sleep; B3.4 deviation 1).

**Development (not results).** On B3's development set (`B3_SET=dev`, `results/dev/b35/`): both arms passed the
3-acquisition subset (values bit-exact, times exact to the nanosecond; an identical retry leaves one copy; an
altered retry is accepted and silently replaces the stored value) and one full load each (correct; 2.4 M and
2.2 M samples/s; storage about the raw float64 size). What development changed or fixed:
- `dn_rpc_address=0.0.0.0` (the client could not connect otherwise).
- Reads select the time as `INT64`: with `time` as a TIMESTAMP column, 128,000 samples took 552 ms against
  55 ms, almost all of it in the Python client's per-element conversion to time-zone-aware datetimes; the
  server answered in about 20 ms either way. Values and times are exact in both forms.
- `fetch_size` 65,536 instead of the client default 5,000 (128,000 samples: 70 ms vs 63 ms; 200,000 rows gave
  67 ms); chosen so that a 4,096-sample read is one fetch.
- No per-column encoding in the table model (above). The tablet size of 100,000 rows was not varied.

**Not in this stage:** IoTDB's tree model, clusters, the AINode, IoTDB's own client tools.

**Downloads (to be approved before development):** `apache/iotdb:2.0.11-standalone` (linux/arm64, 357 MB
compressed) and `apache-iotdb` 2.0.11 with its dependencies (PyPI) into a separate venv.

## Amendment B3.6: TDengine stage (FROZEN before any load of the benchmark set)

Added 2026-11-04. The last stage named at the top of this protocol. Everything common to B3 applies (machine,
container limits of 8 CPUs and 16 GB, budget, dataset, manifest, correctness checks, measurements A–D, decision
rules, "Not in B3"). MorletDB is not re-run: its numbers are B3.3's (`results/b33/`, SQL 0.3.3).

**TDengine TSDB-OSS** (`tdengine/tsdb:3.4.3.3`, released 2026-10-19; linux/arm64 digest
`sha256:a35f623203ffcbafea6817812080b54cbfa3e42aba3c4878d7713d67d814e877`, 256 MB compressed; index
`sha256:ac50defd1b68b551e8f15d927dc37f9718f79fa22d54e07292bc9a6bcf53ad76`). Client: the official Python WebSocket
connector `taos-ws-py` 0.7.0 (2026-07-08; 0.7.1 is one day old and not used), through the image's taosAdapter
(port 6041), with the image's default credentials (container bound to localhost only). The newer 3.4.3.9 is two
days old and not used.
- Single node, image defaults apart from the settings below.
- **Time:** database `PRECISION 'ns'`, so every period of the dataset is exact and the read check covers times
  as well as values; no sample index is stored.
- **Model:** TDengine's documented model: one supertable per group with the group's channels as `DOUBLE`
  columns and `acquisition VARCHAR(64)` as tag, and one subtable per acquisition and group.
- **Arms** (per-column compression, documented in "Data Compression"):
  - **T-default**: DOUBLE defaults, encoding `bss` (byte-stream split), compression `lz4`, level `medium`.
  - **T-zstd**: encoding `bss`, compression `zstd`, level `high` (the documented highest-ratio level).
  - **T-tsz**: encoding `disabled`, compression `tsz` (TDengine's float-specific compressor).
- Write path: `stmt2` parameter binding (`INSERT INTO ? USING <stable> TAGS(?) VALUES (?, ...)`), column-wise
  binding, one prepared statement per acquisition and group, at most 32,767 rows per bind (the largest bind
  the server accepts, found in development). Column names are quoted (`force` is a reserved word). Building the bound columns is timed and reported apart, as for the other systems.
- **Durability:** D1 `WAL_LEVEL 2` with `WAL_FSYNC_PERIOD 0` (fsync on every write); D2 `WAL_LEVEL 1` (the
  default: WAL written, not fsynced).
- **Retry semantics:** a row with an existing timestamp in a subtable is expected to overwrite the stored row;
  checked in development (identical and altered retries) and reported as for the other systems.
- **Settled state for storage (A):** `FLUSH DATABASE`, then wait until no file outside the write-ahead logs has
  changed for 60 s, then the server stopped cleanly; sizes from the volume by file class: vnode time-series
  files (`tsdb`), WAL, other vnode files, mnode and other. `COMPACT DATABASE` is not available in the
  open-source edition, so there is no compacted figure. The WAL is retained by TDengine's defaults and is
  reported separately (as MorletDB's retained WAL is); statements use the data files and the whole volume.
- **Reads (C):** `SELECT CAST(ts AS BIGINT), <channels> FROM <subtable> WHERE ts >= ? AND ts < ? ORDER BY ts`
  (the WebSocket client returns TIMESTAMP values as text without the sub-second part, so time is read as an
  integer); the returned times must equal start + n × period exactly. **Analytics (D):** `MIN`, `MAX`, `AVG`
  and `SQRT(AVG(x * x))` in SQL (supported).

**Runs (fixed at the freeze):** each arm three fresh loads per durability class (D1 r1–r3, D2 r1–r3); storage,
reads (R1–R5) and analytics on D2 r1. Order: T-default, T-zstd, T-tsz (`run_b36.sh`; a completed load is not
repeated, an interrupted one, including one that overlaps a system sleep, is rerun from a fresh volume).
Client: `.venv-b3t` (Python 3.13, the B3 requirements and `taos-ws-py` 0.7.0). The Mac stays awake with the lid
open for the whole run.

**Development (not results).** On B3's development set (`B3_SET=dev`, `results/dev/b36/`): all three arms passed
the 3-acquisition subset (values bit-exact, times exact to the nanosecond; an identical retry leaves one copy;
an altered retry is accepted and silently replaces the stored value) and one full load each (correct; 3.8 M,
0.82 M and 3.9 M samples/s for default, zstd and tsz; data files 626, 566 and 360 MiB). What development found or fixed (harness only; no server
setting was tuned):
- one bind accepts at most 32,767 rows (larger binds fail with "Bind number out of range");
- `force` is a reserved word: column names are quoted;
- TIMESTAMP values come back as text without fractions of a second: time is read as `BIGINT`;
- an internal database's WAL grows continuously on an idle server, so the settle test ignores WAL files (the
  first development run never settled);
- `COMPACT DATABASE` is enterprise-only; the compacted storage figure was dropped;
- the server settings are read after the database is created.

**Not in this stage:** TDengine's enterprise features, clusters, TDgpt (its AI agent for forecasting and
anomaly detection), taosX, the native (non-WebSocket) connector.

**Downloads (to be approved before development):** `tdengine/tsdb:3.4.3.3` (linux/arm64, 256 MB compressed)
and `taos-ws-py` 0.7.0 (PyPI, macOS arm64 wheel) into a separate venv.

### B3.6 deviations

1. **The tsz arm is lossy; stopped after its first load.** T-tsz D2 r1 (23:10) failed the correctness check: two of
   868 channels (vibration of `N09_M07_F10_KA05_1` and `N09_M07_F10_KA06_1`) differed from the source in 7 and 47
   samples, all near the end of the recording, by at most 8 ULP (largest absolute difference 5.6e-17); counts
   and times were exact. Diagnosis (`diag_tdengine_tsz.py`, read from the failed load's volume): TDengine's
   reference documentation for taosd, at the tested version, states that `dPrecision` (default and minimum
   1e-16) truncates the mantissa of doubles under TSZ, i.e. tsz is error-bounded lossy compression; the
   compression page that this protocol used does not say so. The development set happened to show no
   difference. Decided at 23:30, before any further result was read: the tsz arm is reported as lossy with
   this diagnosis and makes no performance statement (a lossy arm cannot meet B3's correctness criterion);
   its remaining loads (D2 r2–r3, D1 r1–r3) are not run. Its development figures stay development figures.
   The default and zstd arms are complete (12 loads, all correct).

## Amendment B3.7: TimescaleDB stage (FROZEN before any load of the benchmark set)

Added 2026-11-04. TimescaleDB is the other PostgreSQL extension for time series, so "why not TimescaleDB?" is
the first question of every PostgreSQL user. Earlier evidence (P2) measured its storage only, on four signals,
outside B3. Everything common to B3 applies (machine, container limits of 8 CPUs and 16 GB, budget, dataset,
manifest, correctness checks, measurements A–D, decision rules, durability classes D1/D2 as for MorletDB). MorletDB
is not re-run: its numbers are B3.3's (`results/b33/`, SQL 0.3.3, PostgreSQL 17).

**TimescaleDB 2.31.0 on PostgreSQL 17** (`timescale/timescaledb:2.31.0-pg17`, released 2026-10-20; linux/arm64
digest `sha256:d971568f9af05e218866181151117f517282f05f299a0e4010dc6a97704f6f2c`, 550 MB compressed; index
`sha256:b56a3f51991df1f79d0c1a0841cad5c5fa8058fdd6a3ee6886394a6d4c174538`). The full image (not `-oss`), because
compression (the columnstore) is under the Timescale License, free to use, and is what users run. Client: psycopg
3.2.10 from B3's existing environment (no new client download). PostgreSQL settings as MorletDB's in B3.3 (image
defaults; `synchronous_commit` per durability class).
- **Time:** PostgreSQL timestamps have microsecond resolution and the 64 kHz period (15.625 µs) is not
  representable, so the hypertables use an **integer time dimension in nanoseconds** (`t BIGINT`, the sample's
  exact time), with `chunk_time_interval` of one day in nanoseconds. Times are exact and part of the read check.
- **Model:** one hypertable per group, `(acquisition TEXT, t BIGINT, <channels> DOUBLE PRECISION)`, a unique index
  on `(acquisition, t)` (TimescaleDB requires the time column in unique indexes).
- **Two states of one arm, measured on the same load:**
  - **TS-row:** the rowstore right after ingestion (how TimescaleDB holds recent data).
  - **TS-col:** the columnstore after `ALTER TABLE … SET (timescaledb.compress, timescaledb.compress_segmentby =
    'acquisition', timescaledb.compress_orderby = 't')` and `compress_chunk` on every chunk, with TimescaleDB's
    default per-type algorithms (DOUBLE: Gorilla). The compression time is reported separately from ingestion.
- Write path: `COPY … FROM STDIN (FORMAT BINARY)` through psycopg, one COPY per acquisition and table, the
  documented bulk path. Conversion to COPY rows is timed and reported apart, as for the other systems.
- **Retry semantics:** with the unique index, a second COPY of the same rows fails with a unique violation
  (identical and altered alike); reported as "refused, identical retries included" (MorletDB accepts an
  identical retry as a no-op and refuses an altered one).
- **Settled state for storage (A):** `VACUUM ANALYZE`, then sizes from the database (`hypertable_detailed_size`,
  per state) and from the volume (data directory, WAL separately), as for MorletDB in B3.
- **Reads (C):** `SELECT t, <channels> FROM <group> WHERE acquisition = %s AND t >= %s AND t < %s ORDER BY t` through
  psycopg with binary results; on TS-row and on TS-col. **Analytics (D):** `min`, `max`, `avg`, `sqrt(avg(x*x))`
  in SQL, on both states.

**Runs (fixed at the freeze):** three fresh loads per durability class (D1 r1–r3, D2 r1–r3); on D2 r1: storage,
reads and analytics on TS-row, then compression, then storage, reads and analytics on TS-col (`run_b37.sh`; a
completed load is not repeated, an interrupted one, including one that overlaps a system sleep, is rerun). The
Mac stays awake with the lid open.

**Development (not results).** On B3's development set (`B3_SET=dev`, `results/dev/b37/`): the 3-acquisition
subset passed in both states (values bit-exact, times exact; identical and altered retries both refused with a
unique violation, stored values unchanged), and one full load passed in both states (0.68 M samples/s; rowstore
6.6 GB, columnstore 805 MiB of hypertables; compression of 3 chunks in 32 s with the documented defaults). The
server ran PostgreSQL 17.11 with default settings (`NO_TS_TUNE`, 128 MB shared buffers), as MorletDB in B3.3.
Harness check (`diag_timescale_copy.py`): a fully vectorised binary COPY stream built with numpy reached the same
rate as psycopg's per-row `write_row` (788 k vs 795 k samples/s), so ingestion is bound by the server, not the
client; the documented per-row path is kept. Nothing was tuned.

**Not in this stage:** TimescaleDB Cloud (Tiger Data), the `-ha` image and its toolkit, continuous aggregates,
tiered storage, columnstore writes on ingest.

**Download (to be approved before development):** `timescale/timescaledb:2.31.0-pg17` (linux/arm64, 550 MB
compressed).

## Amendment B3.8: QuestDB re-measured beside the current MorletDB (FROZEN before any load of the benchmark set)

Added 2026-11-05. The website's QuestDB comparison comes from B3.2, against MorletDB's state at that time, while
CrateDB, IoTDB, TDengine and TimescaleDB are compared with B3.3. B3.3 ran QuestDB once (D2 r1), not enough for a
statement. B3.8 measures both systems again in one session so that every comparison uses the same MorletDB.
Everything common to B3 applies; the harness (`run.py`, `questdb_adapter.py`, `morletdb_adapter.py`) is used
unchanged.

- **QuestDB 10.1.0** (`questdb/questdb:10.1.0`, still the current release; image already local, no download),
  its B3.2 configuration unchanged: WAL tables, deduplication, `TIMESTAMP_NS`, `dataframe()` ingestion with
  `questdb[dataframe]` 6.0.1; Parquet arms (LZ4_RAW after D2 r1, ZSTD after D2 r2) as in B3.2.
- **MorletDB:** the released image `ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1` (SQL 0.3.3, the code users run),
  arm `morletdb-033` (B3.3's base configuration: raw float64 appends and reads, pipelining, native statistics,
  no search features).
- **Runs:** for each repetition r = 1, 2, 3 and durability D2 then D1: MorletDB, then QuestDB (alternating, so
  that drift in the machine affects both); storage, reads and analytics on D2 r1 of each system, Parquet arms on
  QuestDB D2 r1 and r2. `run_b38.sh`; the Mac stays awake with the lid open; an interrupted load is rerun.
- **Reported:** every B3 measure for both systems, QuestDB's storage both native and as Parquet; statements by
  B3's rule. The website chart then takes QuestDB's ratios from B3.8 and shows QuestDB's native storage beside
  the Parquet figure.
- **Smoke check (not a result):** `run.py subset` on the development set, to confirm the environment, before the
  first benchmark load.

## Amendment B3.9: same-session re-pairing of CrateDB, IoTDB, TDengine and TimescaleDB (FROZEN before any load)

Added 2026-11-05, 01:20. B3.4–B3.7 compared each system with MorletDB's B3.3 numbers, measured in an earlier
session on the same machine. The first B3.8 session (00:39–00:51, `results/b38/`) then ingested MorletDB at
4.38 M samples/s against 6.33 M in B3.3; an attribution check with both images (B3.3's and 0.3.5-alpha.1) gave the
same 4.2–4.4 M, so the image was not the cause. The machine was busy (load average 9–10; Messages.app spinning at
one full core since 2026-11-03 01:40, GUI processes), and results did not record machine load. Time-based
ratios across sessions are therefore uncertain by an unknown factor; storage, memory and correctness are not
affected in the same way. B3.9 re-measures each system beside MorletDB in one session, after Messages.app was
quit (load average about 2), and B3.8 is repeated on the quiet machine (`results/b38q/`, same script).

- **Systems and configurations:** unchanged from their amendments, the stage's first arm each: TDengine
  `default` (B3.6), TimescaleDB (B3.7, both states), IoTDB `table` (B3.5), CrateDB `array` then `row` (B3.4).
  Secondary arms (TDengine zstd and tsz, IoTDB table-zstd, CrateDB row-best) are not re-run.
- **MorletDB:** `ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1`, arm `morletdb-033`, as in B3.8.
- **Runs:** per system, in the order above, for r = 1, 2, 3 and durability D2 then D1: MorletDB, then the
  system (alternating); storage, reads and analytics on D2 r1 of each, as in each stage. Results in
  `results/b39-<system>/`. `run_b39.sh`; an interrupted load is rerun.
- **Machine state:** before and after every load, the time, `uptime` and the eight busiest processes are
  appended to `results/<run>/machine/` (also for `results/b38q/`). A load that starts with a load average above
  4 is reported with that note.
- **After B3.9:** B3.8 a third time (`results/b38z/`), to measure drift over the night.
- **Reported:** every ratio from one session's pair only; the earlier cross-session ratios stay in RESULTS.md,
  marked as cross-session. Statements by B3's rule. The website takes its ratios from B3.8/B3.9.

## Amendment B3.10: single-connection ingestion after I1 (FROZEN before any load of the benchmark set)

Added 2026-11-05. I1 (issue #233; development-set work in [`../ingest_i1/ATTRIBUTION.md`](../ingest_i1/ATTRIBUTION.md))
changed how MorletDB ingests from one connection: the importer commits 256 chunks per transaction and keeps one
connection, sends runs of up to 64 consecutive chunks in one `morlet_append_chunks_raw` call (SQL 0.3.4), and
the server encodes a run's chunks on up to 4 threads. On the development set this raised single-connection D2
ingestion from 6.22 M to 13.2 M samples/s with byte-identical stored data. B3.10 measures it on the benchmark
set, beside QuestDB and the released MorletDB, in one session. Everything common to B3 applies.

- **MorletDB, new (arm `morletdb-i1`):** image `morletdb:b310`, built locally with `deploy/Dockerfile` from main at
  the merge of PR #237, commit `dc77ed1` (SQL 0.3.4, unreleased; the image id is recorded in every result). B3.3's base path
  (raw float64 appends and reads, pipelining, native statistics, no search features) plus `commit_chunks=256`, one
  importer connection for all acquisitions, `rpc_chunks=0` (automatic: 64 chunks per call) and the server's
  default `morlet.encode_threads` (4 on this machine's 8-CPU containers).
- **MorletDB, released (arm `morletdb-033`):** `ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1`, exactly as in B3.8, as
  the same-session baseline.
- **QuestDB 10.1.0:** exactly as in B3.8 (B3.2 configuration, Parquet arms on D2 r1 and r2).
- **Runs:** for r = 1, 2, 3 and durability D2 then D1: `morletdb-i1`, `questdb`, `morletdb-033`. Storage,
  reads and analytics on D2 r1 of each system, as in B3.8. `run_b310.sh`; machine snapshots before and after every
  load (`results/b310/machine/`); an interrupted load is rerun. Container limits as in B3 (8 CPU, 16 GB).
- **Machine:** the run starts only when the 1-minute load average is below 3 and no process other than the
  Docker VM uses more than half a core; a load that starts above 4 is reported with that note.
- **Acceptance criteria for `morletdb-i1`** (adopting it as the recommended configuration): (1) every load of
  every arm passes B3's correctness checks; (2) in every load, the stored chunks of `morletdb-i1` are
  byte-identical to `morletdb-033`'s (same count and digest), so storage is unchanged; (3) R1–R5 and analytics p50
  of `morletdb-i1` each at most 1.1× those of `morletdb-033` in this session (reads use the same code).
- **Reported:** every B3 measure for all three arms, statements by B3's rule for `morletdb-i1` against QuestDB
  and against `morletdb-033`, and server peak memory. If the criteria hold, the website's ingestion figures take
  `morletdb-i1`'s numbers, labelled with the unreleased SQL 0.3.4 until it is released.
- **Smoke check (not a result):** one development-set load of `morletdb-i1` (`B3_SET=dev`) before the first
  benchmark load, to confirm the image and the harness.

## Amendment B3.11: B3.10's read criterion over three loads per arm (FROZEN before any load of the benchmark set)

Added 2026-11-05. B3.10 compared reads from one D2 load per arm and missed its read criterion on R2 alone (p50
1.118× the released build; every other measure within 1.04×). Exploratory repeats after the stage
(`results/b310-attr-*`) showed R2 varying between loads by more than that difference, in both directions, while
Spotlight and media analysis were running. B3.11 decides the criterion with three loads per arm. Everything
common to B3 and B3.10 applies; B3.10's ingestion statements are not re-tested here.

- **Arms:** `morletdb-i1` (image `morletdb:b310`, `sha256:4a617625773a…`, as in B3.10) and `morletdb-033` (released
  `0.3.5-alpha.1`). QuestDB is not run.
- **Runs:** for r = 1, 2, 3: a fresh D2 load of `morletdb-i1`, then of `morletdb-033`, each followed by the full
  B3 read benchmark (R1–R5) and interval statistics on that load (`B3_READS_EVERY_REP=1`; otherwise B3 measures
  reads on D2 r1 only). `run_b311.sh`, `results/b311/`, machine snapshots before and after every load.
- **Machine:** the run starts only when the 1-minute load average is below 3 and no process other than the Docker
  VM uses more than half a core, and in particular when Spotlight indexing (`corespotlightd`, `mds_stores`) and
  media analysis (`mediaanalysisd`) are idle; foreign processes above half a core in any snapshot are reported.
- **Acceptance criteria for `morletdb-i1`:** (1) all six loads pass B3's correctness checks; (2) stored chunks
  byte-identical across the six loads; (3) for each of R1, R2, R3, R4 recent, R4 older, R5 and interval
  statistics, the median over the three loads of `morletdb-i1`'s p50 is at most 1.1× the median over the three
  loads of `morletdb-033`'s p50. p95 values are reported, not tested.
- **Decision:** if all three hold, B3.10's criterion 3 is taken as met by this repeated measurement; `morletdb-i1`
  is adopted as the recommended configuration and the website's ingestion figures take B3.10's numbers, labelled
  with the unreleased SQL 0.3.4 until it is released. If criterion 3 fails again, I1 is not adopted, and the read
  difference is investigated as a defect before any further stage.

## Amendment B3.12: B3.11 with reads from a fresh client process (FROZEN before any load of the benchmark set)

Added 2026-11-05. B3.10 and B3.11 timed reads from the Python process that had just ingested, and missed the read
criterion on different queries (R2; then R3 and R5). The investigation after B3.11 (RESULTS.md) found no read
defect in the database: inside the server, reading I1-written data costs 0.97–1.00× reading data written by the
old path, while more than half of a client-measured read is client, Docker Desktop's network path and
conversion. B3.12 repeats B3.11 with the reads taken in a new client process after each load, and also records
the server-side read cost. Everything common to B3, B3.10 and B3.11 applies.

- **Arms:** `morletdb-i1` (`morletdb:b310`, `sha256:4a617625773a…`) and `morletdb-033` (released `0.3.5-alpha.1`).
- **Runs:** for r = 1, 2, 3: a fresh D2 load of `morletdb-i1`, then of `morletdb-033`. After B3's correctness check,
  settle and storage, B3's read benchmark (R1–R5) and interval statistics run in a **new Python process** (the
  same code and read settings, `run.py reads-child`), and then B3's planned intervals are read inside the server
  (one statement per query type, 50 passes, five rounds; `i1_reads_server.py`). `run_b312.sh`, `results/b312/`,
  machine snapshots before and after every load. Smoke check done on the development set
  (`results/dev/b312-smoke/`).
- **Machine:** as B3.11 (load average below 3 at the start, no foreign process above half a core; foreign processes
  in snapshots reported).
- **Acceptance criteria for `morletdb-i1`:** (1) all six loads pass B3's correctness checks; (2) stored chunks
  byte-identical across the six loads; (3) for each of R1, R2, R3, R4 recent, R4 older, R5 and interval
  statistics, the median over the three loads of `morletdb-i1`'s fresh-process p50 is at most 1.1× the median of
  `morletdb-033`'s. Server-side read cost (median of three loads, I1 / old) and p95 values are reported, not tested.
- **Decision:** if all three hold, `morletdb-i1` is adopted as the recommended configuration: the website's
  ingestion figures take B3.10's numbers and its read figures stay as they are, labelled with the unreleased SQL
  0.3.4 until it is released. If criterion 3 fails, I1 is not adopted and the next step is decided by the owner.

## Amendment B3.13: every system beside the released MorletDB 0.3.6-alpha.1 (FROZEN before any load of the benchmark set)

Added 2026-11-05. 0.3.6-alpha.1 releases SQL 0.3.4 with I1 (#233; adopted in B3.12) and verified appends. The
website's chart and tabs compare the systems with 0.3.5-alpha.1 (B3.8, B3.9), except QuestDB's ingestion lane
(B3.10, a development build). B3.13 re-pairs every system with the released image, so that every ratio again
comes from one session against one MorletDB release. It also measures one opt-in feature for interval
statistics, chunk summaries, against QuestDB. Everything common to B3 applies.

- **MorletDB:** `ghcr.io/merkhet-io/morletdb:0.3.6-alpha.1` (index digest `sha256:439b1f01484e…`, recorded in every result), arm
  `morletdb-i1` (B3.10's: B3.3's base path, 256 chunks per transaction, one importer connection, automatic batched
  raw appends, server defaults for `morlet.encode_threads` and `morlet.verify_appends`). In QuestDB's pair only,
  also arm `morletdb-i1-sum`: the same with chunk summaries enabled (`morlet_enable_chunk_summaries` at creation;
  each chunk stores its count, minimum, maximum and sums, so interval statistics decode only the chunks cut by the
  interval).
- **Systems and configurations:** as in B3.8 and B3.9, unchanged: QuestDB 10.1.0, TDengine 3.4.3.3 `default`,
  TimescaleDB 2.31.0 (both states), IoTDB 2.0.11 `table`, CrateDB 6.4.6 `array` and `row`.
- **Reads from a fresh client process for every system** (B3.12's lesson, now for all): after each system's own
  preparation for reads (unchanged per stage), B3's read benchmark and interval statistics run in a new Python
  process with the same code and settings (`reads-child` in each runner, `common.reads_maybe_fresh`). MorletDB's
  server-side read cost is also recorded, as in B3.12.
- **Runs:** `run_b313.sh`. QuestDB's pair first: for r = 1, 2, 3 and D2 then D1, `morletdb-i1`, QuestDB,
  `morletdb-i1-sum`. Then for each other system, in the order TDengine, TimescaleDB, IoTDB, CrateDB `array`,
  CrateDB `row`: for r = 1, 2, 3 and D2 then D1, `morletdb-i1`, then the system. Storage, reads and analytics on D2
  r1 of each arm (QuestDB's Parquet arms as in B3.8). Results in `results/b313-<system>/`, machine snapshots
  before and after every load; a failed load is logged and rerun.
- **Machine:** the run starts only when the 1-minute load average is below 3 and no process other than the Docker
  VM uses more than half a core; foreign processes above half a core in any snapshot are reported.
- **Smoke check (not a result):** one development-set D2 load of every arm and system with fresh-process reads
  (`results/dev/b313-smoke/`, development image of the same SQL 0.3.4 code): all passed.
- **Reported:** every measure for every pair, statements by B3's rule (≥ 10% in all loads for ingestion, at both
  p50 and p95 for latencies; otherwise no statement). For `morletdb-i1-sum` against `morletdb-i1`: interval
  statistics, ingestion and storage, so the feature's cost is shown with its gain. The website's chart and tabs
  then take every ratio from B3.13 (against 0.3.6-alpha.1); interval statistics are shown for the base arm, with
  the summaries arm as a labelled second mark, and QuestDB's ring for ingestion is removed.

## Amendment B3.14: long reads and interval statistics against QuestDB with parallel decode (FROZEN before any load of the benchmark set)

Added 2026-11-05, late evening; frozen by the assistant while the owner was away, at the owner's request to finish
the open benchmark work overnight. B3.13 made no statement against QuestDB for long reads (R2: 0.95 / 1.29 at p50
/ p95) and for interval statistics on the base arm (1.02 / 1.94). The main branch since then decodes the chunks
of one long read on worker threads (#247, `morlet.decode_threads`, by default up to 4 and at most the CPUs
available; reads of fewer than 8 chunks stay on one thread). It is not yet released. B3.14 repeats B3.13's
QuestDB pair with a build of main, and adds the released image in the same session so that the effect of the new
code is measured against the release, not against another day. Everything common to B3 applies.

- **MorletDB, development build:** image `morletdb:b314`, built locally with `deploy/Dockerfile` from main at
  `5274e88` (SQL 0.3.5; `sha256:7dff89832ff3…`, recorded in every result). Arms `morletdb-i1` and
  `morletdb-i1-sum` exactly as in B3.13, server defaults (so `morlet.decode_threads` at its default).
- **MorletDB, released:** `ghcr.io/merkhet-io/morletdb:0.3.6-alpha.1`, arm `morletdb-i1` as in B3.13, results in a
  separate folder.
- **QuestDB:** 10.1.0, unchanged from B3.8–B3.13.
- **Runs:** `run_b314.sh`; for r = 1, 2, 3 and D2 then D1: `morletdb-i1` (build), QuestDB, `morletdb-i1-sum`
  (build), `morletdb-i1` (released). Reads and statistics from a fresh client process for every system, and
  MorletDB's server-side read cost, as in B3.13. Storage, reads and analytics on D2 r1 of each arm. Results in
  `results/b314-questdb/` (build and QuestDB) and `results/b314-released/`; machine snapshots before and after every
  load; a failed load is logged and rerun.
- **Machine:** the run starts only when the 1-minute load average is below 3 and no process other than the Docker
  VM uses more than half a core; the Mac is kept awake with `caffeinate`, lid open, on AC power.
- **Smoke check (not a result):** one development-set D2 load of `morletdb-i1` on `morletdb:b314` with
  fresh-process reads (`results/dev/b314-smoke/`).
- **Reported:** every measure of the QuestDB pair, statements by B3's rule (≥ 10% in all loads for ingestion, at
  both p50 and p95 for latencies; otherwise no statement), with R2 and interval statistics as the questions this
  stage was added for; build against release (same session) for every read measure, ingestion and storage.
- **Scope:** a development build answers whether the code on main closes the gap; it is not a release. The
  website takes B3.14's figures only after a release that contains this code is measured in the same way, or
  labels them as a development build, as it did for B3.10's ingestion lane.
