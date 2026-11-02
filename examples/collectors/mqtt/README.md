# MQTT reference collector

A reference collector for recording batches and operating context sent over MQTT with a small, versioned JSON
contract (`morlet-mqtt/1`). It shows how to keep sample offsets, units, identity and gaps honest over a
transport that may duplicate, reorder or lose messages. This is plain MQTT with JSON payloads, **not Sparkplug**:
there is no Sparkplug topic namespace, birth/death certificates, aliases or protobuf payloads.

```sh
python -m pip install -r examples/collectors/mqtt/requirements.txt
export DATABASE_URL=postgresql://...
python examples/collectors/mqtt/collector.py --host 127.0.0.1 --port 1883 --collection public.mqtt --create
python examples/collectors/mqtt/publisher.py --port 1883 --acquisition A1 --batches 40   # test publisher
python examples/collectors/mqtt/check.py --collection public.mqtt
```

## Contract: morlet-mqtt/1

Topics `morlet/<site>/<asset>/<channel>/samples` and `morlet/<site>/<asset>/<channel>/context`, QoS 1.

**Sample batch** (JSON object):

| Field | Meaning |
|---|---|
| `schema` | `morlet-mqtt/1` |
| `acquisition` | identity of one continuous acquisition (one sample clock) |
| `channel`, `unit` | channel name and unit of the values |
| `sample_rate_hz` | samples per second of the acquisition |
| `seq` | the sender's batch number (for diagnostics) |
| `start_sample` | offset of the batch's first sample within the acquisition: the authority for ordering |
| `start_time` | optional: time of sample 0 of the acquisition (ISO 8601), needed to place context |
| `samples` | 1 to 32,768 numbers |

**Context message:** `schema`, `acquisition`, `key`, `value`, `status` (optional, e.g. `good`, `uncertain`) and
`source_time` (ISO 8601). Context is irregular and is never resampled into a waveform.

A sender must keep `start_sample` continuous within an acquisition and start a new acquisition id when its
sample clock restarts.

## How the collector stores it

- **Recordings:** `<acquisition>.<channel>.<segment start>`; each batch is appended at `start_sample` −
  segment start with `morlet_append_chunk`.
- **Acknowledgement after commit:** a persistent session (fixed client id, clean session off) and manual
  acknowledgement only after the database commit. A collector that crashes or is offline gets the messages
  again from the broker. Exactly-once storage does not come from MQTT's QoS; it comes from MorletDB's
  idempotent appends: a redelivered batch is an identical replay, accepted without change.
- **Altered duplicates** (same offset, different samples) are refused and reported.
- **Reordering:** a batch ahead of the stored end waits, unacknowledged, for the missing ones.
- **Gaps:** if the missing batches have not arrived after `--gap-timeout` seconds (or more than `--max-pending`
  batches wait), the missing range is reported and a new recording starts at the first waiting batch. Samples
  are never invented.
- **Context** is stored as an event of the recording whose samples cover its source time, with value, status and
  source time; context ahead of the stored samples waits; context inside a gap is reported and not stored.

## Verified

`scenarios.sh` against a local Mosquitto 2.0.20 broker (no authentication) and a scratch database, 2026-11-02;
`check.py` compares every stored sample with the publisher's deterministic signal:

| Scenario | Result |
|---|---|
| batches 5, 6 sent twice | accepted once, nothing changed |
| batch 7 followed by an altered copy | altered copy refused (`conflicting replay at chunk offset`) |
| batches 10 and 11 swapped | reordered, no gap |
| batch 20 never sent | gap of 512 samples reported after 3 s, new recording; context split correctly between the two |
| collector offline while 20 batches are published | delivered on reconnect by the persistent session, same recording, no gap |
| collector killed (SIGKILL) mid-stream, restarted | all 120 batches stored once, identical, no gap |

## Limits

- One test broker without authentication or TLS; production brokers need both.
- Batches are acknowledged in order of storage; a batch waiting for a gap holds its acknowledgement until the gap
  timeout.
- One acquisition's context uses that acquisition's `start_time`; acquisitions without it get no context.
- Not Sparkplug, and not tested with any vendor gateway.
