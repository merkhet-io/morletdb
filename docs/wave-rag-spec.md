# Spec: evidence-grounded waveform investigation ("wave-RAG")

Status: **proposed specification**, not implemented. Date: 2026-10-25.

Goal: let an engineer select a waveform interval and get an answer that is
**grounded only in retrieved, citeable evidence**. That evidence is similar
historical intervals plus what is known about them. Every statement cites a
recording interval or an annotation, and the assistant says so when the
evidence is insufficient.

## Naming and claim rule

"Wave-RAG" describes the **application pattern** this spec enables. It is not a
current product claim. Until the [promotion gate](#promotion-gate) is met:

- Use "retrieves citeable waveform evidence" and "RAG-ready evidence API".
- Do not put "wave-RAG database" in the README headline or the repository
  description.
- The assistant summarizes retrieved evidence. It never diagnoses, classifies
  event types or origins, predicts failures, or issues alerts. The existing
  boundaries in [SUPPORT.md](../SUPPORT.md) and the seismic and CNC docs apply.

## Architecture

```text
query interval ──► exact retrieval (pg_morlet) ──► ranked matches
                                                      │
                     annotations overlapping each match (pg_morlet)
                                                      ▼
                                  evidence bundles (bounded, versioned)
                                                      ▼
            grounded assistant (example app) ──► answer with citations
                                                      ▼
                     UI: each citation opens the interval / annotation
```

MorletDB owns storage, retrieval, annotations and evidence bundles. Generation
lives in an example application, never inside the database.

## M1 — Interval annotations (pg_morlet)

Text knowledge attached to recording intervals: analyst notes, reviewed
labels, maintenance or test reports, catalog references.

Logical record:

- annotation id
- recording id
- half-open sample interval, or NULL for the whole recording
- kind: `note` | `label` | `event_reference` | `report`
- body text, up to 8 KB
- author or source
- source URI
- `review_state`: `draft` | `reviewed` | `rejected`
- `valid_from`, `created_at`
- `supersedes` (the annotation id it replaces)

Rules:

- **Append-only.** Corrections create a superseding record, so evidence cited
  in an earlier answer stays reproducible.
- **Interval check at insert.** The interval must lie inside the committed
  samples of the recording.
- **Permissions.** Annotations follow the parent recording's privileges and
  row-level security. A reader who cannot see a recording cannot see its
  annotations.
- **Retrieval stays separate.** Annotations never affect feature ranking.
  Retrieval and evidence remain independent: the
  detection / retrieval / reference separation from the seismic example.
- **Proposed SQL:**
  - `morlet_create_annotation_table(collection)`
  - `morlet_annotate(...)`
  - `morlet_annotations_for_interval(collection, recording, start, end)`,
    which returns overlapping, non-superseded annotations.
- **Installation.** This adds SQL objects, so the existing fresh-install rule
  applies (no upgrade script in the alpha).

Tests: interval bounds, superseding, RLS isolation, overlap semantics at the
half-open edges.

## M2 — Evidence bundles

`morlet_search_evidence(collection, recording, start, end, filter, top_k,
max_candidates, max_annotations_per_match, max_annotation_bytes)` returns, per
match:

- rank and distance
- interval and UTC span
- compatibility fields: representation version, rate, channel, unit
- acquisition metadata
- overlapping annotations with review state

Each match and annotation gets a stable evidence id, for example
`E3` → `(recording, start, end)` or `A7` → `annotation id`. Raw samples are not
in the bundle; they are read on demand with `morlet_read_interval`.

Budgets are explicit, and exceeding one returns an error rather than a
truncated bundle. The bundle records the query interval, the filter, the
candidate count and the representation version, so an answer can be replayed
exactly.

## M3 — Grounded assistant (example)

Built as an example application, reusing the seismic UI patterns. It calls
Claude through the Anthropic Python SDK.

- **Model:** default `claude-opus-5-5` with adaptive thinking and an explicit
  `effort` level. Effort and any cheaper model are chosen by measurement on the
  evaluation set, not assumed.
- **Input to the model:** the evidence bundles rendered as plain-text documents
  with native citations enabled, one document per match or annotation, titled
  by evidence id. Also deterministic descriptors of the query and each match
  (duration, dominant band, RMS, distance, review state), computed by
  MorletDB/Rust and never inferred by the model. Raw sample arrays are not sent.
- **Behaviour:**
  - Answer only from the provided documents, and cite each claim.
  - Distinguish reviewed annotations from drafts and from catalog-derived
    references.
  - Reply "no comparable evidence" when there are no matches, all matches are
    beyond a configured distance, or no reviewed annotation exists.
  - Never state a diagnosis, cause, event type or prediction.
- **Post-checks in code, before display:**
  - Every citation must resolve to a provided evidence id.
  - Sentences without a citation are flagged in the UI.
  - Refusals (`stop_reason: "refusal"`) are handled and shown as such.
- **Constraint:** native citations cannot be combined with structured outputs,
  so structured answer fields, if needed, come from a separate call.
- **Data handling:** bundles and annotations are sent to the Anthropic API.
  Partner or restricted data needs explicit approval and a retention review
  first. A local-model path is out of scope for this spec and must be decided
  per dataset.

## M4 — Evaluation

Reuse the reviewed labels from the
[CNC spindle pilot](cnc-spindle-experiment.md) as the primary set. Seismic
catalog associations serve as a weak proxy smoke test only.

- **Retrieval.** Per-query precision@k, nDCG and first-relevant rank against
  reviewed labels. Compare with statistics and FFT baselines, using the same
  filters for all methods. Bootstrap uncertainty resamples whole tools or runs
  as groups, because queries and overlapping windows from one tool are not
  independent.
- **Citation precision.** The share of citations whose evidence supports the
  cited statement, as judged by reviewers blind to the retrieval method.
- **Unsupported-claim rate.** The share of statements with no supporting cited
  evidence.
- **Abstention.** Correct "no comparable evidence" on held-out negatives, and
  no abstention when reviewed relevant evidence exists.
- **Value of retrieval.** The same questions answered with retrieval, with
  baseline retrieval, and with no retrieval at all. This shows whether grounding
  helps or merely decorates.
- **Reporting.** Per-query tables, model id, effort, prompt version and token
  costs, all recorded with the report provenance.

## Promotion gate

Fix thresholds before running and agree on them in review. Proposed:

- Retrieval shows a consistent per-query advantage over the simple baselines on
  reviewed labels, with grouped-bootstrap intervals that exclude no difference.
- Citation precision ≥ 0.95 and unsupported-claim rate ≤ 5% on the pilot.
- Abstention is correct on ≥ 90% of negatives.

Only then may docs say "wave-RAG" as a capability, scoped to the evaluated
domain. If retrieval does not beat the baselines, the assistant can still ship
as an example, described as summarizing baseline-equivalent retrieval.

## Milestones (rough estimates)

| Milestone | Scope | Estimate |
|---|---|---|
| M1 | Annotation SQL, tests, docs | 1–2 weeks |
| M2 | Evidence-bundle function, budgets, tests | 3–5 days |
| M3 | Grounded assistant example, post-checks, UI | about 1 week |
| M4 | Evaluation harness plus reviewed pilot | 2–4 weeks plus reviewer time |

M1–M3 can proceed with synthetic and seismic data. M4 depends on the CNC
prerequisites: dataset, channels, labels, reviewers and data approval.

## Out of scope

- Text-to-waveform or cross-modal queries ("find chatter")
- Learned embeddings or fine-tuning
- Approximate or vector indexes
- Multichannel synchronized retrieval
- Autonomous agents acting on equipment
- Alerts, and any diagnostic or origin classification

## Open decisions

1. Annotation sources for the pilot, and who reviews them.
2. Whether annotations ship in `pg_morlet` (proposed) or start as an
   example-level table.
3. Data-handling approval for sending partner annotations to the API.
4. Final gate thresholds.
