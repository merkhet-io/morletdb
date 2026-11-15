# WinCC OA integration (design, phase 0)

**Status:** design for [#474](https://github.com/merkhet-io/morletdb/issues/474), with the defaults decided on
2026-11-14. Phase A (the companion and the replay machine) is implemented in `integrations/winccoa/`. Phase B, the
adapter, is implemented in `integrations/winccoa/adapter/` and tested against a fake of the documented WinCC OA
JavaScript Manager API and the real companion; it has never run in WinCC OA. Native acceptance (phase C) is not
done. Until it has passed, the deliverable is "an experimental WinCC OA adapter, awaiting validation in WinCC OA".
It is community-maintained. There is no Siemens certification or partnership, and WinCC OA is installed and licensed
separately. Installation and operation: [The adapter](#the-adapter-phase-b).

**Licence and code boundary.** The integration is open source under the Apache License 2.0, like the rest of
MorletDB ([licensing.md](licensing.md)).

- **Where the code lives.** The adapter and the companion live under `integrations/winccoa/adapter/` and
  `integrations/winccoa/companion/`.
- **Vendor code.** The vendor dependency is confined to the adapter. No proprietary WinCC OA runtime, SDK or file is
  committed or redistributed.
- **Dependency direction.** The core never depends on this code, and no core file imports from `integrations/`. The
  integration uses only the public core: the Python SDK and documented SQL functions.
- **No data is locked in.** Context written by the integration is ordinary context rows, readable and searchable
  without WinCC OA: through the documented context SQL functions, the SDK and the open-protocol collectors (OPC UA,
  MQTT).
- **Core changes.** Anything the design needs in the core is listed under "Core changes" at the end, as separate PRs.

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
segment contexts are `jsonb_object_agg(key, value)`, `streams.sql:2343`). The OPC UA reference collector used to pack
status and source time into `value`, so two equal states with different timestamps never matched; #480 fixed that.
The companion keeps that metadata out of `value` too.

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

**Decided (2026-11-14):** option (a), schema `morlet_winccoa` (see [Defaults](#defaults)).

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
  - Derivation follows commit order, so the adapter never commits a heartbeat ahead of a gap it has queued: a
    heartbeat goes after the last pending gap in the same message, and a message that cannot hold that gap beside
    its heartbeats (by count or by bytes) carries none (#496).
  - The snapshot opens a new state at `max(source_ns, reconnect observed_ns)`.
  - A repeated identical snapshot within a session is a no-op.
- **Late channels.** `morlet_add_acquisition_context` maps only to channels that exist at call time. Per-channel
  batches are `<batch>@<acq>/<role>` (`streams.sql:2905`), so re-running the same acquisition batch after a channel
  is added should fill only the new channel. This is read from the code and has no test yet; phase A adds one.

**Decided (2026-11-14):** 10 s; the adapter is stale after three missed heartbeats, 30 s (see [Defaults](#defaults)).

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
  - The companion takes the receive time when a request arrives, before reading its body or connecting to the
    database. The adapter measures RTT on `GET /v1/clock`, which does no database work, so database latency is not
    counted as offset. Against a companion without `/v1/clock` (404) it uses the RTT of `GET /v1/health`, which is
    larger and so errs on the safe side, and logs this once.
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

**Decided (2026-11-14):** a 50 ms bound; the observed-time basis is not allowed (see [Defaults](#defaults)).

### 4. Adapter–companion protocol

- **Transport.** HTTP/1.1 with JSON on `127.0.0.1:8770`. The adapter is always the client, so the companion never
  connects into WinCC OA.
- **Endpoints:** `POST /v1/observations`, `POST /v1/investigations`, `GET /v1/investigations/{request_id}`,
  `GET /v1/health` and `GET /v1/clock`. `clock` returns `{"schema", "companion_ns"}` (the companion's clock as a
  decimal string) without database work; the adapter measures its heartbeat RTT on it.
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

**Decided (2026-11-14):** one login role with two group roles (see [Defaults](#defaults)).

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

**Decided (2026-11-14):** these are the starting values, to be checked by a load measurement (see [Defaults](#defaults)).

### 7. Viewer links

The viewer binds to 127.0.0.1 (`viewer.py:320`) and answers only `Host: 127.0.0.1` or `localhost` (`viewer.py:249-250`).

**Recommendation:** keep that model.

- Results include a link `http://127.0.0.1:<port>/?collection=…&recording=…&start=…&end=…`
  (`tools/viewer/index.html:132`) only when `viewer.base_url` is configured and is a loopback address. Otherwise
  results carry only the interval references.
- Stated limit: a link opens only in a WinCC OA UI on the same host as the viewer.

**Alternative for a later PR:** the viewer behind an authenticating reverse proxy, with an allowed-host list. That
changes `host_ok` and the viewer's security model, so it is not part of this work.

**Decided (2026-11-14):** not planned now; same-host links only.

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

**Phase B, from the documentation (2026-11-14).** Every API element the adapter uses, with its page, is listed in
`integrations/winccoa/adapter/API.md`. What the documentation settles, and what stays open:

- Question 3, in part: `WinccoaTimeFormat` is `Date` (0), `Number` (1, milliseconds) or `Temporal` (2,
  `Temporal.Instant` with nanosecond precision). The adapter asks for `Temporal` and falls back to `Date` if the
  installed API refuses it. The precision of the source times WinCC OA actually stores is still not documented.
- Question 4: `WinccoaVersionDetails` is `{api: {version}, winccoa: {display, major, minor, numeric, numeric_full,
  patch, platform, revision, version}}`.
- `WinccoaConnectUpdateType` is `Normal` (0), `Answer` (1, initial values) or `Refresh` (2, after a REDU switch or a
  DIST connection). The adapter sends `Answer` and `Refresh` values as snapshots, and a `Refresh` as a gap first.
- Questions 1 and 2 remain open. The attribute names `_original.._value`, `_original.._stime`, `_original.._bad` and
  `_original.._uncertain` are in the config reference. That `dpConnect` with attribute addresses, or
  `dpQueryConnectSingle` with a query naming one element, delivers them is not documented. Both modes are settings
  of the adapter, and a refused subscription falls back to the value alone, which becomes no context.
- Question 5 remains open: no event for a lost connection to the event manager is documented for JavaScript.
- Question 6 remains open.

**Owner decision:** the target patch and OS for phase C, and who provides the machine and licence.

## The adapter (phase B)

**Status: experimental WinCC OA adapter, awaiting validation in WinCC OA.** It has been tested against a fake of
the documented JavaScript Manager API (`test/fake-winccoa.js`) and, end to end, against the real companion and a
disposable MorletDB. It has not run in WinCC OA. Every WinCC OA function and attribute it uses is listed with its
documentation page in [`integrations/winccoa/adapter/API.md`](../integrations/winccoa/adapter/API.md), which also
marks what is unconfirmed.

**Boundary.** WinCC OA is installed and licensed separately; the JavaScript Manager is part of the Server Basic
licence. The adapter is open source (Apache 2.0) and community-maintained, with no Siemens certification or
partnership. The repository and the bundle contain no WinCC OA file: `winccoa-manager` comes with the installation
and is loaded only at run time.

**Scope.**

- The adapter **reads** the configured asset datapoint elements, and its own investigation slots. It **writes**
  only to its namespace: `<prefix>_Adapter` (status) and `<prefix>_Inv_<slot>` (investigations); every write is
  checked against it, and a configured datapoint inside the namespace is refused.
- It talks only to the companion, over loopback HTTP (`http://127.0.0.1:8770`), with the bearer token from a file
  with mode 0600. It logs the token's fingerprint, never the token. It runs no SQL and has no database access.
- It has no runtime dependencies (Node's built-in `fetch`). It is CommonJS, like the documented JavaScript template,
  and targets Node.js 22 LTS, the version installed with WinCC OA.

### Installation

1. Install and configure the companion (`integrations/winccoa/README.md`). Its `[[assets]]` and `[[datapoints]]` are
   the allow-list: the adapter's asset names and datapoint elements must match them.
2. Unpack the bundle (see below), or take `integrations/winccoa/adapter/` from a checkout. Copy that directory
   into the project's JavaScript directory as `morlet-adapter/`. The documentation names this directory in two
   ways (the project's `nodejs` directory, and `javascript/<modulename>`); use the one your installation shows
   ([API.md](../integrations/winccoa/adapter/API.md), unconfirmed item 6). There is nothing to `npm install`.
3. Copy `config/adapter.example.json` to `config/adapter.json` and edit it (see Configuration).
4. Put a copy of the companion's current token into the file named by `companion.token_file` (default `token`,
   next to the configuration). It must be owned by the user WinCC OA runs as, with mode 0600; the adapter refuses
   a file readable by others. On Windows, mode bits say nothing about access: restrict the file's ACL instead.
5. Run the bootstrap once, as a one-time JavaScript Manager:
   `-currentproj morlet-adapter/bootstrap.js --config config/adapter.json`.
6. Add the adapter as a JavaScript Manager (`WCCOAjsManager`) in the console, with the options
   `-num <n> morlet-adapter/index.js --config config/adapter.json`, start mode as needed.

**Bootstrap.** `bootstrap.js` creates the types `<prefix>Adapter` and `<prefix>Investigation` and the datapoints
`<prefix>_Adapter` and `<prefix>_Inv_<slot>` with `dpTypeCreate` and `dpCreate`. It is idempotent: what exists with
the expected structure is left alone, and what exists with another structure is reported as a conflict (exit
code 1) and not changed. It touches nothing outside the namespace. If the manager's user may not create types,
create them by hand in the datapoint type editor (PARA) as structs with these elements, then the datapoints:

- `<prefix>Adapter`: `state`, `detail`, `session`, `companion`, `version`, `tokenFingerprint`, `lastHeartbeat`,
  `health` (string); `lastAckSeq`, `queued`, `dropped`, `gaps`, `lateDropped`, `rttMs` (float).
- `<prefix>Investigation`: `trigger`, `topK`, `matchCount` (int); `startSample`, `endSample` (long);
  `collection`, `recordingId`, `requestId`, `state`, `status`, `detail`, `result`, `updated` (string).

Whether `dpTypeCreate` is permitted from a JavaScript Manager depends on the project's rights; unconfirmed.

### Configuration

`config/adapter.example.json` holds every setting with its default; keys beginning with `_` are comments.

| Setting | Default | Meaning |
|---|---|---|
| `companion.url`, `companion.token_file` | `http://127.0.0.1:8770`, `token` | loopback only; the token file is relative to the configuration |
| `namespace.prefix`, `namespace.slots` | `Morlet`, `["panel1"]` | the namespace datapoints; one slot per UI panel |
| `heartbeat_interval_s` | 10 | heartbeats, health polling and status updates |
| `limits` | 1,000 observations and 1 MiB per message, 500 datapoints, 100,000 queued | the companion's lower limits (from health) win |
| `retry` | backoff 0.5 s doubling to 30 s; investigations: 3 attempts, poll 1 s, wait 60 s | |
| `time_format` | `temporal` | `WinccoaTimeFormat.Temporal` (ns); `date` or `number` give milliseconds |
| `subscription.mode` | `connect` | `connect` (`dpConnect`) or `query` (`dpQueryConnectSingle`) |
| `subscription.*_attribute`, `quality_attributes`, `good_when` | `_original.._value`, `_original.._stime`, `bad`/`uncertain` false | unconfirmed in JavaScript; verify in phase C |
| `datapoints[].value_type` | from `dpElementType` | `bool`, `int`, `float`, `string` or `json` |

The current acquisition of each asset is taken from the companion's health report; before one is known, values are
only cached. Values keep their exact text (signed zero, NaN, 64-bit integers as BigInt); source times are sent as
integer nanoseconds in decimal strings and never rounded. A time or value that cannot be represented exactly is
sent as unreadable or replaced by a gap.

### Start and stop

- **Start.** A new session per start. The adapter sends a gap for each asset (it was not watching before), a
  heartbeat, and the initial values (update type `Answer`) as snapshots, which open at the time of the connect,
  never earlier.
- **Running.** Changes go out in batches; unacknowledged messages are resent identically. Every 10 s: health, a
  heartbeat with the RTT of `GET /v1/clock`, the status datapoint.
- **Stop.** On the manager's exit (PMON or `exit()`), the registered exit callback disconnects, sends the backlog
  and a last heartbeat, then a gap for each asset, and sets `state` to `stopped`. `process.exit()` skips this; the
  next start's gap then closes the open states at the last heartbeat.

### Investigations from a panel

Write `collection`, `recordingId`, `startSample`, `endSample` (one window, 512..32,768 samples) and optionally
`topK` into `<prefix>_Inv_<slot>`, then change `trigger`. The adapter sets `requestId` and `state = running`, then
`done` or `failed` with `status`, `detail`, `matchCount` and `result` (the companion's JSON). A newer trigger on the
same slot supersedes the older request; a result is written only while its `requestId` is the slot's current one,
and late results are counted in `<prefix>_Adapter.lateDropped`. Labels of matches annotate earlier intervals; they
are not a diagnosis.

### Troubleshooting

Look at `<prefix>_Adapter.state`, `.companion` and `.detail` first, then the WinCC OA log (lines start with
`[morlet-adapter]`).

| Symptom | Likely cause and what to do |
|---|---|
| `companion = unreachable` | The companion is not running or listens elsewhere. It binds to 127.0.0.1 only, so it must run on the WinCC OA host; check `companion.url` and `GET /v1/health`. Observations are queued and resent; nothing is lost until the queue is full, which is reported as a gap. |
| `companion = unauthorized` | The token does not match: compare the fingerprint in `.tokenFingerprint` with the companion's log. After 5 failures a minute the companion backs off for 60 s (429). To rotate, add the new token to the companion's file, then replace the adapter's file; it is re-read when it changes. |
| `companion = token_unreadable` | The token file is missing, shorter than 32 characters, or readable by others (`chmod 600`, owner = the WinCC OA user). |
| `state = error`, `companion = conflict` | A 409: a seq of this session was stored with another body. The adapter stops sending instead of retrying; restart it (a new session) and report the detail, which names the seqs. |
| `state = error`, `companion = rejected` | The companion refused a message: 403 for an asset or datapoint not configured there, 422 for an acquisition it does not know. Align the two configurations and restart. |
| `companion = version_mismatch` | The companion speaks another protocol version than `morlet-winccoa/1`; install matching versions. |
| `companion = database_unavailable` | The companion cannot reach MorletDB (health 503); see the companion's log. |
| No context for a datapoint | Values without source time or with unreadable quality become no context. `.detail` names datapoints whose subscription fell back to the value only (`value only (no source time or quality)`): the attribute addresses were refused; try `subscription.mode = "query"` and report the result for phase C. |
| Status datapoint never changes, log says `status not written` | The namespace datapoints do not exist or the manager may not write them: run the bootstrap, check the manager's user rights. |
| Investigation `evidence_unavailable` | The source interval is not searchable yet (not inside built search windows) or does not exist for the companion's role; see `detail`. |
| Investigation `permission_denied` | The companion's database role lacks a grant (docs/access-control.md). |
| Investigation `busy` or `service_failure` | More than 4 investigations at once, a statement timeout, or the database is down. |
| `.detail` says `time format temporal refused` | The installed API has no `Temporal` time format; source times are then whole milliseconds. |
| `asset ... is not configured in the companion` | The adapter's asset name differs from the companion's `[[assets]] name`. |

### Bundle and release

`node integrations/winccoa/adapter/tools/bundle.js --out dist` builds `morlet-winccoa-adapter-<version>.tar.gz`
from the current commit of a clean checkout (`git archive`, so the working tree cannot leak into it): the adapter
without its tests, the configuration templates, the bootstrap, this document, the integration README, LICENSE and
`MANIFEST.json` with the version, the commit and the SHA-256 of every file. CI (`.github/workflows/winccoa.yml`)
builds it and keeps it as a build artifact. Publishing it with releases is not wired: that waits for the owner.

### Limits of phase B

- Not run in WinCC OA. The open questions of section 8 (source time and quality through the JavaScript API,
  reconnect behaviour, the installed API version) are settled only in phase C.
- The fake implements the documented surface; where the documentation is silent, the fake's behaviour is a
  choice, and the tests cover both outcomes where the adapter depends on it (attributes accepted or refused,
  `Temporal` available or not).
- The limits are starting values; no load measurement yet.
- A 409 or a refused message needs an operator restart; there is no automatic new session.

## Defaults

Decided on 2026-11-14. The comparison is WinCC OA's own archiving, which from 3.21 uses TimescaleDB as its standard
time-series backend through the NextGen Archiver (NGA). The NGA TimescaleDB backend publishes no parameter defaults
of its own, so the table uses the documented defaults of the NGA and of the RDB archiver. The jobs differ: the
archiver stores every value change of every archived datapoint, while the companion stores context for a few
configured datapoints and answers interactive similarity searches. So the companion bounds what an operator's click
may cost, where the archiver leaves its queries unbounded by default.

| Setting | Companion | WinCC OA archiving, for comparison | Why this value |
|---|---|---|---|
| Heartbeat / stale | 10 s, stale after 30 s | RDB reports a lost connection after 15 s (`lostConnectionReportInterval`); NGA waits up to 30 s for pending writes (`timeoutForEventManReadConsistency`) | Same order as WinCC OA's own detection; the open state at a disconnect is lost for at most about 30 s |
| Clock bound | 50 ms | WinCC OA only warns at start-up when client and server clocks differ by more than its tolerance | Context is placed on samples, so a large offset puts states on the wrong part of a waveform. NTP on a LAN typically keeps hosts within a few milliseconds; 50 ms leaves margin and refuses a host without time sync |
| Observations per message | 1,000, body 1 MiB | NGA split size, recommended 200–10,000 records | Inside the recommended range; one message is one transaction |
| Query timeout | `statement_timeout` 10 s, HTTP deadline 15 s, then `running` and polling | RDB `queryTimeout` 0 (unlimited); RDB `writeTimeout` 15 s | An investigation is started from a panel and must not hold a connection without bound |
| Concurrent investigations | 4 in total, 1 per slot (a new one supersedes the old) | RDB `maxRequestThreads` 4 | The same number of parallel read connections |
| Results | `top_k` default 10, maximum 50; response 256 KiB, no samples | RDB `maxRequestLineCount` 0 (unlimited) | A ranked list for a panel; samples are read through the viewer or the SDK |
| Datapoints per adapter | 500 | none documented | Context datapoints are a few per machine; 500 covers a large cell |
| Missing quality | no context unless the datapoint opts in | NGA archives the `_online` (or `_original`) values of a datapoint element | Missing evidence is recorded as missing, never invented |
| Roles and schema | `morlet_winccoa`, `winccoa_context` (writes) and `winccoa_reader` (reads) | no comparable setting | Least privilege; the adapter never gets a role that can write waveforms |

Sources: the WinCC OA documentation for the [NGA config entries][nga-cfg], the [NGA buffering][nga-buf] and
[backend][nga-ext] settings and the [RDB config entries][rdb-cfg]; [WinCC OA selects TimescaleDB][wincc-tsdb]
(2026-04-23).

**Not measured yet:** the limits are starting values. A load measurement with the replay machine checks them before
phase C.

[nga-cfg]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/config_entries/sections/NextGenArch.html
[nga-buf]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/NGA/topics/nga_cfg_bknd_ext_set_membuf_2.html
[nga-ext]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/NGA/topics/nga_cfg_bknd_ext_sett.html
[rdb-cfg]: https://www.winccoa.com/documentation/WinCCOA/latest/en_US/RDB/RDB-08_2.html
[wincc-tsdb]: https://www.winccoa.com/news/detail/wincc-oa-selects-timescaledb-as-its-standard-time-series-database.html

## Core changes

These changes belong to the core, not to the integration. Each is a separate core PR, and each is
useful without WinCC OA. None is required to start phase A except possibly item 2.

1. **SDK context methods.** Thin wrappers over documented SQL:
   - `create_acquisition`, `add_acquisition_channel`;
   - `add_context`, `add_acquisition_context`, `revise_context`;
   - `coverage`.

   The SDK has no context, acquisition or coverage methods today (`sdk/python/src/morletdb/_client.py`). Without
   them, the companion calls the documented SQL functions with parameterized SQL, which the boundary allows. With
   them, the open-protocol collectors get the same path.

   **Decided (2026-11-14):** not before phase B; phase A works without them.
2. **A documented acquisition-visibility function** for the companion's row-level-security policy (section 1). This
   could document `morlet_acquisition_has_visible_channel`, or add a function keyed by collection and acquisition.
   It changes the API inventory and possibly SQL, so it may need a SQL version step and upgrade script.

   **Decided (2026-11-14):** the policy reads the documented `<c>_acq_channels` table directly; no core change.
3. **OPC UA reference collector.** Done in #480: the value is the state alone, and the start sample is computed
   exactly.

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
