# Metric bridge (experimental)

Status: experimental, phase 1 of [#639](https://github.com/merkhet-io/morletdb/issues/639). Code in
[`tools/bridge`](../tools/bridge/README.md). The schema and the command line may change between releases.

MorletDB keeps waveforms losslessly and finds similar intervals. Trends, dashboards and alerts usually live
elsewhere: in InfluxDB, TimescaleDB, a plant historian, or whatever Telegraf feeds. The bridge publishes small
metrics computed from exact waveform intervals to those systems, and keeps the way back to the original samples.
MorletDB stays the store of record for the raw samples and for **what was published from them**. Every external
store is a copy.

## What it guarantees

| | Guarantee |
|---|---|
| Values | Computed by MorletDB from the stored samples (`morlet_interval_stats`); callers cannot supply them |
| Identity | Collection, recording, zero-based half-open sample interval and calculation version. An identical retry is a no-op; a changed payload under the same identity fails |
| Time | Exact bigint nanoseconds, from the time anchors (`morlet_sample_utc`) or start + rate. Fractional endpoints and windows across a clock break are refused, never rounded |
| Provenance | The recording's specification, time-axis version and interpretation revision are fixed at registration. If any changes, including a calibration correction that was later reverted (#620), publication of that source stops until someone reviews it |
| Evidence | Each ledger row stores the SHA-256 of the raw interval bytes (signed zero included), so a copy can be checked against the samples it came from |
| PostgreSQL sink | Exactly once: the copy and its delivery record commit in one transaction. A target row that differs from the ledger is reported as an error, not kept silently |
| Line-protocol sink | Handed over at least once: a batch is recorded as delivered only after it was written; after a crash it is written again. InfluxDB identifies a point by measurement, tags and timestamp, so the repeat overwrites itself with the same values. Beyond the handoff (Telegraf's buffer and outputs) delivery is best effort |
| No overwrites downstream | Within one database: two recordings of the same asset, channel and calculation never publish at the same timestamp; the second is refused, because downstream one point would replace the other. **Not covered across databases:** several MorletDB databases writing to one InfluxDB bucket must be told apart by a fixed tag per line sink (`add-sink NAME line --tag site=plant-a`), or use separate buckets |
| Access | Readers of metrics need explicit grants; nothing is granted to PUBLIC. A metric's interval reference grants no access to the waveform |

## What it does not do (yet)

- **No sources with row security.** The ledger and its copies do not inherit recording policies, so the bridge
  refuses a collection whose table or chunks have row security. Use one database per trust domain (#633).
- **One calculation:** `interval-stats-v1` (minimum, maximum, mean, RMS) over fixed windows aligned to sample 0.
  Windows are bucketed by their start; they are not split at calendar boundaries.
- **No read-back verification** of InfluxDB yet, and no retention or lifecycle coordination (#634).
- **Tested end to end with Telegraf 1.36.4 and InfluxDB 2.7.12** in [examples/influxdb](../examples/influxdb/README.md)
  (every point handed to Telegraf arrived with the same timestamp, tags and fields, values bit for bit); not yet
  with a TimescaleDB hypertable as target, and the bridge itself has no read-back check yet.
- **Not a steward job yet.** Run `drain` and `deliver` from a timer, or `telegraf` under Telegraf.
- **Telegraf into MorletDB** (context and events) is a later phase. Waveforms through Telegraf are out of scope:
  its points carry their own timestamps and decimal text, so uniform sampling and bit-exact values are not given.

## Shape

```
waveform samples ──► MorletDB collection ──► morlet_bridge.ledger ──┬─► PostgreSQL table (exact; may be a hypertable)
  (append)             (store of record)      (one row per window)   └─► line protocol ─► Telegraf ─► InfluxDB, MQTT,
                                                                                                     Kafka, Prometheus, …
```

- `morlet_bridge.sources`: a registered recording with its asset, channel, window and fixed provenance.
- `morlet_bridge.ledger`: one row per published window: interval, exact times, values, raw hash, provenance.
- `morlet_bridge.sinks` and `morlet_bridge.deliveries`: each sink and which ledger rows it has been given.
  Deliveries are tracked per row, not by a high-water mark, so a publication that commits late is not skipped.
- `morlet_bridge.metrics`: the default PostgreSQL sink target, an ordinary table.

Publication can share a transaction with the append, so a waveform and its metric commit or roll back together, or
run afterwards with `drain`, which commits one window per transaction and resumes after an interruption.

## Line protocol

One point per window:

```
morlet_metric,asset=pump-A,calculation=interval-stats-v1,channel=vib-x minimum=-1.0,maximum=2.0,mean=0.5,rms=1.224744871391589,sample_count=1024i,start_sample=0i,end_sample=1024i,ledger_id=1i,end_time_ns=1767225601000000000i,recording_id="a",collection="public.signals" 1767225600000000000
```

- Tags are asset, calculation and channel, plus any fixed tags of the sink (such as `site`), so the number of
  series does not grow with every recording.
  Recording, interval and ledger ID are fields: enough to open the original samples (for example from a Grafana
  data link to the MorletDB viewer).
- Floats are written in their shortest round-trip form and parse back to the same float64. NaN and infinities have
  no line-protocol form: they are left out and named in the `nonfinite` field.
- Timestamps are nanoseconds (Telegraf's and InfluxDB's default precision).

## Relation to TimescaleDB

The PostgreSQL sink works with an ordinary table or a TimescaleDB hypertable on `time_ns`; the bridge never requires
TimescaleDB. TimescaleDB's continuous aggregates and compression are under the Timescale License, so MorletDB images
do not include it; use your own installation. TimescaleDB-specific isolation, lifecycle and the Grafana example
are tracked in #631, #633, #634 and #635.
