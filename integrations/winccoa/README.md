
# MorletDB WinCC OA integration: companion, replay and adapter

**Status: experimental WinCC OA adapter, awaiting validation in WinCC OA.** This is phases A and B of
[docs/winccoa.md](../../docs/winccoa.md) (#474): the Python companion, a replay machine, the adapter that runs as a
WinCC OA JavaScript Manager, and their tests. No WinCC OA has been involved: the adapter is tested against a fake of
the documented JavaScript Manager API and against the real companion. Native acceptance in WinCC OA (phase C) is not
done. There is no Siemens certification or partnership; WinCC OA is installed and licensed separately.

Open source under the Apache License 2.0, like the rest of MorletDB ([docs/licensing.md](../../docs/licensing.md)).
It is community-maintained.

The companion uses only the public core: the MorletDB Python SDK and documented SQL functions. Context it writes is
ordinary context rows (`<collection>_ctx`), readable and searchable with the core alone.

| Path | What it is |
|---|---|
| `companion/src/morlet_winccoa/` | the service: `config`, `schema`, `observations`, `derive`, `investigate`, `auth`, `server`, CLI |
| `companion/tests/` | tests against a disposable MorletDB in Docker (pinned release image) |
| `companion/companion.example.toml` | every setting, with the design's proposed defaults |
| `replay/` | the synthetic machine, its waveform writer, a simulated adapter and `replay.py` |
| `adapter/` | the adapter (phase B): a WinCC OA JavaScript Manager script, its bootstrap, `API.md` (every WinCC OA API element used, with its documentation page, confirmed or not), tests and the bundle tool |
| `compose.yaml`, `Dockerfile`, `deploy/` | MorletDB (pinned image) and the companion, with the replay |

## What the companion does

- **Observations** (decision 1, option (a)): the schema `morlet_winccoa` holds `observation` (append-only; the
  exact value text, value type, source time and status, raw quality attributes, observed and received times, kind,
  `(adapter_session, seq)` and a digest), `derivation` (the context batch an observation produced, or the reason it
  produced none) and `acquisition_end` (written by the waveform writer when an acquisition is finished).
- **Row-level security:** each table's policy shows and accepts a row only when its acquisition has a channel the
  caller can see in `<collection>_acq_channels`, which is itself under the core's policy once
  `morlet_enable_row_security` is applied. Installed by the administrator; the companion's login role gets the group
  roles `winccoa_context` (insert observations, write context) and `winccoa_reader` (the analyst grants). Neither
  can write recordings or chunks, run DDL, or update or delete observations.
- **Derivation** (decisions 2 and 3): a state holds from its change until the next change of its key, half-open in
  acquisition time `t = source_ns − origin_ns`. Only closed intervals become context, through
  `morlet_add_acquisition_context` (the server maps time to samples, ceil rule, exact `numeric`; the companion
  never rounds). Batch IDs are `winccoa:<acquisition>:<key>:<sha256 of [t_start, t_end, value_type, value_text]>`.
  Reasons for no context: `open`, `outside_recording`, `untimed`, `bad_quality`, `clock_exceeded`,
  `inconsistent_start`, plus `unchanged`, `out_of_order`, `unrepresentable` and `rejected` (see deviations).
- **Protocol** `morlet-winccoa/1` (decision 4) on `127.0.0.1:8770`: `POST /v1/observations`,
  `POST /v1/investigations`, `GET /v1/investigations/{request_id}`, `GET /v1/health`, `GET /v1/clock` (the
  companion's clock, without database work, for the adapter's heartbeat RTT).
- **Authentication** (decision 5): bearer token from a 0600 file (one or two tokens), `hmac.compare_digest`,
  fingerprints only in logs, 5 failures a minute then a 60 s back-off.
- **Limits** (decision 6): all configurable, see `companion.example.toml`.

### Protocol notes

- Every body carries `"schema": "morlet-winccoa/1"`; another value gets 400 with `supported`.
- Times are integer nanoseconds as **decimal strings** (a JavaScript number is exact only to 2^53).
- An observation: `seq`, `kind` (`change`, `snapshot`, `heartbeat`, `gap`), `acquisition`, then `dpe` (a configured
  datapoint element; `change` and `snapshot`) or `asset` (`heartbeat`, acquisition-wide `gap`), `value_text` and
  `value_type` (`bool`, `int`, `float`, `string`, `json`), `source_ns` or `source_status` (`missing`,
  `unreadable`), `quality` (raw attributes; `good: true|false` gives the verdict) or
  `quality_status: "unreadable"`, `observed_ns`, and for heartbeats optionally `rtt_ns`.
- A message is stored in one transaction: 200 with the session's `highest_seq` only after COMMIT; 409 (listing the
  `seqs`) when any `(session, seq)` was stored with another body, and then nothing of the message is stored; 422 when
  the acquisition does not exist in the asset's collection, is not visible, or belongs to another machine.
- The current acquisition of each asset (the newest one not finished) is reported by `GET /v1/health`, with its
  clock estimate and open states ("open since t").
- Investigations: `request_id` (canonical UUID), `slot`, `collection`, `recording_id`, `start_sample`,
  `end_sample` (one window, 512..32,768 samples), optional `top_k` (1..50). The POST waits up to the HTTP deadline
  (15 s) and then returns 202 `running`; poll the GET. Every result carries `request_id`, `slot`, `status`,
  `current` and `superseded_by`; the adapter writes a result only if its `request_id` is still the slot's current
  one (`replay/adapter_sim.py`, `Adapter.accept`).
- Status: `ok`, `no_matches`, `evidence_unavailable`, `permission_denied`, `service_failure` (HTTP 503 when the
  database is unavailable), `response_too_large` (HTTP 200; the result does not fit `response_bytes` even without
  source context and matches). Readiness (`morlet_coverage` of the window size) and search mode (`exact`/`probes`)
  are reported with every result that got as far as a search.
- The HTTP body of an investigation response is at most `response_bytes` (256 KiB), metadata included. What was
  cut to fit is listed in `truncated` and counted in `omitted` ([docs/winccoa.md](../../docs/winccoa.md), section 4).

## Defaults (decided 2026-11-14)

The reasons and the comparison with WinCC OA's own archiver are in [docs/winccoa.md](../../docs/winccoa.md#defaults).

| Decision | Default | Where |
|---|---|---|
| 1. Option (a) and the schema name | `morlet_winccoa` | `Config.schema` |
| 2. Heartbeat interval | 10 s (health calls the adapter stale after 3 intervals, 30 s) | `Clock.heartbeat_interval_s` |
| 3. Clock bound | 50 ms | `Clock.bound_ms` |
| 3. Opt-in observed-time basis | not allowed (`false`) | `Clock.allow_observed_time_basis` |
| 5. Roles | one login role with two group roles, `winccoa_context` and `winccoa_reader` | `Config.context_role`, `reader_role`, `schema.install` |
| 6. Limits | the table of decision 6: starting values, to be checked by a load measurement | `Limits` |
| Core change 2: the policy reads `<c>_acq_channels` directly | yes (no core change) | `schema._visible` |
| Values without quality attributes | no context (`bad_quality`); a per-datapoint opt-in | `Datapoint.accept_missing_quality` |

Core change 1 (SDK context methods) was not made: the companion calls the documented SQL functions with
parameterized SQL. No core change was needed for phase A.

## Deviations from docs/winccoa.md, and clarifications

- **`acquisition_end` table** (not in the design's table list): "once the writer marks the acquisition finished"
  needs a marker, and acquisitions are immutable. The writer inserts the end in acquisition time
  (`observations.mark_finished`); it is idempotent and refuses a different end.
- **Additional reasons:** `unchanged` (a repeated value, including an identical snapshot, is not a boundary),
  `out_of_order` (an observation dated before the key's latest boundary; stored, not applied, so derived intervals
  of one key never overlap), `unrepresentable` (NaN or an infinity has no JSON form; kept as text, ends the
  previous state) and `rejected` (the server refused the batch, for example because another writer stored
  overlapping context for the same key; the server's message is kept in `detail`, and the other batches are still
  added). `inconsistent_start` is stored as
  a reason, as the design reports it.
- **Gap end:** the open state ends at the last committed *contact* of the acquisition before the gap: a heartbeat
  or any other observation (the design says the last heartbeat; a later committed observation is a later proof
  that the adapter was connected).
- **Untimed values:** a value without source time ends the previous state at an unknown time, so the previous state
  is not derived either (`untimed`), rather than guessing an end.
- **Clock:** before the first heartbeat of an acquisition there is no estimate, and intervals are not derived
  (`clock_exceeded`). The estimate is `|received_ns − observed_ns| + rtt_ns/2 + daq_bound`, from the latest
  heartbeat before each opening and closing observation, so the adapter must take a heartbeat's `observed_ns` just
  before sending it. `received_ns` is taken when the request arrives, before the body is read and the database
  connection is opened. Re-deriving after a clock correction (an explicit operator action in the design) is not
  implemented in phase A.
- **Intervals derived before the end:** an interval closed before the acquisition is finished keeps its extent,
  even past the later end (segments use only committed samples); only intervals closed after the end is known are
  clipped. Revising them with `morlet_revise_context` at the end is not implemented.
- **Cancellation:** a superseded request's backend is cancelled with `pg_cancel_backend`, repeated for up to 1 s
  in case it is between statements; its result is `service_failure` with `superseded_by`.
- **Acquisition identity in observations:** the adapter names the acquisition in every observation; the companion
  checks that it exists in the asset's collection, is visible, and has the asset's machine. The adapter (phase B)
  takes it from `GET /v1/health` (`current_acquisition` of each asset), polled with every heartbeat.
- **`no_matches` and `evidence_unavailable`:** a source recording that does not exist and one hidden by row
  security both give `evidence_unavailable` (they cannot be told apart, by design of row security); a missing
  privilege gives `permission_denied`.
- **Replay context:** the replay sends the recorded first values as `change`, not `snapshot`, because it runs faster
  than real time (a snapshot opens at `max(source time, reconnect time)`). Snapshots are tested separately.

## Running the tests

They start the pinned image `ghcr.io/merkhet-io/morletdb:0.3.14-alpha.1` in Docker on a free loopback port
(container `winccoa-test-<random>`, 1 GB and 2 CPUs), generate every password and token at test time, and remove
the container when they finish. Without Docker they fail; none is skipped.

```sh
python3.11 -m venv .venv && .venv/bin/pip install ./sdk/python
cd integrations/winccoa/companion/tests
../../../../.venv/bin/python -m unittest -v test_offline test_machine test_context test_service test_rls
```

The adapter (Node.js 22 or later; the end-to-end test also needs the venv above and Docker):

```sh
(cd integrations/winccoa/adapter && node --test test/*.test.js)    # unit tests on the fake WinCC OA API
(cd integrations/winccoa/adapter/test/e2e && ../../../../../.venv/bin/python -m unittest -v test_e2e)
node integrations/winccoa/adapter/tools/bundle.js --out dist        # the bundle, from the committed state
```

## The adapter (phase B)

`adapter/` is the client side of `morlet-winccoa/1` that `replay/adapter_sim.py` simulates, as a WinCC OA JavaScript
Manager script (CommonJS, Node.js 22, no dependencies). Installation, configuration, start and stop, the namespace
datapoints and troubleshooting are in [docs/winccoa.md, The adapter](../../docs/winccoa.md#the-adapter-phase-b).

| Path | What it is |
|---|---|
| `adapter/index.js`, `adapter/bootstrap.js` | the manager scripts: the adapter, and the one-time idempotent bootstrap of its namespace |
| `adapter/src/` | `config`, `client` (HTTP, token file), `outbox` (seq, batching), `subscriptions`, `investigations`, `namespace`, `adapter`, `bootstrap` |
| `adapter/config/adapter.example.json` | every setting with its default |
| `adapter/API.md` | every WinCC OA function, type and attribute used, with its documentation page; what is unconfirmed |
| `adapter/test/` | unit tests on `fake-winccoa.js` (exactly the API.md surface), and `e2e/` against the real companion |
| `adapter/tools/bundle.js` | the reviewable tarball with a manifest of file hashes |

What it does, matching the simulation: one session per start and a seq per observation; batches of at most 1,000
observations and 1 MiB, resent identically until acknowledged; a 409 or another refusal stops the flow in an error
state rather than looping; snapshots (never history) for initial values, after a REDU or DIST refresh, for a new
acquisition and after a queue overflow; gaps at start, overflow, refresh, unrepresentable values and clean shutdown;
heartbeats every 10 s with RTT; investigations from its slot datapoints, written back only while their request_id
is the slot's current one (late results dropped and counted); status and health in its namespace datapoint. It
reads asset datapoints and writes only to its namespace.

Not confirmed from the documentation, and therefore settings (see `adapter/API.md`): whether `dpConnect` with
attribute addresses or `dpQueryConnectSingle` delivers source time (`_original.._stime`) and quality
(`_original.._bad`, `_original.._uncertain`). A refused subscription falls back to the value alone, which the
companion stores as untimed and derives no context from.

## Compose (Linux)

```sh
sh integrations/winccoa/deploy/secrets.sh            # secrets/ with mode 0600 files; prints only a token fingerprint
export MORLET_UID=$(id -u) MORLET_GID=$(id -g)
docker compose -f integrations/winccoa/compose.yaml up -d db
docker compose -f integrations/winccoa/compose.yaml run --rm setup       # collection, login role, companion schema
docker compose -f integrations/winccoa/compose.yaml up -d companion
docker compose -f integrations/winccoa/compose.yaml run --rm replay      # history, current acquisition, investigation
```

The companion and the replay use host networking, so the companion listens on the host's 127.0.0.1:8770 only.

## Limits

- No WinCC OA: the replay uses the simulated adapter (`replay/adapter_sim.py`), and the real adapter has run only on
  a fake of the documented API. Source time, quality attributes and reconnect behaviour of the real JavaScript
  Manager API are open questions of docs/winccoa.md section 8, for phase C.
- The synthetic machine shows that the path works and that repeated patterns are found; it says nothing about real
  machines. Labels of matches are annotations of earlier intervals, not a diagnosis.
- The limits are starting values; they have not been measured under load yet.
- Derivation recomputes an acquisition from all of its observations on every pass while it has an open state (every
  `derive_interval_s`, 5 s); an acquisition the writer never finishes stays in that loop. Cost per pass grows with
  its observations (about 8,640 heartbeats a day at 10 s); not measured yet.
