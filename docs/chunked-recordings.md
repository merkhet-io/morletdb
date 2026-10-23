# Continuous recording contract (core foundation)

`morlet_core::chunks::ChunkExtractor` extracts versioned Morlet features from an
append-only single-channel stream. It separates acquisition chunk boundaries from
search-window boundaries; a window can span any number of chunks.

```rust
use morlet_core::chunks::ChunkExtractor;
let mut stream = ChunkExtractor::new(4096.0, 512, 256, "zscore")?;
let first = stream.push(0, &first_samples)?;
let second = stream.push(first_samples.len() as u64, &next_samples)?;
```

A recording starts at sample offset zero. Each chunk supplies its absolute start
sample and 1..32768 bounded finite float64 samples. Start must equal the previous
end. Gaps, duplicate delivery and overlaps raise an error. Changing acquisition
rate, channel, units or calibration starts a new logical stream; the caller owns
that identity and metadata. Timestamps can be derived from a recording start time,
sample coordinate and rate; timestamp/gap policies are not implemented here.

The extractor uses a fixed rate in 128..200000 Hz, window size 512..4096, stride
1..32768, and normalization (`none`, `demean`, `zscore`). Window starts are global
multiples of the stride; normalization is independent per window. Complete windows
emit once and trailing partial windows wait for subsequent chunks. Ending a stream
does not pad the tail. Retained sample count is less than a window; temporary
allocation is bounded by the input chunk plus tail and output. A push may emit at
most 256 windows; otherwise split the input or increase stride.

Pushes stage state changes. Validation or numerical extraction errors leave the
in-memory offset/tail unchanged, allowing corrected input at the same offset.
Global offsets use u64 and total recordings may exceed the old 32768-sample bound.
Tests compare features and starts exactly against whole-recording extraction
across arbitrary boundaries and strides, exercise 70000 samples, and check failure
atomicity and bounded tails.

This is a portable core API, **not yet a durable database ingestion path**. State
has no checkpoint serialization. The Postgres collection still bounds each row to
32768 samples and does not join adjacent rows into a continuous stream. Next:
recording/chunk tables with explicit identities and offsets, idempotent batch
imports, durable extractor checkpoints and transactional publication of emitted
segments. No crash-recovery or asynchronous-search completeness guarantee is made
by this in-memory foundation.
