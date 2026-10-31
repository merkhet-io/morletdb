//! Compute side of an external envelope worker (evaluation CW1): decodes encoded raw chunks and
//! encodes `xc-envelope-f16-v1` envelopes with the same morlet-core code the extension calls, so
//! rows are byte-identical to `morlet_build_envelopes`.
//!
//! Requests on stdin, little-endian, repeated until EOF:
//!   u32 chunks; per chunk: i64 start_sample, u32 bytes, encoded chunk;
//!   i64 first_window_start, i64 recording_end, u32 window, u32 stride, u32 max_windows.
//! Response on stdout: u32 windows; per window: i64 start_sample, u32 bytes, envelope.
use morlet_core::{envelope, rawcodec};
use std::io::{self, BufReader, BufWriter, Read, Write};

fn read<const N: usize>(r: &mut impl Read) -> io::Result<[u8; N]> {
    let mut b = [0u8; N];
    r.read_exact(&mut b)?;
    Ok(b)
}

fn main() -> io::Result<()> {
    let mut input = BufReader::new(io::stdin().lock());
    let mut output = BufWriter::new(io::stdout().lock());
    loop {
        let chunks = match read::<4>(&mut input) {
            Ok(b) => u32::from_le_bytes(b),
            Err(e) if e.kind() == io::ErrorKind::UnexpectedEof => return Ok(()),
            Err(e) => return Err(e),
        };
        let mut base: Option<i64> = None;
        let mut samples: Vec<f64> = Vec::new();
        for _ in 0..chunks {
            let start = i64::from_le_bytes(read::<8>(&mut input)?);
            let len = u32::from_le_bytes(read::<4>(&mut input)?) as usize;
            let mut encoded = vec![0u8; len];
            input.read_exact(&mut encoded)?;
            let decoded = rawcodec::decode(&encoded)
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e.to_string()))?;
            let expected = base.unwrap_or(start) + samples.len() as i64;
            if start != expected {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "chunks are not contiguous",
                ));
            }
            base.get_or_insert(start);
            samples.extend_from_slice(&decoded);
        }
        let first = i64::from_le_bytes(read::<8>(&mut input)?);
        let end = i64::from_le_bytes(read::<8>(&mut input)?);
        let window = u32::from_le_bytes(read::<4>(&mut input)?) as i64;
        let stride = u32::from_le_bytes(read::<4>(&mut input)?) as i64;
        let max_windows = u32::from_le_bytes(read::<4>(&mut input)?) as usize;
        let base = base.unwrap_or(first);
        let mut out = Vec::new();
        let mut s = first;
        while s + window <= end && out.len() < max_windows {
            let (a, b) = ((s - base) as usize, (s - base + window) as usize);
            if b > samples.len() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "window beyond the chunks sent",
                ));
            }
            let env = envelope::encode(&samples[a..b])
                .map_err(|e| io::Error::new(io::ErrorKind::InvalidData, e.to_string()))?;
            out.push((s, env));
            s += stride;
        }
        output.write_all(&(out.len() as u32).to_le_bytes())?;
        for (s, env) in out {
            output.write_all(&s.to_le_bytes())?;
            output.write_all(&(env.len() as u32).to_le_bytes())?;
            output.write_all(&env)?;
        }
        output.flush()?;
    }
}
