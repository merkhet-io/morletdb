# MorletDB

### Signals tell a story. Find it with MorletDB.

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](LICENSE)
[![Go](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml/badge.svg)](https://github.com/merkhet-io/morletdb/actions/workflows/ci.yml)
![Go 1.24+](https://img.shields.io/badge/Go-1.24%2B-00ADD8)

**Signal-native storage and exploration for machine condition monitoring.**

A [Merkhet](https://github.com/merkhet-io) project, built in Go. Starting with CNC vibration data, MorletDB connects raw signals to localized time-frequency features, healthy references, and similar historical episodes.

Ingest a uniformly sampled vibration capture, extract deterministic Morlet wave tokens, compare it with reviewed healthy captures from the same operating conditions, and retrieve similar historical episodes.

This first MVP validates the signal representation and investigation workflow. It retains raw data and derived signatures in immutable JSON capture files. It is not yet the planned columnar database, a compression breakthrough, a certified monitor, or an automatic machine-protection system.

## Try it in two minutes

Requires Go 1.24+ on Linux or macOS. No external Go modules, cloud services, or runtime dependencies.

```sh
go build -o bin/morletdb ./cmd/morletdb
./bin/morletdb demo -data ./data
./bin/morletdb serve -data ./data
```

Open **http://127.0.0.1:8080**. The demo adds eight healthy reference captures, one independent healthy holdout, and one synthetic chatter capture. It prints both holdout assessments. In the dashboard, select `synthetic-chatter`, select `healthy-01` through `healthy-08` as references (Ctrl/Cmd-click), and compare. Then compare `healthy-09` with those same references. The demo is deterministic, apart from capture IDs; rerunning adds another dataset.

The dashboard shows a waveform, band-energy histogram, time-frequency token heatmap, feature contributions, and nearest episodes. It works offline and loads no third-party assets.

## What is a wave token here?

A token contains **time offset, duration, center frequency, and mean squared complex Morlet response**. Twelve logarithmically spaced bands cover sample-rate/64 through 0.4 × sample-rate. Each band emits non-overlapping blocks of up to 128 valid coefficients. Tokens preserve localized energy, not phase or an invertible encoding. Raw samples remain available in each capture.

The transform removes the capture mean, uses omega0=6, truncates kernels at ±3 scale units, corrects kernel DC, normalizes complex L2 energy, and discards boundary coefficients requiring padding. Fingerprints are versioned as `morlet-energy-v1`. Band responses overlap: their sum is not physical total signal power, and the heatmap is not a calibrated PSD.

## Bring CNC data

Start with fixed-speed, steady-state windows from an accelerometer on the spindle housing. Use consistent sensor mounting, calibration, axis, units, tool, operation, material, feed and load. The latter operating details must currently be encoded in `operation` (for example `finish-steel-feed800-load60`). This MVP requires exact RPM matching; it does not perform order tracking or compensate for changing speed.

A capture contains 512–32,768 samples from one channel. The caller guarantees uniform sampling and performs anti-alias filtering, segmentation and calibration upstream. At 4096 Hz, the analysis bands span 64–1638 Hz; phenomena outside that range need a different acquisition configuration or future extractor. Do not assume low-rate controller telemetry captures high-frequency bearing vibration.

### JSON ingestion

Create a capture JSON with this structure; replace the abbreviated samples with at least 512 numerical values:

```json
{
  "context": {
    "machine": "cnc-01",
    "sensor": "spindle-x",
    "operation": "finish-steel-feed800-load60",
    "tool": "endmill-4flute",
    "unit": "g",
    "rpm": 6000
  },
  "start": "2026-01-01T12:00:00Z",
  "sample_rate_hz": 4096,
  "samples": [0.01, -0.02, 0.03],
  "label": "operator-reviewed-healthy"
}
```

```sh
./bin/morletdb ingest -data ./data -file capture.json
# Or send that same file to the running service:
curl --fail-with-body -H 'Content-Type: application/json' \
  --data-binary @capture.json http://127.0.0.1:8080/api/captures
```

### CSV ingestion

Use a one-column CSV with the exact header `value`, one sample per row. Put the capture metadata above in `metadata.json`, omitting `samples`.

```sh
./bin/morletdb import-csv -data ./data -file vibration.csv -metadata metadata.json
```

Irregular timestamps, multiple columns and implicit resampling are deliberately unsupported.

## HTTP API

| Method | Path | Result |
|---|---|---|
| GET | `/healthz` | Liveness and extractor version |
| POST | `/api/captures` | Validate, extract and durably store a capture |
| GET | `/api/captures?machine=cnc-01` | Capture summaries, optional machine filter |
| GET | `/api/captures/{id}` | Full capture, signature and tokens |
| GET | `/api/captures/{id}/similar?k=5` | Nearest compatible captures; k=1..100 |
| POST | `/api/assess` | Explicit reference-based assessment |

Assessment request:

```json
{"target":"<capture-id>","references":["<healthy-id-1>","<healthy-id-2>","<healthy-id-3>","<healthy-id-4>","<healthy-id-5>"]}
```

Choose 5–1000 distinct, independently reviewed healthy captures, excluding the target. Machine, sensor, operation, tool, unit, RPM, sample rate and sample count must all match. Labels are descriptive only and never silently train a baseline. The response records the exact reference IDs for reproducibility.

### Assessment and search semantics

Features are `log1p` of the twelve mean band energies, AC RMS, crest factor and population kurtosis. For each feature, the baseline center is the median, with scale `max(1.4826 × MAD, 0.05)`. The assessment score is the largest absolute standardized deviation. The initial heuristic threshold is **6**. Feature contributions explain which measurements changed. This score is not a probability, validated alarm threshold, or diagnosed defect. Silence and DC changes require separate sensor-health checks: mean removal intentionally ignores DC.

Nearest-neighbor retrieval uses RMS Euclidean distance in that same log feature space, includes amplitude, excludes the target and requires the same operating context. It is an exact linear scan, not ANN. A phase-averaged global signature can miss brief changes; time-local tokens support inspection but are not yet searched as sequences.

## Operation

The default server binds only to `127.0.0.1:8080`. To bind another interface, set a bearer token:

```sh
MORLETDB_TOKEN='replace-with-a-long-random-token' \
  ./bin/morletdb serve -data ./data -addr 0.0.0.0:8080
```

All `/api/` routes then require `Authorization: Bearer <token>`. Enter the token in the dashboard; it stays in page memory. Terminate TLS at a reverse proxy before remote use. The dashboard shell and liveness endpoint remain public. This MVP has no users, roles, quotas or audit log.

Capture files are mode 0600 inside a newly created 0700 directory. Writes use a temporary file, file sync, atomic rename and directory sync. Incomplete temporary files are ignored on restart. Corrupt records cause explicit errors. Capture writes never modify existing records. Back up the directory; there is no retention manager or deletion API yet. Listing and search read all records and are intended for small pilot datasets, not continuous fleet-scale ingestion. Request bodies are capped at 2 MiB; at most two HTTP ingestions run concurrently.

## Development

```sh
go test -race ./...
go vet ./...
go test ./internal/signal -run '^$' -bench . -benchmem
```

See [design and roadmap](docs/design.md) for scope and next steps.

## License

Copyright 2026 MorletDB contributors.

Licensed under the [Apache License, Version 2.0](LICENSE).
