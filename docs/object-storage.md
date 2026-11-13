# Object storage (S3-compatible)

`tools/ingest/morlet_s3.py` imports recording files from an S3-compatible bucket and exports
[exchange files](exchange-format.md) to one. It builds on `morlet_import.py` and `morlet_export.py`; MorletDB
itself does not read from object storage, and query serving from a cold tier is not part of this.

```sh
python -m pip install -r tools/ingest/requirements.txt -r tools/ingest/requirements-s3.txt
export DATABASE_URL=postgresql://...
# credentials: boto3's usual sources (environment, ~/.aws, instance roles); for a non-AWS service also:
export AWS_ENDPOINT_URL=https://s3.example.internal

python tools/ingest/morlet_s3.py import s3://recordings/bench/2024-05/ --collection public.bench --create
python tools/ingest/morlet_s3.py export s3://archive/bench/2024-05.parquet --collection public.bench \
    --metadata-filter '{"rig":"B2"}'
```

Do not put credentials in command lines, files you commit or URLs; the tool never prints them.

## Import

- **Which objects:** every object under the prefix with a suffix the importer reads (`.wav`, `.edf`, `.mseed`,
  `.miniseed`, `.msd`, `.tdms`, `.csv`, `.parquet` exchange files); others are ignored.
- **How:** each object is downloaded to a temporary directory (size checked against the object), then all are
  imported with `morlet_import.py`; further importer options (`--probes`, `--prefix`, `--unit`, …) pass through.
- **Provenance:** every recording's metadata gets `source_object`: bucket, key, ETag, version id (when the bucket
  is versioned), size and the SHA-256 of the downloaded bytes.
- **Reruns:** objects are imported one at a time, and an object counts as imported only when the importer has
  finished it: then a row with its bucket, key, ETag, size, SHA-256 and its recordings' ends is written to
  `<collection>_s3_imports` (#442). A rerun skips the objects with such a row. An object interrupted part-way (its
  first chunks committed, the rest not) has none and is imported again: the committed chunks replay as identical
  no-ops and the rest is appended. An object that changed under the same key is refused, because its recordings
  already exist with other content; nothing is overwritten. Collections imported before this record existed have
  no rows, so the next run reads their objects once more (no-ops).

## Export

- `morlet_export.py` writes the exchange file locally; it is uploaded with `morletdb-format`, `morletdb-sha256`
  (the file) and `morletdb-content-sha256` (the recordings and their sample checksums, without the export time)
  as object metadata. Large files upload in parts.
- If the object already has the same content SHA-256, the upload is skipped.
- After uploading, the object's size and metadata are read back and compared with the local file.

## What was tested

On 2026-11-02, against a local **SeaweedFS 3.73** S3 server (Docker, credentials generated for the test) with
boto3 1.35.36:

- **Import:** a TDMS file, an exchange file and a text file under one prefix: the text file was ignored, five
  recordings were imported with their `source_object`; a rerun skipped both objects; after one object was
  replaced under the same key, the rerun skipped the unchanged one and refused the changed one.
- **Export:** 984 recordings (20.2 M samples) uploaded; the downloaded object's SHA-256 matched its metadata and
  `morlet_export.py --verify` found all 984 recordings identical to the collection; exporting unchanged
  recordings again skipped the upload.
- **Not tested:** Amazon S3, Azure Blob Storage, Google Cloud Storage or MinIO themselves, versioned buckets,
  server-side encryption, and objects larger than a few tens of megabytes. A local S3-compatible test does not
  establish support for a particular cloud service.

Amazon S3 is a trademark of Amazon.com, Inc. or its affiliates; "S3-compatible" describes the API.
