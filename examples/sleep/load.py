#!/usr/bin/env python3
"""Load Sleep-EDF nights into MorletDB for the sleep retrieval demo.

    MORLET_DSN="host=localhost port=28817 dbname=morlet_sleep_demo" \
        .venv-seismic/bin/python -I examples/sleep/load.py data/sleep-edfx/1.0.0/sleep-cassette

Each night's EEG Fpz-Cz, from 30 minutes before sleep onset to 30 minutes after the last sleep
epoch (as in S1), is stored as a raw-only MorletDB recording in the stream collection
`sleep_eeg`. Every scored 30-s epoch becomes a row of `sleep_epochs` that points at its sample
interval. Its features are computed by pg_morlet from the samples read back out of the
recording, and its pgvector embedding is the features standardized with development-subject
statistics (`sleep_scaler`). Development epochs are the gallery (partial HNSW index); held-out
epochs are queries. Re-running skips nights that are already complete.
"""
import os
import sys
from pathlib import Path

import numpy as np
import psycopg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "evaluation" / "sleep"))
import s1  # noqa: E402  (EDF reader, epoch rules and split shared with the S1 evaluation)

DSN = os.environ.get("MORLET_DSN", "host=localhost port=28817 dbname=morlet_sleep_demo")
COLLECTION = "sleep_eeg"
CHUNK = 4096

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS pg_morlet;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE TABLE IF NOT EXISTS sleep_epochs (
    recording_id text NOT NULL,
    epoch integer NOT NULL,                 -- 30-s epoch index from the start of the PSG file
    start_sample bigint NOT NULL,           -- offset in the MorletDB recording
    subject smallint NOT NULL,
    night smallint NOT NULL,
    split text NOT NULL CHECK (split IN ('development', 'held-out')),
    stage smallint NOT NULL CHECK (stage BETWEEN 0 AND 4),   -- W, N1, N2, N3 (S3+S4), REM
    features float8[],
    emb vector(15),
    PRIMARY KEY (recording_id, epoch)
);
CREATE TABLE IF NOT EXISTS sleep_scaler (i integer PRIMARY KEY, mean float8 NOT NULL, sd float8 NOT NULL);
"""


def quantum(psg):
    """Half the EDF step: Sleep-EDF samples sit at (d + 1/2) * step, so they lie on a half-step grid."""
    h = s1.edf_header(Path(psg).read_bytes()[:256 * 16])
    j = h["label"].index(s1.CHANNEL)
    return (float(h["pmax"][j]) - float(h["pmin"][j])) / (float(h["dmax"][j]) - float(h["dmin"][j])) / 2


def clock(start, epoch):
    hh, mm, ss = (int(v) for v in start[1].split("."))
    t = (hh * 3600 + mm * 60 + ss + epoch * 30) % 86400
    return f"{t // 3600:02d}:{t // 60 % 60:02d}:{t % 60:02d}"


def load_night(con, subject, night, psg, hyp, split):
    rid = f"SC4{subject:02d}{night}"
    x, unit, idx, y = s1.epochs_of_night(psg, hyp)
    _, _, _, start = s1.read_signal(psg, s1.CHANNEL)
    lo, hi = int(idx[0]), int(idx[-1])
    span = x[lo * s1.EPOCH:(hi + 1) * s1.EPOCH]
    done = con.execute("SELECT count(*) FROM sleep_epochs WHERE recording_id = %s AND features IS NOT NULL",
                       (rid,)).fetchone()[0]
    if done == len(idx):
        return rid, 0
    meta = {"subject": subject, "night": night, "split": split, "first_epoch": lo,
            "first_clock": clock(start, lo), "source": psg.name}
    with con.transaction():
        exists = con.execute(f"SELECT 1 FROM {COLLECTION} WHERE recording_id = %s", (rid,)).fetchone()
        if not exists:
            con.execute("SELECT morlet_start_recording(%s, %s, %s, %s, %s, %s::jsonb, 512, 256, 'zscore', 'none', %s)",
                        (COLLECTION, rid, s1.RATE, s1.CHANNEL, unit, psycopg.types.json.Json(meta), quantum(psg)))
        for s in range(0, len(span), CHUNK):   # identical retries are no-ops
            con.execute("SELECT morlet_append_chunk(%s, %s, %s, %s)", (COLLECTION, rid, s, span[s:s + CHUNK].tolist()))
        con.execute("DELETE FROM sleep_epochs WHERE recording_id = %s", (rid,))
        with con.cursor().copy("COPY sleep_epochs (recording_id, epoch, start_sample, subject, night, split, stage) "
                               "FROM STDIN") as cp:
            for e, stage in zip(idx, y):
                cp.write_row((rid, int(e), (int(e) - lo) * s1.EPOCH, subject, night, split, int(stage)))
        con.execute("""
            UPDATE sleep_epochs e SET features = morlet_features(
                (SELECT samples FROM morlet_read_interval(%s, e.recording_id, e.start_sample, e.start_sample + %s)),
                %s, %s)
            WHERE recording_id = %s""", (COLLECTION, s1.EPOCH, s1.RATE, s1.EXTRACTOR, rid))
    return rid, len(idx)


def embed(con):
    """Standardize with development statistics (population SD, as S1) and build the gallery index."""
    with con.transaction():
        con.execute("DELETE FROM sleep_scaler")
        con.execute("""
            INSERT INTO sleep_scaler
            SELECT f.i, avg(f.v), CASE WHEN stddev_pop(f.v) = 0 THEN 1 ELSE stddev_pop(f.v) END
            FROM sleep_epochs, unnest(features) WITH ORDINALITY f(v, i)
            WHERE split = 'development' GROUP BY f.i""")
        con.execute("""
            UPDATE sleep_epochs e SET emb = (
                SELECT array_agg((f.v - s.mean) / s.sd ORDER BY f.i)
                FROM unnest(e.features) WITH ORDINALITY f(v, i) JOIN sleep_scaler s ON s.i = f.i)::vector""")
        con.execute("DROP INDEX IF EXISTS sleep_gallery_hnsw")
        con.execute("CREATE INDEX sleep_gallery_hnsw ON sleep_epochs USING hnsw (emb vector_l2_ops) "
                    "WHERE split = 'development'")
    con.execute("ANALYZE sleep_epochs")


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    data = Path(sys.argv[1])
    split = s1.load_split()
    with psycopg.connect(DSN, autocommit=True) as con:
        con.execute(SCHEMA)
        if con.execute("SELECT to_regclass(%s)", (COLLECTION,)).fetchone()[0] is None:
            con.execute("SELECT morlet_create_stream_collection('public', %s)", (COLLECTION,))
        added = 0
        for name, subjects in (("development", split["development"]), ("held-out", split["held_out"])):
            for subject in subjects:
                for night, psg, hyp in s1.night_files(data, subject):
                    rid, n = load_night(con, subject, night, psg, hyp, name)
                    added += n
                    print(f"{rid} {name}: {n} epochs" if n else f"{rid} already loaded", flush=True)
        if added or con.execute("SELECT count(*) FROM sleep_epochs WHERE emb IS NULL").fetchone()[0]:
            embed(con)
        print(con.execute("SELECT split, count(DISTINCT recording_id), count(*) FROM sleep_epochs "
                          "GROUP BY split ORDER BY split").fetchall())


if __name__ == "__main__":
    main()
