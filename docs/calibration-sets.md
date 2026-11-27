# Diagnostic calibration sets (experimental, SQL 0.3.17; revisions and recording references from 0.3.18)

A calibration set is an explicitly selected, versioned set of labelled waveform intervals for diagnostic
adaptation. It is not sensor calibration, a classifier, or a probability calibration service. One immutable
approved revision can later supply retrieval or conventional ML; spectral materialization and exports are separate
work (#602–604). No MCP write tools are added.

## Contract

Enable context first, then `morlet_enable_calibration_sets(collection, approver_role)`, as the collection owner.
This creates `<collection>_calibration_sets` and, from SQL 0.3.18, `<collection>_calibration_refs`, which records
the recordings each revision was captured from. The role must already exist. Enabling again with the same authority
is a no-op; another authority is refused. Provision a dedicated approval role rather than granting it to agents.

Each `(set_id, revision)` stores its explicit members, an evidence manifest and its approval. Members live in one
bounded JSON array rather than independently editable rows: an approval cannot freeze half of a membership update.
A member has exactly `recording_id`, `start_sample`, `end_sample`, `label_id`. Intervals are zero-based and half-open.
An active, visible label must cover the entire interval. One interval cannot appear twice, even under different
labels. The approver attests to the chosen existing labels; this is not a new general label-review system (#217).

The database captures:

- Exact interval SHA-256 over little-endian IEEE-754 float64 bytes, including signed zero; no resampling.
- Recording sample rate, channel, unit and metadata; active overlapping context rows; acquisition-channel
  memberships when present; the current time-axis version. Missing acquisition identity is `unknown`, not an
  assertion of an independent physical specimen. Acquisition-level metadata is not included in this v1 contract.
- The full selected label row, including author, timestamp and retraction state.
- A `raw` representation specification, version 1. Derived feature/scaler profiles are not supported yet.
- From SQL 0.3.18, the interpretation revision of the member's recording and the revision its label was written
  under ([interpretation revisions](python-sdk.md), #600). `unversioned` means there is no history (or the label is
  older than it); `unknown` means the history exists but the capturing role cannot read it. Revisions captured by SQL
  0.3.17 are all `unversioned`. Information availability is `unknown`. Any non-NULL `decision_at` is refused;
  historical causal replay needs #158.

Bounds are 1–100 members, at most 1,000,000 samples/member and 4,000,000 samples/set. Raw chunks must be available in
the encoded collection layout; legacy array-only collections are refused by the raw-read contract. Metadata/context
size is not a byte budget: use database resource limits and `statement_timeout` for untrusted workloads.

The manifest format is `morlet-calibration-v1`. Members are sorted by PostgreSQL JSONB text order with C collation. Capture pins UTC and float precision. `manifest_text`
stores the exact PostgreSQL JSONB textual serialization used to compute `manifest_sha256` (SHA-256 of UTF-8 bytes).
Verify those stored bytes, rather than reserializing JSON in another language. This is a format-specific encoding,
not a claim of RFC 8785 canonical JSON. Approval/audit fields and set identity are outside the content digest, so two
revisions with the same evidence can share a digest. Collection names are included; renaming a collection/schema
requires a future migration and is not supported for enabled calibration sets.

## SQL workflow and privileges

All functions and triggers run with the caller's privileges. The owner provisions grants; enabling does not grant
anything. A writer needs SELECT on the recording, raw, label, context and optional acquisition/time-anchor tables,
and SELECT/INSERT/UPDATE on the calibration table. An approver needs the same read access and UPDATE on the
calibration table, plus membership in the configured approval role. A writer also needs INSERT on the reference table
(SQL 0.3.18), which MorletDB fills when a revision is written. A reader needs SELECT on the calibration table and, under
row security, on the reference table and recordings for its policy; resolving additionally needs SELECT on source
companion tables, including the interpretation history.

```sql
-- Owner setup (example collection already has recordings and context/labels):
CREATE ROLE calibration_reviewers;
SELECT morlet_enable_calibration_sets('signals', 'calibration_reviewers');
-- Grant narrowly scoped privileges to your writer/reviewer roles as above.

BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT morlet_put_calibration_set('signals', 'bearing-commissioning', 1,
  '[{"recording_id":"run-1","start_sample":0,"end_sample":25600,"label_id":12}]');
COMMIT;

-- A reviewer reads the draft and inspects its manifest before approval.
SELECT morlet_get_calibration_set('signals', 'bearing-commissioning', 1);
BEGIN ISOLATION LEVEL REPEATABLE READ;
SELECT morlet_approve_calibration_set('signals', 'bearing-commissioning', 1,
  '<manifest_sha256 returned by the reviewed draft>', 'Reviewed commissioning run and label');
COMMIT;

SELECT morlet_resolve_calibration_set('signals', 'bearing-commissioning', 1);
```

Writes require REPEATABLE READ or SERIALIZABLE. The SDK leaves transactions and commits to its caller. A revision
row lock and normal PostgreSQL write conflicts serialize editing and approval. Retry the entire transaction on
SQLSTATE 40001; there is no automatic transaction retry. Identical create retries return the saved revision without
recapturing data. Changed membership under the same identity is a conflict unless `replace_draft => true` is explicit.
That option also refreshes evidence after a correction, and only works on drafts. Approval requires the digest the
caller reviewed, refuses evidence changed since capture, and records actor, transaction timestamp and reason.
An identical approval retry by the original actor with the same digest and reason is a no-op.

Approval freezes evidence as visible in the transaction snapshot. A concurrent source correction may commit afterward;
this is why resolution is necessary. Approval does not lock source histories indefinitely. For a new interpretation or
membership, create a new revision and review it. Revisions need positive numbers; numbering and names are caller-owned.

Triggers regenerate audit/hash/visibility fields on direct SQL writes, enforce the separate approval authority and
reject edits to approved rows. DELETE and TRUNCATE are refused for all revisions. As elsewhere in PostgreSQL, owners
who can drop tables or disable triggers and superusers remain trusted administrators. Do not grant those powers to
ordinary writers. Role names are persisted for restore portability; do not rename/drop the configured approval role
without a deliberate administrative migration.

`morlet_enable_row_security` protects the new companion in either enablement order. A manifest is visible only when
**every** referenced recording is visible. There is no partial manifest, hidden-member count or fallback to creator
access. A hidden or deleted recording can make the whole revision unavailable to a non-owner; this intentionally
prefers confidentiality to revealing why it disappeared. From SQL 0.3.18, deleting a member recording cuts its
reference for good: a later recording created under the same ID is a different recording, and evidence captured from
the deleted one stays unavailable to non-owners whoever can see the new one. Create a new revision from the new
recording. References are written only by MorletDB and cannot be edited or removed; truncating the collection with
`CASCADE` is refused while they exist. An administrator can resolve deleted sources. Source labels
and context follow recording-level access, as in the existing helper; separately restrictive custom policies on those
tables are not automatically propagated to a previously captured manifest. Grant manifest access only to roles allowed
to see captured annotations as well as the recordings. `FORCE ROW LEVEL SECURITY` follows PostgreSQL's usual rules.

## SDK

```python
import psycopg
from morletdb import Client

with psycopg.connect(dsn) as conn:
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    db = Client(conn)
    draft = db.put_calibration_set("signals", "bearing-commissioning", 1, [
        {"recording_id": "run-1", "start_sample": 0, "end_sample": 25600, "label_id": 12}
    ])
    # Approve only after review, using a connection/role authorized to approve:
    db.approve_calibration_set("signals", "bearing-commissioning", 1,
        expected_digest=draft["manifest_sha256"], reason="Reviewed commissioning run and label")
```

`enable_calibration_sets`, `get_calibration_set` and `resolve_calibration_set` wrap the corresponding SQL functions.
Results are JSON dictionaries; these APIs are experimental. Use a fresh snapshot when resolving newly committed changes.

## Changes, retention and restore

Resolution returns each member as `unchanged`, `changed`, `unverified` or `unavailable`, plus `needs_review`. It does not mutate an
approved revision. Metadata/label/context/time-axis or sample changes make it changed; a retracted/missing label or
missing/compacted raw samples makes it unavailable. From SQL 0.3.18 an interpretation correction of the member's
recording makes it changed even when a later correction restored the original values, and a member whose
interpretation history the resolving role cannot read is `unverified`, never `unchanged`. Revision 0 matches a member
captured before the history existed. Permission errors, corruption and operational failures are raised,
not hidden as missing data. The original manifest remains available under its access rules. A note-only metadata edit
currently also causes review; #600 will provide more precise interpretation-versus-annotation distinctions.

A reference and digest do **not** pin raw chunks or make a backup. Retention/compaction continue normally; no new
retention policy or automatic restore is introduced. To list revisions needing review, select visible set identities
and call `morlet_resolve_calibration_set` laterally, with a bounded application batch and timeout. Resolution reads raw
samples and is not a cheap indexed status query. Preserve data externally or coordinate retention through #216/#204
when a calibration revision must stay reproducible. No change is made to sample storage or persisted chunk formats.

SQL 0.3.16 upgrades to 0.3.17 with `ALTER EXTENSION pg_morlet UPDATE`; existing collections remain unchanged until
enabled. The update to 0.3.18 brings every existing calibration table up to date: each revision gets references to
its recordings, a member whose recording no longer exists starts cut, and row-security policies are rewritten where
the collection uses them. Grant writers INSERT and readers SELECT on the new reference table. A recording that was
deleted and recreated under the same ID before the update cannot be told apart from the original: check revisions
whose members were recreated, and create new revisions where needed. Standard `pg_dump`/restore preserves manifests and audit fields: data is restored before these triggers are
installed. Restore the configured roles as well (roles are cluster-level objects, not included in a database dump).
Do not restore table data alone through active write triggers. The new tests exercise a full schema-and-data restore.
