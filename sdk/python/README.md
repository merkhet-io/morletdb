# MorletDB Python SDK

A small, synchronous Python client for [MorletDB](https://github.com/merkhet-io/morletdb) (PostgreSQL with the
`pg_morlet` extension): store regularly sampled waveforms losslessly with their timing and metadata, read
exact sample intervals back, and search for similar intervals.

Status: alpha (0.1.0a1), not yet published to PyPI. Install from the repository.

```sh
pip install "./sdk/python[plot]"        # from a checkout; [plot] adds matplotlib for the example
```

## A database to talk to

```sh
docker run -d --name morletdb -e POSTGRES_USER=morlet -e POSTGRES_PASSWORD=change-me \
  -e POSTGRES_DB=morlet -p 127.0.0.1:54329:5432 ghcr.io/merkhet-io/morletdb:0.3.3-alpha.1
docker exec morletdb psql -U morlet -d morlet -c "CREATE EXTENSION IF NOT EXISTS pg_morlet"
export DATABASE_URL=postgresql://morlet:change-me@127.0.0.1:54329/morlet
python examples/python/quickstart.py     # ingest, read, search, plot (no download)
```

## Use

```python
from datetime import datetime, timezone
import numpy as np
import morletdb

samples = np.load("pump7_accel.npy")                     # 1-D float or integer array
with morletdb.Client.connect("postgresql://...") as db:  # commits on a clean exit
    db.create_collection("public.signals")
    rec = db.ingest("public.signals", "pump-7/accel-x", samples, sample_rate_hz=25_600,
                    channel="accel-x", unit="m/s^2", metadata={"asset": "pump-7"},
                    start_time=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc))
    iv = db.read("public.signals", "pump-7/accel-x", 40_960, 45_056)     # samples [40960, 45056)
    result = db.search("public.signals", "pump-7/accel-x", 40_960, metadata_filter={"asset": "pump-7"})
    for m in result.matches:
        print(m.rank, m.distance, m.ref)                 # stable reference: recording + [start, end)
        original = db.read_ref(m.ref).samples            # the stored samples, bit for bit
```

| Call | Does |
|---|---|
| `Client.connect(conninfo)` / `Client(connection)` | owned connection (commit on exit) / your connection (you commit) |
| `create_collection(name, searchable=True)` | create a collection if missing; enable search envelopes |
| `ingest(collection, recording_id, samples, sample_rate_hz=, channel=, unit=, start_time=, metadata=, search_window=4096, search_stride=None)` | store a complete waveform (samples 0..n-1) and build its search windows |
| `append(collection, recording_id, start_sample, samples)` | continue a recording at the next sample |
| `read(collection, recording_id, start, end)` / `read_ref(ref)` | original samples of `[start, end)` as float64 |
| `search(collection, recording_id, start, end=None, metadata_filter=, top_k=10)` | similar stored intervals with their distances |
| `stats(collection, recording_id, start, end)` | count, min, max, mean, RMS (server SQL 0.3.3) |
| `recording(...)`, `recordings(collection, metadata_filter)`, `samples_for_time(recording, t0_ns, t1_ns)` | recording information and time-to-sample conversion |

The contract (intervals, timing, what the distance means, transactions, limits) is in the
[Python SDK guide](https://github.com/merkhet-io/morletdb/blob/main/docs/python-sdk.md).
