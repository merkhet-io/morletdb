# Exchange format: recordings as Apache Parquet

`tools/ingest/morlet_export.py` writes recordings to a Parquet file in the **morlet-exchange** format, version 1.
`morlet_import.py` reads it back (rebuilding search), `parquet_import.py` reads it as its chunk layout, and any
Parquet reader (DuckDB, pandas, Apache Arrow, Spark) can explore it.

```sh
python -m pip install -r tools/ingest/requirements.txt
python tools/ingest/morlet_export.py week.parquet --collection public.bearings --metadata-filter '{"bearing":"1"}'
python tools/ingest/morlet_import.py week.parquet --collection public.copy --create      # into another database
python tools/ingest/morlet_export.py --verify week.parquet --collection public.copy    # sample-for-sample check
```

## Schema (version 1)

One row per chunk of at most 32,768 samples, rows ascending by `recording_id` (byte order) and `start_sample`:

| Column | Type | Meaning |
|---|---|---|
| `recording_id` | string | the recording's identifier in its collection |
| `sample_rate_hz` | float64 | samples per second |
| `channel` | string | channel name |
| `unit` | string | unit of the values |
| `metadata` | string | the recording's metadata as a JSON object (keys sorted) |
| `sample_quantum` | float64 | the sample grid the values are stored on (1 for integers, e.g. 0.001) |
| `extractor` | string | the segment representation the recording was stored with |
| `start_sample` | int64 | offset of the chunk's first sample in the recording (sample offsets, not times) |
| `samples` | list&lt;float64&gt; | the samples |

The file's key-value metadata `morletdb.exchange` is a JSON object: `format` (`morlet-exchange`), `version` (1),
`source_collection`, `exported_utc`, `morletdb_release`, and per recording its sample count, the window, stride
and normalization it was stored with, and the SHA-256 of its samples (little-endian float64, in order).

## What is preserved, and what is not

- **Samples:** exactly, as stored in MorletDB (decoded, on their quantum grid). `morlet_import.py` checks every
  recording against its SHA-256 before importing, and `--verify` checks a collection against the file. Tested
  on 2026-11-02: bearing 1 of IMS test 2 (984 recordings, 20.2 M samples, 22.6 MB) exported, re-imported into
  another database and verified identical; recording ids, rates, channels, units, metadata and quanta also
  identical.
- **Metadata:** as JSON; key order is not preserved (PostgreSQL `jsonb` does not keep it either).
- **Not the source files.** An export reproduces the stored samples, not the original WAV, EDF or miniSEED
  file, nor MorletDB's compressed chunks.
- **Not the derived data.** Segment features and search envelopes are rebuilt on import (`morlet_import.py`
  builds envelopes; `parquet_import.py` stores samples and features only).
- **Settings:** an import uses the importer's window, stride and normalization; the values a recording was
  stored with are in the file's metadata.

## Explore an export with DuckDB

DuckDB reads the file directly (tested with DuckDB 1.1.3):

```sql
-- recordings: chunks, samples, rate and start time
SELECT recording_id, count(*) AS chunks, sum(len(samples)) AS samples, any_value(sample_rate_hz) AS rate_hz,
       json_extract_string(any_value(metadata), '$.start') AS start
FROM 'week.parquet' GROUP BY recording_id ORDER BY start;

-- daily peak level across snapshots
SELECT json_extract_string(metadata, '$.start')::TIMESTAMPTZ::DATE AS day,
       count(DISTINCT recording_id) AS snapshots,
       max(list_max(list_transform(samples, x -> abs(x)))) AS peak
FROM 'week.parquet' GROUP BY day ORDER BY day;

-- samples with their offsets
SELECT recording_id, start_sample + i - 1 AS sample, samples[i] AS value
FROM (SELECT recording_id, start_sample, samples, generate_subscripts(samples, 1) AS i
      FROM 'week.parquet' WHERE recording_id = 'B1.20040217T071239')
WHERE i <= 5;

-- the format, the source and a recording's checksum from the file's metadata
SELECT json_extract_string(m, '$.format') AS format, json_extract(m, '$.version') AS version,
       json_extract_string(m, '$.source_collection') AS source,
       json_extract_string(m, '$.recordings."B1.20040217T071239".sha256') AS sha256
FROM (SELECT decode(value) AS m FROM parquet_kv_metadata('week.parquet') WHERE decode(key) = 'morletdb.exchange');
```

On the IMS export, the daily peak of bearing 1 rises from about 0.5 to 1.2 over 17–18 February and reaches the
sensor's limit of 5.0 on the last day.

To keep exports in, or import recordings from, S3-compatible object storage, see [object storage](object-storage.md).

## Versioning

A reader must check `format` and `version` and refuse what it does not know; both MorletDB readers do. A
future version adds columns or metadata keys only with a new version number.
