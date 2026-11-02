# Machine demo: Claude investigates a bearing run to failure

A week of vibration snapshots from four bearings on one test shaft, recorded until one of them failed, in
MorletDB with the read-only [MCP](../../docs/mcp.md) server. An agent such as Claude can follow the history
the way a reliability engineer would: when did the failing bearing start to change, how did it change, and do
the other bearings show anything similar?

You need Docker (Compose v2) and an MCP client such as Claude Code or Claude Desktop. Nothing is installed on
your machine: the database, the importer and the MCP server run in containers. This is the machine twin of
the [seismic agent demo](../agent-demo/README.md).

## 1. Load the data (once, 10–20 minutes)

```sh
cd examples/machine-demo
docker compose build load
docker compose run --rm load
```

This starts PostgreSQL 17 with pg_morlet (the `0.3.2-alpha.1` release image, linux/amd64), downloads the IMS
bearing archive from NASA's prognostics data repository (about 1 GB; SHA-256 checked) and extracts test 2. It
imports every snapshot as four recordings, one per bearing, and builds the search envelopes: 3,936 recordings,
80.6 million samples and 35,424 search windows. Finally it creates the role `machine_reader` that the MCP
server connects as: it may read, nothing else. From already extracted files, add `--from-dir` (see `load.py`).

On an Apple M5 Max the import took under 5 minutes from local files, with the release image emulated.

**Data:** IMS Bearing Data, test 2. J. Lee, H. Qiu, G. Yu, J. Lin and Rexnord Technical Services (2007), IMS,
University of Cincinnati, NASA Prognostics Data Repository. The provider asks for this acknowledgement and
states no licence; the demo downloads from NASA and does not redistribute the data.

**What test 2 is:** four Rexnord ZA-2115 double-row bearings on one shaft at 2,000 rpm under a 6,000 lb radial
load, one housing accelerometer per bearing, a 1-second snapshot at 20 kHz every 10 minutes from 12 to 19
February 2004 (984 snapshots). According to the provider, bearing 1 ended the test with an outer-race failure.
Times are the rig clock from the file names (time zone not stated), shown as UTC. Values are as distributed, on
a 0.001 grid, unit not stated; some late values sit at ±5.0, which looks like the sensor's limit. The last two
snapshots are almost flat: the rig had stopped.

**On Apple Silicon or another ARM machine:** with a release that includes linux/arm64 (after 0.3.2-alpha.1),
set `MORLET_IMAGE` to it and `MORLET_PLATFORM=linux/arm64` before these commands, and the database runs
natively instead of emulated. On an Apple M5 Max that loaded this demo's test 2 in 176 s instead of 246 s and
ran `search_from_time` in 0.6 s instead of 1.2 s (machine demo; measured with a locally built arm64 image).

## 2. Connect Claude

**Claude Code:** start it in this folder. [`.mcp.json`](.mcp.json) declares the server; approve it when
Claude Code asks.

**Claude Desktop:** add to `claude_desktop_config.json`, with the absolute path of this folder:

```json
{"mcpServers": {"morletdb": {"command": "docker",
  "args": ["compose", "-f", "/path/to/morletdb/examples/machine-demo/compose.yaml",
           "--progress", "quiet", "run", "--rm", "-i", "-T", "mcp"]}}}
```

## 3. Ask

> Bearing 1 on this test rig failed at the end of the test with an outer-race fault. From the vibration
> recordings, find when it first started to change, describe how it changed, and check whether the other
> three bearings show anything similar. Cite the snapshots you base this on.

What the tools return, in the order an agent would use them:

1. `coverage` by bearing: 984 snapshots each, 1.02 s every 600 s, from 2004-02-12 10:32:39 to 2004-02-19
   06:22:40, no gaps beyond the schedule.
2. `trend` for bearing 1 (200 of the 984 snapshots, whole snapshots, default bands): the first sustained
   departure from the first day's level is at **2004-02-16 03:22**, in RMS and in the 2.5–5 kHz band at the
   same snapshot; kurtosis follows on 17 February. RMS rises from 0.074 to 0.4–0.7 before the rig stops.
3. `trend` for bearings 2–4: their first departures come later, from 17 February on, and smaller: their RMS
   ends 2–3 times its first-day level, against 6–10 times for bearing 1.
4. `search_from_time` from a late bearing-1 snapshot (19 February, 05:02): the closest snapshots are other
   bearing-1 snapshots from 17–19 February (distances 0.138–0.143). Restricted to each other bearing, the
   closest matches are weaker (0.17–0.26) and come only from the last hours on 19 February.

Published analyses of this test often place the onset of bearing 1's degradation around the 530th–550th
snapshot, early on 16 February (snapshot 533 is 03:12); `trend` finds it at 03:22 without being told.

**Real sessions.** Three unattended Claude Code runs (Opus 5.5, only the `morletdb` server) with the question
above, in [`sessions/`](sessions/). In [run 3](sessions/2026-11-02-bearing-onset-run3.md) (14 tool calls,
65 s) Claude placed the onset at the 16 February 03:22:39 snapshot from new 4–5 kHz energy, followed the jump
to impacts on 17 February, a partial recovery and the final broadband rise, and found bearing 4 carrying a
weak copy of bearing 1's signal while bearings 2 and 3 change only late and below 1 kHz. It said that band
powers cannot confirm the outer-race frequency (about 236 Hz). Runs 1 and 2 reached the same onset within one
snapshot; their first `trend` answers exceeded Claude Code's tool-output limit, which led to the compact
CSV table `trend` returns now.

## Without a language model

```sh
docker compose run --rm -e DATABASE_URL=postgresql://machine_reader:machine-demo-only@db:5432/morlet \
  load python tools/mcp/smoke_stdio.py public.bearings B1.20040219T050239 4096
```

## Limits

- **One test on one rig.** Four bearings on a shared shaft under one operating condition; the faults developed
  naturally. This shows how an agent can investigate a history, not how well anything predicts failure.
- **Change points are candidates.** `trend` flags the first sustained departure from an early baseline; on a
  machine with changing load or speed that would need operating context.
- **Not a diagnosis.** Similar vibration is a lead for an engineer, not a fault classification or a remaining
  life estimate.

## Notes

- **Ports and credentials:** the database listens on `127.0.0.1:54332`; `morlet` / `morlet-demo-only` is the
  owner, `machine_reader` / `machine-demo-only` the read-only role. These are local demo credentials.
- **Clean up:** `docker compose down -v` removes the containers, the database and the downloaded data.
