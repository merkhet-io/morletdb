# Shelly smart-plug collector

Polls a Shelly Gen2+ plug (for example a Shelly Plug S Gen3) over its local RPC once per second. It stores power,
voltage, current, mains frequency and the energy counter in MorletDB, losslessly and without inventing samples. It was
written for the home study [D1](../../evaluation/home_d1/PROTOCOL.md).

```sh
python -m pip install -r examples/collectors/shelly/requirements.txt
export DATABASE_URL=postgresql://...      # a role that may append to the collection
export SHELLY_PASSWORD=...                # only if the plug has authentication on (user "admin")
python examples/collectors/shelly/collector.py --plug 192.168.2.50 --device washer --collection public.home --create
```

- **Segments:**
  - A poll that fails or answers late ends the segment; the next answer starts a new one. A gap stays a gap.
  - Each segment is a synchronous acquisition (`<device>.<start>`) of recordings `<device>.<channel>.<start>`.
  - `morlet_acquisition_cross_stats` gives the exact mean(voltage × current) beside the plug's own power.
  - Segments also end at UTC midnight.
- **Durability:**
  - Every reading is written and fsynced to a spool file before it is stored. Readings are stored in one
    transaction every `--flush-s` seconds (60).
  - After a crash or power cut, the spool is replayed from where the database ended, with the same chunk boundaries.
  - Gaps are logged with their cause in `gaps.jsonl` in the spool directory.
- **Credentials:** the plug's password comes from `SHELLY_PASSWORD` in the environment (for example a systemd
  `EnvironmentFile`), never from the command line.
- **Tests:** `python -m unittest discover -s examples/collectors/shelly` covers segmenting, parsing, the spool and
  replay. With `MORLET_SHELLY_TEST_DSN` it also runs end to end against a database with a fake plug.
