# Machine demo: Claude investigates bearings run to failure

Two run-to-failure tests of four bearings on one test shaft, as vibration snapshots in MorletDB with the
read-only [MCP](../../docs/mcp.md) server. An agent such as Claude can follow the history the way a reliability
engineer would: when did the failing bearing start to change, how did it change, do the other bearings show
anything similar, and has the same fault signature appeared anywhere else?

You need Docker (Compose v2) and an MCP client such as Claude Code or Claude Desktop. Nothing is installed on
your machine: the database, the importer and the MCP server run in containers. This is the machine twin of
the [seismic agent demo](../agent-demo/README.md).

## 1. Load the data (once, 10–20 minutes)

```sh
cd examples/machine-demo
docker compose build load
docker compose run --rm load
```

This starts PostgreSQL 17 with pg_morlet (the `0.3.13-alpha.1` release image, native on amd64 and arm64), downloads the IMS
bearing archive from NASA's prognostics data repository (about 1 GB; SHA-256 checked) and extracts tests 2 and
3. It imports every test-2 snapshot and every sixth test-3 snapshot (one an hour) as four recordings each, one
per bearing, and builds the search envelopes: 8,152 recordings, 167 million samples and 73,368 search
windows. Finally it creates the role `machine_reader` that the MCP
server connects as: it may read, nothing else. From already extracted files, add `--from-dir` (see `load.py`).

On an Apple M5 Max, a fresh run with test 2 only (download, checks, extraction, import) took 10 minutes with
the amd64 image emulated (0.3.2-alpha.1); with a native arm64 build of the image the import itself took 176 s
instead of 246 s, and `search_from_time` 0.6 s instead of 1.2 s. Adding test 3 was not timed separately.

**Data:** IMS Bearing Data, tests 2 and 3. J. Lee, H. Qiu, G. Yu, J. Lin and Rexnord Technical Services (2007), IMS,
University of Cincinnati, NASA Prognostics Data Repository. The provider asks for this acknowledgement and
states no licence; the demo downloads from NASA and does not redistribute the data.

**What test 2 is:** four Rexnord ZA-2115 double-row bearings on one shaft at 2,000 rpm under a 6,000 lb radial
load, one housing accelerometer per bearing, a 1-second snapshot at 20 kHz every 10 minutes from 12 to 19
February 2004 (984 snapshots). According to the provider, bearing 1 ended the test with an outer-race failure.
**Test 3** is the same rig from 4 March to 18 April 2004 (6,324 snapshots as distributed; the provider's readme
lists 4,448 ending 4 April); according to the provider, bearing 3 ended it with an outer-race failure. Neither
failure label is in the database.
Times are the rig clock from the file names (time zone not stated), shown as UTC. Values are as distributed, on
a 0.001 grid, unit not stated; some late values sit at ±5.0, which looks like the sensor's limit. The last two
snapshots are almost flat: the rig had stopped.

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

## 4. Ask across tests

> Bearing 1 in test 2 failed with an outer-race fault. Characterise its fault signature, then find out whether
> the same signature appears on any bearing in test 3, when it first appears there, and whether you can
> confirm that it is the same kind of fault. Cite the snapshots you base this on.

This is where similarity search and the envelope spectrum come in:

1. `envelope_spectrum` on a late bearing-1 snapshot of test 2 (band 3–5 kHz, the computed defect frequencies
   of a ZA-2115 at 2,000 rpm: outer race 236.4 Hz, inner race 296.9 Hz, rolling element 139.9 Hz, cage 14.8 Hz):
   a line at **230.5 Hz**, about 30 dB above the spectrum's median against noise peaks of about 8 dB, with its
   second harmonic; nothing at the other defect frequencies. In the first snapshot of the test there is none.
2. `search_from_time` from that snapshot, restricted to test 3: every one of the top hits is bearing 3, or its
   neighbour bearing 4, on 16–18 April, the last days of test 3.
3. `envelope_spectrum` on those hits: the same line at 231.45 Hz with its harmonic, on bearing 3 from 16 April
   about 08:52 (+22 dB) to +35 dB on 17 April; bearing 4 carries it more weakly; bearings 1 and 2 do not.

As controls, a healthy bearing-1 snapshot and test 2's bearing 4 (which only carries the fault weakly) return
test-3 hits spread over March and April at clearly larger distances, and test 2's bearing 2 at its end (shaking
without a fault) finds test 3's bearing 2 at its end. That is a handful of queries, not an evaluation.

**A real session.** [One unattended Claude Code run](sessions/2026-11-02-fault-across-tests.md) (38 tool calls,
113 s) characterised the 230.5 Hz signature, found it with `search_from_time` only on bearings 3 and 4 in the
last two days of test 3, traced its first appearance to between 07:52 and 08:52 on 16 April, judged bearing 3
the more likely source, and said that an outer-race fault is consistent with the data but would need a
teardown to prove.

## Without a language model

```sh
docker compose run --rm -e DATABASE_URL=postgresql://machine_reader:machine-demo-only@db:5432/morlet \
  load python tools/mcp/smoke_stdio.py public.bearings B1.20040219T050239 4096
```

## Limits

- **Two tests on one rig.** Four bearings on a shared shaft under one operating condition; the faults developed
  naturally. Test 3 is loaded one snapshot an hour, so its onset is known to the hour. This shows how an agent can investigate a history, not how well anything predicts failure.
- **Change points are candidates.** `trend` flags the first sustained departure from an early baseline; on a
  machine with changing load or speed that would need operating context.
- **Not a diagnosis.** Similar vibration is a lead for an engineer, not a fault classification or a remaining
  life estimate.

## Notes

- **Ports and credentials:** the database listens on `127.0.0.1:54332`; `morlet` / `morlet-demo-only` is the
  owner, `machine_reader` / `machine-demo-only` the read-only role. These are local demo credentials.
- **Clean up:** `docker compose down -v` removes the containers, the database and the downloaded data.
