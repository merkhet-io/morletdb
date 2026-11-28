# MorletDB beside InfluxDB

Trends stay in InfluxDB, waveforms go to MorletDB, and every trend point opens the exact waveform it was computed
from. Synthetic data, local demo credentials, one `docker compose up`.

![Grafana showing RMS and peak per 4,096-sample window for two pumps from InfluxDB; pump B's peak jumps from 0.4 to about 0.8 for a minute while its RMS barely moves.](dashboard.jpg)

```
 collector ──4 kHz waveforms──► MorletDB ──metrics per window──► Telegraf (execd) ──► InfluxDB ──► Grafana
  (synthetic)                   (raw samples,  (bridge ledger)                                      │
                                 search)  ◄──────────── "Open waveform" link (viewer) ─────────────┘
```

- **collector:** two pumps, vibration at 4 kHz, ten minutes of history and then live. Each 4,096-sample window is
  appended to MorletDB and its metrics (minimum, maximum, mean, RMS) published to the
  [metric bridge](../../docs/metric-bridge.md) ledger in the same transaction. Pump B has impact-like bursts for one
  minute of the history.
- **telegraf:** runs the bridge as an `execd` input and writes the points to InfluxDB. Tags: `asset`, `channel`,
  `calculation`, `site`; the recording, its sample interval and the ledger ID are fields.
- **grafana:** RMS and peak per window from InfluxDB (Flux). A click on a point offers **Open waveform in MorletDB**,
  which opens the [viewer](../../tools/viewer/README.md) at exactly that recording and interval.

## Run it

```sh
cd examples/influxdb
docker compose up -d --build        # MorletDB 0.4.0-beta.7, InfluxDB 2.7.12, Telegraf 1.36.4, Grafana 11.2.2
```

Open Grafana on http://127.0.0.1:3000 (admin / `influx-demo-only`), **Dashboards → MorletDB → Pumps**. For the links,
start the viewer on this machine (still in `examples/influxdb`):

```sh
python -m pip install -r ../../tools/viewer/requirements.txt
DATABASE_URL=postgresql://morlet:influx-demo-only@127.0.0.1:54333/plant python ../../tools/viewer/viewer.py
```

The dashboard's **MorletDB viewer** field holds the viewer address (default `http://127.0.0.1:8765`). Peak shows pump
B's bursts clearly, RMS hardly: short impacts barely change the energy of a window. That is the point of keeping the
waveform: the trend says *when*, the samples show *what*.

Check that InfluxDB holds exactly what the bridge handed over (no point missing, none extra, every field equal), and
that Grafana's query and link carry what the drill-down needs:

```sh
python check.py                        # needs psycopg; prints PASS or FAIL
```

```sh
docker compose down -v                 # removes everything
```

## Verified

On 2026-11-28 (macOS, Docker Desktop, the image versions above): `check.py` found every one of the ledger rows
handed to Telegraf in InfluxDB, with the same timestamp, tags and fields, values bit for bit, and no other point; Grafana's panel query
returned both pumps with the link fields; in Chrome, clicking a pump-B point in the burst opened the viewer on that
recording at samples 1,560,576 to 1,564,672 (390.1 to 391.2 s after the start, the clicked point's time), with the
impacts visible.

## Limits

- **Demo credentials and synthetic data.** Do not reuse the passwords or the token.
- **Telegraf delivery is at least once up to its input, best effort beyond.** It buffers in memory and does not
  acknowledge delivery to an input; the ledger in MorletDB keeps every point:
  `docker compose exec telegraf python3 /opt/morlet/bridge/cli.py status --sink telegraf` (the container's
  environment holds the connection settings, so no password goes on a command line). There is no read-back check of InfluxDB in the bridge yet; `check.py` does it for this example.
- **The viewer runs on the host** and binds to 127.0.0.1 only, as everywhere in MorletDB.
- Telegraf's image is extended with Python 3 and psycopg for the bridge (`Dockerfile.telegraf`).
