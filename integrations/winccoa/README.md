
# MorletDB WinCC OA integration: phase A (companion and replay)

**Status: experimental, awaiting validation in WinCC OA.** This is phase A of [docs/winccoa.md](../../docs/winccoa.md)
(#474): the Python companion, a replay machine and their tests. No WinCC OA is involved yet: the adapter (phase B)
and native acceptance (phase C) are not built. There is no Siemens certification or partnership; WinCC OA is
installed and licensed separately.

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
  `POST /v1/investigations`, `GET /v1/investigations/{request_id}`, `GET /v1/health`.
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
  database is unavailable). Readiness (`morlet_coverage` of the window size) and search mode (`exact`/`probes`)
  are reported with every result that got as far as a search.

## Proposed defaults standing in for owner decisions

Each is marked "proposed default, owner decision pending" in the code and in `companion.example.toml`.

| Decision | Proposed default used | Where |
|---|---|---|
| 1. Option (a) and the schema name | `morlet_winccoa` | `Config.schema` |
| 2. Heartbeat interval | 10 s (health calls the adapter stale after 3 intervals) | `Clock.heartbeat_interval_s` |
| 3. Clock bound | 50 ms | `Clock.bound_ms` |
| 3. Opt-in observed-time basis allowed at all | not allowed (`false`) | `Clock.allow_observed_time_basis` |
| 5. One login role with two group roles | yes: `winccoa_context`, `winccoa_reader` | `Config.context_role`, `reader_role`, `schema.install` |
| 6. Phase-A limits | the table of decision 6 | `Limits` |
| Core change 2: may the policy read `<c>_acq_channels` directly | yes (no core change) | `schema._visible` |
| Values without quality attributes (open question 2) | no context (`bad_quality`), per datapoint opt-in | `Datapoint.accept_missing_quality` |

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
  before sending it. Re-deriving after a clock correction (an explicit operator action in the design) is not
  implemented in phase A.
- **Intervals derived before the end:** an interval closed before the acquisition is finished keeps its extent,
  even past the later end (segments use only committed samples); only intervals closed after the end is known are
  clipped. Revising them with `morlet_revise_context` at the end is not implemented.
- **Cancellation:** a superseded request's backend is cancelled with `pg_cancel_backend`, repeated for up to 1 s
  in case it is between statements; its result is `service_failure` with `superseded_by`.
- **Acquisition identity in observations:** the adapter names the acquisition in every observation; the companion
  checks that it exists in the asset's collection, is visible, and has the asset's machine. How the real adapter
  learns it (for example from health, or from a datapoint the writer publishes) is for phase B.
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

## Limits of this phase

- No WinCC OA: the adapter is simulated (`replay/adapter_sim.py`). Source time, quality attributes and reconnect
  behaviour of the real JavaScript Manager API are open questions of docs/winccoa.md section 8.
- The synthetic machine shows that the path works and that repeated patterns are found; it says nothing about real
  machines. Labels of matches are annotations of earlier intervals, not a diagnosis.
- The limits are starting values; they have not been measured under load yet.
- Derivation recomputes an acquisition from all of its observations on every pass while it has an open state (every
  `derive_interval_s`, 5 s); an acquisition the writer never finishes stays in that loop. Cost per pass grows with
  its observations (about 8,640 heartbeats a day at 10 s); not measured yet.
