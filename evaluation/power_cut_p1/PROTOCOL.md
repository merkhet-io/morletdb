# P1: power cuts during durable ingestion on a Raspberry Pi (#190)

Status: **DRAFT.** Frozen, with image digest, commit and switch filled in, in a commit made before the first counted
cut. A dry run with a person pulling the plug comes first and is not counted. Changes after freezing are dated
amendments at the end.

## Question

When the power goes at a random moment while MorletDB stores samples with search features, chunk summaries, roll-ups
and context, does everything it acknowledged survive, bit for bit, and is everything it did not acknowledge invisible?

## Setup

- **Box:** Raspberry Pi 3B+ (passively cooled), Debian 11 64-bit, Docker; the database on its SD card (the storage a
  small edge box really has). Nothing else on the Pi's switched socket. The Pi's own operating system can be damaged by
  power cuts too; its SD card is imaged before the study, and a damaged OS is reported as such, separately.
- **Switch:** a Shelly smart plug (Gen2+ local RPC `Switch.Set`) feeding only the Pi's power supply. *Model recorded
  at freezing.*
- **On the Pi** (`compose.yaml`, project `pc-p1`, `restart: always`):
  - the release image `ghcr.io/merkhet-io/morletdb:<tag>@<digest>` (*recorded at freezing*) with data checksums and
    the documented small-disk settings (`max_wal_size=256MB`, `min_wal_size=64MB`, `wal_compression=on`); otherwise
    PostgreSQL defaults (`fsync=on`, `synchronous_commit=on`, `full_page_writes=on`);
  - the steward (`--load-per-cpu 1.0`), building envelopes, chunk summaries, roll-ups and context values meanwhile;
  - the writer (`writer.py`): one transaction per batch of 4,096 samples (append plus one `batch` context row), about
    four batches a second; every 10th batch also a rolled-back write to recording `rb`; after each COMMIT an
    acknowledgement over TCP to the controller.
- **Controller** (`controller.py`) on a second machine on the same network: switches the plug and checks after every
  restart. It makes no cut between 02:45 and 03:45 (another job on the Pi).
- **Samples** are a deterministic function of their index (`writer.values`), so any range can be regenerated.

## Stages

- **P1a, hard resets (no plug):** the controller makes the box restart at once through the kernel (`sysrq-b`: no
  cache write-out, no unmount), over SSH with a key that can do nothing else (`/usr/local/sbin/pc-hard-reset`). This
  loses everything in RAM, PostgreSQL's and the operating system's, like a power cut, but the SD card keeps its power.
  It tests PostgreSQL's crash recovery and MorletDB's consistency, not whether the SD card honours flushes. Controller:
  `--switch "cmd:timeout 15 ssh -i /keys/pc_reset ... dana@<box> true; true;;true"` (the box restarts by itself).
- **P1b, power cuts (with the plug):** the same 300 cycles with `--switch shelly:<host>`; only P1b closes #190. If P1a
  passes and P1b fails, the difference is the storage hardware.

## Procedure

1. **Dry run (not counted):** 5 cycles with `--switch manual` (a person pulls the plug when told), then 5 with the
   plug; the controller and the box are adjusted if needed, and every change is recorded here before freezing.
2. **Freeze.**
3. **Study:** 300 confirmed restarts. Each cycle: power on; wait for the database (at most 10 minutes); run the checks below
   against the last end acknowledged before the previous cut; let the writer run for a seeded random 60-240 s; cut the
   power; wait 15 s. Seed 190. About 15-20 hours.

## Dry-run changes (recorded before freezing)

1. **One snapshot for all checks** (2026-11-08, P1a dry run): the writer restarts with the box and commits while the
   checks run; reading the stored end and then counting context rows a moment later found one row more than batches
   (twice in 5 cycles). Every check now runs in one read-only REPEATABLE READ transaction. Not a MorletDB finding.
2. **Recovery time from the cut** (same dry run): the reset's SSH connection hung until TCP gave up, so the time from
   "on" to a working database looked like 0.1 s. The reset command now has a 15 s timeout, and `since_cut_s` (cut to
   database answering, including the boot) is recorded.

3. **Every reset confirmed** (second P1a dry run): 2 of 5 reset commands had no effect (5 cuts, 3 new boots), and
   those cycles would have been counted as restarts. The controller now confirms each cut: the database must stop
   answering within 30 s, otherwise the reset is repeated (at most 3 times, attempts and errors recorded); and a
   cycle counts only if PostgreSQL's start time changed (`restarted`). Unconfirmed cycles are reported, not counted;
   the study runs until 300 confirmed restarts.

## Checks after every restart (`controller.check`)

- **recovered:** the database accepts a query within 10 minutes of power-on (time recorded);
- **acknowledged:** recording `pc` ends at or after the last acknowledged end; its end is on a batch boundary;
- **bit-exact:** the last three acknowledged batches and one seeded random older batch equal the generator, and their
  interval statistics (served from roll-ups and summaries where present) equal the generator's count, min and max;
  reading decodes every chunk it touches (structure and CRC-32C);
- **atomic:** exactly one `batch` context row per stored batch, contiguous from 0 to the stored end, none beyond it;
- **rolled back:** recording `rb` has no sample and no context row;
- **derived:** no envelope beyond the stored end, all on the 512-sample grid, no hole in their sequence;
- **extension:** its version unchanged;
- **full check:** `morlet_verify` over the collection every 25th cycle and after the last.

The checks were tested before the study by injecting each fault they look for (a lost acknowledged batch, a changed
sample, an extra context row, a changed extension version): each was reported. Eight simulated crashes (writer killed,
PostgreSQL stopped in immediate mode) passed all checks.

## Pass

Over all 300 cycles: **zero lost acknowledged batches, zero changed samples, zero corrupted chunks, zero inconsistent
derived or context rows, zero visible rolled-back writes.** Recovery times are reported (median, maximum), not judged.

A failure is analysed before any change: a lost acknowledgement can also be the SD card acknowledging a flush it did
not do. That is reported as a hardware finding with its evidence, not hidden, and the study is repeated on storage
that honours flushes (an SSD) to tell the two apart.

## Not covered

Other storage than this SD card (unless a failure requires it), other file systems, power cuts during upgrades or
backups, the steward's plans.
