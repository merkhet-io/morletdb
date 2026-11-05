#!/usr/bin/env python3
"""I1 after step 2 (issue #233): where a batched single-connection load spends its time.

Development set only, exploratory. One load through the importer with step 2's settings (256 chunks per
transaction, one connection, automatic batching: 64 chunks per `morlet_append_chunks_raw` call), then, inside
the server, as single statements over the whole load (no client, no network; medians of three):

  full_batched  every run of 64 chunks appended again with morlet_append_chunks_raw into a fresh collection
  batch         morlet_chunk_batch alone on every run (decode bytes, cut, checkpoint, encode)
  encode        morlet_encode_chunk on every chunk (as in step 0)
  check         morlet_valid_encoded_chunk on every chunk (the CHECK constraint)
  insert        the chunk rows inserted set-based into a copy of the table without the CHECK

The client's share is the load's wall time minus `full_batched`.

  B3_SET=dev B3_MORLET_IMAGE=morletdb:i1-dev .venv-b3/bin/python evaluation/ingest_i1/attribute_batch.py
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
from attribute import fresh_replay_collection, timed  # noqa: E402

REPS = 3


def main():
    assert os.environ["B3_SET"] == "dev"
    m = common.manifest()
    data, _ = run.preload(m, range(len(m["acquisitions"])))
    samples = run.total_samples(m)
    mo.VARIANT.update({"raw_read": True, "native_stats": True, "raw_append": True, "pipeline": True,
                       "one_statement": True, "commit_chunks": 256, "reuse_connection": True, "rpc_chunks": 0})
    conn = mo.start("i1-attr2", "D2")
    mo.create(conn)
    t0, preflight, calls = time.perf_counter(), 0.0, 0
    with common.Stats(mo.NAME) as st:
        for a in m["acquisitions"]:
            _, _, rep = mo.load_one(a, data[a["k"]])
            preflight += rep["preflight_s"]
            calls += rep["rpc_calls"]
        wall = time.perf_counter() - t0 - preflight
    mo._WRITER[0].close()
    res = {"set": "dev", "durability": "D2", "image": mo.IMAGE, "samples": samples,
           "image_id": common.sh(["docker", "image", "inspect", mo.IMAGE, "--format", "{{.Id}}"]).strip(),
           "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
           "client_load": {"wall_s": round(wall, 3), "samples_per_s": round(samples / wall), "rpc_calls": calls,
                           "server": st.summary()}}
    conn.execute("DROP TABLE IF EXISTS i1_stage, i1_runs")
    conn.execute(f"""CREATE UNLOGGED TABLE i1_stage AS
        SELECT c.recording_id, c.start_sample, c.sample_count, c.encoded, r.sample_quantum AS q,
               morlet_float8_le_bytes(morlet_decode_chunk(c.encoded)) AS raw,
               (row_number() OVER (PARTITION BY c.recording_id ORDER BY c.start_sample) - 1) / 64 AS run
        FROM {mo.COLLECTION}_chunks c JOIN {mo.COLLECTION} r USING (recording_id)""")
    conn.execute("""CREATE UNLOGGED TABLE i1_runs AS
        SELECT row_number() OVER (ORDER BY recording_id, min(start_sample)) AS o, recording_id,
               min(start_sample) AS start_sample, string_agg(raw, ''::bytea ORDER BY start_sample) AS raw,
               min(q) AS q FROM i1_stage GROUP BY recording_id, run""")
    conn.execute("VACUUM ANALYZE i1_stage")
    conn.execute("VACUUM ANALYZE i1_runs")
    res["chunks"], res["runs"] = (conn.execute("SELECT count(*) FROM i1_stage").fetchone()[0],
                                  conn.execute("SELECT count(*) FROM i1_runs").fetchone()[0])
    t = {k: [] for k in ("full_batched", "batch", "encode_plus_bytes", "bytes", "check", "insert")}
    for _ in range(REPS):
        fresh_replay_collection(conn, "i1x")
        conn.execute("CHECKPOINT")
        t["full_batched"].append(timed(conn, """DO $$ DECLARE s record; BEGIN
            FOR s IN SELECT recording_id, start_sample, raw FROM i1_runs ORDER BY o LOOP
              PERFORM morlet_append_chunks_raw('i1x'::regclass, s.recording_id, s.start_sample, s.raw, 4096);
            END LOOP; END $$"""))
        ok = conn.execute("""SELECT count(*) = 0 FROM i1_stage s LEFT JOIN i1x_chunks c USING (recording_id, start_sample)
            WHERE c.encoded IS DISTINCT FROM s.encoded""").fetchone()[0]
        if not ok:
            raise SystemExit("replay produced different chunks")
        # morlet_chunk_batch from a checkpoint that ends where each run starts (base loads: no extractor)
        t["batch"].append(timed(conn, """SELECT count(*) FROM i1_runs u JOIN i1x i ON i.recording_id = u.recording_id,
            LATERAL morlet_chunk_batch(
              jsonb_set(jsonb_set(jsonb_set(i.checkpoint, '{end}', to_jsonb(u.start_sample)),
                '{next_start}', to_jsonb(u.start_sample)), '{buffer_start}', to_jsonb(u.start_sample)),
              u.start_sample, u.raw, 4096, u.q, false) b"""))
        t["bytes"].append(timed(conn, "SELECT sum(cardinality(morlet_float8_from_le_bytes(raw))) FROM i1_stage"))
        t["encode_plus_bytes"].append(timed(
            conn, "SELECT sum(length(morlet_encode_chunk(morlet_float8_from_le_bytes(raw), q))) FROM i1_stage"))
        t["check"].append(timed(conn, "SELECT count(*) FILTER (WHERE morlet_valid_encoded_chunk(encoded, sample_count)) "
                                      "FROM i1_stage"))
        conn.execute("DROP TABLE IF EXISTS i1y_chunks, i1y CASCADE")
        conn.execute("CREATE TABLE i1y (LIKE i1x INCLUDING ALL)")
        conn.execute("INSERT INTO i1y SELECT * FROM i1x")
        conn.execute("""CREATE TABLE i1y_chunks (recording_id text NOT NULL REFERENCES i1y(recording_id),
            start_sample bigint NOT NULL CHECK (start_sample >= 0),
            sample_count integer NOT NULL CHECK (sample_count BETWEEN 1 AND 32768),
            encoded bytea NOT NULL, PRIMARY KEY (recording_id, start_sample))""")
        conn.execute("CHECKPOINT")
        t["insert"].append(timed(conn, "INSERT INTO i1y_chunks SELECT recording_id, start_sample, sample_count, encoded "
                                       "FROM i1_stage ORDER BY recording_id, start_sample"))
    med = {k: statistics.median(v) for k, v in t.items()}
    med["encode"] = med.pop("encode_plus_bytes") - med["bytes"]
    med["other_server"] = med["full_batched"] - med["batch"] - med["check"] - med["insert"]
    med["client_and_wire"] = wall - med["full_batched"]
    res["seconds"] = {k: round(v, 3) for k, v in med.items()}
    res["share_of_load"] = {k: round(med[k] / wall, 3)
                            for k in ("batch", "encode", "check", "insert", "other_server", "client_and_wire")}
    res["raw_s"] = {k: [round(x, 3) for x in v] for k, v in t.items()}
    common.remove(mo.NAME)
    out = HERE / "results" / f"attribution_batch_{time.strftime('%Y%m%dT%H%M%S')}.json"
    out.write_text(json.dumps(res, indent=1))
    print(json.dumps({k: res[k] for k in ("client_load", "chunks", "runs", "seconds", "share_of_load")}, indent=1))
    print(out)


if __name__ == "__main__":
    main()
