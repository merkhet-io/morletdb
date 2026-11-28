# Agent discovery D1 — development results

The [protocol](PROTOCOL.md) was written before measurement. This measures serialized discovery
context with tiktoken 0.12.0, `o200k_base`; it is **not** an LLM task-success or end-to-end token study.
All data in the behavioral tests is synthetic.

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
