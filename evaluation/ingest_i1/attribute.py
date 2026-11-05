#!/usr/bin/env python3
"""I1 step 0 (issue #233): where single-connection ingestion time goes, without function tracking.

Development set only (B3_SET=dev), exploratory, not a frozen measurement. One load through the B3 client path
(`morletdb-033` arm: raw append, pipelined, one chunk per call), then each piece of the server's append path is
timed on its own inside the server, as one statement over every chunk of the load, so no client or network cost
is included:

  full      every chunk appended again through morlet_append_chunk_raw into a fresh collection (one DO block)
  bytes     morlet_float8_from_le_bytes on every chunk
  encode    morlet_encode_chunk on every chunk (minus `bytes`)
  check     morlet_valid_encoded_chunk on every stored chunk (the table's CHECK constraint)
  relations morlet_stream_relations, once per chunk
  step      morlet_chunk_step on every chunk, starting from each recording's initial checkpoint
  insert    the chunk rows inserted set-based into a copy of the chunks table with its primary key and foreign
            key but without the CHECK (heap, index, FK trigger, WAL)
  update    one checkpoint UPDATE per chunk with static SQL (plan cached)

The residual full - (sum of pieces) is PL/pgSQL interpretation, dynamic EXECUTE parse/plan, the row lock and
the replay lookup. Each piece runs `REPS` times; medians are reported.

  B3_SET=dev B3_MORLET_IMAGE=ghcr.io/merkhet-io/morletdb:0.3.5-alpha.1 \
    .venv-b3/bin/python evaluation/ingest_i1/attribute.py D2
"""
import json
import os
import statistics
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "tsdb_b3"))
os.environ.setdefault("B3_SET", "dev")

import common  # noqa: E402
import morletdb_adapter as mo  # noqa: E402
import run  # noqa: E402

REPS = 3
OUT = HERE / "results"


def timed(conn, sql, params=None):
    t = time.perf_counter()
    conn.execute(sql, params)
    return time.perf_counter() - t


def fresh_replay_collection(conn, name):
    conn.execute(f"DROP TABLE IF EXISTS {name}_segments, {name}_chunks, {name} CASCADE")
    conn.execute("SELECT morlet_create_stream_collection('public', %s)", (name,))
    conn.execute(f"""SELECT count(morlet_ensure_recording('{name}'::regclass, recording_id, sample_rate_hz, channel,
        unit, metadata, (checkpoint->>'window')::int, (checkpoint->>'stride')::int, checkpoint->>'normalization',
        checkpoint->>'extractor_version', sample_quantum)) FROM {mo.COLLECTION}""")


def main(durability="D2"):
    assert os.environ["B3_SET"] == "dev", "attribution runs on the development set only"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True})
    conn = mo.start("i1-attr", durability)
    mo.create(conn)
    with common.Stats(mo.NAME) as st:
        r, _ = run.ingest("morletdb-033", conn, m, data)
    res = {"set": "dev", "durability": durability, "image": mo.IMAGE, "samples": samples,
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "client_load": {"wall_s": round(r["wall_s"], 3), "samples_per_s": round(samples / r["wall_s"]),
                           "server": st.summary()}}
    n = conn.execute(f"SELECT count(*) FROM {mo.COLLECTION}_chunks").fetchone()[0]
    res["chunks"] = n

    # staging: every chunk's samples as little-endian bytes, in append order
    conn.execute("DROP TABLE IF EXISTS i1_stage")
    conn.execute(f"""CREATE UNLOGGED TABLE i1_stage AS
        SELECT row_number() OVER (ORDER BY c.recording_id, c.start_sample) AS o, c.recording_id, c.start_sample,
               morlet_float8_le_bytes(morlet_decode_chunk(c.encoded)) AS raw, r.sample_quantum AS q, c.encoded,
               c.sample_count
        FROM {mo.COLLECTION}_chunks c JOIN {mo.COLLECTION} r USING (recording_id)""")
    conn.execute("VACUUM ANALYZE i1_stage")

    t = {k: [] for k in ("full", "bytes", "encode_plus_bytes", "check", "relations", "step", "insert", "update")}
    for _ in range(REPS):
        fresh_replay_collection(conn, "i1x")
        conn.execute("CHECKPOINT")
        t["full"].append(timed(conn, """DO $$ DECLARE s record; BEGIN
            FOR s IN SELECT recording_id, start_sample, raw FROM i1_stage ORDER BY o LOOP
              PERFORM morlet_append_chunk_raw('i1x'::regclass, s.recording_id, s.start_sample, s.raw);
            END LOOP; END $$"""))
        ok = conn.execute("""SELECT count(*) = 0 FROM i1_stage s LEFT JOIN i1x_chunks c USING (recording_id, start_sample)
            WHERE c.encoded IS DISTINCT FROM s.encoded""").fetchone()[0]
        if not ok:
            raise SystemExit("replay produced different chunks")
        t["bytes"].append(timed(conn, "SELECT sum(cardinality(morlet_float8_from_le_bytes(raw))) FROM i1_stage"))
        t["encode_plus_bytes"].append(timed(
            conn, "SELECT sum(length(morlet_encode_chunk(morlet_float8_from_le_bytes(raw), q))) FROM i1_stage"))
        t["check"].append(timed(conn, "SELECT count(*) FILTER (WHERE morlet_valid_encoded_chunk(encoded, sample_count)) "
                                      "FROM i1_stage"))
        t["relations"].append(timed(conn, """DO $$ DECLARE s record; rel record; BEGIN
            FOR s IN SELECT o FROM i1_stage ORDER BY o LOOP
              SELECT * INTO rel FROM morlet_stream_relations('i1x'::regclass);
            END LOOP; END $$"""))
        # chunk_step from the initial checkpoint of each recording (same work per chunk as during appends)
        t["step"].append(timed(conn, """SELECT count(morlet_chunk_step(r.checkpoint, 0, morlet_float8_from_le_bytes(s.raw)))
            FROM i1_stage s JOIN (SELECT recording_id, morlet_chunk_checkpoint(sample_rate_hz,
              (checkpoint->>'window')::int, (checkpoint->>'stride')::int, checkpoint->>'normalization',
              checkpoint->>'extractor_version') AS checkpoint FROM i1x) r USING (recording_id)"""))
        t["step"][-1] -= t["bytes"][-1]
        conn.execute("DROP TABLE IF EXISTS i1y_chunks, i1y CASCADE")
        conn.execute("CREATE TABLE i1y (LIKE i1x INCLUDING ALL)")
        conn.execute("INSERT INTO i1y SELECT * FROM i1x")
        conn.execute("""CREATE TABLE i1y_chunks (recording_id text NOT NULL REFERENCES i1y(recording_id),
            start_sample bigint NOT NULL CHECK (start_sample >= 0),
            sample_count integer NOT NULL CHECK (sample_count BETWEEN 1 AND 32768),
            encoded bytea NOT NULL, PRIMARY KEY (recording_id, start_sample))""")
        conn.execute("CHECKPOINT")
        t["insert"].append(timed(conn, "INSERT INTO i1y_chunks SELECT recording_id, start_sample, sample_count, encoded "
                                       "FROM i1_stage ORDER BY o"))
        t["update"].append(timed(conn, """DO $$ DECLARE s record; BEGIN
            FOR s IN SELECT recording_id FROM i1_stage ORDER BY o LOOP
              UPDATE i1y SET checkpoint = checkpoint WHERE recording_id = s.recording_id;
            END LOOP; END $$"""))
    med = {k: statistics.median(v) for k, v in t.items()}
    med["encode"] = med.pop("encode_plus_bytes") - med["bytes"]
    pieces = ("bytes", "encode", "check", "relations", "step", "insert", "update")
    med["residual"] = med["full"] - sum(med[k] for k in pieces)
    res["server_s"] = {k: round(v, 3) for k, v in med.items()}
    res["server_share_of_full"] = {k: round(med[k] / med["full"], 3) for k in pieces + ("residual",)}
    res["ns_per_sample"] = {k: round(v / samples * 1e9, 1) for k, v in med.items()}
    res["us_per_chunk"] = {k: round(v / n * 1e6, 1) for k, v in med.items()}
    res["raw_s"] = {k: [round(x, 3) for x in v] for k, v in t.items()}
    res["server_settings"] = dict(conn.execute(
        "SELECT name, setting FROM pg_settings WHERE name IN ('synchronous_commit','shared_buffers','wal_level',"
        "'max_wal_size','fsync','full_page_writes')").fetchall())
    res["morlet_version"] = conn.execute("SELECT extversion FROM pg_extension WHERE extname='pg_morlet'").fetchone()[0]
    common.remove(mo.NAME)
    OUT.mkdir(exist_ok=True)
    out = OUT / f"attribution_{durability}_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("client_load", "chunks", "server_s", "server_share_of_full",
                                          "us_per_chunk")}, indent=1))
    print(out)


if __name__ == "__main__":
    main(*sys.argv[1:])
