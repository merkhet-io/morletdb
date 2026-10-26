# R1 evaluation (2025): materialized envelopes next to compressed raw

**Decision under the frozen rules: NARROW.** Stored envelopes return exactly the
same results as decoding raw. They cut p95 latency to about a tenth and bytes
fetched by about 18×. They fail the storage bound: 27.9% of the raw chunk table
against a limit of 25%. Next step: smaller or quantized envelopes, as the
protocol prescribes.

- **Protocol:** [../../PROTOCOL.md](../../PROTOCOL.md), frozen at `0eb2802`. There
  was one logged post-freeze change, the 2025 fetch driver, which changes no method.
- **Run:** a single run of commit `6ec192c` (clean tree) on 2026-10-26, with
  `--n 200` fixed from the 2023 development run. The 2025 data was not
  downloaded or inspected before the freeze, and nothing was tuned on it.
- **Environment:** Apple M5 Max, 128 GB, macOS 27.0.1; PostgreSQL 17.11 (pgrx),
  pg_morlet 0.2.0 release build, `shared_buffers` 128 MB; client Python 3.12 with
  psycopg 3.2.10 over localhost TCP; one connection.

## Data

- 1,687 windows of 4096 samples each: 1,444 event windows from 275 events, and 243
  no-match windows. CX HHZ, 10 stations.
- 1,444 queries. Mean eligible gallery: 184 windows per query.

## Decision rules applied

| Rule (frozen) | M (materialized) | Pass |
|---|---|---|
| recall@10 vs E ≥ 0.99 | 1.000 (identical set and order in 100% of queries) | yes |
| p95 latency ≤ 0.25 × E | 166 ms vs 1609 ms (0.10×) | yes |
| envelopes ≤ 25% of the raw chunk table | **27.9%** | **no** |
| relevance: upper 95% CI of M − E median \|Δd\| ≤ +1 km | 0.0 km [0.0, 0.0] | yes |

M passes agreement and latency and fails storage, which gives **narrow**. The
pause conditions (latency gain < 2×, agreement < 0.95) are far from met.

C200 against the same thresholds:
- recall@10 0.993;
- p95 171 ms (0.11×);
- relevance +0.025 km [0.005, 0.051];
- storage 32.3%: it needs the envelopes plus the ST table, so it also fails storage.

The cascade gives no latency or storage advantage over M at these gallery sizes,
so the "only C passes" branch does not apply.

## Measurements

### Storage (`pg_total_relation_size`, after load)

| Table | Bytes | Per window | vs raw chunk table |
|---|---|---|---|
| raw chunks (encoded) | 12,754,944 | 7,561 | 1.000 |
| envelopes (410 × float4) | 3,555,328 | 2,107 | 0.279 |
| ST features (51 × float4) | 565,248 | 335 | 0.044 |
| original float64 payload (reference) | 55,279,616 | 32,768 | 4.33 |

- The envelope's value payload is 1,640 bytes.
- The rest of each envelope row is tuple, array and index overhead.
- The development period gave 27.7%, so the result is stable across years.

### Generation and update cost (per window, all 1,687 windows)

| Step | ms |
|---|---|
| read raw back (`morlet_read_interval`) | 11.6 |
| compute the envelope (CPU) | 0.98 |
| compute ST (CPU) | 0.92 |
| insert the derived rows | 3.5 |
| update: append + derive + insert, one transaction (n = 200) | p50 27.7, p95 42.7 |

### Per-query access and latency (timed pass, after one untimed pass)

| Method | p50 ms | p95 ms | Bytes fetched | Samples decoded | Rows read |
|---|---|---|---|---|---|
| E: full raw decode | 1213 | 1609 | 6.04 MB | 755,292 | 184 |
| M: materialized envelopes | 121 | 166 | 0.33 MB | 4,096 | 184 |
| C200: ST, then envelopes | 125 | 171 | 0.35 MB | 4,096 | 358 |

Bytes and decoded samples include the query's own raw window (4,096 samples,
32 kB), which every method fetches.

### Ranking agreement with E

| Method | recall@10 | Identical set | Identical order |
|---|---|---|---|
| M | 1.000 | 1.000 | 1.000 |
| C200 | 0.993 | 0.949 | 0.949 |

### Task relevance (separate from agreement)

Mean over events of the median |Δ hypocentral distance| (km) of the top 10;
bootstrap over 275 events, 2000 resamples, seed 7.

| Method | km | Difference vs E [95% CI] |
|---|---|---|
| E | 11.60 | — |
| M | 11.60 | 0.000 [0.000, 0.000] |
| C200 | 11.62 | +0.025 [+0.005, +0.051] |

## Caveats

- **Heavy background load during the run.** A virtual machine used about 3.4
  cores, and the load average was about 19. The timed pass therefore took much
  longer than the development run.
  - Absolute latencies are inflated and noisy: E's p50 was 1213 ms here against
    924 ms in 2023.
  - The methods run back to back for each query, so they share conditions. The
    ratio of M to E is the meaningful latency result: 0.10× at p95, and 0.105× in
    2023.
- **No true disk-cold measurements.** All data fits in memory, and the OS cache
  cannot be dropped without root.
- **Spurious warnings.** NumPy printed "overflow / divide by zero in matmul"
  warnings in the ST distance step. They are a known false alarm of this NumPy's
  Accelerate BLAS: a matmul of clean random arrays raises the same warning. All
  ST values are finite, with |x| ≤ 4.6. No result was affected.
- **Agreement is exact by construction.** M's agreement is exact because the
  stored envelope is the same float computation as the client-side one, rounded to
  float4. The float4 rounding did not change any ranking.
- **Scope.** One window per recording, catalogue-placed windows, one machine and
  no concurrency. The query's own raw read is included in every method's latency.

## Next step (narrow)

- Study smaller envelope representations against the same frozen rules:
  - float16, about 0.8 kB of payload;
  - int8 with a per-window scale, about 0.4 kB;
  - a coarser decimation.

  Rough estimates for float16 and int8 are 11–14% and 6–8% of the raw chunk
  table.
- Tuning is allowed on 2023 (development) only. 2025 is now consumed: any
  re-run on it is engineering, not validation.
- A confirmatory test needs another unused period or independent stations, with
  the protocol frozen first.
- The envelope stays a replaceable materialized representation, not an index.
  Originals remain the source of truth.

## Reproduce

```sh
python evaluation/materialized/fetch_windows.py 2025 data/r1-runs/eval DATA   # DATA: timing run dir with station coordinates
python evaluation/materialized/r1.py load --year 2025 --windows data/r1-runs/eval --out data/r1-runs/eval
python evaluation/materialized/r1.py run  --year 2025 --windows data/r1-runs/eval --out data/r1-runs/eval --n 200
```
