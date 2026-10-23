# Waveform search: first retrieval baseline

MorletDB now exposes domain-neutral recordings separately from the original CNC captures. CNC monitoring remains an application example. Existing `/api/captures`, `/api/assess`, CLI commands and dashboard are unchanged.

## Run

```sh
go run ./cmd/morletdb serve
# In another terminal:
python3 examples/waveform_search.py
```

The standard-library-only Python example ingests a synthetic electrical transient and locates an amplitude-scaled copy at sample 768. This illustrates generic signal retrieval, not validated battery or electrical diagnostics. Repeated runs add recordings.

## API

- `POST /api/recordings`: `channel`, `unit`, `start` (RFC3339), `sample_rate_hz`, `samples`, optional string-to-string `metadata`. Server assigns `id`.
- `GET /api/recordings`: metadata catalog, including `sample_count`, without raw samples.
- `GET /api/recordings/{id}`: original recording including raw samples.
- `POST /api/search`: uploaded `samples`, or `recording_id` plus `start_sample` and `sample_count`; supply `channel`, `unit`, `sample_rate_hz`, `normalization`, and `top_k`. Optional `extractor`, `stride`, and exact-match metadata `filter`.

Responses report extractor, normalization, effective stride and ranked hits containing recording ID, channel, sample offset, sample count, start seconds and distance. Intervals are half-open `[start_sample, start_sample + sample_count)`. Use the recording endpoint to retrieve the raw data and slice by sample coordinates.

## Search contract

- Extractor: `morlet-window-v1`, wrapping the existing versioned Morlet energy and statistical feature implementation. This is spectral/statistical similarity, not phase-sensitive waveform matching.
- `demean`: remove DC and retain amplitude. `zscore`: additionally normalize each window to unit population standard deviation. Constant windows remain zero. `none` is rejected because the current extractor always removes DC.
- Distance: root mean squared Euclidean distance over log1p band energy, RMS, crest factor and kurtosis. Smaller is closer; not a probability.
- Same channel, unit and exact sample rate are required. Metadata filters require keys to exist and values to match. No resampling, speed invariance, diagnosis or cross-unit conversion.
- Query lengths: 512..4096 samples. Recording lengths: 512..32768 samples. Rates: 128..200000 Hz. One uniformly sampled channel per recording.
- Candidate windows have query length and start at multiples of stride (default half query length). Search is exhaustive over this grid, not every possible offset. Events between grid points can be missed or poorly localized.
- Overlapping hits in a recording are suppressed in ascending distance order; the best interval is retained, not expanded. A recording-interval query excludes all overlapping source windows. Uploaded query samples have no source identity and can match themselves.
- At most 4096 eligible candidate windows per request; larger searches fail explicitly. Two searches/legacy ingestions share a concurrency limiter. HTTP bodies remain capped at 2 MiB.

## Persistence and next steps

New recordings use immutable little-endian float64 sample files (`.f64`) with SHA-256 checksums and JSON manifests (`.meta`) under `data/recordings/`. Samples are synced before publishing the manifest, which acts as the commit marker. Incomplete temporary files and orphan binary files are ignored. Old JSON recordings remain readable without migration. Binary length and checksum are verified when raw data is read.

Features are built lazily on the first query for each recording/window-size/stride/normalization/extractor combination, atomically persisted in `data/indexes/`, and reused after restart. Warm search reads manifests and feature grids without reading binary samples; recording-interval queries additionally read their source waveform. Feature files include source identity, version and a payload checksum. Corrupt indexes produce explicit errors; stop the service and remove the affected index file to rebuild on the next query. Removing all files in `data/indexes/` safely rebuilds derived indexes. Do not alter recording files: they are immutable.

The index is an exact feature scan, not ANN. No background indexing, cache eviction, retention, deletion, replication, multi-process writer coordination or generic UI is implemented. Different query configurations create additional index files; operators must manage disk usage. Warm searches do not scrub raw sample checksums. Back up manifests and binary files together; derived indexes can be omitted. Listing still scans the directory and manifests. This is a working single-node prototype for bounded pilot datasets, not fleet-scale storage.

Local CLI equivalents (JSON inputs use the same API schemas):

```sh
./bin/morletdb record -data ./data -file recording.json
./bin/morletdb recordings -data ./data
./bin/morletdb search -data ./data -file query.json
```

The local CLI requires exclusive operational control of its data directory for writes; run the HTTP service for concurrent clients. Existing CNC capture files and commands remain separate and unchanged.

Tests cover embedded-event localization, amplitude normalization, noise, distractors, exact metadata filtering, interval bounds, overlapping self-match exclusion, result suppression, cancellation, restart, corruption and authenticated HTTP ingestion/search. Synthetic results do not establish real-world retrieval quality.


## Prototype validation

A local end-to-end smoke test ingested the Python transient example, searched, stopped and restarted the service, listed metadata, retrieved binary samples and repeated search through the persisted index. The best match remained sample 768. Race tests include concurrent feature-grid builds, restart stability, raw checksum failures, index corruption/rebuild and legacy JSON compatibility. A warm-search microbenchmark on the development host (one 4096-sample recording, 512-sample query, stride 128) measured about 0.70 ms/op; this is not a fleet-scale or target-hardware performance claim.
