# IU1-E: search latency under edge-sized container limits (2 CPUs, 512 MB)

Evidence for the paper. Public data only (the collections of `industrial_iu1` and `industrial_iu2`).

| File | Content |
|---|---|
| `PROTOCOL_AND_RESULTS.md` | Rules written on 2026-11-11 before measuring, the two configurations, results, limits |
| `compose.yaml` | Configuration `ref`: no CPU or memory limit, `shared_buffers=1GB`, `work_mem=64MB`, 4 parallel workers per query |
| `compose.edge.yaml` | Override for `e512`: `cpus: 2`, `mem_limit: 512m`, `memswap_limit: 512m`, `shared_buffers=64MB`, `max_connections=20`, `maintenance_work_mem=32MB` (as EX1 `e512`) |
| `edge.py` | Harness: draws the queries (seed 20261111), runs every task twice, writes p50 and p95 per round |
| `edge_queries.py` | Writes the drawn queries without a database |
| `results/edge_queries.json` | The queries: recording, sample interval and metadata filter per task (E1 200, E2 50 exact and 50 probes, E3 200, E4 30 runs of 24 searches, E5 100) |
| `results/edge_ref.json`, `results/edge_e512.json` | p50 / p95 in ms for both rounds, and the PostgreSQL settings read back from the server |

## Reproduce

Needs the loaded collections `public.bosch` (IU1, with probes enabled) and `public.toollife` (IU2), and
`windows.json` from `iu1.py rms`.

```sh
docker compose up -d db                                    # ref
python -I edge.py ref
docker compose -f compose.yaml -f compose.edge.yaml up -d db   # e512: same volume, new limits
python -I edge.py e512
docker inspect <db container> --format '{{.State.OOMKilled}} {{.RestartCount}}'
docker exec <db container> cat /sys/fs/cgroup/memory.events /sys/fs/cgroup/memory.peak
```

Memory figures of the `e512` run (read after the second round): `OOMKilled` false, no restart, cgroup
`oom` 0, `oom_kill` 0, `max` 0, `memory.peak` 403,795,968 bytes, `anon` 4,739,072, `file` 375,451,648,
`shmem` 73,519,104.

Host: Windows 11 laptop, Docker Desktop (WSL2), 16 logical CPUs and 7.2 GB for the Docker VM, NVMe disk.
MorletDB main at 088f03b (SQL 0.3.8), PostgreSQL 17.6, pgvector 0.8.7. One client, warm cache.
