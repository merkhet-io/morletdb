# WinCC OA integration (design, phase 0)

**Status:** design proposal for [#474](https://github.com/merkhet-io/morletdb/issues/474), awaiting the owner's
approval. Nothing here is implemented. Until native acceptance in WinCC OA (phase C) has passed, the deliverable is
"an experimental WinCC OA adapter, awaiting validation in WinCC OA". It is part of MorletDB Enterprise (see
below). There is no Siemens certification or partnership, and WinCC OA is installed and licensed separately.

**Licence and code boundary.** The integration is part of **MorletDB Enterprise**, not the Apache-2.0 core. This
follows the rules in [licensing.md](licensing.md) and [enterprise/README.md](../enterprise/README.md) (in force since
#473).

- **Where the code lives.** The adapter and the companion live under `enterprise/winccoa/adapter/` and
  `enterprise/winccoa/companion/`.
- **Licence.** Every source file there starts with the enterprise header and is under the MorletDB Enterprise
  License (`LICENSE-ENTERPRISE.md`). Production use is free on up to 3 machines; beyond that it needs a
  subscription.
- **Dependency direction.** The core never depends on this code, and no core file imports from `enterprise/`. The
  integration uses only the public core: the Python SDK and documented SQL functions.
- **No data is locked in.** The core keeps everything needed to store and read context without WinCC OA: the
  documented context SQL functions, the SDK and the open-protocol collectors (OPC UA, MQTT). Context written by
  the integration is ordinary context rows, readable and searchable with the core alone.
- **Core changes.** Anything the design needs in the core is listed under "Core changes (Apache 2.0)" at the end.
  Those changes are made in the core under Apache 2.0, as separate PRs.

**Blocker for phase A (owner action):** the enterprise licence text is version 0.1. A lawyer must review it before
the first file is placed under it.

Checked against main `9ea29d3`; the SQL, SDK, viewer and collectors are unchanged in release 0.3.14-alpha.1 (SQL
0.3.11). Every SQL
reference below was read in `extensions/pg_morlet/sql/streams.sql` (cited as `streams.sql:line`). `context-model.md`
is partly a proposal and is not relied on.

**Shape.** There are three parts, and waveforms never pass through WinCC OA:

- The DAQ or replay writer appends samples through the SDK.
- A JS/TS **adapter** runs as a WinCC OA JavaScript Manager. It reads configured datapoints and writes only to its
  own datapoint namespace.
- A Python **companion** runs next to MorletDB. It stores observations, derives context and runs bounded searches.

The adapter talks only to the companion, and the companion talks only to PostgreSQL. WinCC OA's archive is not used.

## Design

### 1. Where context observations live: a companion-owned table with row-level security (option a)

Shipped context rows hold `recording_id, kind, key, value, start_sample, end_sample, batch_id` plus revision fields
(`streams.sql:2075-2088`). Context matching compares `value` as JSON (`morlet_context_match`, `streams.sql:2565`;
segment contexts are `jsonb_object_agg(key, value)`, `streams.sql:2343`). The OPC UA reference collector packs
status and source time into `value` (`examples/collectors/opcua/collector.py:99-104`). If we did the same, two equal
states with different timestamps would never match. We do not follow that example.

**Recommendation:** a schema `morlet_winccoa`, owned by the deploying administrator, with the following tables.

- **`observation`**, append-only. Its columns:
  - `collection`, `acquisition_id`, `dpe` and `key` (the configured mapping);
  - `value_json` and `value_text`, the exact text the adapter sent, kept for lossless audit (jsonb `numeric` has no
    `-0`, and JSON has no NaN);
  - `value_type`;
  - `source_ns` and `source_status` (`ok`, `missing` or `unreadable`);
  - `quality` (raw attributes as received, or NULL with `quality_status = 'missing'`);
  - `observed_ns`, the adapter's host clock;
  - `kind` (`change`, `snapshot`, `heartbeat` or `gap`);
  - `adapter_session` and `seq`, unique together;
  - `digest`.

  An identical retry of `(adapter_session, seq)` is a no-op, and a different body under the same pair is a conflict.
- **`derivation`** maps observations to the context `batch_id` they produced, or to a reason for producing none
  (`open`, `outside_recording`, `untimed`, `bad_quality`, `clock_exceeded`).

Context rows are derived from these tables with the shipped functions `morlet_add_acquisition_context`
(`streams.sql:2882`) and `morlet_add_context` (`streams.sql:2238`). Both are stable in
`extensions/pg_morlet/api/functions.tsv`.

**Access control.** `morlet_enable_row_security` covers a fixed list of table names (`streams.sql:982-985`), so it
will not cover our schema.

- The companion enables RLS on its own tables. Its policy follows the collection: a row is visible when its
  acquisition has a channel the caller can see, i.e. `EXISTS (SELECT 1 FROM <c>_acq_channels ch WHERE
  ch.acquisition_id = observation.acquisition_id)`. The channel table is itself under the shipped policy
  (`streams.sql:1003-1009`).
- We do not call the internal helper `morlet_acquisition_has_visible_channel` (`streams.sql:961`; classified
  `internal`).
- The policy reads the companion table `<c>_acq_channels`. That table is documented
  (`docs/context-workflow.md:198`), but it is not a SQL function. If the boundary rule is read as "documented
  functions only", the core needs a documented visibility function first (core change 2 below).
- Phase A tests a non-owner role, direct `SELECT` and `INSERT` on both tables, and both enablement orders (companion
  schema before and after `morlet_enable_row_security`).

**Alternatives considered:**

- **(b) Extension tables and functions.** These would be reusable by other collectors. However, they need a SQL
  version step, a frozen upgrade script and API-inventory entries, all for a model that is not validated yet. We
  revisit this once phase C has shown which fields matter.
- **(c) Metadata in `value`.** Rejected, because it breaks matching (see above).

**Owner decision:** approve (a), and name the schema.

### 2. From observation to sample range

A state holds from its change until the next change, as a half-open interval in acquisition time. The companion
converts with `t = source_ns − origin_ns` and lets the server map each channel with `morlet_time_to_samples` (ceil
rule, exact `numeric`; `streams.sql:2765-2779`). It never rounds itself. The OPC UA collector rounds
(`collector.py:95`); we do not copy that.

- **Open last state.** No context row is written while a state is open: a single key may not have overlapping
  intervals (`streams.sql:2218-2234`), and a provisional end would need a revision on every batch.
  - The open state is reported from `observation` (shown as "open since t") and is closed by the next change, a
    `gap` or the end of the acquisition.
  - Batch IDs are `winccoa:<acquisition>:<key>:<sha256 of the closed interval and value>`, so an identical retry
    returns 0 (`streams.sql:2256`).
  - A row is derived only once its interval is closed. A wrong derivation is corrected with `morlet_revise_context`
    (`streams.sql:2280`), never overwritten.
- **Before the first sample.** A state that began before sample 0 holds at sample 0. The server clamps its start to 0
  (`streams.sql:2777`), and that is correct: the state is known to hold there.
- **After the last sample.** An interval past committed samples may be stored. Segments use only pieces inside
  committed samples (`streams.sql:2350`), so that part becomes usable when the samples arrive.
  - Once the writer marks the acquisition finished, intervals are clipped to the recording end.
  - An interval that maps to no sample of a channel is skipped by the server (`streams.sql:2903`). The companion
    records it as `outside_recording`.
- **Reconnect snapshots.** A snapshot is the current value. It is not recovered history.
  - The adapter tags initial values from `dpConnect(..., answer = true)` (update type `Answer`, see section 8) as
    `snapshot`.
  - On a disconnect, the open state ends at the last committed `heartbeat` before the outage. The span up to the
    snapshot gets a `gap` observation and no context row, so it reads as missing evidence, not as "no state".
  - The snapshot opens a new state at `max(source_ns, reconnect observed_ns)`.
  - A repeated identical snapshot within a session is a no-op.
- **Late channels.** `morlet_add_acquisition_context` maps only to channels that exist at call time. Per-channel
  batches are `<batch>@<acq>/<role>` (`streams.sql:2905`), so re-running the same acquisition batch after a channel
  is added should fill only the new channel. This is read from the code and has no test yet; phase A adds one.

**Owner decision:** the heartbeat interval (proposed 10 s), which bounds how much state is lost at a disconnect.

### 3. Time alignment

- **Start time comes from the waveform writer, never from WinCC OA.** There are two stored forms:
  - the acquisition's `origin_utc` (`timestamptz`, microseconds; `streams.sql:2734`) plus each channel's `offset_ns`
    (`streams.sql:2817`);
  - the SDK's `start_ns` in recording metadata (`_client.py:169-198`), read by search time filters.

  The companion requires `start_ns = origin_ns + offset_ns` for every channel. On a mismatch it refuses to derive
  context for that acquisition and reports `inconsistent_start`.
- **A changed spec stops a recording.** `morlet_ensure_recording` already refuses changed metadata or rate
  (`streams.sql:306-308`). The writer starts a new recording and acquisition; it never continues the old one.
- **Allowed offset between the WinCC OA host and the DAQ host.**
  - The configured bound defaults to **50 ms**: both hosts are expected on NTP or PTP.
  - The adapter sends its clock in every heartbeat, and the companion estimates `|offset| + RTT/2` against its own
    clock.
  - When the companion is not on the DAQ host, the operator must declare the companion–DAQ bound, because the
    companion cannot measure it.
  - Segments built for WinCC OA keys use `trim_samples = ceil(bound · rate)` (`streams.sql:2317-2318`). Channel
    `offset_uncertainty_ns` describes the channel's own clock, not WinCC OA's.
- **When the bound is exceeded,** observations are still stored but derivation stops (`clock_exceeded`) and health
  becomes `degraded`. No offset correction is applied automatically: that would change calibration. Re-deriving
  after a correction is an explicit operator action.
- **No source time, or unreadable quality.**
  - The value is stored with `source_status`/`quality_status = 'missing'` and no context row (`untimed` or
    `bad_quality`).
  - A change to bad or unknown quality ends the previous state at that change.
  - Using `observed_ns` as the time basis is opt-in per datapoint, with the observed clock named in the derivation.

**Owner decision:** the default bound (50 ms), and whether the opt-in observed-time basis is allowed at all.

### 4. Adapter–companion protocol

- **Transport.** HTTP/1.1 with JSON on `127.0.0.1:8770`. The adapter is always the client, so the companion never
  connects into WinCC OA.
- **Endpoints:** `POST /v1/observations`, `POST /v1/investigations`, `GET /v1/investigations/{request_id}` and
  `GET /v1/health`.
- **Versioning.** Every body carries `"schema": "morlet-winccoa/1"`. An unknown major version returns 400, listing
  the supported versions.
- **Why not WebSocket.** It would add a long-lived connection with its own reconnect and ordering logic on both
  sides. Results are small and request-shaped.
- **Acknowledgement means committed.** `observations` returns 200 with the highest committed `seq` only after
  `COMMIT`, and the adapter resends anything unacknowledged.
- **Investigations.**
  - **Request.** A request names a `request_id` (UUID), a `slot` (one per UI panel) and a stable source interval:
    `collection`, `recording_id`, `start_sample`, `end_sample` (half-open, one window).
  - **Retries.** A repeated `request_id` with the same body returns the same result, and one with a different body
    returns 409.
  - **Late responses.** The adapter writes a result into its namespace datapoints only if its `request_id` is still
    the slot's current one. Late results are dropped and counted.
  - **Superseded requests.** The companion cancels a superseded request's query (`pg_cancel_backend` on its
    connection).
  - **Status.** Results carry one of `ok`, `no_matches`, `evidence_unavailable` (not searchable yet, from
    `morlet_coverage`, `streams.sql:3581`), `permission_denied` or `service_failure`.
  - **Search mode and readiness.** Results report the search mode (`exact`/`probes`) and readiness.
- **Search.** `morlet_search_similar_evidence` (`streams.sql:1947`; stable). We do not use
  `morlet_search_similar_excluding`, which is experimental and unreleased.

### 5. Authentication

- **Shared token.**
  - The bearer token is at least 32 random bytes, read from a file owned by the service user with mode `0600`. The
    companion refuses to start if the file mode is wider.
  - Tokens are compared with `hmac.compare_digest`, as the viewer does (`tools/viewer/viewer.py:289-290`).
  - The adapter reads its copy from a file in the WinCC OA project, never from a datapoint.
- **Rotation.** The file may hold two lines (current and next), and both are accepted. The companion reloads it when
  its modification time changes. To rotate: add the new token, update the adapter, then remove the old one.
- **Logging.** Logs record the token fingerprint (first 8 hex digits of its SHA-256), the remote address and the
  outcome, never the token itself. Failures are rate-limited (5 per minute before a 60 s back-off).
- **Database credentials.** They come from a libpq passfile or service file with mode `0600`. The configured DSN
  holds no password.
- **Database roles.** Two NOLOGIN roles are granted to one login role.
  - `winccoa_context`: `INSERT` on the companion tables, plus the context-writer grants that `morlet_add_context`
    needs (`INSERT` on `_ctx`, `UPDATE` on `_ctx_segments` for staleness).
  - `winccoa_reader`: the analyst grants from `docs/access-control.md:52-60`.
  - Neither role has DDL rights, and neither may write to recordings or chunks.
- **No arbitrary SQL.** Only configured collections and datapoints are accepted. Any other name returns 403, even if
  the role could see it.
- **Remote binding.** Not in phase A or B. It needs explicit configuration and TLS in front, and is a later PR.

**Owner decision:** one login role with two group roles, or two separate services.

### 6. Limits

These are initial values, to be measured in phase A and then fixed.

| Limit | Value | Basis |
|---|---|---|
| Samples per writer batch (per channel, one transaction including `build_search_index`) | 65,536 (16 chunks of 4,096) | SDK chunk 4,096 (`_client.py:18`); ≈ 32 windows per batch at stride 2,048 |
| Observations per message / body size | 1,000 / 1 MiB | |
| Configured datapoints per adapter | 500 | |
| `top_k` | default 10, max 50 | server maximum 100 (`streams.sql:1505`) |
| Concurrent investigations | 1 per slot (new supersedes old), 4 overall, then 429 | |
| Response size | 256 KiB; no samples, overview 16 buckets | |
| Query timeout | `statement_timeout` 10 s, HTTP deadline 15 s | `max_candidates` stays 1,000,000 |
| Source interval | exactly one built window (512–32,768; default 4,096) | `_client.py:21`, `streams.sql:1199` |

**Search readiness.** SDK `append()` does not build envelopes (`_client.py:209`). The writer calls
`build_search_index` in the same transaction as each bounded batch, so samples become searchable at the commit
that stores them (`docs/search-contract.md:100-104`). The companion never reports a source as searchable without
`morlet_coverage`.

**Owner decision:** approve these numbers as phase-A starting values.

### 7. Viewer links

The viewer binds to 127.0.0.1 (`viewer.py:320`) and answers only `Host: 127.0.0.1` or `localhost` (`viewer.py:249-250`).

**Recommendation:** keep that model.

- Results include a link `http://127.0.0.1:<port>/?collection=…&recording=…&start=…&end=…`
  (`tools/viewer/index.html:132`) only when `viewer.base_url` is configured and is a loopback address. Otherwise
  results carry only the interval references.
- Stated limit: a link opens only in a WinCC OA UI on the same host as the viewer.

**Alternative for a later PR:** the viewer behind an authenticating reverse proxy, with an allowed-host list. That
changes `host_ok` and the viewer's security model, so it is not part of this work.

**Owner decision:** whether to plan that later PR.

### 8. Target version

**Target:** WinCC OA **3.21** at the latest patch at acceptance time (the documentation currently states 3.21.7),
**Node.js 22 LTS**, **Debian 12 x86-64**.

What the official documentation supports:

- **WinCC OA versions and licence.** The JavaScript Manager needs WinCC OA 3.20 or later and is part of the Server
  Basic licence. Node.js is installed with WinCC OA, and Node.js 22 LTS is supported ([requirements]).
- **Operating systems.** 3.21 lists Windows 11, Windows Server 2022 and 2025, RHEL 10, Oracle Linux 10 and Debian 12
  (native, Docker, ARM64). Only 64-bit systems are supported ([operating systems]).
- **Manager executable.** `WCCOAjsManager`. Script paths are relative to the project's `nodejs` directory
  ([configuration]).
- **API package.** `winccoa-manager`, class `WinccoaManager`. The 3.21 documentation links API **2.6.6**
  ([API overview]). However, the 3.21.2 patch notes say the API "was updated to version 2.3.10" ([patch notes]).
  Which API version each 3.21 patch ships is therefore not stated.
- **Methods the adapter would use**, as documented for 2.6.6 ([WinccoaManager]):
  - `dpConnect(callback, dpeNames, answer?) → number`: with `answer = true`, the callback receives the initial
    values right away;
  - `dpDisconnect(id)`;
  - the callback type `(dpeNames: string[], values: unknown[], type: WinccoaConnectUpdateType, error?) => void |
    Promise<void>` ([callback]);
  - `dpQueryConnectSingle(callback, answer, query, blockingTime?) → number`, which delivers a table with a header
    row ([query callback]);
  - `dpQuery(query)`. Its documented example selects `'_original.._stime', '_original.._value'`;
  - `dpSetWait`, `dpExists`, `dpCreate`, `dpTypeCreate`, `dpTypeGet`;
  - `setOptions({longAsBigInt, timeFormat, ...})`, `getVersionInfo()`, `registerExitCallback` and `exit`.
- **Times.** `WinccoaTime` is `Date | number | Temporal.Instant`, selected by `timeFormat` (default `Date`)
  ([WinccoaTime], [options]).
- **Numbers.** Numbers are JavaScript `number`. 64-bit integers are exact only with `longAsBigInt: true`
  ([data types]), so the adapter sets it.

**Not confirmed from official documentation (open questions; not assumed from CTRL):**

1. Whether `dpConnect` or `dpQueryConnectSingle` deliver source time. We would use a query like `SELECT
   '_original.._value', '_original.._stime' FROM ...`, by analogy with the documented `dpQuery` example.
2. Which attribute names give quality (status bits, invalid flag) in the JavaScript API.
3. The values of `WinccoaTimeFormat`, and the precision WinCC OA source times actually carry.
4. The fields of `WinccoaVersionDetails`.
5. How the API reports loss of the connection to the event manager, and how it reconnects.
6. The API version installed with each 3.21 patch, and the examples shipped with an installation.

These are settled in phase B against an installation's installed examples and type definitions, or else in phase
C. Until then, quality and source time are optional in the protocol, and missing values are recorded as missing
(section 3).

**Owner decision:** the target patch and OS for phase C, and who provides the machine and licence.

## Core changes (Apache 2.0)

These changes belong to the core, not to `enterprise/`. Each is a separate core PR under Apache 2.0, and each is
useful without WinCC OA. None is required to start phase A except possibly item 2.

1. **SDK context methods.** Thin wrappers over documented SQL:
   - `create_acquisition`, `add_acquisition_channel`;
   - `add_context`, `add_acquisition_context`, `revise_context`;
   - `coverage`.

   The SDK has no context, acquisition or coverage methods today (`sdk/python/src/morletdb/_client.py`). Without
   them, the companion calls the documented SQL functions with parameterized SQL, which the boundary allows. With
   them, the open-protocol collectors get the same path.

   **Owner decision:** whether to add them before phase A.
2. **A documented acquisition-visibility function** for the companion's row-level-security policy (section 1). This
   could document `morlet_acquisition_has_visible_channel`, or add a function keyed by collection and acquisition.
   It changes the API inventory and possibly SQL, so it may need a SQL version step and upgrade script.

   **Owner decision:** whether the policy may read the documented `<c>_acq_channels` table directly instead.
3. **OPC UA reference collector.** It packs status and source time into the context `value` and rounds the sample
   offset (`examples/collectors/opcua/collector.py:95-104`). This is a separate core issue: store those fields
   outside `value`, and use `morlet_time_to_samples`. It is not needed for this integration.

[requirements]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/NodeJS/topics/nodejs_requirements.html
[operating systems]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/WCCOA_Voraussetzungen/sw_req_operating_systems.html
[configuration]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/NodeJS/topics/nodejs_basic_configuration.html
[API overview]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/winccoa_manager_overview.html
[patch notes]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/patch_notes/3.21/UpdateInfo.html
[WinccoaManager]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/classes/WinccoaManager.html
[callback]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/types/WinccoaDpConnectCallback.html
[query callback]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/types/WinccoaDpQueryConnectCallback.html
[WinccoaTime]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/types/WinccoaTime.html
[options]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/apis/winccoa-manager/2.6.6/interfaces/WinccoaOptions.html
[data types]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/NodeJS/topics/nodejs_valid_data_types.html
