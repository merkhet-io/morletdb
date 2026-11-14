# morlet-steward

Keeps a MorletDB database healthy on an edge machine without a DBA. Design and rules:
[docs/steward-design.md](../../docs/steward-design.md) (#287).

**Phase 1 (this directory):** automatic upkeep in one budgeted loop.

- **Jobs:** envelopes, chunk summaries, roll-ups, context values, a rolling `morlet_verify` pass every 7 days, and
  `ANALYZE` of changed tables.
- **Gating:** work runs only below the load threshold and the disk ladder.
- **Audit:** an action row for every call.
- **Breakers:** a circuit breaker per job.
- **Status:** a plain status (`morlet_steward.status`) for people and HMIs.
- **Safety:** the steward's role can build derived data and summaries and nothing that removes stored samples. Its
  database test shows that `DELETE`, `UPDATE` of samples, `TRUNCATE` and `DROP` are refused.
- **Ingest hold (P2):** at the disk reserve the steward sets `morlet_ingest_hold`, so appends that would store new
  samples fail with SQLSTATE 53100 (`disk_full`) and the reason; identical replays still pass. When space is free
  again, it releases the hold it set. A hold set by a person (`morlet_set_ingest_hold`) stays until a person
  releases it.
- **Plans (P3):** anything that removes or changes stored samples, or updates the extension, is a plan.
  - The steward proposes extension updates on its own when the server has a newer pg_morlet. A person proposes
    memory-level changes from a policy dry run (`propose-levels COLLECTION MOVES.json`; the evidence is the dry
    run's bytes freed).
  - Another person with `morlet_approver` approves it, and a person applies it with their own privileges, in one
    transaction.
  - `morlet_steward.decide` and `morlet_steward.apply` enforce the rules in SQL: no self-approval; a payload tied to
    its hash and the hash that was approved; expiry after 24 hours; applied once.
  - The steward's role can propose and read plans, but cannot decide, apply or delete them, and cannot remove
    samples itself.
- **Packaging (P4):**
  - `deploy/steward/Dockerfile` is a sidecar image. Its health check is healthy while the status is normal or
    attention.
  - `deploy/steward/compose.yaml` runs the release image with data checksums and small-disk WAL settings, plus a
    one-shot `init` and `grant`, plus the steward with the data volume read-only.
  - `deploy/steward/morlet-steward.service` is a systemd unit for package installs.
- **Metrics (#197):** `morlet-steward metrics` serves Prometheus metrics on `/metrics` (default
  `127.0.0.1:9188`), using only the standard library. A scrape only reads. It covers:
  - the overall state (0 normal, 1 attention, 2 action required) and the items by level;
  - the heartbeat age, circuit breakers, actions in the last 24 hours, and waiting plans;
  - the ingest hold, and disk free, total and database bytes;
  - the last integrity pass per collection;
  - `morlet_collection_status` per collection and window size (backlogs and bytes);
  - dead tuples and vacuum age of each collection's tables.

  Per-collection statistics are cached for `--collection-every` seconds (default 60), and every query has a
  5-second timeout. Run it as `morlet_steward`. When the database cannot be reached, it reports only
  `morlet_up 0`.
- **Not yet:** dropping derived data under disk pressure by policy, automatic retention proposals, and the multi-week
  soak on the Raspberry Pi, which starts with the home plug study D1.

```sh
pip install -r tools/steward/requirements.txt
# once, as the database owner: schema, roles (morlet_steward, morlet_approver), grants
python tools/steward/steward.py init  --dsn "postgresql://owner@host/db"   # again after updating the steward
python tools/steward/steward.py grant --dsn "postgresql://owner@host/db"      # again after new collections
psql -c "ALTER ROLE morlet_steward LOGIN PASSWORD '...'"
# the service
python tools/steward/steward.py run   --dsn "postgresql://morlet_steward@host/db" --data-path /var/lib/postgresql/data
# for people and health checks
python tools/steward/steward.py status --dsn ...     # NORMAL / ATTENTION / ACTION REQUIRED, with one action per item
python tools/steward/steward.py check  --dsn ...     # exit 0, 1 or 2
python tools/steward/steward.py reset envelopes --dsn ...   # close a breaker (approver)
python tools/steward/steward.py plans --dsn ...              # proposed plans and their state
python tools/steward/steward.py propose-levels public.line moves.json --dsn ...   # a person proposes
python tools/steward/steward.py approve 12 --dsn ...         # another person (morlet_approver) approves
python tools/steward/steward.py apply 12 --dsn ...           # and applies it, in one transaction
python tools/steward/steward.py metrics --dsn ... --listen 0.0.0.0:9188   # Prometheus scrape target (read-only)
```

- **Load gate:** `--load-per-cpu` sets it (default 0.7). A due verification still gets one batch every
  `--verify-share-every` seconds (default 900), even while the gate is closed; only disk pressure stops it. Inside Docker Desktop the steward sees the Linux VM's load,
  which is also the database's.
- **Data path:** `--data-path` is the directory whose filesystem holds the database: the data volume, mounted
  read-only into a sidecar container. Without it the steward uses `SHOW data_directory`. If it cannot read free
  space it pauses all work and says so.
- **Row security:** the steward maintains only the rows it may see. Give `morlet_steward` `BYPASSRLS` if some are
  hidden from it.
- **Tests:**
  - `test_policy.py`: every decision, no database.
  - `test_steward_db.py`: needs a superuser DSN in `MORLET_STEWARD_TEST_DSN`; it creates and drops the database
    `morlet_steward_test`.
