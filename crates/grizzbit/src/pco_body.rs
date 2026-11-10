//! The body of a version-4 (Grizzbit 2) chunk: pco's wrapped format, one pco chunk with one page, written and
//! read with the `pco` crate pinned at 1.0.4 (study B5, evaluation/storage_b5/PROTOCOL.md).
//!
//! ```text
//! body = pco wrapped header | pco chunk metadata | pco page (all n samples)
//! ```
//!
//! The wrapped format stores no sample count; the frame around the body carries it (`n`), as pco's wrapped
//! format requires of its wrapper. Compression level 8; mode and delta detection at pco's defaults; one page,
//! since pco's default page limit (2^18 numbers) is above a chunk's 32,768 samples.
//!
//! Under Kani these functions are replaced by stubs (bottom of this file): the proofs cover MorletDB's frame
//! code around the body, not pco itself (docs/verification.md).

/// pco's compression level for version-4 chunks (B5's frozen build).
pub const LEVEL: usize = 8;

/// The largest pco lookback window (log2 of its numbers) a version-4 chunk may declare: what pco 1.0.4's
/// encoder writes at most (`ENCODING_LOOKBACK_MAX_WINDOW_N_LOG`), and 2^15 is at least a chunk's 32,768
/// samples, so no chunk the encoder writes is refused. Larger windows are refused before pco allocates them.
#[cfg(not(kani))]
const MAX_LOOKBACK_WINDOW_N_LOG: u32 = 15;

/// The pco body of `samples` (1..=32,768 valid samples), or `None` if pco refuses them (it does not for valid
/// float64 samples; the keep-the-smaller encoder then keeps Grizzbit 1).
#[cfg(not(kani))]
pub fn compress(samples: &[f64], out: Vec<u8>) -> Option<Vec<u8>> {
    use pco::wrapped::FileCompressor;
    use pco::ChunkConfig;
    let config = ChunkConfig::default().with_compression_level(LEVEL);
    let fc = FileCompressor::default();
    let out = fc.write_header(out).ok()?;
    let mut cc = fc.chunk_compressor(samples, &config).ok()?;
    if cc.n_per_page() != [samples.len()] {
        return None;
    }
    let out = cc.write_meta(out).ok()?;
    cc.write_page(0, out).ok()
}

/// The dictionary length a pco body declares, read before pco reads it, or `None` if its mode is not a
/// dictionary. pco allocates the declared dictionary (up to 2^25 numbers, 256 MiB) before checking that the
/// body holds it, so a few corrupt bytes would cost that much memory. Layout (pco format 4.x, as written by
/// pco 1.0.4): the wrapped header is the major format version byte, plus a minor version byte when the major
/// is 4 or more; the chunk metadata starts at the next byte with the mode, read least significant bit first:
/// a 4-bit variant, and for the dictionary mode (4) a 25-bit length. Missing bytes read as zero bits.
#[cfg(not(kani))]
pub(crate) fn dictionary_len(body: &[u8]) -> Option<u64> {
    let major = *body.first()?;
    let meta = body.get(if major >= 4 { 2 } else { 1 }..).unwrap_or(&[]);
    let word = meta
        .iter()
        .take(4)
        .enumerate()
        .fold(0u64, |w, (i, &b)| w | (u64::from(b) << (8 * i)));
    (word & 0xF == 4).then_some((word >> 4) & ((1 << 25) - 1))
}

/// Decodes a pco body that must hold exactly `n` samples and nothing after them.
#[cfg(not(kani))]
pub fn decompress(body: &[u8], n: usize) -> Result<Vec<f64>, &'static str> {
    use pco::metadata::{DynLatents, Mode};
    use pco::wrapped::FileDecompressor;
    // A dictionary holds distinct sample values, so never more entries than the chunk has samples.
    let dictionary = dictionary_len(body);
    if dictionary.is_some_and(|k| k > n as u64) {
        return Err("pco dictionary");
    }
    let (fd, rest) = FileDecompressor::new(body).map_err(|_| "pco header")?;
    let (mut cd, rest) = fd
        .chunk_decompressor::<f64, _>(rest)
        .map_err(|_| "pco chunk metadata")?;
    // The pre-read above must agree with what pco read.
    let read = match &cd.meta().mode {
        Mode::Dict(DynLatents::U8(d)) => Some(d.len() as u64),
        Mode::Dict(DynLatents::U16(d)) => Some(d.len() as u64),
        Mode::Dict(DynLatents::U32(d)) => Some(d.len() as u64),
        Mode::Dict(DynLatents::U64(d)) => Some(d.len() as u64),
        Mode::Dict(_) => return Err("pco dictionary"),
        _ => None,
    };
    if read != dictionary {
        return Err("pco dictionary");
    }
    // pco's reader accepts lookback windows up to 2^24 numbers and allocates the window before reading the
    // page (128 MiB or more from a few bytes); its encoder writes at most 2^15, which also covers a whole chunk.
    if let pco::metadata::DeltaEncoding::Lookback { config, .. } = &cd.meta().delta_encoding {
        if config.window_n_log > MAX_LOOKBACK_WINDOW_N_LOG {
            return Err("pco lookback window");
        }
    }
    let mut out = vec![0.0; n];
    let mut pd = cd.page_decompressor(rest, n).map_err(|_| "pco page")?;
    let progress = pd.read(&mut out).map_err(|_| "pco page")?;
    if !progress.finished || progress.n_processed != n {
        return Err("pco sample count");
    }
    if !pd.into_src().is_empty() {
        return Err("trailing data");
    }
    Ok(out)
}

// Kani stubs: pco is not proved. `compress` appends a nondeterministic body; `decompress` returns either an
// error or a nondeterministic sample vector of a nondeterministic length (at most n + 1, so the frame code's
// length check is exercised), whatever the body.
#[cfg(kani)]
pub fn compress(_samples: &[f64], mut out: Vec<u8>) -> Option<Vec<u8>> {
    if kani::any() {
        return None;
    }
    let len: usize = kani::any();
    kani::assume(len <= 2);
    for _ in 0..len {
        out.push(kani::any());
    }
    Some(out)
}

#[cfg(kani)]
pub fn decompress(_body: &[u8], n: usize) -> Result<Vec<f64>, &'static str> {
    if kani::any() {
        return Err("pco stub");
    }
    let len: usize = kani::any();
    kani::assume(len <= n + 1 && len <= 3);
    let mut out = Vec::new();
    for _ in 0..len {
        out.push(f64::from_bits(kani::any()));
    }
    Ok(out)
}
