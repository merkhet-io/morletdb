# Bounded-lag envelope retrieval (BL1)

Research question: can bounded-lag envelope search (pg_morlet's `xc-envelope-f16-v1`
scorer) be served from standard vector indexes without losing its top ten, and later, does
that stay true while recordings are ingested?

- [RELATED_WORK.md](RELATED_WORK.md): related-work matrix and the open combination.
- [PROTOCOL.md](PROTOCOL.md): Stage A protocol (frozen before measuring) and amendments.
- [RESULTS.md](RESULTS.md): Stage A results and verdict (**CONTINUE**, development data).
- `probes.py`, `bl1.py`, `test_bl1.py`, `figure.py`, `dev_queries.json`, `results/`.


Update: [CONFIRMATION.md](CONFIRMATION.md) and [STAGE_B.md](STAGE_B.md) were frozen and run;
results in [STAGE_B_RESULTS.md](STAGE_B_RESULTS.md) (**confirmed**; **usable during
ingestion**, synchronous maintenance recommended). The RisingWave ADR is still deferred.
