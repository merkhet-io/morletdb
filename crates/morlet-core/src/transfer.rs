//! Compressed transfer frames (docs/compressed-transfer.md): an interval's stored chunks sent to the client
//! unchanged, or, when the client cannot decode a chunk's version, the interval's samples.
//!
//! ```text
//! chunk frame: "MTX1" | interval_start i64 | interval_end i64 | n u32 |
//!              n x (start_sample i64 | byte_length u32 | chunk bytes) | crc32c u32
//! raw frame:   "MTR1" | interval_start i64 | interval_end i64 | n u32 | n x f64 | crc32c u32
//! ```
//!
//! All integers little-endian; the CRC-32C covers every byte before it. Chunk bytes are the stored chunks, each
//! with its own checksum, verified again when decoded. `decode_frame` writes exactly the interval's samples, or
//! fails: a frame for another interval, a gap, an overlap or a chunk outside the interval is an error, so a
//! frame can never yield samples that differ from a read of the same interval.
use crate::rawcodec::{self, CodecError};
use std::fmt;

pub const CHUNK_MAGIC: [u8; 4] = *b"MTX1";
pub const RAW_MAGIC: [u8; 4] = *b"MTR1";
/// The highest chunk format version this build decodes.
pub const MAX_CHUNK_VERSION: u8 = rawcodec::FORMAT_VERSION;
/// Longest interval in one frame, as for raw reads.
pub const MAX_INTERVAL: i64 = 1 << 24;
const HEADER: usize = 4 + 8 + 8 + 4;
const CRC: usize = 4;

#[derive(Debug, PartialEq)]
pub enum FrameError {
    /// The frame's structure is wrong (truncated, bad magic, bad checksum, another interval).
    Frame(&'static str),
    /// A chunk failed to decode; `start_sample` says which.
    Chunk {
        start_sample: i64,
        error: CodecError,
    },
    /// The chunks do not cover the interval exactly (gap, overlap or chunk outside it).
    Coverage,
    /// The caller's arguments are wrong (interval, output length, chunk order).
    Argument(&'static str),
}
impl fmt::Display for FrameError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Frame(m) => write!(f, "corrupt transfer frame: {m}"),
            Self::Chunk {
                start_sample,
                error,
            } => {
                write!(f, "chunk at sample {start_sample}: {error}")
            }
            Self::Coverage => f.write_str("transfer frame does not cover the interval exactly"),
            Self::Argument(m) => f.write_str(m),
        }
    }
}
impl std::error::Error for FrameError {}

fn check_interval(start: i64, end: i64) -> Result<usize, FrameError> {
    if start < 0 || end <= start || end - start > MAX_INTERVAL {
        return Err(FrameError::Argument(
            "interval must contain 1..16777216 samples at nonnegative coordinates",
        ));
    }
    Ok((end - start) as usize)
}

fn header(magic: [u8; 4], start: i64, end: i64, n: usize, capacity: usize) -> Vec<u8> {
    let mut out = Vec::with_capacity(HEADER + capacity + CRC);
    out.extend_from_slice(&magic);
    out.extend_from_slice(&start.to_le_bytes());
    out.extend_from_slice(&end.to_le_bytes());
    out.extend_from_slice(&(n as u32).to_le_bytes());
    out
}

fn seal(mut out: Vec<u8>) -> Vec<u8> {
    let crc = rawcodec::crc32c(&out);
    out.extend_from_slice(&crc.to_le_bytes());
    out
}

/// A chunk frame of `chunks` (start sample and stored bytes, strictly ascending starts) for the interval.
/// Coverage is not checked here (the server checks it from the stored sample counts, the client on decoding).
pub fn chunk_frame(chunks: &[(i64, &[u8])], start: i64, end: i64) -> Result<Vec<u8>, FrameError> {
    check_interval(start, end)?;
    if chunks.len() > u32::MAX as usize {
        return Err(FrameError::Argument("too many chunks"));
    }
    let bytes: usize = chunks.iter().map(|(_, c)| 12 + c.len()).sum();
    let mut out = header(CHUNK_MAGIC, start, end, chunks.len(), bytes);
    let mut previous = i64::MIN;
    for (s, c) in chunks {
        if *s <= previous {
            return Err(FrameError::Argument(
                "chunk starts must be strictly ascending",
            ));
        }
        previous = *s;
        let len = u32::try_from(c.len()).map_err(|_| FrameError::Argument("chunk too long"))?;
        out.extend_from_slice(&s.to_le_bytes());
        out.extend_from_slice(&len.to_le_bytes());
        out.extend_from_slice(c);
    }
    Ok(seal(out))
}

/// A raw frame carrying the interval's samples.
pub fn raw_frame(samples: &[f64], start: i64, end: i64) -> Result<Vec<u8>, FrameError> {
    let n = check_interval(start, end)?;
    if samples.len() != n {
        return Err(FrameError::Coverage);
    }
    let mut out = header(RAW_MAGIC, start, end, n, 8 * n);
    for v in samples {
        out.extend_from_slice(&v.to_le_bytes());
    }
    Ok(seal(out))
}

fn take<const N: usize>(b: &[u8], pos: &mut usize) -> Result<[u8; N], FrameError> {
    let s = b
        .get(*pos..*pos + N)
        .ok_or(FrameError::Frame("truncated"))?;
    *pos += N;
    Ok(s.try_into().unwrap())
}

/// Checks a frame and writes the interval's samples to `out` (length `interval_end - interval_start`). Chunks
/// are decoded on up to `threads` scoped threads when there are at least 8 of them. On error, `out` may be
/// partly written and must not be used.
pub fn decode_frame(
    frame: &[u8],
    start: i64,
    end: i64,
    out: &mut [f64],
    threads: usize,
) -> Result<(), FrameError> {
    let n = check_interval(start, end)?;
    if out.len() != n {
        return Err(FrameError::Argument(
            "output length differs from the interval",
        ));
    }
    if frame.len() < HEADER + CRC {
        return Err(FrameError::Frame("truncated"));
    }
    let (body, tail) = frame.split_at(frame.len() - CRC);
    if rawcodec::crc32c(body).to_le_bytes() != tail {
        return Err(FrameError::Frame("checksum mismatch"));
    }
    let mut pos = 0;
    let magic: [u8; 4] = take(body, &mut pos)?;
    let fs = i64::from_le_bytes(take(body, &mut pos)?);
    let fe = i64::from_le_bytes(take(body, &mut pos)?);
    let count = u32::from_le_bytes(take(body, &mut pos)?) as usize;
    if (fs, fe) != (start, end) {
        return Err(FrameError::Frame("frame is for another interval"));
    }
    match magic {
        RAW_MAGIC => {
            if count != n || body.len() - pos != 8 * n {
                return Err(FrameError::Frame("raw length"));
            }
            for (o, b) in out.iter_mut().zip(body[pos..].chunks_exact(8)) {
                *o = f64::from_le_bytes(b.try_into().unwrap());
            }
            Ok(())
        }
        CHUNK_MAGIC => {
            // Each chunk holds at least one sample and needs at least 12 bytes of framing.
            if count > n || count > (body.len() - pos) / 12 {
                return Err(FrameError::Frame("chunk count"));
            }
            let mut items: Vec<(i64, &[u8])> = Vec::with_capacity(count);
            for _ in 0..count {
                let s = i64::from_le_bytes(take(body, &mut pos)?);
                let len = u32::from_le_bytes(take(body, &mut pos)?) as usize;
                let c = body
                    .get(pos..pos + len)
                    .ok_or(FrameError::Frame("truncated"))?;
                pos += len;
                items.push((s, c));
            }
            if pos != body.len() {
                return Err(FrameError::Frame("trailing bytes"));
            }
            write_chunks(&items, start, end, out, threads)
        }
        _ => Err(FrameError::Frame("unknown magic")),
    }
}

fn write_chunks(
    items: &[(i64, &[u8])],
    start: i64,
    end: i64,
    out: &mut [f64],
    threads: usize,
) -> Result<(), FrameError> {
    let decode = |(s, c): &(i64, &[u8])| {
        rawcodec::decode(c).map_err(|error| FrameError::Chunk {
            start_sample: *s,
            error,
        })
    };
    let threads = if items.len() >= 8 {
        threads.clamp(1, 64).min(items.len())
    } else {
        1
    };
    let decoded: Vec<Result<Vec<f64>, FrameError>> = if threads <= 1 {
        items.iter().map(decode).collect()
    } else {
        let per = items.len().div_ceil(threads);
        std::thread::scope(|scope| {
            let handles: Vec<_> = items
                .chunks(per)
                .map(|part| scope.spawn(move || part.iter().map(decode).collect::<Vec<_>>()))
                .collect();
            handles
                .into_iter()
                .flat_map(|h| h.join().expect("decode thread panicked"))
                .collect()
        })
    };
    // Coverage: every chunk must overlap the interval and continue exactly where the previous one ended.
    let mut cursor = start;
    for ((s, _), samples) in items.iter().zip(decoded) {
        let samples = samples?;
        let lo = start.max(*s);
        let hi = end.min(s.saturating_add(samples.len() as i64));
        if lo >= hi || lo != cursor {
            return Err(FrameError::Coverage);
        }
        out[(lo - start) as usize..(hi - start) as usize]
            .copy_from_slice(&samples[(lo - s) as usize..(hi - s) as usize]);
        cursor = hi;
    }
    if cursor != end {
        return Err(FrameError::Coverage);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn chunks(total: usize, size: usize) -> (Vec<f64>, Vec<(i64, Vec<u8>)>) {
        let samples: Vec<f64> = (0..total)
            .map(|i| ((i * 7919) % 2001) as f64 - 1000.0)
            .collect();
        let enc = samples
            .chunks(size)
            .enumerate()
            .map(|(k, c)| ((k * size) as i64, rawcodec::encode(c, 1.0).unwrap()))
            .collect();
        (samples, enc)
    }

    fn frame_of(enc: &[(i64, Vec<u8>)], start: i64, end: i64) -> Vec<u8> {
        let sel: Vec<(i64, &[u8])> = enc
            .iter()
            .filter(|(s, c)| {
                let n = rawcodec::decode(c).unwrap().len() as i64;
                *s < end && s + n > start
            })
            .map(|(s, c)| (*s, c.as_slice()))
            .collect();
        chunk_frame(&sel, start, end).unwrap()
    }

    #[test]
    fn decodes_intervals_bit_for_bit_with_any_thread_count() {
        let (samples, enc) = chunks(100_000, 4096);
        for (start, end) in [
            (0, 100_000),
            (1, 2),
            (4095, 4097),
            (5000, 70_001),
            (99_999, 100_000),
        ] {
            for threads in [1, 3, 4, 64] {
                let frame = frame_of(&enc, start, end);
                let mut out = vec![f64::NAN; (end - start) as usize];
                decode_frame(&frame, start, end, &mut out, threads).unwrap();
                let want = &samples[start as usize..end as usize];
                assert!(out
                    .iter()
                    .zip(want)
                    .all(|(a, b)| a.to_bits() == b.to_bits()));
            }
        }
    }

    #[test]
    fn raw_frames_round_trip() {
        let v = [1.5, -0.0, 3.25];
        let f = raw_frame(&v, 10, 13).unwrap();
        let mut out = [0.0; 3];
        decode_frame(&f, 10, 13, &mut out, 1).unwrap();
        assert_eq!(out.map(f64::to_bits), v.map(f64::to_bits));
    }

    #[test]
    fn gaps_overlaps_and_foreign_intervals_fail() {
        let (_, enc) = chunks(16_384, 4096);
        let mut out = vec![0.0; 16_384];
        // gap: the second chunk left out
        let gap: Vec<(i64, &[u8])> = enc
            .iter()
            .enumerate()
            .filter(|(k, _)| *k != 1)
            .map(|(_, (s, c))| (*s, c.as_slice()))
            .collect();
        let f = chunk_frame(&gap, 0, 16_384).unwrap();
        assert_eq!(
            decode_frame(&f, 0, 16_384, &mut out, 1),
            Err(FrameError::Coverage)
        );
        // short: the last chunk left out
        let f = chunk_frame(&gap[..2], 0, 16_384).unwrap();
        assert_eq!(
            decode_frame(&f, 0, 16_384, &mut out, 1),
            Err(FrameError::Coverage)
        );
        // overlap: a chunk restated one sample later
        let c0 = rawcodec::encode(&[0.0; 4096], 1.0).unwrap();
        let f = chunk_frame(&[(0, &c0), (4095, &c0)], 0, 8191).unwrap();
        let mut o = vec![0.0; 8191];
        assert_eq!(
            decode_frame(&f, 0, 8191, &mut o, 1),
            Err(FrameError::Coverage)
        );
        // a chunk entirely outside the interval
        let f = chunk_frame(&[(0, &c0), (4096, &c0)], 0, 4096).unwrap();
        let mut o = vec![0.0; 4096];
        assert_eq!(
            decode_frame(&f, 0, 4096, &mut o, 1),
            Err(FrameError::Coverage)
        );
        // another interval
        let f = frame_of(&enc, 0, 4096);
        let mut o = vec![0.0; 4095];
        assert!(matches!(
            decode_frame(&f, 1, 4096, &mut o, 1),
            Err(FrameError::Frame(_))
        ));
    }

    #[test]
    fn corruption_is_detected() {
        let (_, enc) = chunks(40_000, 4096);
        let frame = frame_of(&enc, 0, 40_000);
        let mut out = vec![0.0; 40_000];
        for i in (0..frame.len()).step_by(997) {
            let mut f = frame.clone();
            f[i] ^= 0x10;
            assert!(
                decode_frame(&f, 0, 40_000, &mut out, 4).is_err(),
                "byte {i}"
            );
        }
        // a corrupt chunk under a valid frame checksum is still found, with its start
        let sel: Vec<(i64, Vec<u8>)> = enc
            .iter()
            .map(|(s, c)| {
                let mut c = c.clone();
                if *s == 8192 {
                    let k = c.len() / 2;
                    c[k] ^= 1;
                }
                (*s, c)
            })
            .collect();
        let refs: Vec<(i64, &[u8])> = sel.iter().map(|(s, c)| (*s, c.as_slice())).collect();
        let f = chunk_frame(&refs, 0, 40_000).unwrap();
        assert!(matches!(
            decode_frame(&f, 0, 40_000, &mut out, 4),
            Err(FrameError::Chunk {
                start_sample: 8192,
                ..
            })
        ));
        for cut in [0, 5, HEADER, frame.len() - 1] {
            assert!(decode_frame(&frame[..cut], 0, 40_000, &mut out, 1).is_err());
        }
    }
}
