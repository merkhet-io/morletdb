# MorletDB benchmark baseline

This harness measures the current bounded prototype before choosing a Postgres,
OLAP or hybrid architecture. It is not evidence of production scalability.

## Run

Use a disposable PostgreSQL 17 database with the current `pg_morlet` installed.
The caller needs schema creation permission. The harness creates a unique schema
and removes it in `finally`; an interrupted process can leave that schema behind.
It never drops existing application schemas or installs the extension itself.

```sh
python3 -m venv /tmp/morlet-bench-env
. /tmp/morlet-bench-env/bin/activate
python -m pip install -r benchmarks/requirements.txt
# Install the extension in release mode for meaningful performance measurements:
# cd extensions/pg_morlet
# cargo pgrx install --release --pg-config /path/to/pg_config
# Create the extension in your disposable benchmark database beforehand.
export DATABASE_URL='postgresql://localhost/morlet_bench'
export MORLET_BUILD_PROFILE=release
python benchmarks/run.py --rows 64 --samples 4096 --repeats 20 --output benchmark-output
```

Alternatively use standard `PGHOST`, `PGPORT`, `PGDATABASE` and `PGUSER` variables.
Credentials and connection strings are not included in the JSON report. Choose a
new output directory on each run. `--parquet-only` runs file generation and sample
scans without connecting to Postgres; it does not run feature retrieval.

The pinned Python versions are a reproducibility baseline, not a claim that they
are the newest releases. CI runs 32 recordings of 2048 samples, ten repetitions,
and an **optimized release-build smoke test**, uploading `report.json` as an Actions artifact.
CI timing is diagnostic; it is not an architecture-selection result.

## Workloads and output

A seeded synthetic dataset contains sinusoidal signals with varying frequency,
phase and gain, noise and a short transient. All data is float64 at 4096 Hz,
with four asset groups. The report includes a SHA-256 fingerprint, dependency
versions, host information, PostgreSQL settings and the declared build profile.

1. Write raw recordings as Zstd Parquet and scan decoded samples with DuckDB.
2. COPY raw recordings into Postgres in one batch, including commit, then backfill
   generated Morlet grids. Measure these two operations separately.
3. COPY the same recordings into a table with generated features enabled. This
   measures the current synchronous extraction-on-write implementation.
4. Export those exact features to typed Parquet columns. Compare exact top-10
   retrieval in Postgres JSONB and DuckDB/Parquet, using the same precomputed query
   features, both unfiltered and filtered to one asset. Fail if IDs/intervals differ
   or distances disagree beyond floating-point tolerance.
5. Time the Postgres search API including query normalization/extraction.
6. Run the API against a fixed cohort while another connection commits one
   feature-generating recording per transaction. Report search latency, writer
   commit latency and how many writes committed during the search measurement.
   A zero overlap count means the mixed-workload result is inconclusive.

Queries have three untimed warmups, then report median and nearest-rank p95/min/max.
First-scan timing does not clear OS caches. The retrieval comparison disables
source exclusion and overlap suppression in both paths. Postgres scans JSONB;
DuckDB scans typed feature columns. Differences reflect those implementations,
not an intrinsic limit of either database. This harness does not compare feature
extraction implementations or claim signal-retrieval quality from synthetic data.

Sample/feature limits remain in force: 512..32768 samples per recording, 512-sample
windows with stride 256, at most 4096 total candidate segments. Long recordings,
cross-chunk windows, async workers and indexed retrieval are not implemented by
this benchmark. The mixed test uses one writer and a growing relation; it is not
an HTAP workload-isolation test.

Storage measurements include raw logical float64 bytes, raw Parquet bytes,
Postgres relation sizes before and after features, and feature-only Parquet bytes.
They have different metadata/index/encoding overheads and must not be treated as
identical storage layouts. COPY includes client serialization and transport;
Parquet write time is local file creation. DuckDB uses two threads, recorded in
output; Postgres retains its reported server settings.

## Decision gates after this baseline

- Build a common chunked recording contract, including continuous sample offsets,
  gaps and boundary-spanning windows; verify against whole-recording extraction.
- Compare typed Postgres segment rows with typed Parquet features to remove the
  current JSONB-layout confounder.
- Add Parquet import mappings and bounded batches using the shared chunk contract.
- Evaluate asynchronous extraction with durable progress/checkpoints; measure
  ingestion-to-search latency and failure recovery separately from throughput.
- Increase data beyond RAM and test controlled cold/warm reads, concurrent clients,
  selective filters, index build/rebuild cost, recall and resource consumption.
- Test Iceberg snapshots/catalog costs only when multi-file lifecycle and concurrent
  writers are part of the workload. Add RisingWave or another streaming layer only
  when continuous computation requirements justify it.

Keep the same datasets and signal semantics across implementations. No engine
selection is made by the smoke benchmark.
