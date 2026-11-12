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
| `coverage` | an overview per station, sensor or asset: recordings, samples, first and last time, the recording schedule (continuous or snapshots) and the largest gaps; with SQL 0.3.6 also how much of it search can find (`search_states_s`) |
| `list_recordings` | recordings with length and UTC start and end where known; rate, channel and unit once when shared; metadata on request; filter by metadata |
| `latest` | the current value of every sensor: each recording's last value, or its last values with statistics, or the newest chunk's statistics, with the end time and how long ago that was (SQL 0.3.6) |
| `trend` | how level, impulsiveness and band power change over days or weeks, one point per snapshot or per time step, with candidate change points against an early baseline; with `segment_context` (e.g. `{"phase": "cut"}`) only inside matching context segments, so a machining trend is not the idle lead-in (#385) |
| `locate_time` | the recordings containing a UTC time, and the sample offset of that time; each match says how its time is derived (`time_basis`: start + rate) and, when the import measured how the source's timestamps departed from it, the uncertainty in seconds and samples (#393) |
| `describe_interval` | level, impulsiveness (crest factor, kurtosis) and spectrum (dominant frequency, centroid, energy share and power per band) of an interval; your own bands in Hz, and a minimum frequency so slow background motion does not dominate |
| `envelope_spectrum` | the repetition frequencies of impacts in an interval (band-pass, amplitude envelope, spectrum): strongest lines, a noise-peak level, and the level at frequencies you name, such as bearing defect frequencies, and their harmonics |
| `compare_intervals` | an interval against the background around it (median over the surrounding minutes) or an explicit reference (a quiet stretch, normal operation, an earlier event): power ratio overall and per band in dB, and how the energy above the reference splits across bands; warns if an explicit reference contains an onset |
| `find_onsets` | candidate onsets in an interval, where short-term energy rises above the preceding background (STA/LTA, optional band-pass); candidates to inspect, not evaluated detections |
| `search_from_time` | the intervals most similar to what happens at a UTC time: finds the onset near that time, frames the window and searches (with `shifts`); the easiest way to search when you know when something happened |
| `search_similar` | the intervals most similar to a query interval, tolerant to small time shifts, with distances and UTC times; optionally also searches with the window moved a little (`shifts`) and merges the results. With SQL 0.3.3: `before_only` (only what happened before the query) and a time range, and each hit's rms, min, max and an 8-part sketch inline |
| `decision_state` | for the context segment (a cut, a cycle, a part) containing a sample: its context, each value's deviation from the same-context history (median, relative deviation, quantile), a summary score, and the nearest earlier cases with their labels; the state a fast decision model ("System 1") decides from, and that an agent can cite afterwards (#328). A history smaller than `reliable_history` (10) is flagged, because one transient can then dominate. Optionally also the drift from the asset's own reference segments (`baseline_recordings`, e.g. a tool's first cuts after a change) and the nearest labelled cases as worked examples (`examples`): the whole history is searched, `examples_note` says when no labelled case exists, and `example_match_keys` widens the search, e.g. to other tools of the same process. From Python, `decision.decision_states` returns the states of all channels of a cut in one call (one history query instead of three SQL calls per channel; #403). Needs a collection with context |
| `decisions` | decisions a fast model logged on recordings (labels `s1:<answer>`): answer, model, question, probability, threshold, time, the recordings and sample ranges, and on request the exact state the model saw; the starting point for explaining a decision |
| `read_interval` | original samples of an interval, or a min/max envelope for long intervals, with statistics |

**Safety:** the tools are typed signal operations; there is no tool that runs arbitrary SQL. Every call runs in a read-only transaction with a statement timeout, as the database role in
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
seismic recordings and this server in Docker, the [machine demo](../examples/machine-demo/README.md) a bearing
run to failure, or [import WAV, EDF, miniSEED or CSV files](import.md).

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

## Weak signals: compare with a reference

A weak signal's spectrum is mostly background. In the demo data, a magnitude 3.0 earthquake in Potosí,
Bolivia, 323 km from station PB02, is only about three times the background level there, and
`describe_interval` reports a dominant frequency of 0.2 Hz: ocean microseism, present all the time.
Two calls give the event's own frequency content:

- `describe_interval(..., min_frequency_hz=1)` reports a dominant frequency of 11.3 Hz.
- `compare_intervals` against the background (by default the median over windows in the 10 minutes before and
  after) gives +8.1 dB overall. It puts 13%, 14%, 31%, 38% and 3% of the energy above the background into the
  bands 0–3, 3–6, 6–12.5, 12.5–25 and 25–50 Hz, close to the split of the local earthquakes 100 km away.
  Against a single quiet window 13 minutes earlier the result is similar (+9.1 dB).

A single reference window can mislead: on 14 March, a night window at PSGCX came out 47 dB louder than the
afternoon because it held an uncatalogued regional earthquake. `compare_intervals` now warns when an explicit
reference contains an onset, and its default background is a median, which one event barely moves.

`find_onsets` with a 2 Hz high-pass finds the strong arrival at 17:34:06 UTC, about 80 s after the origin time.

## Reading distances

`search_similar` returns `1 - ` the best correlation of two amplitude envelopes over the allowed time shifts.
The server tells agents what that does and does not mean:

- **It compares envelope shape.** Every window is normalised, so level and offset do not count, and the
  frequency content under the envelope does not count either: the same burst on a 120 Hz and on a 40 Hz
  carrier is close. Use `describe_interval` or `envelope_spectrum` to check frequency content.
- **0 means the same envelope shape, not the same samples.** Raw samples are compared by reading them.
- **It is not a probability or a confidence**, and there is no universal cut-off: what is close for one kind
  of signal is not for another. Rank hits against each other and inspect them.
- **A constant window has no envelope.** Its distance to everything, itself included, is 1; `search_similar`
  adds a `query_note` instead of a meaningless ranking. A nearly constant window still has an envelope,
  because normalisation rescales any variation, but it may be mostly noise: check the query's and the hits'
  `rms` in the evidence.

Earlier versions of the server told agents that distances below about 0.3 are strong and above about 0.6 weak;
those numbers were never evaluated across signal types and were removed. The core test
`distance_semantics_for_agents` checks the four cases above.

## Not found versus not searched

Search sees only stored search windows. Older data can still be readable but out of search, compacted to
summaries, or gone ([memory levels](memory-design.md)); new data may not be indexed yet. So an agent that finds
no match must not conclude "this has never happened" unless everything was searched. Every `search_similar`
and `search_from_time` result therefore carries a `scope`:

- `recordings` and `searched_windows`: the recordings the search could match (same rate, channel and unit,
  the metadata filter, the time range or `before_only`) and the stored windows of the query's length there;
- with SQL 0.3.6, `seconds` per state of [`morlet_coverage`](search-contract.md#not-found-versus-not-searched-sql-036),
  `complete`, the largest `not_searched_periods` with their state and UTC times, and the event-library entries
  in range (`event_library`: exempt episodes at full level or out of search);
- a `note` in plain words. A not-indexed range shorter than one window (usually a recording's last samples)
  is counted as `shorter_than_window` and does not make the scope incomplete.

Computing the scope costs about 1.3 µs per stored window of the matching recordings (2 million windows: 2.6 s on
an Apple M-series laptop), comparable to an exact search. It runs under its own time limit
(`MORLET_MCP_SCOPE_TIMEOUT_MS`, default 5000); past it the scope says it is unknown (`complete: null`) and the agent
is told to treat a missing match as unverified, never as complete.

The server instructions tell agents to state the scope with every negative result, for example "no similar
event in the searched 2024-03 to 2026-11; 2021 to 2023 is summarized only and was not searched". Servers
before SQL 0.3.6 report the searched windows and say that they cannot list what was not searched.

## Check an installation

```sh
DATABASE_URL=... python tools/mcp/smoke_stdio.py public.r2 PB01.d14.0 7579648
```

This starts the server over stdio as an MCP client would, lists its tools and runs the steps above.

## Notes

- **Speed:** with SQL 0.3.2 (`morlet_search_similar`), search uses probe search where it is enabled
  (tens of milliseconds); older servers use exact search (about 1 s on 235,000 windows).
- **Where the window starts matters:** moving the query window by a few seconds changes which hits rank first,
  and not in a way that is easy to reason about. On the demo data (203 queries from 52 catalogued earthquakes,
  share of the top 10 overlapping a catalogued event), starting 15 s before the onset gave 0.278, 6 s after
  0.280, 2 s before 0.217, and the 41 s with most energy 0.194. Merging searches with the window moved by up
  to 10 s (`shifts=2`, 2 s before the onset) gave 0.318. `search_from_time` does exactly that, so agents need
  not choose. These numbers come from the development data the choice was made on; they are not a held-out
  result.
- **Window length:** search intervals must have the stored window length, 4,096 samples unless built
  otherwise; `list_collections` shows it.
- **Scope:** the server reads only. Importing, labelling and building search structures stay with the
  operator.
