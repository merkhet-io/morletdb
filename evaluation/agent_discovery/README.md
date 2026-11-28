# Agent discovery D1 — development results

The [protocol](PROTOCOL.md) was written before measurement. This measures serialized discovery
context with tiktoken 0.12.0, `o200k_base`; it is **not** an LLM task-success or end-to-end token study.
All data in the behavioral tests is synthetic.

## Follow-up #643: 24-tool measurement

[Review D1 artifact](results-d1-20261128-643-review.json), measured from clean source commit
`072330042e1c26d2fbc2646133386c97e1f55a1e` (merged main b9359f7 plus #643). The baseline is 921bfe7's
19 unchanged registrations and original instructions; current registrations total 24. The four scenarios
remain those frozen in [PROTOCOL.md](PROTOCOL.md), with the baseline-count adjustment documented before
this rerun in [REBASE.md](REBASE.md). The original 17/22-tool artifacts remain unchanged.

| Scenario | Discovery invocations | UTF-8 bytes | Tokens |
|---|---:|---:|---:|
| Legacy eager: 19 tools | 0 | 37,766 | 8,512 |
| Current eager: 24 tools | 0 | 64,494 | 14,854 |
| Selective CLI, known operation | 2 | 3,289 | 810 |
| Selective CLI, unknown operation | 3 | 8,908 | 2,571 |

The [initial #643 artifact](results-d1-20261128-643.json) is retained unchanged. Its last scenario
counted 2,578 tokens. The review adds sensor-identity qualifications, changing the incident contract
hash in the index: the new hash tokenizes differently (2,571 tokens for the same 8,908 bytes).
This difference does not measure the added notes' size or a behavioral improvement.
New reports include `source_dirty`, sampled before output is written; if true, `source_commit`
identifies the base revision and `source_sha256` identifies the actual working files. An absent dirty
flag in older artifacts does not by itself establish a clean checkout.

The index now includes hashes covering the semantic notes for `explain_match` and `resolve_incident`.
These cases do **not** retrieve those notes: the frozen selective operation remains
`investigate_interval_v2`. Eager definitions contain schemas and descriptions, not the separate discovery
semantics. These are exact context counts, not measurements of an agent using the two newer tools.

Validation: 14 discovery tests passed with no skips on disposable PostgreSQL 17.11 / installed SQL 0.3.16;
three measurement tests passed, including clean/staged/unstaged/untracked provenance and corruption rejection under `python -O`. New and historical
artifacts passed recounts; source hashes matched the measured checkout, and repository prechecks passed.
Tool execution and SQL behavior are unchanged; the full MCP integration suite was not rerun for this
metadata-only follow-up. No migration is required.

```sh
python evaluation/agent_discovery/measure.py --recount evaluation/agent_discovery/results-d1-20261128-643-review.json
```

## Historical 17/22-tool measurement

**These counts precede the PR rebase.** The PR preserves newer main features and now has 24 tools;
see [the rebase note](REBASE.md) for changes and exact archived-count reproduction. Do not use the
22-tool counts below as current PR measurements.

| Scenario | Discovery invocations | UTF-8 bytes | Tokens |
|---|---:|---:|---:|
| Legacy eager: 17 tools and original instructions | 0 | 33,252 | 7,506 |
| Current eager: 22 tools and current instructions | 0 | 58,958 | 13,604 |
| Selective CLI, known operation: input schema and semantics | 2 | 3,086 | 758 |
| Selective CLI, unknown operation: index, input schema and semantics | 3 | 8,225 | 2,376 |

Eager injection costs **more**, because it now includes explicit evidence result schemas and discovery
tools. Selective retrieval loads substantially less text for this one request-construction task, but
requires a client that can defer schema loading or a shell agent using the CLI. It omits unrelated tools
and output schemas, so these scenarios do not provide equal total information. A consumer that needs
output or error schemas must retrieve them too. MCP clients that always inject all tools do not gain
this context reduction simply by connecting this server.

The selective cases count JSON request descriptions and responses, including contract hashes and local
schema definitions. Eager cases count tool definitions and initialization instructions. Neither counts
provider message framing, model reasoning, database execution requests/results or retries. Discovery
invocations are deterministic scripted calls, not observed agent behavior. No model chose an operation
or interpreted evidence in this measurement. There is no diagnostic-quality or real-agent savings claim.

## Artifacts and reproduction

- [First measurement](results-d1-20261128.json).
- [Final measurement](results-d1-20261128-final.json): same frozen scenarios and counts after adding CLI
  execution and output consistency checks. The first artifact is retained. These later changes did not
  change serialized schemas; there was no scenario tuning or protocol deviation.

Both artifacts contain the exact serialized contexts, their hashes, operation contract hashes, baseline
Git revision, working-source hashes, package versions and one development timing observation. The
baseline is the 17 unchanged legacy tool registrations in the same environment, plus initialization
instructions from `f141ccc5c2f675a5bbeddec7a0e12adcc732e0d3`. This is local working-tree evidence, not
a measurement of a published release. Timing is not a latency distribution.

```sh
python -m pip install -r tools/mcp/requirements.txt ./sdk/python
python -m pip install -r evaluation/agent_discovery/requirements.txt
python -m unittest discover -s evaluation/agent_discovery -p 'test_*.py'
python evaluation/agent_discovery/measure.py --output /tmp/discovery-new-run.json
```

The tokenizer downloads public encoding data on first use; `TIKTOKEN_CACHE_DIR` controls its cache.
The script refuses to overwrite results. `--output` and `--recount` are mutually exclusive.
For new runs, `--baseline` selects only the initialization instructions and tool names from that
revision; tool schemas and descriptions come from current registrations. The report records this
in `baseline_definition_source`. This is not a reconstruction of historical tool definitions;
compare unchanged registrations only, or recount archived contexts for historical measurements. Run from a checkout containing the baseline Git revision.

## Behavioral verification

The pre-rebase local run passed **115 tests with no skips** on PostgreSQL 17.11, pg_morlet SQL 0.3.15,
using the existing installed extension library (not rebuilt for this Python-only change). The 19 new
native-contract tests are included in that total. Repository prechecks and `git diff --check` also passed.

The MCP suite exercises registered tools and stdio transport, not just schema generation. Native-tool
tests verify input/output schemas, agreement with existing investigation evidence, comma-containing
context keys/labels, unknown and summarized-only coverage, result-size and deadline failures,
both RLS/context enablement orders, hidden recordings, signed zero, unequal summary bucket boundaries,
concurrent changes under a read-only repeatable snapshot, and allowlisted CLI execution.

Run with a disposable PostgreSQL database where the test role may create the extension, schemas and
roles (tests create and remove synthetic data):

```sh
MORLET_MCP_TEST_DATABASE_URL='host=localhost port=28817 dbname=postgres' \
  python -m unittest discover -s tools/mcp -p 'test_*.py'
```

Without the environment variable, database tests are skipped; that is not a passing integration run.
These tests establish the tested contracts, not agent diagnosis, production scale, or probe recall.
