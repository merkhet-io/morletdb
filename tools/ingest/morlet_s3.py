#!/usr/bin/env python3
"""Import recording files from, and export MorletDB exchange files to, S3-compatible object storage.

  python tools/ingest/morlet_s3.py import s3://bucket/prefix/ --collection public.signals [--create] [importer options]
  python tools/ingest/morlet_s3.py export s3://bucket/path/week.parquet --collection public.signals \
                                   [--metadata-filter '{"asset":"a1"}']

Credentials and the endpoint come from boto3's usual sources (environment, shared config, instance roles);
set AWS_ENDPOINT_URL for a non-AWS service. Nothing here prints, logs or stores credentials.

import: every object under the prefix with a supported suffix (WAV, EDF, miniSEED, TDMS, CSV, exchange Parquet)
is downloaded to a temporary directory and imported with morlet_import.py. Each recording's metadata gets
"source_object" (bucket, key, ETag, version id, size, SHA-256 of the downloaded bytes). Objects whose key and
ETag are already in the collection are skipped, so an interrupted import can be rerun; an object that changed
under the same key re-imports into the same recording ids, and the importer refuses conflicting samples.

export: morlet_export.py writes an exchange file locally, which is uploaded with its format, its SHA-256 and a
content SHA-256 (the recordings and their sample checksums, without the export time) in the object's metadata.
If the object already has that content SHA-256, the upload is skipped. After uploading, the object's
size and metadata are read back and compared.
"""
import argparse
import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
import morlet_import as mi  # noqa: E402

SUPPORTED = set(mi.SUFFIXES) - {".bdf"}


def client():
    try:
        import boto3
    except ImportError as exc:
        raise ValueError("S3 needs boto3: python -m pip install -r tools/ingest/requirements-s3.txt") from exc
    return boto3.client("s3")


def split(url):
    u = urlparse(url)
    if u.scheme != "s3" or not u.netloc:
        raise ValueError(f"expected s3://bucket/key, got {url!r}")
    return u.netloc, u.path.lstrip("/")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def already_imported(dsn, collection, bucket):
    """(key, etag) pairs already in the collection, from recordings' source_object metadata."""
    import psycopg
    with psycopg.connect(dsn) as conn:
        if conn.execute("SELECT to_regclass(%s)", (collection,)).fetchone()[0] is None:
            return set()
        rows = conn.execute(f"SELECT DISTINCT metadata->'source_object'->>'key', metadata->'source_object'->>'etag' "
                            f"FROM {collection} WHERE metadata->'source_object'->>'bucket' = %s", (bucket,)).fetchall()
    return {(k, e) for k, e in rows}


def import_prefix(url, collection, dsn, passthrough):
    bucket, prefix = split(url)
    s3 = client()
    objects = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for o in page.get("Contents", []):
            if Path(o["Key"]).suffix.lower() in SUPPORTED:
                objects.append(o)
    done = already_imported(dsn, collection, bucket)
    todo = [o for o in objects if (o["Key"], o["ETag"].strip('"')) not in done]
    report = {"bucket": bucket, "prefix": prefix, "objects": len(objects), "skipped_already_imported":
              len(objects) - len(todo), "imported": []}
    if not todo:
        return report, 0
    with tempfile.TemporaryDirectory(prefix="morlet-s3-") as tmp:
        files, provenance = [], {}
        for i, o in enumerate(sorted(todo, key=lambda o: o["Key"])):
            local = Path(tmp) / f"{i:06d}" / Path(o["Key"]).name      # keeps the file name, which ids use
            local.parent.mkdir()
            head = s3.head_object(Bucket=bucket, Key=o["Key"])
            s3.download_file(bucket, o["Key"], str(local))
            if local.stat().st_size != head["ContentLength"]:
                raise ValueError(f"s3://{bucket}/{o['Key']}: downloaded size differs from the object's")
            provenance[str(local)] = {"bucket": bucket, "key": o["Key"], "etag": head["ETag"].strip('"'),
                                      "version_id": head.get("VersionId"), "size": head["ContentLength"],
                                      "sha256": sha256(local)}
            files.append(local)
        readers = dict(mi.READERS)

        def with_provenance(fmt):
            def read(path, opts):
                recs = readers[fmt](path, opts)
                for r in recs:
                    r.metadata = {**r.metadata, "source_object": provenance[str(path)]}
                return recs
            return read
        for fmt in readers:
            mi.READERS[fmt] = with_provenance(fmt)
        try:
            code = mi.main([*map(str, files), "--collection", collection, "--dsn", dsn, *passthrough])
        finally:
            mi.READERS.update(readers)
        report["imported"] = [{"key": p["key"], "etag": p["etag"], "sha256": p["sha256"]} for p in provenance.values()]
        return report, code


def export_object(url, collection, dsn, flt):
    import morlet_export as me
    bucket, key = split(url)
    if not key or key.endswith("/"):
        raise ValueError("give the object key of the exchange file, e.g. s3://bucket/exports/week.parquet")
    s3 = client()
    with tempfile.TemporaryDirectory(prefix="morlet-s3-") as tmp:
        local = Path(tmp) / Path(key).name
        summary = me.export(dsn, collection, local, flt)
        digest = sha256(local)
        m = me.manifest_of(local)                                    # content identity, without the export time
        content = hashlib.sha256(json.dumps({k: m[k] for k in ("format", "version", "source_collection", "recordings")},
                                            sort_keys=True).encode()).hexdigest()
        meta = {"morletdb-format": f"{me.FORMAT}-{me.VERSION}", "morletdb-sha256": digest,
                "morletdb-content-sha256": content}
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
        except Exception:                                            # missing object (or no permission to read it)
            head = None
        if head and head.get("Metadata", {}).get("morletdb-content-sha256") == content:
            return {**summary, "object": url, "content_sha256": content, "uploaded": False,
                    "note": "the object already holds these recordings with the same samples"}
        s3.upload_file(str(local), bucket, key, ExtraArgs={"Metadata": meta, "ContentType": "application/vnd.apache.parquet"})
        head = s3.head_object(Bucket=bucket, Key=key)
        if head["ContentLength"] != local.stat().st_size or head.get("Metadata", {}).get("morletdb-sha256") != digest:
            raise ValueError(f"{url}: the uploaded object does not match the exported file")
        return {**summary, "object": url, "sha256": digest, "content_sha256": content, "uploaded": True,
                "etag": head["ETag"].strip('"'),
                "version_id": head.get("VersionId")}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["import", "export"])
    p.add_argument("url", help="s3://bucket/prefix/ (import) or s3://bucket/key.parquet (export)")
    p.add_argument("--collection", required=True)
    p.add_argument("--dsn", default=os.environ.get("DATABASE_URL", ""))
    p.add_argument("--metadata-filter", default="{}", help="export: JSON object matched by containment")
    opts, rest = p.parse_known_args(argv)
    try:
        if opts.command == "import":
            report, code = import_prefix(opts.url, opts.collection, opts.dsn, rest)
            print(json.dumps(report, indent=2))
            return code
        if rest:
            raise ValueError(f"unknown export options: {' '.join(rest)}")
        flt = json.loads(opts.metadata_filter)
        if not isinstance(flt, dict):
            raise ValueError("--metadata-filter must be a JSON object")
        print(json.dumps(export_object(opts.url, opts.collection, opts.dsn, flt), indent=2))
        return 0
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
