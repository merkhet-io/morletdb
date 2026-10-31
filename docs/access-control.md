# Access control

pg_morlet's functions run as `SECURITY INVOKER`: every read, search and write uses the
caller's privileges and row-level security (RLS). That is necessary but not sufficient.

## The gap `morlet_enable_row_security` closes

A collection is several caller-owned tables:
- the recordings table `<c>`;
- raw chunks `<c>_chunks`, feature segments `<c>_segments`, envelopes `<c>_envelopes`;
- probe vectors `<c>_probes_<w>`, context tables `<c>_ctx*`, labels `<c>_labels`, and
  acquisition tables `<c>_acq*`.

To search, a role needs `SELECT` on the companion tables it reads, and that grant also lets it
query them **directly**. RLS on the recordings table alone therefore protects the functions'
results, but not the data. Measured with two tenants before this change: a tenant restricted
by RLS to its own recordings could read and decode another tenant's raw chunks, envelopes and
probe vectors with plain `SELECT`s, while every search function correctly returned only its own
recordings.

```sql
ALTER TABLE signals ENABLE ROW LEVEL SECURITY;               -- your policies, e.g. per tenant
CREATE POLICY own ON signals FOR SELECT TO analyst_a USING (metadata->>'tenant' = 'a');
SELECT morlet_enable_row_security('signals');                 -- every companion table follows
```

- **What the helper does:** enables RLS on every companion table and adds the policy
  `morlet_follows_recording`. A row is visible and writable only if its recording is visible
  to the caller in the recordings table, so the recordings table's own policies decide
  everything.
- **Exceptions to the recording rule:**
  - context values follow their context segment;
  - an acquisition is visible through any of its visible channels. Inserting an acquisition
    is allowed, because its channels can only be added after it exists.
- **Later tables:** the helper is idempotent. Once used, enabling envelopes, probe search,
  context or acquisitions later applies it to the new table automatically.
- **Status:** `morlet_row_security_enabled(collection)` reports whether it has been applied.

## Roles

| Role | Grants | Notes |
|---|---|---|
| Analyst (read, search) | `USAGE` on the schema; `SELECT` on the recordings table and the companion tables it searches | Sees only recordings its RLS policies allow, in tables and in results |
| Ingester | as analyst, plus `INSERT` on recordings and chunks, `UPDATE` on recordings (checkpoint) | Appends only to recordings it can see |
| Representation worker (in-database or external) | `SELECT` on recordings and chunks; `INSERT` on envelopes and probe tables | `WITH CHECK` rejects rows for recordings it cannot see |
| Owner / administrator | owns the tables | Owners bypass RLS unless `FORCE ROW LEVEL SECURITY` is set |

Tested: analyst reads and searches, worker inserts for hidden recordings (rejected), and roles
without grants (`permission denied`). The ingester's grants are derived from the statements the
append functions run and are not yet covered by a test.

## The worker write path

An external worker (evaluation CW1) needs `INSERT` on the envelope table. The policy limits it
to recordings it can see, but **cannot check that the envelope bytes are correct**: a worker
with `INSERT` can store a wrong envelope for a visible recording, which would change search
results without touching raw data.

Treat the worker role as trusted for the recordings it serves. To audit its output, recompute
a sample of windows in the database and compare:

```sql
SELECT count(*) FILTER (WHERE e.envelope <> morlet_envelope_encode(i.samples)) AS mismatches
FROM signals_envelopes e TABLESAMPLE SYSTEM (1),
     LATERAL morlet_read_interval('signals', e.recording_id, e.start_sample, e.start_sample + e.window_size) i;
```

## Cost (measured)

The confirmation database had 234,622 windows in 59 recordings. A non-owner role ran 20
queries, each once to warm up and once timed:

| Search | No policies | With `morlet_enable_row_security` |
|---|---|---|
| Exact, unfiltered (p50) | 946 ms | 995 ms |
| Exact, one station-day (p50) | 53 ms | 54 ms |
| Probe search, unfiltered (p95) | 40 ms | 46 ms |

The policies compare each row's `recording_id` against the array of visible recordings,
computed once per query. A first version used a correlated `EXISTS`; PostgreSQL could then not
run the exact scan in parallel, and the scan took 2,821 ms. The array comparison is linear in
the number of visible recordings, and collections with thousands of recordings have not been
measured.

## Limits

- **Not a security proof.** These are tested paths (direct reads of every companion table,
  search results, writes for hidden recordings, hidden recordings as query sources, roles
  without grants), not a formal argument.
- **Existence of identifiers.** Foreign-key checks bypass RLS. A tenant inserting a row with
  another tenant's `recording_id` gets a policy violation rather than a foreign-key error, so
  the existence of that identifier is not revealed. Constraint violations elsewhere may still
  reveal that an identifier exists.
- **Collection-level metadata.** Catalog information is visible to every role with schema
  access, as usual in PostgreSQL: table names and the window sizes encoded in probe-table
  names.
