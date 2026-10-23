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

Recordings use immutable, synced JSON files under `data/recordings/`, separate from legacy captures. Restart preserves recordings. Features are recomputed on search; there is no persistent segment index or cache. This is intentionally the correctness baseline before indexing. Binary sample chunks, persisted versioned features, extractor/index interfaces, ANN, multichannel alignment, a packaged SDK and a generic dashboard remain future work. Current retrieval is bounded and unsuitable for fleet-scale archives.

Tests cover embedded-event localization, amplitude normalization, noise, distractors, exact metadata filtering, interval bounds, overlapping self-match exclusion, result suppression, cancellation, restart, corruption and authenticated HTTP ingestion/search. Synthetic results do not establish real-world retrieval quality.
