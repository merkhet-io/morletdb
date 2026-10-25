# Morlet extractor numerical contract

The Rust core and Go reference implementation now share `morlet-energy-v1`. The PostgreSQL extension exposes the Rust port as immutable, parallel-safe, strict SQL functions. The Go implementation is retained only as the frozen numerical reference in tools/reference-go; its former HTTP/CLI application has been removed.

## Representation

- Uniformly sampled single-channel input, 512..32768 finite samples within +/-1e9.
- Finite sample rate in [128,200000] Hz.
- Remove the capture mean. Compute AC RMS, absolute centered peak, crest factor and population kurtosis.
- Twelve logarithmically spaced center frequencies from fs/64 through 0.4fs.
- Complex Morlet kernels with omega0=6 and scale=6fs/(2 pi f). Truncate at +/-ceil(3 scale), subtract discrete kernel DC and normalize complex L2 energy.
- Discard boundary coefficients that would need padding. Pool valid coefficient energies into consecutive blocks of at most 128 samples per band. Final blocks can be shorter.
- Features: log1p of 12 mean band energies, AC RMS, crest factor and kurtosis, in that order.
- Feature distance: root mean squared Euclidean distance across those 15 features. The SQL wrapper requires equal capture lengths and a common sample rate; units and operating context remain the caller's responsibility.

Overlapping bands do not sum to physical signal power. Tokens contain local energies, not phase or invertible wavelet coefficients. A similar spectral signature does not guarantee the same waveform, condition or fault. DC is ignored, and there is no speed invariance or resampling. Per-window amplitude normalization can be requested explicitly by nesting `morlet_normalize(...,'zscore')` before feature extraction.

## API distinction

`morlet_rms` includes DC and accepts 1..32768 samples. Signature RMS removes DC and requires at least 512 samples. `morlet_distance` compares aligned normalized samples; `morlet_feature_distance` compares spectral/statistical features. These are different metrics and must not be interchanged.

Numerical underflow can make kurtosis undefined for extremely tiny signals. Rust returns an explicit error when resulting features are non-finite rather than returning NaN/Infinity. This is a validation guard, not a changed transform definition. There is no bitwise cross-platform equivalence guarantee.

## Shared fixtures

`testdata/morlet-energy-v1` contains eight JSON fixtures with exact input samples, expected full Go signatures/tokens, and expected 15-element feature vectors. They cover silence, constant/DC, a tone, mixed frequencies plus seeded noise, an odd-length transient, edge impulses, a high-rate chirp and amplitude scaling with DC offset. Sample rates include both supported endpoints.

Go and Rust tests independently load the same committed files. Every numeric field is compared with tolerance `1e-10 + 1e-9 * abs(expected)`; versions, array sizes and structure must match. Additional analytical tests check tone localization, quadratic amplitude-energy scaling, DC invariance and token-weighted/global energy agreement. PostgreSQL tests send fixture samples through SQL and verify the feature vector and full-signature shape.

To deliberately regenerate fixtures from the Go reference:

```sh
make golden
```

Review fixture changes as numerical API changes. CI checks committed expectations and does not rewrite them. Changing band layout, kernel normalization, pooling or feature semantics requires a new extractor version and migration planning.

## Scope

The active Rust/PostgreSQL path provides continuous recording storage, durable synchronous appends/checkpoints, interval reads and exact feature retrieval. See [recording workflow](recording-workflow.md). Background extraction and indexed/approximate retrieval remain unimplemented. The [reference module](../tools/reference-go/README.md) and [fixture provenance](../testdata/morlet-energy-v1/README.md) preserve the independent numerical check.
