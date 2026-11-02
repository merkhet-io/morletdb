# Agent demo: Claude investigates an earthquake archive

Four days of public seismic recordings in MorletDB and a read-only [MCP](../../docs/mcp.md) server, so an agent
such as Claude can look through them the way an analyst would: find a moment, characterise it, search the
archive for similar signals and check every hit against the original samples.

You need Docker (Compose v2) and an MCP client such as Claude Code or Claude Desktop. Nothing is installed
on your machine: the database, the importer and the MCP server run in containers.

## 1. Load the data (once, about 10 minutes)

```sh
cd examples/agent-demo
docker compose build load
docker compose run --rm load
```

This starts PostgreSQL 17 with pg_morlet (the `0.3.2-alpha.1` release image, linux/amd64) and fetches
four days, 11–14 March 2025, of four broadband stations in northern Chile (CX.PB01, PB02, PB06 and PSGCX,
vertical, 100 Hz, about 115 MB) from the GEOFON data service. It checks them against
[`manifest.json`](manifest.json), imports them and builds the search envelopes: 77 recordings (a recording
ends at every gap in the data), 135.6 million samples and 66,104 search windows. It finally creates the
role `agent_reader` that the MCP server connects as: it may read, nothing else.

On an Apple M5 Max, `docker compose run --rm load` took 6 min 48 s, download included (the release image
is amd64 and runs emulated there); building the tools image and pulling the release image add a few minutes
the first time.

Data: GFZ & CNRS-INSU (2006), *IPOC Seismic Network*, [doi:10.14470/PK615318](https://doi.org/10.14470/PK615318),
open access.

## 2. Connect Claude

**Claude Code:** start it in this folder. [`.mcp.json`](.mcp.json) declares the server; approve it when
Claude Code asks.

```sh
claude
```

**Claude Desktop:** add to `claude_desktop_config.json`, with the absolute path of this folder:

```json
{"mcpServers": {"morletdb": {
  "command": "docker",
  "args": ["compose", "-f", "/path/to/morletdb/examples/agent-demo/compose.yaml",
           "--progress", "quiet", "run", "--rm", "-i", "-T", "mcp"]}}}
```

## 3. Ask

> There was a magnitude 2.6 earthquake offshore Tarapacá on 14 March 2025 at about 21:03 UTC. Find it at
> station PB01, describe the signal, and find where similar signals occur in the archive. For each match,
> give the station and UTC time and check it against the original samples.

What the tools return, step by step (the same calls `replay.py` makes, without a language model):

1. `locate_time` at 21:03:30 UTC, station PB01: recording `CX.PB01..HHZ.20250312T010454`, sample 24,471,588.
2. `describe_interval` on the 4,096 samples (41 s) from 21:03:16 UTC: RMS 820 counts, peak 2,797 counts,
   crest factor 3.4, kurtosis 7.3.
3. `search_similar`, five closest intervals across the four stations and four days:

   | Station | Start (UTC) | Distance | Catalogued earthquake (EMSC) |
   |---|---|---|---|
   | PB02 | 2025-03-11 09:36:10 | 0.104 | M2.7 Tarapacá, 09:35:46 |
   | PB01 | 2025-03-14 01:06:26 | 0.106 | M3.0 Tarapacá, 01:06:10 |
   | PB02 | 2025-03-13 01:58:29 | 0.107 | none in the catalogue |
   | PB02 | 2025-03-11 11:50:18 | 0.115 | M3.5 Tarapacá, 11:49:53 |
   | PB01 | 2025-03-13 05:42:49 | 0.119 | M2.5 Tarapacá, 05:42:33 |

Four of the five closest matches are other catalogued earthquakes, each starting 16–25 s after the event's
origin time, which is about when its first waves reach the station. The fifth has no catalogue entry, and
an analyst would look at it next. The server knows nothing about earthquakes. The catalogue column was
added here from the public EMSC catalogue; to check hits against it, Claude would need a web search tool.

On the same stations over two weeks, and on held-out stations and a later year, this search returns
windows with catalogued earthquakes about twice as often as zero-lag similarity
([E5](../../evaluation/task_relevance/RESULTS.md), [E5-H](../../evaluation/task_relevance/RESULTS_HELDOUT.md)).
It finds leads for review. It is not an earthquake detector.

## Without a language model

```sh
docker compose run --rm -e DATABASE_URL=postgresql://agent_reader:agent-demo-only@db:5432/morlet \
  load python examples/agent-demo/replay.py
```

This starts the MCP server over stdio, makes the calls above and prints the results.

## Notes

- **Ports and credentials:** the database listens on `127.0.0.1:54331`; `morlet` / `morlet-demo-only` is
  the owner, `agent_reader` / `agent-demo-only` the read-only role. These are local demo credentials.
- **Your own data:** import WAV, EDF, miniSEED or CSV files with the [importer](../../docs/import.md) into
  the same database, `GRANT SELECT` to `agent_reader`, and ask about them.
- **Search speed:** the release image has no pgvector, so search is exact: about 0.35 s over the 66,104
  windows here on an Apple M5 Max, slower where Docker emulates amd64. With pgvector and probe search it takes tens of milliseconds on much larger archives.
- **Clean up:** `docker compose down -v` removes the containers, the database and the downloaded data.
