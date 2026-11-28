# MorletDB for InfluxDB users

**Keep InfluxDB for your trends. Put your waveforms in MorletDB.** This page is for teams that store high-rate
signals (vibration, motor current, acoustics; hundreds of Hz to tens of kHz) in InfluxDB and run into its limits
there. MorletDB does not replace InfluxDB for DevOps metrics or low-rate telemetry; InfluxDB is good at those.

## When MorletDB fits

| You need | InfluxDB | MorletDB |
|---|---|---|
| Store kHz waveforms compactly and bit for bit | one row and timestamp per sample | uniformly sampled recordings in compressed chunks; every read is bit-exact, signed zero included |
| Find earlier intervals that look like this one | not built in | search by signal shape, with an explanation of each match |
| Know which calibration and unit a sample was read under | not built in | interpretation revisions: corrections never rewrite samples ([Python SDK](python-sdk.md), [calibration sets](calibration-sets.md)) |
| Join signals with assets, work orders, labels | limited | it is PostgreSQL: plain SQL joins, transactions, roles |
| Run on a small edge box | see below | designed for it, with an unattended steward |

Measured once on our waveform workload (B3.18 and M1c, [results](../evaluation/tsdb_b3/RESULTS.md)): InfluxDB 3
Core 3.12.2 with one client writing the seven channels of a machine recording (three at 64 kHz, three at 4 kHz,
one temperature at 1 Hz), against MorletDB 0.3.9-alpha.1 on the same
machine. All loads were bit-exact in both. Ingestion was about 15× faster in MorletDB without durable
acknowledgement (D2) and about 130× with it (D1, where InfluxDB's one-second WAL flush dominates a single
sequential client; several writers were not measured). After InfluxDB's snapshot to Parquet, MorletDB's database took 2.5× less
space than InfluxDB's volume; counting MorletDB's write-ahead log too (which B3 leaves out by convention), about
1.2×. InfluxDB needed a 4 GB memory cap to finish the load, MorletDB passed at 256 MB (caps were
powers of two). These are one workload, one client and one machine, not a general ranking: several writers, other
data and other InfluxDB versions were not measured. The results page states every condition.

## 1. Try it on your own data first

**Easiest: straight from your running InfluxDB 1.x or 2.x**, with one command on a laptop in your network and a
read-only user or token; no export and nothing installed on the InfluxDB host. See the
[trial kit](../examples/trial/README.md):

```sh
cd examples/trial
export INFLUX_USERNAME=reader INFLUX_PASSWORD='...'         # 1.x; for 2.x: export INFLUX_TOKEN='...'
docker compose run --rm trial --influx-url http://192.168.0.10:8086 --influx-db plant \
  --measurement vibration --start 2026-11-28T10:00:00Z --end 2026-11-28T10:10:00Z
```

It reads in slices of 60 seconds with a pause between them, so a controller is not burdened, and keeps values
exact: floats with every digit, negative zero and integer types included.

**From an export.** Nothing leaves your machine. Start the release image as a disposable database:

```sh
docker run -d --name morlet-trial -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me -e POSTGRES_DB=trial \
  -p 127.0.0.1:54329:5432 ghcr.io/merkhet-io/morletdb:0.4.0-beta.7
docker exec morlet-trial psql -U morlet -d trial -c "CREATE EXTENSION pg_morlet"
```

Export a short period as line protocol. Replace the paths, names and times with yours. InfluxDB 1.x exports a whole
database (`influx_inspect` has no measurement filter); that is fine, because the trial imports only fast numeric
series and lists everything else it left out. InfluxDB 2.x can select one measurement:

```sh
# InfluxDB 1.x
influx_inspect export -datadir /var/lib/influxdb/data -waldir /var/lib/influxdb/wal -database plant \
  -start 2026-11-01T00:00:00Z -end 2026-11-01T01:00:00Z -lponly -out export.lp
# InfluxDB 2.x
influxd inspect export-lp --bucket-id 0123456789abcdef --engine-path ~/.influxdbv2/engine \
  --measurement vibration --start 2026-11-01T00:00:00Z --end 2026-11-01T01:00:00Z --output-path export.lp
```

Then run the trial against the disposable database (the password goes in the environment, not on the command line):

```sh
python -m pip install -r tools/ingest/requirements.txt
export DATABASE_URL='host=localhost port=54329 dbname=trial user=morlet' PGPASSWORD=change-me
python tools/ingest/morlet_trial.py export.lp
```

Optionally add `--influx-bytes N`: the size of the same data in your InfluxDB, measured by you, for example with
`du -sb` on the directory that holds it. `--max-points` (default 10 million per file, about 1 GB of memory) applies
to line protocol; other formats are read whole.

The trial imports every eligible numeric series at 100 Hz or faster, reads each back and compares it bit for bit,
checks the time of the samples against the original timestamps (at every time anchor and at a sample of up to
2,000 points per recording), and writes `morlet-trial-report/report.md`: what was imported, what was left out and
why (slow trends, booleans, strings, conflicting points), storage, import and build time. A trial is one run on one
machine, not a benchmark.

## 2. Bring history over

```sh
python tools/ingest/morlet_import.py export.lp --collection public.plant --create
```

Each numeric series (measurement, tag set, field) at `--min-rate` (default 100 Hz) or faster becomes a recording;
the tags stay in its metadata. Slower series are trends and are left out, with the reason in the report. The
timestamps become time anchors, so jitter, gaps and clock steps are kept, not smoothed: MorletDB reports the time
of any sample with its uncertainty. Details: [import](import.md#influxdb-line-protocol).

## 3. Keep your dashboards

- **Grafana:** MorletDB is PostgreSQL, so Grafana's built-in PostgreSQL data source reads it; grant the dashboard
  role read access only.
- **Trends back into InfluxDB:** the metric bridge (experimental, [#639](https://github.com/merkhet-io/morletdb/issues/639))
  publishes RMS, peak and mean of exact waveform windows as line protocol through Telegraf's `execd` input, with the
  recording and interval in each point, so a dashboard can open the original waveform.

## 4. Collect new waveforms

Telegraf is a good collector for trends, not for waveforms: its points carry their own timestamps and decimal
text, so even sampling and bit-exact values are not guaranteed. Use a MorletDB collector instead (MQTT, OPC UA,
SeedLink in `examples/collectors/`, WinCC OA in `integrations/winccoa/`), or append from your own code with the
[Python SDK](python-sdk.md).

## Status and limits

- MorletDB is in **beta** (0.4.0-beta.7): single node, PostgreSQL 17 to 19, Linux and macOS.
- The line-protocol import and the trial are new; the bridge is experimental and not yet tested end to end with a
  real Telegraf and InfluxDB.
- Not supported: InfluxQL or Flux queries, the InfluxDB write API, irregular series below `--min-rate` as
  recordings.

Questions and reports: [GitHub issues](https://github.com/merkhet-io/morletdb/issues).
