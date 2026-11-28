# Try MorletDB on your InfluxDB data

One command on a laptop in your network reads a few minutes of one measurement from your InfluxDB 1.x or 2.x,
stores it in a throwaway MorletDB, verifies every value bit for bit, checks the timestamps at every time anchor and
at a sample of points (reporting the largest deviation), and writes a report. Nothing is installed on the
controller or the InfluxDB host, no export is needed, and nothing leaves your network.

## What you need

- A laptop with Docker (Docker Desktop on Windows or macOS, or Docker Engine on Linux) that can reach the InfluxDB.
- Read access only: in **InfluxDB 1.x** a user with `READ` on the database; in **InfluxDB 2.x** a read token for the
  bucket. InfluxDB 3 is not covered by this kit; export line protocol instead ([import](../../docs/import.md)).
- One measurement with signals sampled at 100 Hz or faster (vibration, current, acoustics), and its time window.
  Start with about 10 minutes.
- About 15 minutes, most of it the first build of the trial container.

## Run it

```sh
git clone https://github.com/merkhet-io/morletdb && cd morletdb/examples/trial

# InfluxDB 1.x: a read-only user (kept in the environment, never on the command line)
export INFLUX_USERNAME=reader INFLUX_PASSWORD='...'
docker compose run --rm trial --influx-url http://192.168.0.10:8086 --influx-db plant \
  --measurement vibration --start 2026-11-28T10:00:00Z --end 2026-11-28T10:10:00Z

# InfluxDB 2.x: a read token
export INFLUX_TOKEN='...'
docker compose run --rm trial --influx-url http://192.168.0.10:8086 --influx-org plant --influx-bucket machines \
  --measurement vibration --start 2026-11-28T10:00:00Z --end 2026-11-28T10:10:00Z
```

Times are UTC. Use your InfluxDB's address; for one running on the laptop itself, `http://host.docker.internal:8086`.
**Plain `http://` sends the password or token and the data unencrypted:** use it only on a trusted local network,
otherwise `https://`. Credentials can also go in a `.env` file next to `compose.yaml` (it is ignored by Git).
On **Linux**, run `export TRIAL_UID=$(id -u) TRIAL_GID=$(id -g)` first, so the report folder stays writable.
Already have a line-protocol export? Put it in `input/` and run `docker compose run --rm trial /input/export.lp`.

When you are done:

```sh
docker compose down -v        # removes the trial database and everything in it
```

## What happens on your InfluxDB

Only read queries: one `SHOW FIELD KEYS` (1.x) and then one query per 60 seconds of data, with half a second's
pause between queries (`--slice-s`, `--pause-s` change both). Beyond 10 million values (`--max-points`) the run
stops with an error and keeps nothing; choose a shorter window or raise the limit. On a busy controller, choose a
shorter window or a longer pause.

## What you get

`morlet-trial-report/report.md` (and `report.json`):

- which series became recordings, and which were left out and why (slow trends, booleans, strings, ...);
- **bit-exact:** every value read back from MorletDB compared with what InfluxDB returned;
- **time:** MorletDB's time of the samples against the original timestamps, at every time anchor and at a sample
  of up to 2,000 points per recording;
- storage per sample and import time on this laptop.

`source.lp` is the data that was read (line protocol) and `source.json` where it came from (without credentials).
The report contains series names and numbers, no samples; if you share it with us, check the names first.
`source.lp` contains your data: keep it.

This is a trial of your data on your laptop, not a benchmark. Questions:
[GitHub issues](https://github.com/merkhet-io/morletdb/issues).
