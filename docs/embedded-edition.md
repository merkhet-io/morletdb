# Embedded edition: MorletDB in one SQLite file (experimental, #351)

**Status: experimental.** The edition is experimental. The file format ([file-format.md](file-format.md),
format 1) is frozen with the release that first ships it, with a compatibility promise. The server edition
(PostgreSQL with pg_morlet) is the supported product. Nothing here is a benchmark statement yet (see "Evidence").

The embedded edition stores recordings in **one SQLite file**, in the program that uses it: no server, no
installation, no administration. It uses the same chunk encoding and summaries as pg_morlet (`morlet-core`), so the
two editions hold the same bytes for the same samples and compute the same interval statistics.

| | Embedded edition (SQLite) | Server edition (PostgreSQL + pg_morlet) |
|---|---|---|
| Where | inside one program: logger, test bench, device, laptop, notebook | a shared database for a line or a plant |
| Writers | one at a time per file (readers alongside) | many, in parallel |
| Access | the file's permissions | roles, row-level security |
| Operations | copy the file | replication, point-in-time recovery, monitoring, the steward |
| Search | not yet | envelope and probe search, memory levels, roll-ups |
| Interval statistics, 128,000 samples | about 0.12 ms in-process (development figure) | about 0.41 ms end to end (#349) |

Use it to record next to the machine, to keep a test run or a failure case as a file, or to analyse recordings
without Docker. Use the server edition when several machines, people or agents share the data.

## Use

Python (the same package as the server client; the platform wheels carry the library):

```python
import morletdb
with morletdb.open("line3.morlet") as db:                # created if missing
    db.ingest("pump-7/accel-x", samples, sample_rate_hz=25_600, channel="accel-x", unit="m/s^2",
              start_time=datetime(2026, 3, 1, 8, 0, tzinfo=timezone.utc), metadata={"asset": "pump-7"})
    db.append("pump-7/accel-x", len(samples), more)       # at the recording's end; a replay is a no-op
    iv = db.read("pump-7/accel-x", 40_960, 45_056)        # the stored samples, bit for bit
    st = db.stats("pump-7/accel-x", 0, 1_000_000)         # count, min, max, mean, rms
```

Command line (`crates/morlet-sqlite`, samples as raw little-endian float64 files):

```sh
morlet-sqlite create line3.morlet pump-7/accel-x 25600 --channel accel-x --unit m/s^2
morlet-sqlite append line3.morlet pump-7/accel-x 0 samples.f64 --chunk-size 4096
morlet-sqlite stats  line3.morlet pump-7/accel-x 0 1000000
morlet-sqlite read   line3.morlet pump-7/accel-x 40960 45056 out.f64
morlet-sqlite info   line3.morlet
```

Between a file and a server (Python):

```python
with morletdb.Client.connect("postgresql://...") as server:
    server.import_file("line3.morlet", "public.signals")          # file -> server; continues where it stopped
    server.export_file("public.signals", "case-1187.morlet", ["pump-7/accel-x"])   # server -> file
```

The receiving side encodes with the sending side's chunk boundaries. Each batch of 256 chunks is checked by what
lossless means, the samples: a hash of the decoded float64 values (every bit, including the sign of zero) on both
sides (#356). The chunk bytes need not match, so a file written by an older or newer encoder, which may store the
same samples as other bytes, imports and exports as well. When both sides report the same encoder version (the
file's `created_by`, the server's `morlet_version()`) the copied chunk bytes are compared too; with
`report=[...]` each transfer appends a `TransferCheck` per recording (samples copied and verified; chunk bytes
`identical`, `different` or `not compared`). Equal versions with different bytes give a warning, not an error: the
samples are verified. A recording that already exists continues from its end if its samples equal the other side's
first samples (identical chunk bytes settle that without decoding); otherwise the transfer is refused. Recordings
whose raw samples a memory level removed cannot be exported (they are no longer lossless).

Rust: `morlet_sqlite::Store`. C and every language that calls C (C++, Go, .NET, LabVIEW, Node): the shared
library with `include/morlet_sqlite.h` (ABI 1: open, create, append, append with chunk lengths, read, stats,
recordings as JSON, last error); `tests/c/smoke.c` is a complete example.

## Guarantees

- **Lossless:** every chunk is decoded again after encoding and compared bit for bit before it is written.
- **Durable:** an append is one SQLite transaction in WAL mode with `synchronous=FULL` (default): once it returns,
  it survives a power loss. `durability="normal"` may lose the newest appends on a power loss, never older ones.
- **Contiguous:** an append starts at the recording's end; an identical replay of stored samples is a no-op;
  gaps and different values are refused, and a refused append changes nothing.
- **Same as the server:** for the same samples and chunk size, the chunk bytes, the summaries and the interval
  statistics equal pg_morlet's bit for bit (`crates/morlet-sqlite/tests/cross/cross_pg.py`: 293 chunks, 200
  intervals, 0 differences).

## File format

Format 1 is specified in [file-format.md](file-format.md):
- **The file:** an ordinary SQLite database with `recordings` and `chunks` tables, and an application id and
  version.
- **Promise:** a format-1 file stays readable by every later version.
- **Without MorletDB:** the reference reader `reference/python/morletfile.py` (Python standard library only)
  reads it, and CI checks both readers against a corpus that covers every chunk kind
  (`testdata/morlet-file-1`).

## Distribution (plan)

| Channel | What | State |
|---|---|---|
| Python wheels | `morletdb` with `libmorlet_sqlite` in `_lib/`, one wheel per platform for every Python ≥ 3.10 (C ABI via ctypes): Linux x86_64 and aarch64 (manylinux; Raspberry Pi OS 64-bit), macOS arm64 | built and tested from the installed wheel in `sdk-wheels.yml`; attached to GitHub releases |
| Command line and C library | `morlet-sqlite-<platform>.tar.gz`: the `morlet-sqlite` binary, `libmorlet_sqlite` and its C header, for the same platforms, with checksums and build provenance | built in `sdk-wheels.yml`, smoke-tested from the unpacked archive (command line and a C program); attached to GitHub releases |
| Rust | the `morlet-sqlite` crate | after `morlet-core` is published |
| PyPI | `pip install morletdb` | the owner's decision, together with the server client |

Next in the edition itself: envelope search, Windows and macOS x86_64 builds, a Homebrew formula, and a SQLite
loadable extension for SQL access from other SQLite tools.

## Evidence before claims

Before any number goes public: a frozen B3-style protocol, same session, on the Mac and the Raspberry Pi 3B+
(ingest, interval reads, statistics, storage), and an FD1- and L1-style full-disk and power-loss test of the file.
