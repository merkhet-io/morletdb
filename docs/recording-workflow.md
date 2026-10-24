# Import, query, inspect

This is the canonical Rust/PostgreSQL prototype path. The first run builds an
extension image and downloads Python packages; it needs Docker Compose v2,
network access and an x86-64 Linux Docker environment. Other architectures have
not been verified. This is a local preview, not production deployment guidance.

```sh
docker compose run --rm walkthrough
# Verify persistence and replay after restart:
docker compose restart db
docker compose up -d --wait db
docker compose run --rm walkthrough
```

The walkthrough creates three synthetic recordings, runs the real Parquet
importer twice, reads across 300-sample chunks, queries `[768,1280)` and checks an
identical event at `[1536,2048)` in another recording. All returned matches can
be reconstructed with `morlet_read_interval`. No synthetic result establishes
real-world retrieval relevance or fault-detection accuracy.

The demo database is bound to localhost port 54329 with user/database `morlet`
and password `morlet-demo-only`. These credentials are deliberately public; use
a separate isolated instance. The walkthrough user can install extensions.
Real applications should use restricted roles and an administrator-installed extension.

`docker compose down` preserves data. **`docker compose down -v` deletes the demo
recordings.** The preview uses an unchanged persistent named volume across runs.
Base image patch versions and package versions are fixed, but tags and apt inputs
are not content-addressed release artifacts. For versioned prebuilt images, exact digests and the publication procedure, see
[alpha releases](releases.md). Source builds remain available with the command above.

## Python in an existing environment

```sh
python -m pip install -r tools/ingest/requirements.txt
export DATABASE_URL=postgresql://morlet:morlet-demo-only@localhost:54329/morlet
python examples/inspect_recordings.py
# Optional visual inspection (install matplotlib separately):
python examples/inspect_recordings.py --output matches.png
```

The thin client is in `tools/client/morlet_client.py`. Put that directory on
`PYTHONPATH` or import it from your application checkout:

```python
import psycopg
from morlet_client import RecordingClient

with psycopg.connect() as conn:  # standard PG* environment variables
    client = RecordingClient(conn, "public.signals")
    query = client.read_interval("recording-1", 768, 1280)
    matches = client.search_interval("recording-1", 768, 1280, top_k=10)
    for match in matches:
        samples = client.read_interval(match["recording_id"],
                                       match["start_sample"], match["end_sample"])
```

The client uses the caller's connection/transaction and never commits it or
changes roles. Collection names are bound as `regclass` values, not interpolated
SQL. Reads and search use invoker privileges and parent/child table RLS.

## SQL contract

- `morlet_read_interval(collection, recording_id, start, end)` reconstructs
  1..32768 samples with acquisition metadata and representation version. Negative,
  missing, incomplete, out-of-bounds or partly hidden intervals fail explicitly.
- `morlet_search_interval(collection, recording_id, start, end, metadata_filter,
  top_k, exclude_source, suppress_overlaps, max_candidates)` searches an existing interval. Query
  length must equal the stored extraction window (512..4096 samples).
- Source exclusion and overlap suppression default to true. Intervals are
  half-open: adjacent intervals do not overlap. Source exclusion removes matches
  intersecting the query in its source recording, not the entire recording.
- Greedy overlap suppression follows `(distance, recording_id, start_sample)`
  rank order and runs before top-k. Results include metadata, rate, channel, unit
  and extractor version. Distances are feature distances, not confidence scores.
- Compatibility requires rate/channel/unit/window/normalization/extractor match.
  Search is exact over at most 4096 visible eligible segments. It errors beyond
  that budget instead of searching a prefix. An explicit trailing `max_candidates`
  argument allows experiments up to 1,000,000; see [evaluation](../evaluation/README.md).
- `morlet_search_streams` remains the input-array compatibility API. It delegates
  to the same eligibility/scoring function but retains its original result shape
  and source-inclusive behavior.

These changes target fresh prototype installations. Existing installed 0.1.0
extensions do not acquire new SQL functions just by replacing the shared library.
No `ALTER EXTENSION UPDATE` path is supplied yet; use a new disposable database
for this preview and preserve existing data/backups. Do not drop an extension in
an existing application database to follow this walkthrough.
