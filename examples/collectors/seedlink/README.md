# SeedLink collector

Stores live waveforms from a public SeedLink server (for example GEOFON, `geofon.gfz.de:18000`) in MorletDB,
losslessly and without inventing samples. Written for the soak test S1 (#191, `evaluation/soak_s1`).

```sh
pip install -r examples/collectors/seedlink/requirements.txt
export DATABASE_URL=postgresql://...
python examples/collectors/seedlink/collector.py --server geofon.gfz.de:18000 \
  --streams 'GE.WLF..HH?' 'CX.PB01..HH?' --collection public.live --create
```

- **One recording per segment.** A stream's packets continue a recording while each starts where the previous one ended
  (within half a sample). A gap, a rate change, resent samples that differ from what was received, or a new UTC day
  start a new recording `<net>.<sta>.<loc>.<cha>.<start>`. Resent identical samples (after a reconnect) are dropped.
- **Spool first.** Every packet goes to an hourly spool file (with its recording and sample offset) before it is
  buffered; the spool is fsynced every second. Buffers are stored every `--chunk` samples (default 4096) or
  `--flush-s` seconds. After a crash the collector replays the spool and appends what the database lacks; at most the
  last second of received packets can be lost. SIGTERM (docker stop) stores the buffers before exiting.
- **Evidence.** `gaps.jsonl` (every segment end with its cause, every reconnect, every replay) and `status.json`
  (packets, samples, stored, backlog, reconnects, stale streams) in the spool directory.
- **Check against the archive.** `verify_fdsn.py` downloads the stored spans from the data centre's FDSN service and
  compares them sample for sample (exit status 1 on any difference).

Limits: it does not resume from SeedLink sequence numbers, so data sent while it is disconnected is not received (it
is in the archive; `verify_fdsn.py` reports it as missing, not as different). The data's terms are the networks':
the soak uses openly accessible GEOFON (GE, doi:10.14470/TR560404) and IPOC (CX, doi:10.14470/PK615318) stations; cite
them by DOI, and check each network's licence before redistributing waveforms.

Tests: `python -m unittest discover -s examples/collectors/seedlink -p 'test_*.py'` (no server, no database).
