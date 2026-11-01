# E4: scaling crossover between probe search and materialized exact search — protocol (FROZEN)

Frozen 2026-11-01, before any E4 data was downloaded or measured. Deviations are appended below and committed
before the run they affect. Owner approval for the download (about 1.4 GB) and the new databases (about 17 GB)
was given in chat on 2026-11-01.

## Question

E3 found no latency advantage for probe search over materialized exact search at 234,762 windows. At which
gallery size N, if any up to about 1M windows, does probe search (P-PG) become faster at p95 than the fastest
exact arm of E3 (X-MAT32R), and how does recall behave as N grows?

## Data

Development data only: CX HHZ, the four R2 stations (PB01, PB02, PB06, PSGCX), 100 Hz, from GEOFON (FDSN),
fetched exactly as R2 (`evaluation/retrieval_scaling/fetch.py`: one miniSEED per station-day, touching
records merged, real gaps kept as separate segments).

- Days 1–14 (2025-03-01..14): the existing `data/cx-scale` (checksums in `retrieval_scaling/results/SHA256SUMS.mseed`).
- Days 15–61 (2025-03-15..04-30): new, `data/cx-scale-e4`; checksums committed as `results/SHA256SUMS.mseed`
  after the download.

No held-out data or data from another year is used.

## Galleries (nested)

| Gallery | Days | Database |
|---|---|---|
| N1 | 1–14 | `morlet_r2` (existing, 234,762 windows; as left by E1, D1 and E3) |
| N2 | 1–30 | `morlet_e4_30` (new) |
| N3 | 1–61 | `morlet_e4_61` (new) |

New databases are loaded by `load_e4.py` with R2's recording identifiers, metadata, chunking (4,096-sample
chunks), envelopes (4096 / 2048) and collection name `public.r2`, followed by
`morlet_enable_envelope_probes('public.r2', 4096)` with `maintenance_work_mem = 4GB` (as `prepare_db.sh`).
R2's study side tables are not built (no arm uses them). Window counts, load times, index build times and
sizes are recorded.

## Build

Installed library `3c786684…` (`main` 68594d7, pg_morlet 0.3.1), as E3, so all three galleries use the same
build. New databases get their SQL objects from that build's installed extension files.

## Queries

E1's 100 development queries (`evaluation/bounded_lag/dev_queries.json`; from day 1 of the four stations), the
same in every gallery.

## Arms

E3's harness (`evaluation/strong_exact/e3.py`) unchanged in method: X-PG, P-PG, X-MAT64, X-MAT32R, as defined in
E3's protocol. The arms of interest are P-PG and X-MAT32R; X-PG and X-MAT64 are recorded because they are cheap
and show how the other paths scale. One configuration: `VECLIB_MAXIMUM_THREADS` unset (E3 found that the
setting had no effect).

## Correctness gate (per gallery, before its timing)

E3's gate: synthetic selection tests, scorer and decode checks, and list identity of X-MAT64 and X-MAT32R with
the in-database exact search for all 100 queries. A failure stops that gallery's timing until explained.

## Procedure

Galleries are measured in the order N1, N2, N3, each in its own process directly after its gate, with no load
or index build running at the same time. Within a gallery: E3's warm-up and two interleaved rounds.

## Metrics

Per gallery: request-latency p50 / p95 / p99 of all arms, kernel and search parts of the matrix arms, recall@10
of P-PG against the exact lists, materialization time and memory, probe index size and build time.

## Decision rules

1. **Per gallery:** R(N) = p95(P-PG) / p95(X-MAT32R) at the request boundary, 95% bootstrap interval over
   queries (10,000 resamples, seed 20261101). "Probes faster" if the whole interval is < 1; "exact faster" if
   it is > 1; otherwise "tie". (This is a crossover question, so the threshold is 1, not E3's 0.5 for "a clear
   advantage"; E3's 0.5 is reported alongside.)
2. **Crossover:** the smallest measured N with "probes faster". If none, an extrapolation from fits to the
   three galleries' p95 values, X-MAT32R linear in N (least squares) and P-PG a power law in N (least squares
   on logs), reported only as an extrapolation with the fitted values, and never as a measured crossover.
3. **Recall:** P-PG's mean recall@10 at each N is reported; a fall below 0.95 at any N is flagged.

## Budget and stop

At most one working day and two hours of timed measurement; disk under 50 GB. Stop after E4. No further
gallery sizes, new data or paper edits without the owner.

## Deviations

(none yet)
