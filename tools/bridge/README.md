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

The target may be a TimescaleDB hypertable (bring your own TimescaleDB; it is not part of MorletDB images). Its
key includes `time_ns` for this; the conversion has not been tested yet:

```sql
SELECT create_hypertable('morlet_bridge.metrics', 'time_ns', chunk_time_interval => 86400000000000, migrate_data => true);
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
buffers in memory, so delivery beyond that handoff, to Telegraf's outputs, is best effort. This path has not yet
been tested end to end with a real Telegraf and InfluxDB. The ledger keeps every point; `status --sink telegraf` shows what was handed over.

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
