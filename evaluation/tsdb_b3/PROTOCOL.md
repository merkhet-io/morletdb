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

## Amendment B3.4: CrateDB stage (DRAFT, not frozen; frozen in its own commit before any CrateDB load)

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

**Not in this stage:** CrateDB's vector search (similarity search is not part of B3), clusters of more than
one node, CrateDB Cloud.

**Downloads (to be approved before the stage):** `crate:6.4.6` (arm64 image, 244 MB compressed) and `crate` 2.3.0
(PyPI, pure Python) into a separate venv.
