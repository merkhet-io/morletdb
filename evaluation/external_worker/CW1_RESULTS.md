# CW1 results: external compute workers — PURSUE (and the in-database builder is the bigger inefficiency)

Run once on 2026-10-31 as frozen in [CW1_PROTOCOL.md](CW1_PROTOCOL.md) (6738d4e); harness
7bd8de7. R2's days 1–2, four stations: 8 recordings, 69.1 M samples, 33,736 windows (4096 /
2048). Database server and workers on the same 18-core machine.

| N | IN windows/s | IN DB CPU ms/window | EXT windows/s | EXT DB CPU ms/window | EXT worker CPU ms/window |
|---|---|---|---|---|---|
| 1 | 740 | 1.311 | 2,856 | 0.134 | 0.210 |
| 2 | 1,486 | 1.300 | 5,708 | 0.134 | 0.214 |
| 4 | 2,660 | 1.424 | 11,183 | 0.130 | 0.224 |
| 8 | 4,580 | 1.608 | 13,593 | 0.161 | 0.347 |

WAL per run: 35.1–36.0 MB in both arms (the same envelope rows are written).

| Pre-registered condition | Result |
|---|---|
| EXT rows byte-identical to IN, every window, every run | yes (33,736 / 33,736 in all 8 runs) |
| Robustness: SIGKILL a worker at 3 s and restart; two workers on the same recordings | every window exactly once, all byte-identical |
| DB CPU per window ≤ 0.3 × IN at every N | yes: 0.09–0.10 × |
| EXT throughput at N = 4 ≥ IN at N = 4 | yes: 11,183 vs 2,660 windows/s |

**Verdict: PURSUE.** A stateless worker that reads encoded chunks through SQL, computes with
`morlet-core` and writes back with key-idempotent inserts is exact and crash-safe, and takes
90% of the per-window CPU off the database server.

## The finding the protocol did not ask about

EXT uses about **4× less CPU in total** than IN (0.34 vs 1.31 ms per window at N = 1). The
envelope arithmetic is the same code, so most of IN's cost is the in-database build path,
not the computation:
- `morlet_build_envelopes` executes one dynamic SQL statement per window, reading the window
  through `morlet_read_interval`;
- with stride 2048 every raw sample is decoded twice (each window spans two chunks), and
  every window round-trips through `float8[]` arrays;
- EXT decodes each chunk once per batch of 64 windows and encodes envelopes in one process.

So the separation "external vs in-database" is confounded with "batched vs per-window" here.
A batched in-database builder (one Rust call per batch, decoding each chunk once) would likely
recover most of the gain without any external process; that is a product change worth its own
measurement, and probably the first one to make.

## Limits

- One machine: CPU moved out of PostgreSQL is still on the same host; the measure is database
  CPU, as stated in the protocol. Network transfer to a second machine was not tested (EXT
  moves the encoded chunks out, about 5 bits/sample, and envelope rows back).
- Database CPU is summed over `postgres` processes, including background processes active
  during the run; it is the same accounting for both arms.
- Envelopes only. HNSW maintenance stays in PostgreSQL; raw repacking is not tested.
- Scaling flattens beyond 4 external workers (11.2k → 13.6k windows/s), where the shared
  machine and database writes become the limit.

## Next steps (not started)

1. **Batched in-database builder** (product): a Rust function building a batch of envelopes
   from the chunks it decodes once; measure DB CPU per window against today's 1.31 ms, with
   byte-identical output.
2. **External worker as a supported tool** (`tools/`), with a claim table or advisory locks so
   workers do not overlap, if separate compute is needed for capacity rather than for CPU
   efficiency; then raw repacking as a second job type.
