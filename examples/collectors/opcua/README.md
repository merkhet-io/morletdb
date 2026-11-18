# OPC UA reference collector

A minimal, read-only reference for collecting from OPC UA into MorletDB: a regularly sampled waveform delivered in
numbered blocks, plus scalar operating context, from a **local test server with a documented information model**.
It shows a mapping that keeps sample timing, identity, quality and gaps honest. It is not a general OPC UA
connector: other servers need the same model or an adapted mapping, and nothing here was tested against a
commercial server or device.

```sh
python -m pip install -r examples/collectors/opcua/requirements.txt
python examples/collectors/opcua/server.py --port 4840 &                  # the test server
export DATABASE_URL=postgresql://...
python examples/collectors/opcua/collector.py opc.tcp://127.0.0.1:4840/morletdb/ \
    --collection public.machine --create --sample-quantum 0.001
python examples/collectors/opcua/check.py --collection public.machine     # compare with the server's signal
```

## Information model (test server)

Namespace `urn:morletdb:example-machine`, under `Objects`:

| Node | Type | Meaning |
|---|---|---|
| `Machine.Speed` | Double, rpm | scalar process tag, irregular updates with source timestamp and status (some Uncertain) |
| `Machine.Load` | Double, % | scalar process tag, as Speed |
| `Machine.State` | String | operating state: Running, Setup |
| `Machine.Vibration.SampleRate` | Double | 5,120 samples per second |
| `Machine.Vibration.BlockSize` | UInt32 | 1,024 samples per block |
| `Machine.Vibration.Unit` | String | `g` |
| `Machine.Vibration.LatestBlock` | UInt64 | newest block; block *i* holds samples *i*·BlockSize … (*i*+1)·BlockSize−1 of one continuous signal |
| `Machine.Vibration.OldestBlock` | UInt64 | oldest block still held (a ring buffer of 64 blocks, 12.8 s) |
| `Machine.Vibration.ReadBlock(index)` | Method → Double[], DateTime | one block and the time of its first sample |

Block indices follow a monotonic sample counter, so they continue across server restarts. The samples are a
deterministic function of the block index (`signal_model.py`), which lets `check.py` compare every stored sample.

## Mapping into MorletDB

- **Waveform → recording.** Blocks are appended at sample offset (*i* − first block) · BlockSize with
  `morlet_append_chunk`, one transaction per block. The recording id is `<prefix>.<first block>`; its metadata holds
  the endpoint, the node, the first block, the block size and the time of the first sample (`start`).
- **Restarts and reconnects.** The collector resumes from the recording's stored progress and reconnects with
  back-off; nothing is kept only in memory.
- **Duplicates.** A block delivered again is an identical replay, which MorletDB accepts without change; a
  different block at the same offset is refused.
- **Gaps.** Blocks the server no longer holds (below `OldestBlock`) are reported with their range, and a new
  recording starts at the oldest available block. Samples are never invented to bridge a gap.
- **Scalar tags → context, not waveforms.** Each change of Speed, Load or State becomes a context event in the
  current recording's sample coordinates. It holds from the first sample at or after its source time
  (`context_rows.py`, exact, never rounded to the nearest sample). The context value is the tag's value alone, so
  the same state in two cycles has the same segment context and can be compared with history (#480); a status
  other than Good (Uncertain, …) is stored as its own event, `<key>.status`, at the same sample. A change without
  a source timestamp is not placed; the server time is not used in its place. No sample rate is invented for
  them. The batch id is
  derived from the change, so a change delivered twice is stored once. Changes stamped before the recording
  starts are not stored and are reported.

## Verified

`scenarios.sh` runs these against a scratch database; results on 2026-11-02 (asyncua 1.1.5, PostgreSQL 17 with
pg_morlet 0.3.3-alpha.1):

| Scenario | Result |
|---|---|
| normal run | contiguous recording; every sample equal to the server's signal |
| collector restarted after 4 s (inside the ring buffer) | resumed the same recording, no gap |
| collector stopped 16 s (longer than the ring) | gap reported with its range (19 blocks), new recording at the oldest available block |
| server restarted while collecting | disconnect, reconnect, same recording continued |
| block delivered twice | identical: accepted, nothing changed; altered: refused (`conflicting replay at chunk offset`) |
| context | value, status and source time stored; an Uncertain Speed value kept its status (verified before #480 with status inside the value; since #480 the status is a `<key>.status` event, checked by `test_context_rows.py`, not rerun against the server) |

## Limits

- One test server and information model; no security (`NoSecurity`), which is acceptable only locally.
- No OPC UA events or alarms, no historical access (HA), no structured or vendor-specific waveform types, no
  multi-channel acquisition.
- Context is stored as events; operating states are not yet turned into intervals.
- A real device's block counter, timing and ring size must be checked before trusting the gap handling.
