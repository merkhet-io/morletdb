# MorletDB for AI agents (MCP)

`tools/mcp/morlet_mcp.py` is a read-only [Model Context Protocol](https://modelcontextprotocol.io) server.
It lets an agent such as Claude look through recorded signals the way an engineer would:
- see what is recorded;
- characterise an interval;
- find where similar behaviour happened;
- read the original samples of every candidate;
- cite them.

## Tools

| Tool | What it does |
|---|---|
| `list_collections` | collections the role can read, with recording counts, samples and search paths (window sizes, probe search) |
| `list_recordings` | recordings with rate, channel, unit, metadata, length and UTC start and end where known; filter by metadata |
| `locate_time` | the recordings containing a UTC time, and the sample offset of that time |
| `describe_interval` | level, impulsiveness (crest factor, kurtosis) and spectrum (dominant frequency, centroid, band energies) of an interval |
| `search_similar` | the intervals most similar to a query interval, tolerant to small time shifts, with distances and UTC times |
| `read_interval` | original samples of an interval, or a min/max envelope for long intervals, with statistics |

**Safety:** every call runs in a read-only transaction with a statement timeout, as the database role in
`DATABASE_URL`, so privileges and [row-level security](access-control.md) decide what the agent sees.
Errors (an unknown collection, an interval of the wrong length) come back to the agent as tool errors with
the database's message, so it can correct itself.

## Set up

```sh
python -m pip install -r tools/mcp/requirements.txt
```

**Claude Code:**

```sh
claude mcp add morletdb --env DATABASE_URL=postgresql://reader@localhost/signals \
  -- python /path/to/morletdb/tools/mcp/morlet_mcp.py
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{"mcpServers": {"morletdb": {
  "command": "python",
  "args": ["/path/to/morletdb/tools/mcp/morlet_mcp.py"],
  "env": {"DATABASE_URL": "postgresql://reader@localhost/signals"}}}}
```

**A dedicated read-only role** (recommended):

```sql
CREATE ROLE signals_reader LOGIN PASSWORD '...';
GRANT USAGE ON SCHEMA public TO signals_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO signals_reader;   -- or only the collection's tables
```

No data yet? The [agent demo](../examples/agent-demo/README.md) sets up a database with four days of public
seismic recordings and this server in Docker, or [import WAV, EDF, miniSEED or CSV files](import.md).

## An investigation, end to end

These are real tool results on the development data of the technical report: CX seismic stations PB01,
PB02, PB06 and PSGCX, 1–14 March 2025, 128 recordings of 100 Hz data.

> **User:** There was an earthquake offshore Tarapacá on 14 March 2025 around 21:03 UTC. Find it at
> station PB01, describe it, and find where similar signals occur in the archive.

1. **Find the moment.** `locate_time(collection="public.r2", utc_time="2025-03-14T21:03:30Z",
   metadata_filter='{"station":"PB01"}')` returns recording `PB01.d14.0`, sample 7,581,000.
2. **Characterise it.** `describe_interval` on the 4,096-sample window from sample 7,579,648 (21:03:16 UTC)
   gives an RMS of 820 counts, a peak of 2,797 counts and a crest factor of 3.4.
3. **Search.** `search_similar` on that window returns the five closest intervals across all four stations
   and two weeks:

   | Recording | Start (UTC) | Distance | Catalogued earthquake (EMSC) |
   |---|---|---|---|
   | PB06.d08.0 | 2025-03-08 16:53:25 | 0.089 | M2.8 Antofagasta, 16:53:08 |
   | PB02.d14.0 | 2025-03-14 17:34:02 | 0.099 | M3.0 Potosí, 17:32:46 |
   | PB02.d11.0 | 2025-03-11 09:36:10 | 0.101 | M2.7 Tarapacá, 09:35:46 |
   | PB06.d04.0 | 2025-03-04 05:11:58 | 0.101 | none in the catalogue |
   | PB02.d05.0 | 2025-03-05 00:02:23 | 0.102 | none in the catalogue |

4. **Check and cite.** The agent reads or describes each hit and cites it as `public.r2`,
   `PB06.d08.0`, samples 6,080,512..6,084,608. These are the original stored samples, read back bit for bit.

Three of the five closest matches are other catalogued earthquakes. The two without a catalogue entry
deserve a look: they may be events too small or too distant to have been catalogued. The catalogue column
was added for this page from the public EMSC catalogue; the server does not know it.

On the same development data and its held-out counterpart, this kind of search returns windows containing
catalogued earthquakes about twice as often as zero-lag similarity
([E5](../evaluation/task_relevance/RESULTS.md), [E5-H](../evaluation/task_relevance/RESULTS_HELDOUT.md)).

## Check an installation

```sh
DATABASE_URL=... python tools/mcp/smoke_stdio.py public.r2 PB01.d14.0 7579648
```

This starts the server over stdio as an MCP client would, lists its tools and runs the steps above.

## Notes

- **Speed:** with SQL 0.3.2 (`morlet_search_similar`), search uses probe search where it is enabled
  (tens of milliseconds); older servers use exact search (about 1 s on 235,000 windows).
- **Window length:** search intervals must have the stored window length, 4,096 samples unless built
  otherwise; `list_collections` shows it.
- **Scope:** the server reads only. Importing, labelling and building search structures stay with the
  operator.
