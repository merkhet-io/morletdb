//! MorletDB's embedded edition (#351): lossless waveform recordings in one SQLite file.
//!
//! Experimental. The chunks are `morlet_core::rawcodec` encodings with `IntervalStats` summaries, exactly as
//! pg_morlet stores them, so a chunk moves between the editions bit for bit. Interval statistics merge the same
//! pieces in the same order as pg_morlet's aggregate (exact sums), so they are the same bit for bit.
//!
//! File format 0 (`PRAGMA user_version`, `PRAGMA application_id` = `MRLT`):
//!
//! - `morlet_meta(key, value)`: `format`, `format_version`, `created_by`.
//! - `recordings(recording_id, sample_rate_hz, channel, unit, sample_quantum, metadata, end_sample)`, as pg_morlet's
//!   recording rows (the start time is in the metadata as `start` and `start_ns`, as the Python SDK writes it).
//! - `chunks(recording_id, start_sample, sample_count, encoded, summary)`, contiguous from 0 per recording.
//!
//! Every other tool can read the tables; only the samples need this library (or pg_morlet) to decode.
use morlet_core::exactstats::ExactStats;
use morlet_core::intervalstats::IntervalStats;
use morlet_core::{rawcodec, MAX_SAMPLES};
use rusqlite::{params, Connection, OptionalExtension, TransactionBehavior};
use std::fmt;
use std::path::Path;

pub const FORMAT_VERSION: i64 = 0;
/// `PRAGMA application_id`: "MRLT".
pub const APPLICATION_ID: i64 = 0x4d52_4c54;
/// The largest interval `read` returns in one call (as pg_morlet's interval reads, 2^24 samples).
pub const MAX_READ: i64 = 1 << 24;

const SCHEMA: &str = "
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
  summary BLOB NOT NULL,
  PRIMARY KEY(recording_id, start_sample)
) WITHOUT ROWID;
";

#[derive(Debug)]
pub enum Error {
    Sqlite(rusqlite::Error),
    Invalid(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Sqlite(e) => write!(f, "sqlite: {e}"),
            Error::Invalid(m) => f.write_str(m),
        }
    }
}

impl std::error::Error for Error {}

impl From<rusqlite::Error> for Error {
    fn from(e: rusqlite::Error) -> Self {
        Error::Sqlite(e)
    }
}

pub type Result<T> = std::result::Result<T, Error>;

pub mod ffi;

fn invalid<T>(m: impl Into<String>) -> Result<T> {
    Err(Error::Invalid(m.into()))
}

/// Durability of a committed append. `Full` (default): the commit returns after the file is synced, so an
/// acknowledged append survives a power loss. `Normal`: WAL mode may lose the newest commits on power loss, never
/// older ones, and the file stays consistent.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Durability {
    Full,
    Normal,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Recording {
    pub recording_id: String,
    pub sample_rate_hz: f64,
    pub channel: String,
    pub unit: String,
    pub sample_quantum: f64,
    pub metadata: String,
    pub end_sample: i64,
}

#[derive(Debug)]
pub struct Store {
    conn: Connection,
}

impl Store {
    /// Opens (or creates) a MorletDB file.
    pub fn open(path: impl AsRef<Path>) -> Result<Store> {
        Self::open_with(path, Durability::Full)
    }

    pub fn open_with(path: impl AsRef<Path>, durability: Durability) -> Result<Store> {
        let conn = Connection::open(path)?;
        conn.busy_timeout(std::time::Duration::from_secs(10))?;
        conn.pragma_update(None, "journal_mode", "WAL")?;
        conn.pragma_update(
            None,
            "synchronous",
            match durability {
                Durability::Full => "FULL",
                Durability::Normal => "NORMAL",
            },
        )?;
        conn.pragma_update(None, "foreign_keys", "ON")?;
        let store = Store { conn };
        store.init()?;
        Ok(store)
    }

    fn init(&self) -> Result<()> {
        let app: i64 = self
            .conn
            .pragma_query_value(None, "application_id", |r| r.get(0))?;
        let version: i64 = self
            .conn
            .pragma_query_value(None, "user_version", |r| r.get(0))?;
        let tables: i64 = self.conn.query_row(
            "SELECT count(*) FROM sqlite_schema WHERE type='table'",
            [],
            |r| r.get(0),
        )?;
        if app == 0 && tables == 0 {
            let tx = self.conn.unchecked_transaction()?;
            tx.execute_batch(SCHEMA)?;
            tx.execute(
                "INSERT INTO morlet_meta VALUES ('format','morletdb-sqlite'),('format_version',?1),('created_by',?2)",
                params![FORMAT_VERSION.to_string(), format!("morlet-sqlite {}", env!("CARGO_PKG_VERSION"))],
            )?;
            tx.pragma_update(None, "application_id", APPLICATION_ID)?;
            tx.pragma_update(None, "user_version", FORMAT_VERSION)?;
            tx.commit()?;
            return Ok(());
        }
        if app != APPLICATION_ID {
            return invalid("not a MorletDB file (application_id differs)");
        }
        if version != FORMAT_VERSION {
            return invalid(format!(
                "MorletDB file format {version}; this library reads format {FORMAT_VERSION}"
            ));
        }
        Ok(())
    }

    /// The underlying connection, for queries over the tables (read-only use is advised).
    pub fn connection(&self) -> &Connection {
        &self.conn
    }

    pub fn create_recording(
        &self,
        recording_id: &str,
        sample_rate_hz: f64,
        channel: &str,
        unit: &str,
        sample_quantum: f64,
        metadata_json: &str,
    ) -> Result<()> {
        if recording_id.is_empty() || channel.is_empty() {
            return invalid("recording_id and channel must not be empty");
        }
        if !(sample_rate_hz > 0.0 && sample_rate_hz.is_finite()) {
            return invalid("sample_rate_hz must be positive and finite");
        }
        if !(sample_quantum > 0.0 && sample_quantum.is_finite()) {
            return invalid("sample_quantum must be positive and finite");
        }
        self.conn
            .execute(
                "INSERT INTO recordings(recording_id,sample_rate_hz,channel,unit,sample_quantum,metadata)
                 VALUES (?1,?2,?3,?4,?5,json(?6))",
                params![recording_id, sample_rate_hz, channel, unit, sample_quantum, metadata_json],
            )
            .map_err(|e| match e {
                rusqlite::Error::SqliteFailure(f, _) if f.code == rusqlite::ErrorCode::ConstraintViolation => {
                    Error::Invalid(format!("recording {recording_id:?} exists or its metadata is not JSON"))
                }
                e => Error::Sqlite(e),
            })?;
        Ok(())
    }

    pub fn recording(&self, recording_id: &str) -> Result<Option<Recording>> {
        Ok(self
            .conn
            .query_row(
                "SELECT recording_id,sample_rate_hz,channel,unit,sample_quantum,metadata,end_sample
                 FROM recordings WHERE recording_id=?1",
                [recording_id],
                |r| {
                    Ok(Recording {
                        recording_id: r.get(0)?,
                        sample_rate_hz: r.get(1)?,
                        channel: r.get(2)?,
                        unit: r.get(3)?,
                        sample_quantum: r.get(4)?,
                        metadata: r.get(5)?,
                        end_sample: r.get(6)?,
                    })
                },
            )
            .optional()?)
    }

    pub fn recordings(&self) -> Result<Vec<Recording>> {
        let mut st = self
            .conn
            .prepare("SELECT recording_id FROM recordings ORDER BY recording_id")?;
        let ids: Vec<String> = st
            .query_map([], |r| r.get(0))?
            .collect::<rusqlite::Result<_>>()?;
        ids.iter()
            .map(|id| Ok(self.recording(id)?.expect("listed")))
            .collect()
    }

    /// Appends samples at `start_sample`, which must be the recording's current end (an identical retry of the
    /// last append is a no-op). Chunks of `chunk_size` (1..32768) samples are encoded, decoded again and compared
    /// bit for bit, summarized, and committed in one transaction. Returns the new end.
    pub fn append(
        &mut self,
        recording_id: &str,
        start_sample: i64,
        samples: &[f64],
        chunk_size: usize,
    ) -> Result<i64> {
        if !(1..=MAX_SAMPLES).contains(&chunk_size) {
            return invalid("chunk_size must be 1..32768");
        }
        if samples.is_empty() {
            return invalid("no samples to append");
        }
        let tx = self
            .conn
            .transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (end, quantum): (i64, f64) = tx
            .query_row(
                "SELECT end_sample,sample_quantum FROM recordings WHERE recording_id=?1",
                [recording_id],
                |r| Ok((r.get(0)?, r.get(1)?)),
            )
            .optional()?
            .ok_or_else(|| Error::Invalid(format!("no recording {recording_id:?}")))?;
        let new_end = start_sample
            .checked_add(samples.len() as i64)
            .ok_or_else(|| Error::Invalid("sample offset overflow".into()))?;
        if start_sample != end {
            if new_end <= end
                && start_sample >= 0
                && read_in(&tx, recording_id, start_sample, new_end)?
                    .iter()
                    .zip(samples)
                    .all(|(a, b)| a.to_bits() == b.to_bits())
            {
                return Ok(end); // an identical retry of samples already stored
            }
            return invalid(format!(
                "append must start at the recording's end ({end}), not {start_sample}"
            ));
        }
        {
            let mut ins = tx.prepare_cached(
                "INSERT INTO chunks(recording_id,start_sample,sample_count,encoded,summary) VALUES (?1,?2,?3,?4,?5)",
            )?;
            let mut offset = start_sample;
            for chunk in samples.chunks(chunk_size) {
                let encoded =
                    rawcodec::encode(chunk, quantum).map_err(|e| Error::Invalid(e.to_string()))?;
                let back = rawcodec::decode(&encoded).map_err(|e| Error::Invalid(e.to_string()))?;
                if back.len() != chunk.len()
                    || back
                        .iter()
                        .zip(chunk)
                        .any(|(a, b)| a.to_bits() != b.to_bits())
                {
                    return invalid("encoded chunk does not decode to its input samples");
                }
                let mut s = IntervalStats::default();
                s.add(chunk);
                ins.execute(params![
                    recording_id,
                    offset,
                    chunk.len() as i64,
                    encoded,
                    s.to_summary().as_slice()
                ])?;
                offset += chunk.len() as i64;
            }
        }
        tx.execute(
            "UPDATE recordings SET end_sample=?2 WHERE recording_id=?1",
            params![recording_id, new_end],
        )?;
        tx.commit()?;
        Ok(new_end)
    }

    /// The samples of `[a, b)`, bit for bit as appended.
    pub fn read(&self, recording_id: &str, a: i64, b: i64) -> Result<Vec<f64>> {
        read_in(&self.conn, recording_id, a, b)
    }

    /// `[count, min, max, mean, rms]` of `[a, b)`: whole chunks from their summaries, partial chunks decoded,
    /// merged exactly in start order, as pg_morlet's `morlet_interval_stats`.
    pub fn interval_stats(&self, recording_id: &str, a: i64, b: i64) -> Result<[f64; 5]> {
        check_interval(&self.conn, recording_id, a, b, i64::MAX)?;
        let mut st = self.conn.prepare_cached(
            "SELECT start_sample,sample_count,CASE WHEN start_sample<?2 OR start_sample+sample_count>?3 THEN encoded END,
                    summary
             FROM chunks WHERE recording_id=?1 AND start_sample<?3 AND start_sample>?2-32768 AND start_sample+sample_count>?2
             ORDER BY start_sample",
        )?;
        let mut rows = st.query(params![recording_id, a, b])?;
        let mut total = ExactStats::default();
        let mut covered = 0i64;
        while let Some(r) = rows.next()? {
            let (start, count): (i64, i64) = (r.get(0)?, r.get(1)?);
            let (lo, hi) = (a.max(start), b.min(start + count));
            let piece = if start >= a && start + count <= b {
                let sm: Vec<u8> = r.get(3)?;
                let s = IntervalStats::from_summary(&sm)
                    .ok_or_else(|| Error::Invalid("invalid chunk summary".into()))?;
                if s.count() != count as u64 {
                    return invalid("chunk summary does not match the chunk's sample count");
                }
                s
            } else {
                let enc: Vec<u8> = r.get(2)?;
                let samples = rawcodec::decode(&enc).map_err(|e| Error::Invalid(e.to_string()))?;
                if samples.len() != count as usize {
                    return invalid("encoded chunk does not match its sample count");
                }
                let mut s = IntervalStats::default();
                s.add(&samples[(lo - start) as usize..(hi - start) as usize]);
                s
            };
            let mut e = ExactStats::default();
            e.add_piece(&piece);
            total.merge(&e);
            covered += hi - lo;
        }
        if covered != b - a {
            return invalid("stored chunks do not cover the interval");
        }
        Ok(total.finish())
    }
}

fn json_str(s: &str) -> String {
    let mut o = String::from("\"");
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o + "\""
}

fn json_f64(x: f64) -> String {
    if x.is_finite() {
        format!("{x:?}")
    } else {
        "null".into()
    }
}

impl Recording {
    /// The recording as one JSON object (`metadata` as stored).
    pub fn to_json(&self) -> String {
        format!(
            "{{\"recording_id\":{},\"sample_rate_hz\":{},\"channel\":{},\"unit\":{},\"sample_quantum\":{},\"end_sample\":{},\"metadata\":{}}}",
            json_str(&self.recording_id),
            json_f64(self.sample_rate_hz),
            json_str(&self.channel),
            json_str(&self.unit),
            json_f64(self.sample_quantum),
            self.end_sample,
            self.metadata
        )
    }
}

impl Store {
    /// `{"format_version": .., "recordings": [..]}`.
    pub fn info_json(&self) -> Result<String> {
        let items: Vec<String> = self.recordings()?.iter().map(Recording::to_json).collect();
        Ok(format!(
            "{{\"format_version\":{FORMAT_VERSION},\"recordings\":[{}]}}",
            items.join(",")
        ))
    }
}

fn check_interval(conn: &Connection, recording_id: &str, a: i64, b: i64, max: i64) -> Result<()> {
    let end: i64 = conn
        .query_row(
            "SELECT end_sample FROM recordings WHERE recording_id=?1",
            [recording_id],
            |r| r.get(0),
        )
        .optional()?
        .ok_or_else(|| Error::Invalid(format!("no recording {recording_id:?}")))?;
    if a < 0 || b <= a {
        return invalid("the interval must be a nonempty [start, end) with start >= 0");
    }
    if b > end {
        return invalid("source interval exceeds recording length");
    }
    if b - a > max {
        return invalid(format!("an interval read returns at most {max} samples"));
    }
    Ok(())
}

fn read_in(conn: &Connection, recording_id: &str, a: i64, b: i64) -> Result<Vec<f64>> {
    check_interval(conn, recording_id, a, b, MAX_READ)?;
    let mut st = conn.prepare_cached(
        "SELECT start_sample,sample_count,encoded FROM chunks
         WHERE recording_id=?1 AND start_sample<?3 AND start_sample>?2-32768 AND start_sample+sample_count>?2
         ORDER BY start_sample",
    )?;
    let mut rows = st.query(params![recording_id, a, b])?;
    let mut out = Vec::with_capacity((b - a) as usize);
    while let Some(r) = rows.next()? {
        let (start, count): (i64, i64) = (r.get(0)?, r.get(1)?);
        let enc = r.get_ref(2)?.as_blob().map_err(rusqlite::Error::from)?;
        let samples = rawcodec::decode(enc).map_err(|e| Error::Invalid(e.to_string()))?;
        if samples.len() != count as usize {
            return invalid("encoded chunk does not match its sample count");
        }
        if start - a != out.len() as i64 && !(out.is_empty() && start < a) {
            return invalid("stored chunks do not cover the interval");
        }
        let (lo, hi) = (a.max(start), b.min(start + count));
        out.extend_from_slice(&samples[(lo - start) as usize..(hi - start) as usize]);
    }
    if out.len() as i64 != b - a {
        return invalid("stored chunks do not cover the interval");
    }
    Ok(out)
}
