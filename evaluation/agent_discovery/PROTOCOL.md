# Agent discovery D1 — development protocol

Frozen before measurement on 2026-11-28. This is a deterministic context-size and scripted-call
measurement, not an LLM task-success study or a production latency benchmark. No held-out data.
Do not change scenarios after viewing results; record deviations separately.

## Questions

How much text does eager schema injection cost after adding discovery and native evidence tools?
How much context does targeted retrieval require when the caller can defer tool-schema loading?
Do native requests pass schema validation and preserve evidence semantics on synthetic data?

## Fixed scenarios

Serialize JSON with sorted keys, UTF-8, compact separators and no non-finite numbers. Count UTF-8
bytes and tokens using tiktoken 0.12.0, o200k_base, with no special tokens. Record tokenizer version,
encoding, schema hashes and the exact serialized inputs in the result artifact. These counts omit
provider-specific message envelopes and hidden reasoning. Include initialization instructions in
both eager cases, but not in selective CLI cases where no MCP session initialization occurs.

1. **Legacy eager:** all 17 pre-discovery MCP tool definitions, with original initialization
   instructions from the baseline Git revision. Exclude list_operations, describe_operation,
   describe_collection, investigate_interval_v2 and read_interval_v2.
2. **Current eager:** all current MCP tool definitions and initialization instructions. This exposes
   the overhead for clients that always load every tool; do not claim savings for such clients.
3. **Known-operation selective CLI:** explain investigate_interval_v2 at /input_schema and /semantics.
   Count both tool/CLI request descriptions and both responses; two discovery invocations.
4. **Unknown-operation selective CLI:** list_operations, then the two explanations in scenario 3.
   Count requests and responses; three discovery invocations. No claim that all real agents choose
   the correct operation from this index without further calls.

Record elapsed discovery time as a single development observation, not a latency distribution.
Baseline tool definitions are taken from the unchanged legacy registrations in the same environment;
record the Git baseline and working-source hashes so this is not confused with a released build.

## Behavioral evidence

Use the disposable PostgreSQL MCP test fixtures (synthetic waveforms). Test native input validation,
equivalence to existing investigation search/statistics/context results, comma-containing context keys
and labels, explicit unknown/unsearched coverage, non-owner access, both RLS/context enablement orders,
budgets, and errors that cannot masquerade as empty matches. Test read samples versus summaries,
exact bucket boundaries for unequal partitions, signed zero, and consistent snapshots. All successful
new tool results must validate against their advertised schemas over the MCP transport.

Report test counts and skips separately. Scripted requests do not measure agent reasoning, retries,
diagnostic quality, or end-to-end token savings. No percentage savings claim beyond the measured text
for these exact discovery scenarios.
