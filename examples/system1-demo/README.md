# System 1 demo: a fast model decides per cut, Claude explains

A milling machine wears down its cutting insert cut by cut. After every cut a fast decision model ("System 1")
decides whether to replace the insert. It never sees a waveform: it reads MorletDB's **decision state**, how this
cut's sensor values deviate from earlier cuts of the same setting made with a sharp insert. Every decision is
logged in MorletDB with the exact state the model saw. Later, Claude ("System 2") explains a decision over the
read-only [MCP server](../../docs/mcp.md) from the same recordings.

Why this split: a typed-decision model answers in about 0.25 s for a fraction of a cent but gives "a number, not a
reason"; an LLM can reason over the data but is too slow and costly for every cut. Both work from one memory, and
every decision points to the samples it was about.

**Evidence first:** in study [J1](../../evaluation/system1_j1/RESULTS.md), TypeSafe's Jev, zero-shot from this state,
ranked worn inserts nearly as well as the score built for the task (AUROC 0.948 against 0.966; time in use 0.909).
Its probabilities stay between 0.3 and 0.8, so the threshold (0.6 here) was read off J1's results on the same
data; a real deployment sets it on its own known cases.

## Run it

You need a PostgreSQL with pg_morlet (for example the release image, see [installation](../../docs/installation-and-queries.md)),
Python with `numpy scipy psycopg[binary]`, and the NASA milling data:

```sh
curl -LO "https://phm-datasets.s3.amazonaws.com/NASA/3.+Milling.zip"   # 14.7 MB; SHA-256 bdba8d52…c474
unzip "3.+Milling.zip" && unzip "3. Milling/mill.zip"                  # gives mill.mat
export DATABASE_URL=postgresql://morlet:...@localhost:5432/morlet      # a role that may write
python worker.py mill.mat --case 10 --reload                           # with Jev
python worker.py mill.mat --case 10 --decider score                    # without a key: the plain score
```

`--decider jev` reads a TypeSafe key from `~/.config/typesafe/jev.key` (mode 600; never printed or logged).
`--decider score` uses C1's conditioned score with a threshold of 1.0 (a deviation of 100%, not tuned) and needs no account.

One run with Jev (`jev-1.14.0`), insert set 10 (cast iron, 1.5 mm depth of cut, 0.25 mm/rev feed), on an Apple M5 Max:

| Cut | Flank wear VB (mm, not shown to the model) | Jev: p(replace) | Decision |
|---:|---:|---:|---|
| 1 | 0.00 | 0.42 | continue |
| 2 | 0.04 | 0.49 | continue |
| 3 | 0.08 | 0.31 | continue |
| 4 | 0.16 | 0.44 | continue |
| 5 | 0.25 | 0.60 | **replace_insert** (one cut early: the wear limit is 0.3 mm) |
| 6 | 0.36 | 0.65 | replace_insert |
| 7–10 | 0.43–0.70 | 0.65–0.69 | replace_insert |

Building the state from six channels took about 0.37 s per cut, a Jev call about 0.25 s (one outlier 1.2 s).
On other insert sets it is not this tidy: on set 11, J1's probabilities crossed 0.6 at a wear of 0.12 mm, ten
cuts too early.

## Ask Claude why

Connect the MCP server with a **read-only** role (the worker's role can write; the MCP server should not):

```json
{"mcpServers": {"morletdb": {"command": "python", "args": ["/path/to/morletdb/tools/mcp/morlet_mcp.py"],
  "env": {"DATABASE_URL": "postgresql://reader:...@localhost:5432/morlet"}}}}
```

> A fast decision model watches this milling machine and decides after every cut whether the cutting insert
> should be replaced; each decision is logged in MorletDB. Look at the decisions for the insert whose recordings
> start with c10. Explain the first replace_insert decision from the recordings: which sensors and values drove
> it, how this cut compares with earlier cuts of the same setting and with the cuts just before it, and whether
> the decision looks justified or premature. Cite the recordings you base this on.

**A real session.** [One unattended Claude Code run](sessions/2026-11-08-explain-first-replace.md) (Opus 5.5,
26 tool calls, 88 s) found the first `replace_insert` at cut 5, read the state the model saw, and noticed that its
largest deviation, spindle-vibration kurtosis 38× the history, came from a half-second transient compared with
only three earlier cuts, while a similar spike at cut 2 had not triggered anything. It traced the real wear sign to
spindle-motor current rising every cut (+43% from cut 1 to cut 5) and judged the decision justified by analogy with
an earlier insert of the same setting. That analogy rests on a label meaning "no longer sharp" (VB > 0.2 mm), not
the 0.3 mm limit: by the measured wear (0.25 mm) the decision was one cut early. Claude said its analogy was not
ground truth, and suggested a minimum history size for decision states, which is now an open point (#328).

The tools an agent uses: `decisions` (what was decided, by which model, with which probability and threshold, on
which recordings), `decision_state` (how the cut deviates from its history), `describe_interval` and
`read_interval` (the samples themselves).

## Limits

- One machine, one task, 64 cuts in J1; the demo replays recorded cuts, it does not control a machine.
- The model is a cloud service in early access. Many plants allow no cloud calls from the shop floor; the decision
  state is model-neutral, so an on-premises model can take Jev's place.
- MorletDB never sits in a control or safety loop. Decisions here are per part, with seconds to spare.

**Data:** NASA PCoE "3. Milling" (A. Agogino and K. Goebel, BEST lab, UC Berkeley; Goebel 1996), NASA Prognostics
Data Repository. Downloaded from NASA, not redistributed.
