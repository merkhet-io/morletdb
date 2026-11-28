# PR rebase note

The original D1 artifacts and protocol are retained unchanged. They measured the initial implementation
on f141ccc (17 baseline tools, 22 with discovery/native evidence). They are historical development
measurements, **not counts for the rebased PR**.

The PR now targets 921bfe7. Main added explain_match and resolve_incident, independent context-history
limits, total wire-size accounting and interpretation revisions. The PR preserves those additions;
the evidence schemas include the new settings/provenance and revision fields. There are now 19 baseline
tools and 24 tools with this change. Native results remain experimental; no previously released native
contract is changed by this pre-publication reconciliation.

Post-rebase validation on PostgreSQL 17.11 / installed SQL 0.3.16: 151 MCP tests ran, 150 passed,
one existing explanation/interpretation-history test skipped because it requires SQL 0.3.17 or later.
All new discovery and native-contract tests passed, including both RLS/context enablement orders.
Repository prechecks, staged diff checks and archived D1 hash/byte/token recounts passed.
The current SQL 0.3.18 integration path remains for CI; this local run does not establish that coverage.

Reproduce the original exact counts independently of the current source:

```sh
python evaluation/agent_discovery/measure.py --recount evaluation/agent_discovery/results-d1-20261128-final.json
```

For a fresh rebase measurement, the same four scenario definitions apply, with the explicit baseline
921bfe7 and its 19 registrations. The script defaults to that baseline and records the full revision,
source hashes and actual tool counts. Such results must be saved separately and must not replace or
be presented as the original D1 run. No new context measurement was required for preparing this PR.
