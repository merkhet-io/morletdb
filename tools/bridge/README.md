# MorletDB metric bridge (experimental)

Publishes metrics computed from exact waveform intervals, with provenance, to a PostgreSQL table or as InfluxDB
line protocol (for Telegraf). Design and guarantees: [docs/metric-bridge.md](../../docs/metric-bridge.md), #639.

Requires pg_morlet SQL 0.3.18 or later (0.4.0-beta.7) for interpretation revisions, Python 3.10+ and `psycopg` 3.2
(`pip install -r requirements.txt`). The database comes from the libpq environment (`PGHOST`, `PGDATABASE`, …)
or `--dsn`.

## Quick start

```sh
python tools/bridge/cli.py install                                  # creates schema morlet_bridge
python tools/bridge/cli.py register public.signals pump-A-2026-11-28 pump-A vib-x --window-samples 4096
python tools/bridge/cli.py drain 1                                  # publish complete windows
python tools/bridge/cli.py add-sink dashboard postgres              # target: morlet_bridge.metrics
python tools/bridge/cli.py deliver dashboard                        # exactly once
python tools/bridge/cli.py status --sink dashboard
```

Grant dashboard readers only what they need:

```sql
GRANT USAGE ON SCHEMA morlet_bridge TO grafana_reader;
GRANT SELECT ON morlet_bridge.metrics TO grafana_reader;
```

The target may be a TimescaleDB hypertable (bring your own TimescaleDB; it is not part of MorletDB images). Its key
includes `time_ns` for this. Tested with TimescaleDB 2.32.1, including compression, a continuous aggregate and dump
and restore (`test_bridge_timescale.py`, [details](../../docs/metric-bridge.md#relation-to-timescaledb)):

```sql
SELECT create_hypertable('morlet_bridge.metrics', by_range('time_ns', 86400000000000::bigint), migrate_data => true);
-- continuous aggregates on an integer time column need a "now" in the same unit
CREATE FUNCTION morlet_bridge.now_ns() RETURNS bigint LANGUAGE sql STABLE
  AS $$ SELECT (extract(epoch FROM now()) * 1e9)::bigint $$;
SELECT set_integer_now_func('morlet_bridge.metrics', 'morlet_bridge.now_ns');
```

The hypertable tests need a server with both extensions; `timescale/Dockerfile` builds a local, unpublished test
image from the MorletDB release image and Timescale's apt repository:

```sh
docker build -t morlet-bridge-timescale-test tools/bridge/timescale
docker run -d --name morlet-bridge-ts -e POSTGRES_PASSWORD=... -p 127.0.0.1:28933:5432 morlet-bridge-timescale-test
export PGPASSWORD=...                   # the POSTGRES_PASSWORD given to the container
MORLET_BRIDGE_TIMESCALE_DSN='host=127.0.0.1 port=28933 user=postgres dbname=postgres' \
  MORLET_PG_BIN=/usr/lib/postgresql/17/bin python -m unittest tools/bridge/test_bridge_timescale.py
```

## Telegraf

```sh
python tools/bridge/cli.py add-sink telegraf line --measurement vibration
```

Then let Telegraf run the bridge as an `execd` input. At each interval Telegraf writes a newline, and the bridge
publishes new windows and answers with the pending points. See [telegraf.conf](telegraf.conf):

```toml
[[inputs.execd]]
  command = ["python3", "/opt/morletdb/tools/bridge/cli.py", "telegraf", "telegraf", "--drain-all"]
  signal = "STDIN"
  data_format = "influx"
  environment = ["PGHOST=localhost", "PGDATABASE=plant", "PGUSER=morlet_bridge"]
```

The bridge hands each point to Telegraf at least once. Telegraf does not acknowledge receipt to an input and
buffers in memory, so delivery beyond that handoff, to Telegraf's outputs, is best effort. It is tested end to end with
Telegraf 1.36.4 and InfluxDB 2.7.12 in [examples/influxdb](../../examples/influxdb/README.md), which also shows the
Telegraf image with Python 3 and psycopg: Telegraf itself needs neither, but the bridge command its `execd` input
starts runs in Telegraf's container and does. The ledger keeps every point; `status --sink telegraf`
shows what was handed over.

Atomic append and publication from Python, on one connection:

```python
import psycopg
from morlet_bridge import Bridge            # tools/bridge on PYTHONPATH

WINDOW = 4096                               # must equal the registered window_samples
with psycopg.connect(autocommit=True) as conn:
    b = Bridge(conn)
    with b.transaction():
        source_id = b.register("public.signals", rid, "pump-A", "vib-x", WINDOW)   # idempotent
    with b.transaction():
        conn.execute("SELECT morlet_append_chunk('signals', %s, %s, %s::float8[])", (rid, offset, values))
        b.publish(source_id, offset, offset + WINDOW)   # offset: a multiple of WINDOW; values: WINDOW samples
```

## Tests

```sh
python -m unittest discover -s tools/bridge                         # line protocol only
MORLET_BRIDGE_TEST_DSN='host=localhost port=28817 dbname=postgres' python -m unittest discover -s tools/bridge
```

The database tests create and drop their own databases and roles.
