# API and on-disk stability (freeze candidate, #195)

What you can rely on when you build on pg_morlet, and how CI holds us to it. This is the **freeze candidate** for
the beta: from the first beta release the promises below apply; from 1.0 they apply under semantic versioning.

## Function classes

Every SQL function and aggregate of the extension is classified in
[`extensions/pg_morlet/api/functions.tsv`](../extensions/pg_morlet/api/functions.tsv):

| Class | What it means | Count today |
|---|---|---|
| **stable** | Documented and in use for at least two releases. Its name, arguments and result do not change and it is not removed, except through a deprecation cycle. Its documented behaviour is kept; bug fixes are allowed. | 64 (65 signatures) |
| **experimental** | Documented, but new (SQL 0.3.5 or later): memory levels, `morlet_verify`, compressed transfer, notifications, synchronous groups, the ingest hold, roll-ups, `morlet_latest`, `morlet_coverage`, TOAST pinning, `morlet_search_similar_excluding`. It may still change between minor releases; every change is in the changelog. It becomes stable after it has been in use for two releases without a breaking change. | 34 |
| **internal** | Helpers of other functions (aggregate steps, triggers, relation lookups) and undocumented functions. No promise: they may change or disappear in any release. Do not call them from applications. | 95 |
| **deprecated** | Stable functions on their way out (none today). | 0 |

## The promises

1. **Stable SQL API.** A stable function keeps its signature and documented behaviour. Removing or changing one takes
   a deprecation cycle: it is marked deprecated in the changelog and documentation (and, where possible, raises a
   `NOTICE` when called) for at least one minor release, with its replacement named, before it changes. From 1.0, a
   breaking change to a stable function needs a new major version.
2. **Stored data stays readable.** Chunks written by any release from the beta on are readable by every later
   release of the same major version. The chunk format is versioned; golden chunk files that a change may not alter
   are checked in CI (`docs/verification.md`). Derived data (chunk summaries, envelopes, probe rows, roll-ups) can
   always be rebuilt from the chunks; when its format changes, the update script or the steward rebuilds it. Until
   then CI checks that derived data written by each release reads back identically after the update: envelopes,
   context segments and values, comparisons, chunk summaries and interval statistics (from SQL 0.3.3), the
   integrity check (0.3.5), roll-ups and coverage (0.3.6). Probe rows need pgvector and are not part of the upgrade
   test; they are rebuilt from the envelopes (`morlet_enable_envelope_probes`).
3. **Every version upgrades.** Every SQL version ships an update script from the previous one, and CI upgrades
   populated databases from every release (`tests/upgrade.sh`, `tests/upgrade_from_release.sh`): the upgraded catalog
   must equal a fresh installation, and reads before and after must be identical. No release requires dumping and
   restoring the data.
4. **Experimental is labelled.** Documentation marks experimental functions; their changes are listed in the
   changelog under the release that makes them.

## How CI enforces it

- `extensions/pg_morlet/tests/api_stability.sh` (CI, every PostgreSQL major) fails when a function of the extension
  is not classified, when a stable signature differs from
  [`api/stable.txt`](../extensions/pg_morlet/api/stable.txt) or disappears, or when a function classified stable is
  missing from that snapshot. A deliberate change regenerates the snapshot (`--write`) in the same pull request,
  where it is visible to review, together with the deprecation note.
- The golden chunk files and the upgrade tests above.

## Still to do before 1.0

- Probe rows in the upgrade test (needs a pgvector build of every release).
- Mark experimental functions in the documentation pages, not only in `functions.tsv`.
- Deprecate or document the undocumented older functions that look public (`morlet_search`,
  `morlet_create_collection`, `morlet_search_recording`, `morlet_decode_interval`, ...): today they are internal.
