# Study P1a: 300 kernel hard resets on a Raspberry Pi — results

> **DRAFT.** All three parts are complete. One check is still open (the final full `morlet_verify`, below); not
> published until it has run and the owner approves.

Protocol: [PROTOCOL.md](PROTOCOL.md), frozen 2026-11-08 (`42b7368`), with two dated amendments for Wi-Fi outages
(2026-11-09, `6a6defc`; 2026-11-10, `7588106`). Image `ghcr.io/merkhet-io/morletdb:0.3.9-alpha.1` (linux/arm64; the SQL
extension reports version 0.3.7), Raspberry Pi 3B+ on its SD card, Wi-Fi only. The controller on a second machine
cuts the box with a kernel hard reset (`sysrq-b`, no sync) after 60–240 s of writing, waits for it to come back and
runs the checks listed in the protocol after every restart.

## Result

**Pass on every check that ran: 300 confirmed restarts, 0 problems.** No lost acknowledged batch, no changed sample,
no corrupted chunk, no inconsistent derived or context row, no visible rolled-back write; the extension version
stayed 0.3.7.

| | Part 1 (cycles 0–239) | Part 2 (cycles 0–4, 43–) | Part 3 (cycles 0–59) | Total |
|---|---:|---:|---:|---:|
| Cycles counted | 240 | 5 | 60 | 305 |
| Confirmed restarts | 239 | 3 | 58 | **300** |
| Cycles with a problem | 0 | 0 | 0 | **0** |
| Cycles without a restart (not counted) | 1 (cycle 0, baseline) | 2 (cycle 0, baseline; cycle 4) | 2 (cycle 0, baseline; cycle 2) | 5 |
| Full `morlet_verify` (every 25th cycle) | 9 | 0 | 2 | 11, all 0 problems |

Part 2 ended at cycle 42 when the second outage was found, so it had no 25th cycle after its outage.

Outage cycles, reported separately and not part of the result (see the amendments): part 1 cycles 240–295 (56) and
part 2 cycles 5–42 (38). In all of them the box could not reach the controller's machine over Wi-Fi; none had a
restart, and none showed a data problem once the link was back.

## Recovery

Time from power-on until the database accepts a query, over the 300 confirmed restarts: median **46.9 s**, 10th
percentile 37.1 s, 90th percentile 77.0 s, maximum **135.1 s**. Median time from cut to a usable database: 80.8 s.

One cycle recorded 0.1 s (part 1, cycle 173): the controller's own step from the cut (2026-11-09 03:47 local time) to
power-on took 57 minutes, in the box's nightly backup window, so the box was already up and answered the first query.
It is a controller delay, not a database figure; leaving it out does not change the median.

## Data

- Stored end after the last cycle: 32,776,192 samples of recording `pc` (including about 9.2 million from the dry
  runs the study continued on); 64,013 envelopes.
- After every restart the stored end was at or beyond the last acknowledged end. The extra samples stored beyond the
  acknowledgement ranged from 0 to 671,744 (median 2,048): writes that reached the disk before the cut but whose
  acknowledgement did not reach the writer. That is allowed; losing an acknowledged batch is not.
- 14 resets needed a second attempt; each was confirmed (database down within 30 s) before the cycle counted.

## Open before publication

- **Final full verification.** The protocol asks for a full `morlet_verify` "every 25th cycle and at the end". The
  controller ran it every 25th cycle, but not after the last cycle of a part. It is run once on the box's database in
  its final state and the result is added here.

## Not shown by this study

As the protocol says: other storage than this SD card, other file systems, power cuts during upgrades or backups,
the steward's plans. P1a is a kernel hard reset, not a physical power cut: the SD card keeps power, so its own
write cache is not tested. Stage P1b (physical cuts through a switched plug) closes #190; if P1a passes and P1b fails,
the difference is the storage hardware.

## Files

`results/p1a.jsonl` (part 1, including the outage cycles), `results/p1a-part2.jsonl`, `results/p1a-part3.jsonl`;
controller, writer and checks in `controller.py`, `writer.py`.
