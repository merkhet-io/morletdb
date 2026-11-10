# MorletDB file format 1

**Status: frozen** in the release that first ships it (#351). It is the format of the embedded edition
([embedded-edition.md](embedded-edition.md)): a lossless waveform recording, with its timing and metadata, in
one SQLite file that any SQLite tool can open. It uses the same chunk encoding as the server
([chunk-format.md](chunk-format.md)).

Two implementations read it: the library (`crates/morlet-sqlite`, also inside the Python SDK) and the reference
reader `reference/python/morletfile.py` (Python standard library only, no MorletDB code). The corpus in
`testdata/morlet-file-1` covers every chunk kind, and both implementations must read it bit for bit (CI).

## The file

An SQLite 3 database with:

- `PRAGMA application_id` = `0x4D524C54` ("MRLT") and `PRAGMA user_version` = `1`. A reader refuses any other
  application id, and any version it does not know.
- Any journal mode. The library writes in WAL mode; a file copied while it has a `-wal` file next to it needs
  that file too, or a checkpoint first. Files exchanged as one file should use `journal_mode=DELETE`.
- Exactly these tables (more tables or columns are an extension; readers ignore them, and writers of format 1
  never rely on them):

```sql
CREATE TABLE morlet_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
CREATE TABLE recordings(
  recording_id TEXT PRIMARY KEY,
  sample_rate_hz REAL NOT NULL CHECK(sample_rate_hz > 0 AND sample_rate_hz < 9e999),
  channel TEXT NOT NULL CHECK(channel <> ''),
  unit TEXT NOT NULL,
  sample_quantum REAL NOT NULL DEFAULT 1 CHECK(sample_quantum > 0 AND sample_quantum < 9e999),
  metadata TEXT NOT NULL DEFAULT '{}' CHECK(json_valid(metadata)),
  end_sample INTEGER NOT NULL DEFAULT 0 CHECK(end_sample >= 0)
) WITHOUT ROWID;
CREATE TABLE chunks(
  recording_id TEXT NOT NULL REFERENCES recordings(recording_id),
  start_sample INTEGER NOT NULL CHECK(start_sample >= 0),
  sample_count INTEGER NOT NULL CHECK(sample_count BETWEEN 1 AND 32768),
  encoded BLOB NOT NULL,
  summary BLOB,
  PRIMARY KEY(recording_id, start_sample)
) WITHOUT ROWID;
```

## Meaning

- **`morlet_meta`:** at least `format` = `morletdb-sqlite`, `format_version` = `1` and `created_by` (the
  writer and its version). Other keys are free.
- **A recording** is one uniformly sampled channel. Sample `i` is at `i / sample_rate_hz` seconds after
  sample 0.
  - `sample_quantum`: the value grid the writer encoded with. It is a compression hint only; a reader never
    needs it.
  - `metadata`: a JSON object.
  - `metadata.start`: the UTC time of sample 0 as ISO 8601 (with `Z`), when known.
  - `metadata.start_ns`: the same as integer nanoseconds since the Unix epoch. It is exact; prefer it.
  - `end_sample`: the number of samples stored.
- **Chunks:**
  - The chunks of a recording cover `[0, end_sample)` contiguously, without gaps or overlaps: the first
    starts at 0, each next one at the previous `start_sample + sample_count`, and the last ends at
    `end_sample`.
  - `encoded` is one encoded chunk (chunk format versions 2 and 3) that decodes to exactly `sample_count`
    float64 samples, checksum verified.
  - `summary` is NULL or a version-1 chunk summary of exactly those samples (chunk-format.md).
- **Samples** are finite float64 within ±1e9. Every bit is significant, including the sign of zero.

## Compatibility promise

- A file of format 1 stays readable by every later version of the library and of the reference reader.
- A later format gets a new `user_version`. The library reads it and every earlier format; it never changes a
  file's format unless asked.
- Chunk format versions 2 and 3 stay decodable (chunk-format.md, Compatibility). A new chunk version needs a
  new file format version, so a format-1 reader never meets a chunk it cannot decode. Version 4 (Grizzbit 2,
  under study B5) is therefore never written into a format-1 file, and the library refuses it when reading
  one.
- Files are not compatible with format 0, which was only on the development branch of #352 and was never
  released.

## Reading without MorletDB

1. Open the file with SQLite and check the application id and the version.
2. List `recordings`.
3. For an interval `[a, b)`, select the chunks with `start_sample < b AND start_sample + sample_count > a`
   in `start_sample` order.
4. Decode each chunk (chunk-format.md) and check its length and the contiguity.
5. Cut the edges.

`reference/python/morletfile.py` does exactly this in about 250 lines:

```sh
python reference/python/morletfile.py testdata/morlet-file-1/corpus.morlet                  # recordings
python reference/python/morletfile.py testdata/morlet-file-1/corpus.morlet edge-values 0 9   # samples
```
