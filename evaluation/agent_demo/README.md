# Agent demo: "Why this part?" on the Bosch CNC milling data

A flagged part is investigated step by step with MorletDB's existing, read-only [MCP](../../docs/mcp.md) tools, and
every statement names its evidence: collection, recording and sample range, which read back bit for bit. The
agent is a script (`demo_agent.py`) that calls the MCP server's tools in a fixed order with fixed rules, so it
is reproducible and needs no language model; the same server can be given to Claude or any MCP client to ask
the questions interactively (below).

**Development data, not study results.** Only machine **M01** is used. M02 and M03 are reserved as
confirmation data for a planned frozen study and were not opened. M01 was used before (C2, F2 development).
The figures below describe how this scripted agent behaved on 34 flagged runs of one machine; they are not
evidence for or against any hypothesis.

The questions, rules and metrics were fixed in [QUESTIONS.md](QUESTIONS.md) and committed before the database
was loaded; its "Deviations" section lists what was added afterwards.

**Data:** Bosch CNC milling vibration data, three axes at 2 kHz, runs labelled good or bad. Tnani, Feil and
Diepold, *Procedia CIRP* 107 (2022), [doi:10.1016/j.procir.2022.04.022](https://doi.org/10.1016/j.procir.2022.04.022),
licence CC BY 4.0. Local copy at dataset commit `d60581d6a3ab6015dcc5488c3d76112bb8e1bcb1`; every file is
checked against its `SHA256SUMS`.

## What the agent does, per flagged run

| Step | Tools | Question |
|---|---|---|
| 1 | `latest`, `list_recordings`, `coverage` | Which machine, operation and period; what data exists for that operation |
| 2 | `decision_state` (per axis) | How the whole run deviates from the good history of the same operation and axis |
| 3 | `describe_interval`, `compare_intervals`, `envelope_spectrum` | Which 2-s window deviates most; level or shape; which band |
| 4 | `find_onsets` | Where it starts; onsets a good run does not have |
| 5 | `trend` | Where the run sits among the operation's runs |
| 6 | `search_similar`, `search_from_time`, `decisions` | Seen before? Were those runs bad, were they flagged? |
| 7 | `read_interval` | The raw samples behind the answer |

The answer is plain English: what is different, where, since when, seen before and what happened then, a
recommendation, evidence links. Example, abridged from
[`results/answers/M01_Feb_2019_OP10_000-92ff92.md`](results/answers/M01_Feb_2019_OP10_000-92ff92.md):

> - Whole run against the 29 good OP10 runs of the same axis: the largest summary deviation is on axis z, std
>   +153% from the history median.
> - The most deviating window of this run is on axis z, samples 0..4096 (0.0 s to 2.0 s into the run): its
>   vibration power is +18.2 dB against the median of the same window in 5 good OP10 runs. Across the flagged
>   run and 5 good OP10 runs, this is the largest window deviation of all (420 windows scored).
>   `public.bosch_m01 / M01_Feb_2019_OP10_000-92ff92-z / 0..4096`
> - Against the same window of a good run: +18.2 dB overall, mostly in 125-250 Hz (92% of the excess power).
> - Similar windows elsewhere in OP10 (own run excluded; 4 of 50 hits were from this run itself, #384): 5 of the
>   nearest 5 and 8 of the nearest 10 are from bad runs; by chance 8% of the windows are.
> - Recommendation: Inspect this part at 0.0 s..2.0 s on axis z; the deviation is clear. Similar windows were
>   mostly in bad runs before.

All 34 answers are in [`results/answers.md`](results/answers.md); every tool call with its arguments, result
and time is in `results/transcripts/<run>.json.gz`.

## Results (34 flagged M01 runs, 2026-11-11)

Database: `ghcr.io/merkhet-io/morletdb:0.3.10-alpha.1` (pg_morlet SQL 0.3.8; the locally built `morletdb:main`
was three days behind main, so the release was used). 519 runs, 1,557 recordings, 168 million samples, 80,187
search windows; loading took 162 s ([`results/load.json`](results/load.json)). Context, segments, values,
labels and logged decisions all worked in this version.

| Metric | Value | Chance |
|---|---|---|
| Pool-wide top window lies in the flagged run (flagged run + 5 good runs of its operation) | 21 / 34 (0.62) | 0.14 |
| `search_similar`: precision@5 of hits being from bad runs | 0.41 | 0.06 |
| `search_similar`: precision@10 | 0.28 | 0.06 |
| `search_from_time`: precision@5 / @10 | 0.28 / 0.15 | 0.06 |
| Statements with an evidence link | 515 / 549 (0.94) | |
| Wall time per investigation | median 9.1 s, max 18.7 s (434 tool calls median) | |
| Tool calls that failed | 0 of 15,300 | |
| Evidence windows bit-identical to the HDF5 source | 34 / 34 | |

Chance for precision is the share of the operation's other stored windows that belong to bad runs (mean over
runs). Details in [`results/metrics.json`](results/metrics.json).

**Read these numbers with the following, which the demo also found:**

- **The top window is almost always the first one.** In 31 of 34 runs the most deviating window is samples
  0..4096, nearly always on axis z and +15 dB (median) louder than the good runs there. The good runs' first
  2 s are mostly a quiet lead-in with isolated spikes (reference kurtosis 4.5 to 79, median 34.7); the bad runs' first 2 s are not.
  Whether that is the fault or a difference in when recording started is not something these tools can tell:
  windows are compared by position, and runs are not aligned.
- **The thresholds are not selective.** Applied the same way to the pools' 65 good runs (leave-one-out), the
  "inspect" threshold (≥ 6 dB) fires on 43% of them and "check" (≥ 3 dB) on 82%, against 97% and 100% of the
  flagged runs. The "level or shape" rule called 33 of 34 runs "level and shape", mostly because a good lead-in
  window is far more impulsive than a cutting window. On its own the recommendation is not a detector; the
  pool comparison (step 3) is the more honest signal, and it points at a good run in 13 of 34 cases.
- **"Seen before" hits are mostly the same batch.** 59 of the 69 bad hits in the top 5 come from bad runs of the
  flagged run's own period (19 of the 34 bad runs are from February 2019). Search finds the sibling parts of
  a bad batch; it rarely links a bad run to one in another period. For operations with a single bad run,
  precision is 0 by construction.
- **Known gaps, worked around:** `search_similar` returns windows of the query's own recording (#384): on
  average 2.9 of 50 hits (up to 11) were from the flagged run and were dropped before scoring.
  `search_from_time` treats these recordings (< 2 min) as snapshots and leaves the own recording out, one hit
  per recording. `trend` measures each recording's first 4,096 samples (#385); here that happens to be the
  deviating window, so the trend rank says something, but by accident.
- The flag is the dataset's bad label, logged as a decision `s1:flagged` so that `decisions` has something to
  show; no model was run. Run labels are also in the metadata, which is what precision is scored against.
- Start times are **nominal** (period month + 10 min per run): the dataset has none. They only order runs so
  that `coverage`, `trend` and `latest` work.

## Reproduce

Needs Docker, the local Bosch data (`data/bosch/` with `COMMIT`, `SHA256SUMS` and `repo/`), a Python with numpy
and h5py, and one with numpy, psycopg and pyarrow (the importer), plus the MCP server's requirements
(`tools/mcp/requirements.txt`) for the agent. About 10 minutes in total on a laptop.

```sh
cd evaluation/agent_demo
docker compose up -d --wait                               # PostgreSQL 17 + pg_morlet on 127.0.0.1:54351
python extract_m01.py ../../data/bosch /tmp/m01           # M01 only, SHA-256 checked (h5py)
DATABASE_URL=postgresql://morlet:morlet-demo-only@127.0.0.1:54351/morlet \
  python load.py /tmp/m01 results/load.json               # about 3 min
DATABASE_URL=postgresql://agent_reader:agent-demo-only@127.0.0.1:54351/morlet \
  python -I demo_agent.py results                         # about 5 min; add run ids to investigate only those
python verify_evidence.py ../../data/bosch/repo results   # raw samples against the HDF5 files (h5py)
python metrics.py results
docker compose down -v                                    # stop and remove the database volume
```

Tool calls go through the MCP server's own `call_tool`, as the read-only role `agent_reader`. Rerunning gives
the same answers and metrics except wall times.

## Ask it yourself (Claude Code or any MCP client)

With the database loaded, point the client at the same server and role. For Claude Code, a `.mcp.json` in any
folder (use absolute paths and a Python with the MCP requirements):

```json
{"mcpServers": {"morletdb": {
  "command": "/path/to/python", "args": ["-I", "/path/to/morletdb/tools/mcp/morlet_mcp.py"],
  "env": {"DATABASE_URL": "postgresql://agent_reader:agent-demo-only@127.0.0.1:54351/morlet"}}}}
```

Then ask, for example: *"Run M01_Feb_2019_OP10_000-92ff92 in public.bosch_m01 was flagged. Why this part? Work
through the questions in evaluation/agent_demo/QUESTIONS.md and cite every claim as collection / recording /
sample range."* The scripted answers above are a reference to check its answers against.

## Files

- `QUESTIONS.md`: questions, rules and metrics, fixed before the run.
- `extract_m01.py`, `load.py`, `compose.yaml`: data and database.
- `demo_agent.py`: the scripted agent; `metrics.py`, `verify_evidence.py`: scoring.
- `results/`: answers, transcripts, summary, metrics, evidence check, load report.
