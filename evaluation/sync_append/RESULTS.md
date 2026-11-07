# Synchronous group append: 8 channels at 50 kHz (#292)

**Result.** Appending 8 synchronous channels as one group (`morlet_append_acquisition_raw`) is as fast as appending
them separately, in one transaction per batch, with `morlet_append_chunks_raw`:

| Arm | Median samples/s (all channels) | Rounds |
|---|---:|---|
| group, one call per batch | **8.66 M** | 8.66 M, 8.54 M, 8.98 M |
| separate, 8 calls per batch | 8.34 M | 8.34 M, 8.40 M, 8.27 M |

Every recording was read back bitwise. The stored size is 1.95 bytes per sample: a 16-bit grid of a noisy 50 Hz signal.

## Run

- **Script:** `bench.py`. Results: `results/desktop.json`.
- **Data:** 8 channels at 50 kHz, 20 batches of 1 s per arm and round (8 × 50,000 samples per batch, chunks of
  32,768), 3 rounds.
- **Order:** the two arms alternate in one session, and the order flips each round.
- **Machine:** Apple M desktop, release image of this change (`deploy/Dockerfile`, PostgreSQL 17.6), client on the
  host. The load average was 2.4–2.6.
- **Method note: send sample bytes as binary parameters** (psycopg's `%b`).
  - With `%s`, psycopg sends a list of bytes as a text array. Sending 8 × 400 KB took 105 ms against 24 ms in
    binary, which made the group arm look 2.8× slower (3.0 M samples/s) in a first run.
  - Server-side, measured in one `DO` block, 10 group batches took 185 ms and 10 × 8 separate appends 166 ms.
  - The 11% difference is plpgsql array access: separate appends that take their samples from a `bytea[]` also
    took 188 ms.
