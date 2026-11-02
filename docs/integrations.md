# Works with

Technologies MorletDB has a working, documented integration with. Each entry says what you can do, how to run
it, its scope and how it was verified. Listing means a documented integration only; it does not imply
endorsement, partnership or certification by the owners of these names. The website shows this list as plain
text: most owners' trademark policies do not allow third-party projects to show their logos without permission
(see [logo and trademark notes](#logo-and-trademark-notes)).

Verification levels:
- **CI:** tested on every pull request.
- **Release:** tested by the release workflow before an image is published.
- **Manual:** run by hand on the date given; not repeated automatically.

## Database and search

### PostgreSQL extension
- **What:** `pg_morlet` adds recording storage (exact, compressed), interval reads, segment features and
  bounded-lag similarity search to PostgreSQL 17 and 18.
- **Run:** [installation and queries](installation-and-queries.md).
- **Scope:** PostgreSQL 17 (release images, upgrades, benchmarks) and 18 (fresh source installations).
- **Verified:** CI, extension tests on PostgreSQL 17 and 18, with and without pgvector; upgrade tests from
  earlier releases.

### Optional pgvector indexing
- **What:** probe search over envelope windows through pgvector's HNSW index, with exact fallback for small or
  selective galleries.
- **Run:** [optional indexed probe search](installation-and-queries.md); `morlet_import.py --probes`.
- **Scope:** pgvector 0.8 or later; optional, not in the release image.
- **Verified:** CI with pgvector 0.8.0; recall and latency in the [technical report](../research/paper/STUDIES.md).

### Docker images
- **What:** PostgreSQL 17 with `pg_morlet`, ready to run.
- **Run:** [releases](releases.md); `ghcr.io/merkhet-io/morletdb:<version>`.
- **Scope:** linux/amd64; linux/arm64 from 0.3.3-alpha.1. PostgreSQL and `pg_morlet` only (no pgvector).
- **Verified:** Release, a clean-checkout walkthrough and restart trial on each architecture, before and after
  publishing.

### MCP server for AI agents
- **What:** a read-only Model Context Protocol server: an agent such as Claude can list, describe, compare,
  trend, search and read recordings, and cite what it finds.
- **Run:** [MorletDB for AI agents](mcp.md); demos in [`examples/agent-demo`](../examples/agent-demo/README.md)
  and [`examples/machine-demo`](../examples/machine-demo/README.md).
- **Scope:** any MCP client over stdio; set-up documented for Claude Code and Claude Desktop.
- **Verified:** CI (unit tests without a database); Manual (2026-11-02), stdio round trips against both demo
  databases and recorded unattended Claude Code sessions, kept in the demos' `sessions/` folders.

## Acquisition and import

### WAV, EDF/EDF+ and CSV files
- **What:** `tools/ingest/morlet_import.py` imports files, one recording per channel, stored exactly on their
  sample grid, and builds the search envelopes.
- **Run:** [from files to a first search](import.md).
- **Scope:** PCM WAV (8–32-bit), EDF and continuous EDF+ (EDF+D rejected), CSV with a header row; see the guide
  for what is not supported.
- **Verified:** CI (reader tests: integer and full-scale WAV, EDF physical values and quantum, CSV quantum,
  rejected inputs); Manual, a Sleep-EDF night round-tripped bit for bit.

### miniSEED files (via ObsPy)
- **What:** the same importer reads miniSEED through ObsPy: one recording per contiguous segment, gaps kept.
- **Run:** [from files to a first search](import.md) with `python -m pip install obspy`.
- **Scope:** integer and float traces ObsPy reads; recording ids carry the segment start time.
- **Verified:** Manual (2026-11-02), the seismic agent demo imports four days of CX data this way; not in CI
  (ObsPy is not installed there).

## Analysis and visualization

### Grafana dashboards
- **What:** a provisioned dashboard on Grafana's built-in PostgreSQL data source: recordings, samples, search
  readiness, samples over time and a recording list that opens the viewer.
- **Run:** [`examples/grafana`](../examples/grafana/README.md).
- **Scope:** Grafana 11.2.2 (tested), read-only role, plain SQL; overview only, no waveform panels or
  annotations.
- **Verified:** Manual (2026-11-02) against the machine demo: data source health, every panel query through
  Grafana's API, and the link into the viewer.

### Python and Jupyter
- **What:** a Python client (`tools/client/morlet_client.py`, psycopg) and a notebook that imports, searches and
  plots recordings.
- **Run:** [`examples/notebooks`](../examples/notebooks/README.md).
- **Scope:** the notebook runs against a local Docker database with synthetic recordings; no third-party data.
- **Verified:** CI (the Recording preview workflow executes the notebook twice against PostgreSQL, with
  assertions on raw values and reconstructed chunk boundaries, when notebook files change).

### MorletDB viewer
- **What:** a local, read-only web page: waveform with zoom, context lanes, comparison with history and
  similarity search; opens at a recording from a link (`?collection=…&recording=…&start=…&end=…`).
- **Run:** [`tools/viewer`](../tools/viewer/README.md).
- **Verified:** CI (helper tests); Manual, links from Grafana.

## Archive exchange

### Apache Parquet import
- **What:** `tools/ingest/parquet_import.py` imports recordings from Parquet tables with a declared column
  mapping; the file importer builds on it.
- **Run:** [`tools/ingest`](../tools/ingest/README.md).
- **Scope:** import only; a documented export format is planned.
- **Verified:** CI (Parquet input contract tests).

## Logo and trademark notes

Checked on 2026-11-02 against each owner's published policy. The website lists all entries as plain text.

| Name | Logo on this site | Why |
|---|---|---|
| PostgreSQL | no | the logo needs permission; the name may be used to say software runs with PostgreSQL |
| pgvector | no | no logo policy published |
| Python | allowed, not used | the PSF allows unaltered logos to indicate compatibility, with ™ |
| Docker | no | logos need Docker's authorisation; the word mark may describe compatibility |
| Grafana | no | the logo needs permission for this use |
| Model Context Protocol | no | LF Projects requires written permission for logos on websites |
| Claude, Anthropic | no | marks only as Anthropic specifically permits; plain factual text, no ™ |
| Apache Parquet, Apache Arrow | allowed, not used | the ASF allows unmodified project logos on websites, with attribution |
| Jupyter | allowed, not used | LF Charities allows unaltered logos for non-commercial nominative use |
| DuckDB | no | logos may not be combined with others; plain text needs no permission |
| ObsPy | no | no logo policy published |

Trademark notices:
- Postgres, PostgreSQL and the Slonik Logo are trademarks or registered trademarks of the PostgreSQL Community
  Association of Canada.
- Docker is a trademark or registered trademark of Docker, Inc. in the United States and/or other countries.
- Python is a registered trademark of the Python Software Foundation.
- Apache, Apache Parquet, Parquet, Apache Arrow and Arrow are trademarks of The Apache Software Foundation.
- Jupyter is a registered trademark of LF Charities. Grafana is a trademark of Grafana Labs. Model Context
  Protocol is a trademark of LF Projects, LLC. Claude is a trademark of Anthropic, PBC.
- All other names are the property of their respective owners. MorletDB is not affiliated with, endorsed or
  sponsored by any of them.
