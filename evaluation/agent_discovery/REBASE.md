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

## Follow-up #643: frozen rerun plan

Rerun the same four scenarios on merged main b9359f7 plus the #643 discovery qualifications.
Use baseline 921bfe7 (19 registrations), current total 24, tiktoken 0.12.0 / o200k_base, and
save a new `results-d1-20261128-643.json`. The original protocol's 17-tool baseline is historical;
this 19-tool rerun follows the explicit rebase adjustment above. Do not change the selective
operation or paths after measurement. Record the measured source commit as well as source hashes.
The new notes affect contract hashes in the operation index; the frozen selective cases still
retrieve investigate_interval_v2, not the full semantics of the two newer tools.

### Recorded result

The [#643 rerun](results-d1-20261128-643.json) measured source commit
`50f86b5ea90c7805b1a059d861df3958f4eac01a` with baseline
`921bfe7982badf8ab0c9be5f0b6410b3d90c04d8`. Actual counts: 19 baseline and 24 current tools.
The four scenarios contain 8,512 / 14,854 / 810 / 2,578 tokens respectively; see
[the results table](README.md#follow-up-643-24-tool-measurement) for bytes and interpretation.
The artifact's source hashes match the measured source; all archived context hashes, byte counts
and token counts were recounted. This is a separate development rerun, not the original D1 run,
and makes no claim about task success or end-to-end token savings.

### Review follow-up: provenance and sensor identity

Before rerunning: retain the same four scenarios and baseline, add the missing sensor identity
qualifications, and save `results-d1-20261128-643-review.json` separately. New artifacts record
`source_dirty` alongside `source_commit`: true means HEAD identifies the base checkout, while
`source_sha256` identifies the actual working files. The flag includes staged, unstaged and
untracked changes and is sampled before writing the output. Older artifacts lack this field;
its absence alone does not establish a clean checkout. The #643 review rerun will use committed
sources and record `source_dirty: false`.
