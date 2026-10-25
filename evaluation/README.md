# Evaluate before expanding the architecture

Two separate questions require separate reports: do the representations retrieve
useful events, and how expensive is exact retrieval over a larger population?
Neither report establishes a production operating envelope.

## Real-recording relevance baseline

```sh
python -m pip install -r evaluation/requirements.txt
cargo build --locked --release -p morlet-core --example features
python evaluation/prepare_cwru.py /tmp/morlet-cwru
python evaluation/retrieval.py /tmp/morlet-cwru/manifest.json \
  --output /tmp/cwru-report.json
```

The preparer downloads six recordings from the [CWRU Bearing Data Center](https://engineering.case.edu/bearingdatacenter/12k-drive-end-bearing-fault-data),
checks pinned source hashes, extracts the drive-end channel and creates a local
manifest. Raw `.mat`/`.npy` files are not redistributed. Source URLs, SHA-256 hashes,
load and channel provenance are recorded in the report. Consult the upstream
provider's terms for intended use; the repository's code license does not relicense data.

The committed [report](reports/cwru-007-cross-load-v1.json) evaluates 18 query
windows against 72 gallery windows. Query recordings are 0 hp; gallery recordings
are 1 hp. All use 12 kHz and 0.007-inch seeded defects at three locations. Each
recording contributes evenly spaced disjoint 1024-sample windows. The split is
by recording/run, not by independently held-out bearing, machine or site.

| Representation | Macro recording precision@10 |
|---|---:|
| AC RMS / crest / kurtosis | 1.000 |
| FFT band energy | 0.950 |
| Production Morlet pooled energy | 1.000 |
| Experimental temporal Morlet bins | 1.000 |
| Aligned normalized samples | 0.378 |

**Morlet does not beat the simple statistical baseline in this small test.** It
supports an executable real-recording baseline, not a differentiated product
claim. Fault-location labels are only a proxy for useful event similarity. The
same laboratory apparatus and potentially the same physical fault specimens span
loads. There is no healthy class or negative query in this dataset slice; negative
query behavior is covered by controls, not empirically calibrated here.

All positive methods with perfect precision have recall@10 of 10/24, because
there are 24 relevant gallery windows per fault family. Windows within a run are
correlated. Aggregate within recordings before macro averaging; no confidence
interval or independent-trial interpretation is claimed.

The adapter calls the actual Rust core. Baselines use the same normalization and
RMS feature-space distance without fitted weights. The temporal experiment bins
existing localized tokens into four coarse temporal bins; it is not added to the
production extractor or SQL index. Its unit control shows that time reversal can
change temporal features while leaving pooled features effectively unchanged.

### Partner-approved data

Supply a JSON manifest with a `recordings` array. Each entry requires `id`, `path`
(relative `.npy`, one-dimensional finite samples), `sha256`, `split` (`query` or
`gallery`), `run_id`, `label`, `sample_rate_hz`, `channel`, and `unit`. Include a
manifest-level name, source/permission context and label limitations. Query and
gallery run IDs must be disjoint. Mismatched rate/channel/unit are excluded.
A query label absent from the compatible gallery is a negative query: recall is
null and nearest distances are reported without inventing an acceptance threshold.

Windows are disjoint by construction and matches additionally undergo greedy
non-overlap suppression before top-k. The entire query acquisition run is excluded.
The evaluator uses cohort labels for relevance; interval-level reviewed judgments
and variable-duration events need an explicitly extended evaluation contract.
No resampling, order tracking or cross-channel alignment is performed.

## Exact search beyond the default budget

```sh
# Disposable PostgreSQL 17 database with this extension installed:
python evaluation/scale.py --sizes 10000,100000 --output /tmp/scale.json
# Explicit larger experiment, not a supported-capacity claim:
python evaluation/scale.py --sizes 1000000 --output /tmp/scale-million.json
```

Stream APIs and the Python client retain the default **4096**-candidate budget.
A trailing `max_candidates` argument permits explicit exact-search experiments
up to 1,000,000. Invalid budgets or larger eligible populations fail; candidates
are never silently truncated. This does not introduce an approximate index or
promise bounded latency. Caller timeouts and resource settings still apply.

The harness creates/removes a unique disposable schema and copies *synthetic
feature fixtures directly into segment rows*. It intentionally bypasses raw
chunks/extraction; those fixture recordings cannot be inspected as raw archives.
This isolates SQL candidate retrieval and must not be reported as ingest throughput
or real-waveform scaling. An independent NumPy oracle verifies IDs, coordinates
and distances for every timed query. Fixture construction has its own timing.

Reports include population, storage, first-query and repeated latency, server
settings, host, library/extractor versions, explicit default-budget rejection and
raw query timings. One warmup precedes measured queries; size order is seeded and
shuffled. Neither first query nor warm queries clear OS caches. Timeouts remain
failed cases in saved reports and yield a failing command. Larger-than-RAM,
concurrent-write and workload-isolation experiments remain necessary.

CI runs a 10,000-candidate correctness/ranking smoke. Use the **Manual workload
benchmarks** workflow for broader exact/streaming sweeps. Synthetic extraction and
storage benchmarks remain in `benchmarks/`; they are separate from relevance.

## Checks

```sh
python -m unittest discover -s evaluation -p 'test_*.py'
```

Build the Rust adapter first so numerical integration tests run rather than skip.
Normal CI does this and never downloads third-party recordings. The tests cover
split leakage, source hashes, compatibility, negatives, suppression before top-k,
fixture agreement and the complete evaluator with synthetic control recordings.

## Seismic replay reports

[`seismic-cx-north-chile-2024-v2`](reports/seismic-cx-north-chile-2024-v2.md) is the reference
report for the [seismic example](../examples/seismic/README.md): a fresh database, with provenance
(code revision, representation, definition and manifest hashes) in the report, and run reports
in `reports/seismic-cx-north-chile-2024-v2-runs/`.
[`v1`](reports/seismic-cx-north-chile-2024-v1.md) is historical; v2 reproduces its metrics and
rankings. Summary and limits: [docs/seismic-investigation.md](../docs/seismic-investigation.md).

