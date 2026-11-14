# Fleet investigator: a plant graph and its vibration recordings in one PostgreSQL

**A fictional plant with real recordings.** The plant's context graph lives in [Apache AGE](https://age.apache.org)
(a graph extension) and its bearing vibration recordings in MorletDB (pg_morlet), both in one PostgreSQL 17
database. The graph follows the ISA-95 structure: equipment hierarchy, bearings with serial numbers and lots,
installs with time bounds, maintenance, spares, operating context. An investigation answers multi-hop questions
that alternate between graph traversal and waveform similarity search:

> Pump P-102's vibration changed. Have we seen this before, what was it, which other running assets share the risk
> (same lot), and what fixed it last time?

What being an extension makes possible, and what [CHECKS.md](CHECKS.md) measured of it:

| Claim | Status here |
|---|---|
| One SQL statement joining a Cypher traversal with the waveform search (LATERAL) | holds ([queries.py](queries.py) `SIMILAR_FAILURES`; check 2) |
| One transaction snapshot over graph and recordings (REPEATABLE READ) | holds, shown with concurrent changes to both (check 5) |
| Atomic write-back of a confirmed finding to both | holds: graph edge, MorletDB label and episode commit together or not at all (check 4) |
| One set of access rules (RLS) for both | **holds only with two measures in place**: in AGE 1.7.0, variable-length paths, `vertex_stats` and `graph_stats` bypass row-level security (fixed on AGE master by [apache/age#2561](https://github.com/apache/age/pull/2561), in no PG17 release yet) and are revoked from non-owner roles, and every new graph label must be secured before restricted roles can read it (check 3) |
| One backup, one connection | follows from one database; not tested separately here |

The organisation is fictional ([MODEL.md](MODEL.md)): Aldermere Water, its two works, sixteen pumps and blowers,
eight bearing lots, two suppliers and every work order are invented. The recordings and the three failures in the
graph are the NASA/IMS bearing run-to-failure data; the only real product named is the bearing named in the IMS
readme.

## Quick start

You need Docker (Compose v2) and the extracted IMS bearing archive (below). From this folder:

```sh
export MORLET_DATA=/path/to/ims          # holds 1st_test, 2nd_test and 4th_test/txt (directly or under runs/)
docker compose build                     # the database image (AGE built from source) and the tools image
docker compose run --rm load             # checks 1,564 files against manifest.json, loads everything
docker compose run --rm investigate      # the reference investigation, no language model
```

On an Apple M5 Max (native arm64, other work running on the machine), a fresh `docker compose run --rm load` took
119 s (a run from the host, outside compose, 153 s), after building the images:
6,256 recordings, 128 million samples, 56,304 search windows, 259 graph vertices and 555 edges. Running `load`
again checks the manifest, finds everything in place and changes nothing.

**With Claude.** Claude Code started in this folder reads [`.mcp.json`](.mcp.json): two read-only MCP servers,
`morletdb` (the general MorletDB server, [tools/mcp](../../tools/mcp)) and `graph`
([graph_mcp.py](graph_mcp.py): `plant_overview`, `isa95_path`, `installed_at`, `same_lot`, `failure_history`,
`spare_stock`, and `similar_failures`, which runs the one-statement join). Both connect as `fleet_reader`; to see what
a contractor sees, set its role in `compose.yaml` (`fleet_contractor` / `fleet-contractor-demo-only`).
[agent.py](agent.py) drives the same servers with the Anthropic API (model `claude-opus-5-5`) when
`ANTHROPIC_API_KEY` is set. **It has not been run yet**; the scripted trace is the reference.

**Without a model, through MCP:** `docker compose run --rm investigate python examples/fleet-investigator/replay.py`.

**Recording a confirmed finding** is a person's step, outside MCP:

```sh
docker compose run --rm -i -e DATABASE_URL=postgresql://fleet_engineer:fleet-engineer-demo-only@db:5432/morlet \
  investigate sh -c 'python examples/fleet-investigator/investigate.py --trace /tmp/t.json >/dev/null &&
                     python examples/fleet-investigator/confirm_finding.py --trace /tmp/t.json --confirmed-by PC-RELENG'
```

It shows the finding and writes it only after you type its id: in one transaction, a `RESEMBLES` edge from the
bearing to the earlier failure in the graph, and a `finding:resembles` label plus an exempt episode (kept by
compaction) on the query interval in MorletDB.

## The reference investigation

[investigate.py](investigate.py) answers the question with direct queries, recording each hop's query, parameters,
result and time ([reference/flagship_trace.json](reference/flagship_trace.json), 13 hops, about 10 s in queries on
the machine above). Its answer at the plant's present, 2004-04-17 12:00:

1. **What changed** (graph, then MorletDB): P-102's position `BRG-3`, bearing SN-0402-007 of LOT-0402, installed
   2004-03-04: RMS 0.145 against 0.068 on its first day (2.1×); the other three positions 1.2–1.5×.
2. **Seen before?** (one SQL statement: graph, search, graph): of the 50 windows most similar to its latest snapshot,
   recorded before this bearing was installed, 48 come from SN-0311-001, the bearing in P-102's position `BRG-1`
   on 17–19 February 2004 (best distance 0.356, rank 1). The next bearings appear at ranks 41 and 48: P-102's
   `BRG-4` and `BRG-3` in their last hours of February, on the same shaft, with no recorded failure. The test-1
   bearings that failed differently (inner race, roller) are not among the 50.
3. **What was it:** SN-0311-001 failed with an **outer race defect** (FL-2004-02-P102-BRG1). The IMS readme gives
   an outer race failure on bearing 3 at the end of test 3, which is the held-out end of P-102's current run: the
   answer agrees with it. That is one case, not an evaluation.
4. **Same lot:** LOT-0402 bearings run in P-102 `BRG-1/2/4` (monitored: 1.2–1.5× their first day), P-104 `BRG-2`,
   F-113 `BRG-1`, P-203 `BRG-4` and F-214 `BRG-3` (not monitored: no vibration evidence either way), and 2 are on
   the shelf at North works.
5. **Fixed last time:** WO-2004-0219-01, corrective: all four bearings replaced (overhaul), outer race failure
   found on position 1, back in service 2004-03-04, with LOT-0402 bearings.
6. **Spares:** North works 3 on hand (reorder point 2), 2 of them from LOT-0402; South works 3, none.

As `fleet_contractor` (Site A only) the same investigation reaches the same precedent, and sees only the Site A
siblings and stores. Similarity is evidence for a hypothesis, not a diagnosis; the trace says so with its answer.

## Data: terms and attribution

**IMS Bearing Data.** J. Lee, H. Qiu, G. Yu, J. Lin, and Rexnord Technical Services (2007). IMS, University of
Cincinnati. "Bearing Data Set", NASA Prognostics Data Repository, NASA Ames Research Center, Moffett Field, CA.
Generated by the NSF I/UCR Center for Intelligent Maintenance Systems with support from Rexnord Corp.
(the archive's *Readme Document for IMS Bearing Data.pdf*).

- **Terms**, checked 2026-11-14 on the [NASA PCoE data set repository page](https://www.nasa.gov/intelligent-systems-division/discovery-and-systems-health/pcoe/pcoe-data-set-repository/):
  publications using its data sets are requested to acknowledge the repository and the donors of the data; users
  employ the data at their own risk, and neither NASA nor the donors assume liability. Neither the page nor the
  readme names a licence. This example does not redistribute the data: it reads your copy and checks it.
- **The archive:** `4. Bearings.zip` from the NASA repository (`https://phm-datasets.s3.amazonaws.com/NASA/4.+Bearings.zip`,
  SHA-256 `21001ac266c465f5d345ec42d7b508c6a6328487fd9d4d7774422dd5ea10ad83`). It holds `IMS.7z`, which holds
  `1st_test.rar`, `2nd_test.rar` and `3rd_test.rar` (the last extracts to `4th_test/txt`). With bsdtar
  (libarchive): `bsdtar -xOf "4. Bearings.zip" "4. Bearings/IMS.7z" > IMS.7z && bsdtar -xf IMS.7z && for r in
  *_test.rar; do bsdtar -xf "$r"; done`. The [machine demo's loader](../machine-demo/load.py) automates the same.
- **Checked:** [manifest.json](manifest.json) holds the SHA-256 of each of the 1,564 files loaded; the loader
  refuses to load if any differs.
- **What the readme states**, used in the graph: test 1, inner race defect on bearing 3 and roller element defect
  on bearing 4; test 2, outer race failure on bearing 1; test 3, outer race failure on bearing 3. Test 1 recorded
  two accelerometers per bearing; this example loads the first of each (channels 1, 3, 5, 7). Test 3 is distributed
  with 6,324 files up to 18 April 2004, while the readme lists 4,448 ending 4 April. File names give the rig clock;
  its time zone is not stated, and times are shown as UTC. Values are on a 0.001 grid, unit not stated.

## Files

| File | What |
|---|---|
| [Dockerfile](Dockerfile) | database image: the MorletDB release image (unchanged, by digest) plus AGE 1.7.0 for PG17 built from the ASF source release (SHA-512 checked) |
| [tools.Dockerfile](tools.Dockerfile), [requirements.lock](requirements.lock) | tools image; every Python package pinned with hashes |
| [plant.py](plant.py), [MODEL.md](MODEL.md) | the fictional plant and its mapping to ISA-95 and to the IMS runs |
| [security.sql](security.sql) | the access rule for recordings and graph, and the gap check |
| [load.py](load.py), [manifest.json](manifest.json) | loader and the hashes it checks |
| [queries.py](queries.py) | every Cypher template and the one-statement join |
| [graph_mcp.py](graph_mcp.py) | read-only graph MCP server |
| [investigate.py](investigate.py), [reference/](reference/) | scripted reference investigation and its trace |
| [replay.py](replay.py) | the investigation through both MCP servers, no model |
| [agent.py](agent.py) | Anthropic API runner over both MCP servers (not run yet) |
| [confirm_finding.py](confirm_finding.py) | the person-confirmed, atomic write-back |
| [checks.py](checks.py), [CHECKS.md](CHECKS.md) | the five risk checks and what they showed |
| [tests/](tests/) | end-to-end tests on a disposable container |

## Tests

```sh
python -m pip install --require-hashes -r examples/fleet-investigator/requirements.lock
docker build -f examples/fleet-investigator/Dockerfile -t morletdb-fleet-db:local examples/fleet-investigator
MORLET_DATA=/path/to/ims python -m unittest discover -s examples/fleet-investigator/tests -v
```

They start one disposable database container, load it with the real data, and check: the loader's idempotency,
manifest refusal and bit-exact samples; every graph hop against plain SQL on the label tables; install resolution
(including the mid-history replacement and all 6,256 recordings); the flagship answer against the IMS readme's
outcomes and the committed trace; the same hops through MCP; the contractor's view in both enablement orders and the
AGE bypasses; the write-back's atomicity and the CLI's confirmation; one snapshot across both stores; and that no
container is left. Without `MORLET_DATA` or Docker they fail; they are never skipped.

## Limits

- **Fictional plant, real recordings, one real case.** Three IMS runs on one rig design under one operating
  condition, mapped onto two units. The flagship answer is one case whose outcome is known, not an evaluation of
  diagnosis, early warning or remaining life.
- **Similarity is evidence, not diagnosis.** The search compares envelope shapes on a stored window grid. Here the
  closest earlier windows came from a bearing that failed with an outer race defect; confirming the mode needs an
  inspection or, at least, an envelope spectrum at the defect frequencies (the `envelope_spectrum` tool).
- **Access rules hold as tested, with conditions.** See CHECKS.md, check 3: only fixed-length Cypher patterns are
  available to non-owner roles (variable-length paths are withheld until a PG17 release of AGE includes
  [apache/age#2561](https://github.com/apache/age/pull/2561)), every new label needs `fleet.secure_graph`, and an AGE update could restore the
  default grants (`fleet.graph_security_gaps` reports it). Enterprise-level properties (such as a lot's quantity)
  are visible to every site.
- **14 of 16 units are not monitored.** For them the graph is the only evidence; "no recordings" is reported as
  such, never as "no change".
- **Hourly snapshots.** Every 6th IMS file is loaded; onsets are known to the hour.
- **Local demo credentials.** The database listens on `127.0.0.1:54333`; `morlet` / `morlet-demo-only` is the owner,
  and the three roles' passwords are in [fleetdb.py](fleetdb.py). `docker compose down -v` removes everything.
