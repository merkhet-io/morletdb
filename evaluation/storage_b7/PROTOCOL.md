# B7: MorletDB and ModelarDB on float32-exact sensor signals — protocol

Status: **FROZEN** in the commit that adds this file, before any load of the benchmark set. Only the development
set (below) was loaded before, to make the harness work. Deviations are appended at the end, dated.

## Question

ModelarDB (ModelarDB-RS) is the closest specialised system: high-frequency sensor time series, model-based
compression (constant and linear models plus residuals), SQL over Arrow Flight, edge and cloud. Where does
MorletDB's recording model offer measurable advantages or costs against it, for **storage, ingestion, memory,
transfer volume, aggregates and interval extraction**, under which fidelity requirement? The outcome is a map of
trade-offs, not a winner. Similarity search is not compared: ModelarDB has none, and retrieval needs its own
baselines and quality measures (studies E1–E6).

## Systems (pinned)

- **MorletDB** `ghcr.io/merkhet-io/morletdb:0.3.13-alpha.1` (linux/arm64; PostgreSQL 17.6, pg_morlet SQL 0.3.10),
  image defaults (`synchronous_commit` on).
  - **m-g1:** the release default chunk format (Grizzbit 1).
  - **m-g21:** Grizzbit 2.1, opt-in (`morlet.chunk_format = grizzbit21`), storage arm.
  - One stream collection per group, one recording per (unit, channel), extractor `none` (raw samples, no search
    structures), sample quantum 1. Checked on the development set: no force or ctrl channel lies on a value grid (they
    are full float32 values); pmid speed and torque do. Quantum 1 everywhere: no per-channel tuning. Values are sent as the exact float64 upcast of the float32 input.
  - Path: the supported importer core (`tools/ingest/parquet_import.py` `import_files`), chunk 4,096, defaults.
- **ModelarDB** built from `github.com/ModelarData/ModelarDB-RS` at commit `1b90706` (2026-10-30; the project has no
  releases): `cargo build --release -p modelardb_server` (the project's release profile: full LTO, one codegen unit)
  with rustc 1.100.0 in `rust:latest`, the binary placed as in the project's Dockerfile (image `modelardb:1b90706`,
  id `sha256:fe83f610…`), native arm64; `modelardbd edge /data`, all defaults (WAL on, default memory
  reservations), no remote object store.
  - One time series table per group: `ts TIMESTAMP, unit TAG, <one FIELD per channel>`.
  - **mdb-l:** `FIELD` (error bound 0, the documented lossless setting).
  - **mdb-r1, mdb-r5:** `FIELD(1%)`, `FIELD(5%)` (relative error bounds), storage and fidelity arms, reported
    separately and never compared with MorletDB as if lossless.
  - Path: Arrow Flight `do_put`, one stream per unit, record batches of 65,536 rows, timestamps in microseconds,
    the tag as `Utf8View` (the server decodes batches with its table schema), float32 columns (the documented binary
    ingestion path; SQL `INSERT` is documented as slow).
  - Settled state: the Flight action `FlushMemory` (compresses buffered data and writes it to disk), timed.
    (`FlushNode` also transfers to a remote object store and fails on an edge without one.)

## Machine and limits

As B3: Apple M5 Max, Docker Desktop Linux VM (aarch64). Every system in its own container with
`--cpus 8 --memory 16g`, data on a named volume; the Python client on the host, single process
(`.venv-b3`, pyarrow from it). The machine is otherwise idle (benchmark hygiene); background services that cannot be
stopped are noted with the load average.

## Dataset (fixed now)

Precision is aligned: ModelarDB stores float32 only. Every main group is exactly representable in float32, so both
systems receive identical values; MorletDB stores them as float64 and is checked against the same float32 bits.

| Group | Source | Channels | Rate (period) | Units | Samples |
|---|---|---|---|---|---|
| **force** | IU4 milling data (Denkena et al., Data in Brief 2023, CC BY 4.0), float32 at the source | force_sensor_x/y/z | 25 kHz (40 µs) | every 20th of the 6,418 runs sorted by (machine, tool, run): 321 | 107,495,250 |
| **ctrl** | the same runs, controller signals, float32 at the source | torque_spindle, torque_axis_x/y/z, position_control_deviation_axis_x/y | 500 Hz (2 ms) | 321 | 4,301,616 |
| **pmid** | Paderborn bearing data (B3's 124 acquisitions, CC BY-NC 4.0), float64 **rounded to float32** | force, speed, torque | 4 kHz (250 µs) | 124 | 5,952,744 |

- force: dense CNC cutting forces; ctrl: slow, smooth controller signals (the kind ModelarDB's models target);
  pmid: test-rig signals that are not float32 at the source. The float64 → float32 rounding of pmid is reported
  separately (manifest: values changed, max absolute and RMS change); it affects both systems equally.
- **pvib (side arm, only if time remains after the main runs):** Paderborn vibration_1 at 64 kHz (15.625 µs, not a
  whole microsecond: ModelarDB's timestamps are rounded to whole microseconds and become irregular), also rounded to
  float32. Reported separately.
- Unit k starts at 2026-01-01T00:00:00Z + k × 60 s; sample n at start + n × period, whole microseconds for the main
  groups.
- `results/manifest.json` (written before any system is loaded) holds every source file's SHA-256 and per channel
  the sample count and the SHA-256 of the float32 values; every load checks its input against it.
- **Development set** (harness work only): the disjoint units offset by 10 (`B7_SET=dev`), results in
  `results/dev/`, not reported.

## Correctness (before measurements count)

- **Full read-back, every arm, every round:** every stored series is read back completely.
  - MorletDB: the float64 read-back equals the float32 input's upcast, and its downcast equals the float32 bits.
  - mdb-l: the float32 values equal the input bit for bit (`uint32` view), timestamps equal start + n × period.
  - Lossy arms: timestamps equal; the maximum absolute and relative errors are reported against the bound.
- **Stress vector** (`b7.py stress`, once): -0.0 inside runs of 0.0, NaNs with distinct payloads, ±inf,
  subnormals, linear ramps and a constant run, through mdb-l and MorletDB, compared bit for bit. ModelarDB documents
  "lossless" as value equality (`v1 == v2` or both NaN); a difference in bits that is equal in value is reported as
  "value-equal, not bit-exact", not as data loss.
- A system that fails the main-group read-back is reported as failing and not measured further on that group.

## Measurements (per round)

1. **Storage** after the settled state (MorletDB: `CHECKPOINT`, `VACUUM (ANALYZE)`; ModelarDB: `FlushMemory`), the
   server stopped, files on the volume:
   - MorletDB: chunk table with TOAST and indexes, recordings table, `pg_wal`, whole database;
   - ModelarDB: per table Parquet data and `_delta_log`, `wal/`, other;
   - per group bytes per sample, against raw float32 (4 B) and raw float64 (8 B).
2. **Transfer volume** (what an edge would ship): MorletDB's encoded chunk bytes (what `morlet_ship.py` sends)
   and ModelarDB's Parquet segment bytes per table. A proxy, not a network measurement.
3. **Ingestion:** end-to-end samples/s per group and for the whole load, from float32 arrays in memory; client-side
   conversion time separately; MorletDB's commits are durable on acknowledgement, ModelarDB's `do_put` returns
   when its WAL holds the data (its default); this difference is stated, not corrected for. ModelarDB's flush
   time is reported with its load.
4. **Memory:** the container's `memory.peak` (cgroup v2) over start and ingestion.
5. **Aggregates:**
   - whole column (min, max, sum, count over every unit of a channel): ModelarDB `SELECT min, max, sum, count` on
     one field with no filter (the only shape eligible for model-based execution; its `EXPLAIN` is recorded);
     MorletDB the sum of `morlet_interval_stats` over every recording of the channel;
   - per interval: 40 seeded 1-s intervals and 40 whole-unit intervals per group: MorletDB
     `morlet_interval_stats`, ModelarDB `count, min, max, avg` with a time and unit filter.
6. **Interval extraction:** the same 80 intervals per group; MorletDB `morlet_read_interval_raw`, ModelarDB
   `SELECT ts, <field> … WHERE unit = … AND ts in [start, end) ORDER BY ts` over Flight; latency and exactness of
   every result. Queries alternate between the systems (MorletDB first on even queries), both running, in one
   session.

## Rounds and order

Three rounds (`b7.py round 1..3`). In each round: the two main arms (m-g1, mdb-l) are loaded fresh twice in ABBA
order (BAAB in even rounds), each on an empty volume; the second load of each is kept for the read-back,
aggregates and queries. Then m-g21, mdb-r1 and mdb-r5 are loaded once each (storage, ingestion, memory, fidelity).
Reported: medians over the loads of an arm; storage is deterministic and must agree across loads (a difference is
reported).

## Decision rules (fixed now)

- **Storage:** the smaller lossless store per group, with the ratio; differences under 5 % are reported as level.
- **Ingestion and queries:** a system is faster when its median is lower by at least 10 % in every round;
  otherwise level.
- **Fidelity:** mdb-l counts as lossless for a group only if every value is bit-identical; value-equal but
  bit-different results are reported as such.
- Results are stated per group; no overall winner is declared.

## Budget and stop rules

- Run (from the freeze on 2026-11-13): at most 5 hours for the three rounds and the stress vector. If the third
  round does not fit, two rounds are reported and the decision rules are applied to them, stated as such.
- A system that cannot ingest a group (error, memory exhaustion, more than 30 minutes for one group) is reported for
  that group with the error, and the round goes on without it.

## Not in B7

Similarity search; network transfer (B7 measures the volume that would be shipped); clusters; object storage; lossy
compression inside MorletDB (it has none); float64-native data (ModelarDB cannot store it losslessly: stated, not
measured); ModelarDB's embedded mode and Python bindings.

## Results

`results/round_*.json`, `results/stress.json`, `results/manifest.json`, RESULTS.md. Unpublished until the owner
approves.

## Revision 2026-11-13 (before any load of the benchmark set)

The draft of 2026-11-12 was never committed; B7 was paused before any load. Changes, all made before the benchmark
set was loaded (only the development set and the stress vector had been):

- **MorletDB 0.3.13-alpha.1** instead of 0.3.12-alpha.1: the current release (security and data-safety fixes; chunk
  format, ingest and read paths unchanged).
- **ModelarDB build:** the project's Dockerfile builds the whole workspace without a job limit, which exhausted the
  Docker VM on 2026-11-12. The server alone is built with the same release profile in a memory-capped container;
  the full-LTO link needs more than 14 GB (it was killed at 14 GB and completed at 22 GB with one job).
- **Settled state `FlushMemory`**, tags as **`Utf8View`**: needed for an edge node without a remote store and by the
  server's batch decoding (found on the development set).
- **Stress vector:** MorletDB refuses NaN and ±inf by design (documented), so its arm receives the finite values of
  the vector and the number left out is recorded; ModelarDB's arm receives the whole vector. A first run on
  2026-11-13 (before this revision): ModelarDB lossless returned 509 of 510 values bit-identical (NaN payloads, ±inf
  and subnormals kept); -0.0 inside a run of 0.0 came back as +0.0. The stress vector is run again after the freeze.
- **Lossy arms (known before the freeze, from the development set):** with `FIELD(1%)` and `FIELD(5%)` ModelarDB
  returned values outside the bound (on 40 development units with 5 %: 95,846 of 13,230,000 force values, all 120
  series, some NaN or about 3e38). The likely cause is in ModelarDB's decoder (the residuals after a linear model are
  decoded against the model's recomputed last value, not the value the encoder used). The lossy arms are run as
  planned; their read-back reports, per group, the number of values outside the bound and the largest errors, and
  their storage is not presented as valid lossy compression while values fall outside the bound.
- **Data and ModelarDB issues found on 2026-11-13 that do not touch B7's data:** every B7 table has one tag and
  strictly increasing timestamps per unit, so neither the tag-hash collision nor the duplicate-timestamp case can
  occur; every ingest is sent once.
- **Budget:** the 2026-11-12/13 budget lapsed; see "Budget and stop rules".
- **Machine:** background services still running during the runs (a langfuse stack, three idle PostgreSQL
  containers) are recorded with the load average.
