# Filling a real disk with the steward on (#287)

**Result: pass.**

- The steward stopped new recordings before the disk was full, and PostgreSQL never failed.
- Freeing space resumed ingest within 15 s, with no person involved.
- Every acknowledged sample read back bitwise.

| Check | Result |
|---|---|
| Ingest hold before the disk is full | set when free space reached 295 MB, with a 300 MB reserve, after 856 s and 54.3 M samples |
| How appends were refused | SQLSTATE 53100 with the steward's reason, never a PostgreSQL write error |
| PostgreSQL | no PANIC, FATAL, "could not extend/write" or "No space left" in the database log |
| Resume after freeing 200 MB | the hold was released and the next append succeeded 14.5 s later |
| Stored samples | 208 acknowledged batches (54.5 M samples) read back with **0** SHA-256 mismatches |
| Audit | two `ingest_hold` actions: set, then release |

## Run (2026-11-07, 22:51–23:08 UTC)

- **Machine:** Raspberry Pi 3B+ (passively cooled; 64 °C at the end, soft temperature limit active
  (`throttled=0x80008`), no undervoltage).
- **Filesystem:** a 1.2 GB ext4 file, loop-mounted at `/mnt/morlet-filltest`, holding the data directory and a 200 MB
  ballast file. Nothing outside it could fill.
- **Images:** `morletdb:main` (main of 7 Nov 2026, with the ingest hold) with `--data-checksums`, `max_wal_size=64MB`,
  and `morlet-steward:fill` (`--tick 3 --reserve-bytes 300000000 --reserve-fraction 0`).
- **Writer:** `fill.py` appends float64 noise (incompressible, about 8 bytes per sample) in batches of 262,144 samples,
  one transaction each, until refused. It then deletes the ballast and appends until accepted, and finally reads every
  acknowledged batch back.
- **Files:** `run_pi.sh`, `fill.py`, and `results/` (`result.json`, `db.log`, `steward.log`).
- **First attempt:** an earlier one, at 22:36, was stopped by hand after 8 minutes, at 540 MB free, because the power
  strip feeding the Pi made a clicking sound. It reached no result. The Pi kept power throughout (no restart, no
  undervoltage flag).

## Not covered here

- The time-remaining warnings: they need hours of growth history.
- Power cuts (#190).
- A multi-week soak (#191).
