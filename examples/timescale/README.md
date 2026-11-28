# Timescale trends to MorletDB waveforms

An experimental, synthetic example of one PostgreSQL database with both extensions.
Timescale stores irregular telemetry and small waveform statistics. MorletDB stores
original waveforms and searches similar intervals. The [adapter guide](../../tools/timescale/README.md)
explains identity, permissions, scheduling and retention; [TC1](../../evaluation/timescale_coexistence/README.md)
records the initial experiment separately from implementation tests.

## Run locally

Choose local demo passwords, then start the database and Grafana:

```sh
export POSTGRES_PASSWORD='choose-a-local-demo-password'
export READER_PASSWORD='choose-a-separate-reader-password'
export GRAFANA_ADMIN_PASSWORD='choose-a-grafana-password'
docker compose -f examples/timescale/compose.yaml up --build -d
# Wait for the database health check to pass.
export DATABASE_URL="postgresql://postgres:${POSTGRES_PASSWORD}@127.0.0.1:54348/morlet_demo"
python examples/timescale/seed.py
```

Python needs `psycopg[binary]==3.2.10` and `numpy==2.2.6`. If a password contains URL
special characters, supply a correctly escaped connection URL or libpq conninfo.
The seed is synthetic and verifies its recording/metric retries. Its raw telemetry
fixture uses fixed input and is not a production telemetry ingestion adapter.

Open Grafana at `http://127.0.0.1:3008`, log in as `admin`, and open **MorletDB +
TimescaleDB investigation**. The five panels show irregular telemetry, materialized
waveform summaries, interval references, publication backlog and aggregate freshness.
The energy counter is shown as text to preserve values larger than JavaScript's
exact integer range. Exact nanosecond timestamps are also text; displayed UTC times
are a microsecond-resolution projection.

Start the existing read-only waveform viewer using the restricted reader:

```sh
export DATABASE_URL="postgresql://timescale_demo_reader:${READER_PASSWORD}@127.0.0.1:54348/morlet_demo"
python tools/viewer/viewer.py
```

Click a `waveform_url` in the interval panel. The link selects the original recording
and exact half-open sample interval, including identifiers with spaces, ampersands
and slashes. Select a waveform region and use **Find similar** to retrieve the other
synthetic capture. Similarity alone does not establish a fault or diagnosis.

The example is one trust domain. For a second tenant, use a second database with
its own owner/reader roles; revoke PUBLIC CONNECT in both. Do not reuse this reader
for multiple mutually isolated databases. This setup does not claim that an
aggregate inherits source RLS. The adapter refuses RLS sources.

## Upkeep and expiry

Run the adapter's `tick` command as the writer or explicitly install its systemd
timer. A reader cannot run maintenance. Tick refreshes only its configured recent
window; historical backfill needs an explicit refresh. Pending samples and missing
source evidence are not interpreted as healthy signal values.

The adapter can preview/apply derived-metric retention while preserving the ledger,
historical aggregate and raw waveform. A metric row can disappear while the interval
panel continues to show `metric expired`; a missing raw source has its own state.
Do not install a competing Timescale retention/refresh policy on the metric table.
The example contains no automated waveform deletion.

Stop containers with `docker compose -f examples/timescale/compose.yaml down`.
The named database volume persists. No command in these instructions deletes it.

## Automated smoke

```sh
python -m pip install 'psycopg[binary]==3.2.10' 'numpy==2.2.6' 'playwright==1.56.0'
python -m playwright install chromium
python examples/timescale/smoke.py
```

The smoke starts uniquely named disposable containers and a local viewer, checks
all five panel queries through Grafana's API, renders the dashboard in Chromium,
clicks the real waveform link and verifies the selected recording/interval and
read API. It also repeats seeding, runs a bounded worker tick, applies a retention
plan and checks that retained history and raw evidence remain available. Containers,
network and anonymous database volumes are removed in `finally`; it never connects
to your running demo or other databases. The Docker image must already be built.

This example is not an Influx migration tool, an alert delivery service, a
multi-tenant shared-table solution or a production performance benchmark. Extension
upgrades, pgvector coexistence, failover and sustained mixed-load evaluation remain
tracked in #631. The combined build includes Timescale-licensed functionality;
MorletDB's Apache license does not relicense those dependencies.
