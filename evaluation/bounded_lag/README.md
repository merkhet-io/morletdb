# Bounded-lag envelope retrieval (BL1)

Research question: can bounded-lag envelope search (pg_morlet's `xc-envelope-f16-v1`
scorer) be served from standard vector indexes without losing its top ten, and later, does
that stay true while recordings are ingested?

- [RELATED_WORK.md](RELATED_WORK.md): related-work matrix and the open combination.
- [PROTOCOL.md](PROTOCOL.md): Stage A protocol (frozen before measuring) and amendments.
- [RESULTS.md](RESULTS.md): Stage A results and verdict (**CONTINUE**, development data).
- `probes.py`, `bl1.py`, `test_bl1.py`, `figure.py`, `dev_queries.json`, `results/`.

Stage B (streaming maintenance), the confirmation slice and the RisingWave ADR are not in
this PR.
