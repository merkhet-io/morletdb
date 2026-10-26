//! Lossless storage encoding for raw chunks.
//!
//! Samples that lie exactly on a recording's sample grid (integer ADC counts,
//! fixed decimals, a dyadic ADC step) or within a thousandth of a step of it
//! (floats scaled by a calibrated step) are stored as integers with a fixed
//! polynomial predictor and partitioned Rice coding, the scheme FLAC uses for
//! its fixed subframes, plus last-place corrections when needed. A chunk with any sample off the grid codes the float64
//! bit patterns, mapped to order-preserving integers, with the same coder, or is
//! stored as plain float64 when that is not smaller. Every kind decodes bitwise.
//! The stage-1 compression benchmark (evaluation/compression) motivates this:
//! a standard codec on native integers beat every decomposition codec tested.
use crate::{validate, SignalError, MAX_SAMPLES};
use std::fmt;

/// Version 2 appends a CRC-32C of all preceding bytes. Version 1 (no checksum)
/// existed only on an unreleased development branch and is rejected, never
/// reinterpreted.
pub const FORMAT_VERSION: u8 = 2;
const CHECKSUM_BYTES: usize = 4;
const KIND_RAW: u8 = 0;
const KIND_GRID: u8 = 1;
const KIND_FLOAT: u8 = 2;
const MODE_MUL: u8 = 0;
const MODE_DIV: u8 = 1;
const MAX_ORDER: usize = 4;
const MAX_PARTITION_ORDER: u32 = 8;
const ESCAPE: u64 = 63;
/// Samples within this fraction of a step of a grid point are coded on the grid.
const NEAR_GRID: f64 = 1e-3;
/// Integers above 2^53 do not convert to float64 exactly.
const MAX_GRID_INDEX: f64 = 9_007_199_254_740_992.0;

#[derive(Debug, PartialEq, Eq)]
pub enum CodecError {
    Signal(SignalError),
    InvalidQuantum,
    UnsupportedVersion(u8),
    Corrupt(&'static str),
}
impl fmt::Display for CodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Signal(e) => e.fmt(f),
            Self::InvalidQuantum => f.write_str("sample_quantum must be finite and positive"),
            Self::UnsupportedVersion(v) => write!(
                f,
                "unsupported encoded chunk format version {v}; re-append the samples"
            ),
            Self::Corrupt(what) => write!(f, "corrupt encoded chunk: {what}"),
        }
    }
}
impl std::error::Error for CodecError {}

/// How an encoded chunk stores its samples.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Grid,
    Float,
    Raw,
}
impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Grid => "grid",
            Self::Float => "float",
            Self::Raw => "raw",
        }
    }
}

/// Grid index <-> sample mapping. A quantum of 1/d for integer d decodes as
/// k / d, so decimal grids such as 0.001 reproduce parsed values exactly.
#[derive(Clone, Copy)]
struct Grid {
    mode: u8,
    scale: f64,
}
impl Grid {
    fn new(quantum: f64) -> Result<Self, CodecError> {
        if !quantum.is_finite() || quantum <= 0.0 {
            return Err(CodecError::InvalidQuantum);
        }
        let inverse = 1.0 / quantum;
        let rounded = inverse.round();
        if (1.0..=MAX_GRID_INDEX).contains(&rounded) && (inverse - rounded).abs() <= 1e-9 * rounded
        {
            Ok(Self {
                mode: MODE_DIV,
                scale: rounded,
            })
        } else {
            Ok(Self {
                mode: MODE_MUL,
                scale: quantum,
            })
        }
    }
    fn step(self) -> f64 {
        if self.mode == MODE_DIV {
            1.0 / self.scale
        } else {
            self.scale
        }
    }
    /// Nearest grid index, if `x` is within NEAR_GRID steps of it. The exact
    /// float64 is restored from a correction in units of the last place.
    fn index(self, x: f64) -> Option<i64> {
        let k = if self.mode == MODE_DIV {
            (x * self.scale).round()
        } else {
            (x / self.scale).round()
        };
        if !k.is_finite() || k.abs() > MAX_GRID_INDEX {
            return None;
        }
        let k = k as i64;
        ((self.value(k) - x).abs() <= NEAR_GRID * self.step()).then_some(k)
    }
    fn value(self, k: i64) -> f64 {
        if self.mode == MODE_DIV {
            k as f64 / self.scale
        } else {
            k as f64 * self.scale
        }
    }
}

/// Encodes validated samples. The result decodes bitwise to the input.
pub fn encode(samples: &[f64], quantum: f64) -> Result<Vec<u8>, CodecError> {
    let mut out = encode_body(samples, quantum)?;
    let checksum = crc32c(&out);
    out.extend_from_slice(&checksum.to_le_bytes());
    Ok(out)
}

fn encode_body(samples: &[f64], quantum: f64) -> Result<Vec<u8>, CodecError> {
    validate(samples).map_err(CodecError::Signal)?;
    let grid = Grid::new(quantum)?;
    let mut out = vec![FORMAT_VERSION];
    let indices: Option<Vec<i64>> = samples.iter().map(|&x| grid.index(x)).collect();
    match indices {
        Some(ints) => {
            // Data scaled in float64 by a slightly different step (e.g. a
            // calibrated ADC step) needs no corrections at the fitted step.
            let mut candidates = vec![grid];
            // x / k at the largest |k| is within an ulp or two of such a step.
            let (k_max, x_max) = ints
                .iter()
                .zip(samples)
                .max_by_key(|(k, _)| k.unsigned_abs())
                .expect("nonempty chunk");
            let fitted = x_max / *k_max as f64;
            if grid.mode == MODE_MUL && fitted.is_finite() && fitted > 0.0 {
                let mut s = fitted;
                for _ in 0..2 {
                    s = f64::from_bits(s.to_bits() - 1);
                }
                for _ in 0..5 {
                    candidates.push(Grid {
                        mode: MODE_MUL,
                        scale: s,
                    });
                    s = f64::from_bits(s.to_bits() + 1);
                }
            }
            let corrections = |g: Grid| -> Vec<i64> {
                ints.iter()
                    .zip(samples)
                    .map(|(&k, &x)| ordered_bits(x).wrapping_sub(ordered_bits(g.value(k))))
                    .collect()
            };
            let cost = |c: &Vec<i64>| {
                let nonzero = c.iter().filter(|&&v| v != 0).count();
                let size: u128 = c.iter().map(|&v| v.unsigned_abs() as u128).sum();
                (nonzero, size)
            };
            let (grid, fix) = candidates
                .into_iter()
                .map(|g| (g, corrections(g)))
                .min_by_key(|(_, c)| cost(c))
                .expect("at least one candidate");
            out.push(KIND_GRID);
            put_varint(&mut out, samples.len() as u64);
            out.push(grid.mode);
            out.extend_from_slice(&grid.scale.to_le_bytes());
            let has_fix = fix.iter().any(|&v| v != 0);
            out.push(has_fix as u8);
            let mut stream = Vec::new();
            encode_ints(&ints, &mut stream);
            put_varint(&mut out, stream.len() as u64);
            out.extend(stream);
            if has_fix {
                encode_ints(&fix, &mut out);
            }
            if out.len() < 3 + 8 * samples.len() {
                return Ok(out);
            }
            out.truncate(1);
            encode_off_grid(samples, &mut out);
        }
        None => encode_off_grid(samples, &mut out),
    }
    Ok(out)
}

fn encode_off_grid(samples: &[f64], out: &mut Vec<u8>) {
    out.push(KIND_FLOAT);
    put_varint(out, samples.len() as u64);
    let ordered: Vec<i64> = samples.iter().map(|&x| ordered_bits(x)).collect();
    encode_ints(&ordered, out);
    if out.len() >= 3 + 8 * samples.len() {
        // Plain little-endian float64, the byte layout of a float8[] payload.
        out.truncate(1);
        out.push(KIND_RAW);
        put_varint(out, samples.len() as u64);
        out.extend(samples.iter().flat_map(|x| x.to_le_bytes()));
    }
}

/// The checksummed body (version byte through payload) of a version-2 chunk.
fn verified(encoded: &[u8]) -> Result<&[u8], CodecError> {
    match encoded.first() {
        None => return Err(CodecError::Corrupt("empty")),
        Some(&FORMAT_VERSION) => {}
        Some(&v) if v < FORMAT_VERSION => return Err(CodecError::UnsupportedVersion(v)),
        Some(_) => return Err(CodecError::Corrupt("unknown format version")),
    }
    if encoded.len() < 3 + CHECKSUM_BYTES {
        return Err(CodecError::Corrupt("truncated"));
    }
    let (body, tail) = encoded.split_at(encoded.len() - CHECKSUM_BYTES);
    if crc32c(body).to_le_bytes() != tail {
        return Err(CodecError::Corrupt("checksum mismatch"));
    }
    Ok(body)
}

/// Verifies the checksum and returns the chunk's storage kind.
pub fn kind(encoded: &[u8]) -> Result<Kind, CodecError> {
    kind_of(verified(encoded)?)
}
fn kind_of(body: &[u8]) -> Result<Kind, CodecError> {
    match body[1] {
        KIND_GRID => Ok(Kind::Grid),
        KIND_FLOAT => Ok(Kind::Float),
        KIND_RAW => Ok(Kind::Raw),
        _ => Err(CodecError::Corrupt("unknown kind")),
    }
}

/// Verifies the checksum, then decodes and validates the samples.
pub fn decode(encoded: &[u8]) -> Result<Vec<f64>, CodecError> {
    let encoded = verified(encoded)?;
    let kind = kind_of(encoded)?;
    let mut pos = 2;
    let n = get_varint(encoded, &mut pos)?;
    if n == 0 || n > MAX_SAMPLES as u64 {
        return Err(CodecError::Corrupt("sample count"));
    }
    let n = n as usize;
    let samples: Vec<f64> = match kind {
        Kind::Raw => {
            let body = encoded.get(pos..).ok_or(CodecError::Corrupt("truncated"))?;
            if body.len() != 8 * n {
                return Err(CodecError::Corrupt("raw length"));
            }
            body.chunks_exact(8)
                .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
                .collect()
        }
        Kind::Float => decode_ints(&encoded[pos..], n)?
            .into_iter()
            .map(float_from_ordered)
            .collect(),
        Kind::Grid => {
            let header = encoded
                .get(pos..pos + 9)
                .ok_or(CodecError::Corrupt("truncated"))?;
            let mode = header[0];
            let scale = f64::from_le_bytes(header[1..9].try_into().unwrap());
            if mode > MODE_DIV || !scale.is_finite() || scale <= 0.0 {
                return Err(CodecError::Corrupt("grid header"));
            }
            let grid = Grid { mode, scale };
            pos += 9;
            let has_fix = *encoded.get(pos).ok_or(CodecError::Corrupt("truncated"))?;
            pos += 1;
            if has_fix > 1 {
                return Err(CodecError::Corrupt("grid header"));
            }
            let len = get_varint(encoded, &mut pos)?;
            let end = pos
                .checked_add(usize::try_from(len).map_err(|_| CodecError::Corrupt("length"))?)
                .filter(|&e| e <= encoded.len())
                .ok_or(CodecError::Corrupt("truncated"))?;
            let ints = decode_ints(&encoded[pos..end], n)?;
            if has_fix == 1 {
                let fix = decode_ints(&encoded[end..], n)?;
                ints.iter()
                    .zip(fix)
                    .map(|(&k, c)| float_from_ordered(ordered_bits(grid.value(k)).wrapping_add(c)))
                    .collect()
            } else if end != encoded.len() {
                return Err(CodecError::Corrupt("trailing data"));
            } else {
                ints.iter().map(|&k| grid.value(k)).collect()
            }
        }
    };
    validate(&samples).map_err(CodecError::Signal)?;
    Ok(samples)
}

/// CRC-32C (Castagnoli, reflected polynomial 0x82F63B78), as used by iSCSI and ext4.
const fn crc32c_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut bit = 0;
        while bit < 8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0x82F6_3B78
            } else {
                c >> 1
            };
            bit += 1;
        }
        table[i] = c;
        i += 1;
    }
    table
}
const CRC32C_TABLE: [u32; 256] = crc32c_table();
fn crc32c(bytes: &[u8]) -> u32 {
    !bytes.iter().fold(!0u32, |c, &b| {
        CRC32C_TABLE[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8)
    })
}

/// Monotonic float64 -> i64 map: +0 is 0, -0 is -1, and numeric order is kept,
/// so smooth signals within a binade give small prediction residuals.
fn ordered_bits(x: f64) -> i64 {
    let bits = x.to_bits();
    if bits >> 63 == 1 {
        -((bits & !(1 << 63)) as i64) - 1
    } else {
        bits as i64
    }
}
fn float_from_ordered(o: i64) -> f64 {
    if o < 0 {
        f64::from_bits((o.wrapping_add(1).wrapping_neg() as u64) | (1 << 63))
    } else {
        f64::from_bits(o as u64)
    }
}

fn predict(x: &[i64], i: usize, order: usize) -> i64 {
    match order.min(i) {
        0 => 0,
        1 => x[i - 1],
        2 => x[i - 1].wrapping_mul(2).wrapping_sub(x[i - 2]),
        3 => x[i - 1]
            .wrapping_sub(x[i - 2])
            .wrapping_mul(3)
            .wrapping_add(x[i - 3]),
        _ => x[i - 1]
            .wrapping_add(x[i - 3])
            .wrapping_mul(4)
            .wrapping_sub(x[i - 2].wrapping_mul(6))
            .wrapping_sub(x[i - 4]),
    }
}

fn zigzag(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}
fn unzigzag(u: u64) -> i64 {
    ((u >> 1) as i64) ^ -((u & 1) as i64)
}

/// Rice parameter (or ESCAPE) and its estimated bit cost for one partition.
fn best_param(sum: u128, max: u64, count: usize) -> (u64, u128) {
    let m = count as u128;
    let mut best = (0, u128::MAX);
    for k in 0..ESCAPE {
        // floor(v >> k) summed is within `count` of (sum >> k).
        let cost = m * (k as u128 + 1) + (sum >> k);
        if cost < best.1 {
            best = (k, cost);
        }
    }
    let width = 64 - max.leading_zeros() as u128;
    let escape = 7 + m * width;
    if escape < best.1 {
        best = (ESCAPE, escape);
    }
    (best.0, best.1 + 6)
}

fn partition_bounds(n: usize, porder: u32, p: usize) -> (usize, usize) {
    ((p * n) >> porder, ((p + 1) * n) >> porder)
}

fn encode_ints(x: &[i64], out: &mut Vec<u8>) {
    let n = x.len();
    let max_porder = MAX_PARTITION_ORDER.min(usize::BITS - 1 - n.leading_zeros());
    let mut best: Option<(u128, usize, u32, Vec<u64>)> = None;
    for order in 0..=MAX_ORDER {
        let r: Vec<u64> = (0..n)
            .map(|i| zigzag(x[i].wrapping_sub(predict(x, i, order))))
            .collect();
        for porder in 0..=max_porder {
            let parts = 1usize << porder;
            let mut cost = 0u128;
            for p in 0..parts {
                let (a, b) = partition_bounds(n, porder, p);
                let chunk = &r[a..b];
                let sum = chunk.iter().map(|&v| v as u128).sum();
                let max = chunk.iter().copied().max().unwrap_or(0);
                cost += best_param(sum, max, chunk.len()).1;
            }
            if best.as_ref().is_none_or(|b| cost < b.0) {
                best = Some((cost, order, porder, r.clone()));
            }
        }
    }
    let (_, order, porder, r) = best.expect("at least one candidate");
    let mut w = BitWriter::default();
    w.put(order as u64, 3);
    w.put(porder as u64, 4);
    for p in 0..(1usize << porder) {
        let (a, b) = partition_bounds(n, porder, p);
        let chunk = &r[a..b];
        let sum = chunk.iter().map(|&v| v as u128).sum();
        let max = chunk.iter().copied().max().unwrap_or(0);
        let (k, _) = best_param(sum, max, chunk.len());
        w.put(k, 6);
        if k == ESCAPE {
            let width = 64 - max.leading_zeros();
            w.put(width as u64, 7);
            for &v in chunk {
                w.put(v, width);
            }
        } else {
            for &v in chunk {
                w.put_unary(v >> k);
                w.put(v, k as u32);
            }
        }
    }
    out.extend(w.finish());
}

fn decode_ints(bytes: &[u8], n: usize) -> Result<Vec<i64>, CodecError> {
    let mut rd = BitReader::new(bytes);
    let order = rd.get(3)? as usize;
    let porder = rd.get(4)? as u32;
    if order > MAX_ORDER || porder > MAX_PARTITION_ORDER || (n >> porder) == 0 {
        return Err(CodecError::Corrupt("predictor header"));
    }
    let mut x = Vec::with_capacity(n);
    for p in 0..(1usize << porder) {
        let (a, b) = partition_bounds(n, porder, p);
        let k = rd.get(6)?;
        let width = if k == ESCAPE {
            let width = rd.get(7)? as u32;
            if width > 64 {
                return Err(CodecError::Corrupt("escape width"));
            }
            width
        } else {
            0
        };
        for i in a..b {
            let v = if k == ESCAPE {
                rd.get(width)?
            } else {
                let q = rd.get_unary()?;
                if q > (u64::MAX >> k) {
                    return Err(CodecError::Corrupt("rice quotient"));
                }
                (q << k) | rd.get(k as u32)?
            };
            let value = unzigzag(v).wrapping_add(predict(&x, i, order));
            x.push(value);
        }
    }
    if !rd.rest_is_padding() {
        return Err(CodecError::Corrupt("trailing data"));
    }
    Ok(x)
}

fn put_varint(out: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        out.push((v as u8) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}
fn get_varint(bytes: &[u8], pos: &mut usize) -> Result<u64, CodecError> {
    let mut v = 0u64;
    for shift in (0..64).step_by(7) {
        let b = *bytes.get(*pos).ok_or(CodecError::Corrupt("truncated"))?;
        *pos += 1;
        v |= ((b & 0x7f) as u64) << shift;
        if b < 0x80 {
            return Ok(v);
        }
    }
    Err(CodecError::Corrupt("varint"))
}

#[derive(Default)]
struct BitWriter {
    out: Vec<u8>,
    acc: u64,
    bits: u32,
}
impl BitWriter {
    /// Appends the low `n` bits of `v`, most significant first.
    fn put(&mut self, v: u64, n: u32) {
        if n > 32 {
            self.put(v >> 32, n - 32);
            self.put(v & 0xffff_ffff, 32);
            return;
        }
        let v = if n == 0 {
            0
        } else {
            v & (u64::MAX >> (64 - n))
        };
        self.acc = (self.acc << n) | v;
        self.bits += n;
        while self.bits >= 8 {
            self.bits -= 8;
            self.out.push((self.acc >> self.bits) as u8);
        }
        self.acc &= (1u64 << self.bits) - 1;
    }
    fn put_unary(&mut self, mut q: u64) {
        while q >= 32 {
            self.put(0, 32);
            q -= 32;
        }
        self.put(1, q as u32 + 1);
    }
    fn finish(mut self) -> Vec<u8> {
        if self.bits > 0 {
            self.out.push((self.acc << (8 - self.bits)) as u8);
        }
        self.out
    }
}

struct BitReader<'a> {
    bytes: &'a [u8],
    pos: usize,
}
impl<'a> BitReader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, pos: 0 }
    }
    fn bit(&mut self) -> Result<u64, CodecError> {
        let byte = *self
            .bytes
            .get(self.pos / 8)
            .ok_or(CodecError::Corrupt("truncated bitstream"))?;
        let b = (byte >> (7 - self.pos % 8)) & 1;
        self.pos += 1;
        Ok(b as u64)
    }
    fn get(&mut self, n: u32) -> Result<u64, CodecError> {
        let mut v = 0;
        for _ in 0..n {
            v = (v << 1) | self.bit()?;
        }
        Ok(v)
    }
    fn get_unary(&mut self) -> Result<u64, CodecError> {
        let mut q = 0;
        while self.bit()? == 0 {
            q += 1;
        }
        Ok(q)
    }
    fn rest_is_padding(&self) -> bool {
        let used = self.pos.div_ceil(8);
        used == self.bytes.len()
            && (self.pos.is_multiple_of(8) || self.bytes[used - 1] & (0xff >> (self.pos % 8)) == 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lcg(seed: &mut u64) -> u64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *seed >> 11
    }
    fn roundtrip(samples: &[f64], quantum: f64) -> (Kind, usize) {
        let encoded = encode(samples, quantum).unwrap();
        let decoded = decode(&encoded).unwrap();
        assert_eq!(decoded.len(), samples.len());
        for (a, b) in decoded.iter().zip(samples) {
            assert_eq!(a.to_bits(), b.to_bits());
        }
        (kind(&encoded).unwrap(), encoded.len())
    }

    #[test]
    fn integer_counts_use_the_grid_and_compress() {
        let mut seed = 1;
        let samples: Vec<f64> = (0..32768)
            .map(|i| ((i as f64 / 40.).sin() * 3000. + (lcg(&mut seed) % 64) as f64 - 32.).round())
            .collect();
        let (kind, bytes) = roundtrip(&samples, 1.0);
        assert_eq!(kind, Kind::Grid);
        // Uniform 64-level noise costs about 8 bits/sample after prediction; float8 costs 64.
        assert!(bytes * 8 < samples.len() * 10, "{bytes} bytes");
    }

    #[test]
    fn decimal_and_dyadic_grids_are_exact() {
        // Parsed decimals: k * 0.001 differs from the parsed value for some k; k / 1000 does not.
        let decimals: Vec<f64> = (-3000..3000)
            .map(|k| format!("{:.3}", k as f64 / 1000.).parse().unwrap())
            .collect();
        assert!(decimals
            .iter()
            .enumerate()
            .any(|(i, &x)| ((i as i64 - 3000) as f64 * 0.001).to_bits() != x.to_bits()));
        assert_eq!(roundtrip(&decimals, 0.001).0, Kind::Grid);
        let step = 100.0 / 32768.0;
        let adc: Vec<f64> = (-2000..2000).map(|k| k as f64 * step).collect();
        assert_eq!(roundtrip(&adc, step).0, Kind::Grid);
    }

    #[test]
    fn calibrated_and_rounded_floats_stay_on_the_grid() {
        let mut seed = 11;
        let nominal = 100.0 / 32768.0;
        let calibrated = nominal * (1.0 - 7e-10);
        let counts: Vec<i64> = (0..32768)
            .map(|i| ((i as f64 / 50.).sin() * 2000.) as i64 + (lcg(&mut seed) % 33) as i64 - 16)
            .collect();
        let exact: Vec<f64> = counts.iter().map(|&k| k as f64 * calibrated).collect();
        let (kind, exact_bytes) = roundtrip(&exact, nominal);
        assert_eq!(kind, Kind::Grid);
        assert!(exact_bytes * 8 < 32768 * 8, "{exact_bytes}");
        // One-ulp noise, as from float32 or a second rounding step, costs corrections only.
        let noisy: Vec<f64> = exact
            .iter()
            .map(|&x| match lcg(&mut seed) % 4 {
                0 => f64::from_bits(x.to_bits() + 1),
                1 if x != 0.0 => f64::from_bits(x.to_bits() - 1),
                _ => x,
            })
            .collect();
        let (kind, noisy_bytes) = roundtrip(&noisy, nominal);
        assert_eq!(kind, Kind::Grid);
        assert!(noisy_bytes < exact_bytes + 32768 / 8 * 3, "{noisy_bytes}");
    }

    #[test]
    fn off_grid_chunks_code_float_bits_or_fall_back_to_raw() {
        let mut samples: Vec<f64> = (0..1000).map(|i| i as f64).collect();
        samples[500] = 0.5;
        assert_eq!(roundtrip(&samples, 1.0).0, Kind::Float);
        for x in [
            0.0,
            -0.0,
            1e-300,
            -1e-300,
            1e9,
            -1e9,
            f64::MIN_POSITIVE,
            -5e-324,
        ] {
            assert_eq!(float_from_ordered(ordered_bits(x)).to_bits(), x.to_bits());
        }
        assert!(ordered_bits(-1.0) < ordered_bits(-0.0) && ordered_bits(-0.0) < ordered_bits(0.0));
        // Negative zeros stay on the grid and keep their sign bit.
        assert_eq!(roundtrip(&[1.0, -0.0, 2.0, -0.0, -0.0], 1.0).0, Kind::Grid);
        roundtrip(&[-0.0], 0.001);
        assert_eq!(roundtrip(&[-0.0; 64], 0.001).0, Kind::Grid);
        let smooth: Vec<f64> = (0..4096).map(|i| (i as f64 / 300.).sin() + 2.0).collect();
        let (kind, bytes) = roundtrip(&smooth, 1.0);
        assert!(kind == Kind::Float && bytes < 4 * 4096, "{kind:?} {bytes}");
        // Full-entropy mantissas do not compress: plain float64 bounds the size.
        let mut seed = 3;
        let noise: Vec<f64> = (0..777)
            .map(|_| (lcg(&mut seed) as f64 / 9_007_199_254_740_992.0 - 0.5) * 2e9)
            .collect();
        assert_eq!(
            roundtrip(&noise, 1.0),
            (Kind::Raw, 2 + 2 + 8 * 777 + CHECKSUM_BYTES)
        );
    }

    #[test]
    fn extremes_and_every_partition_shape_roundtrip() {
        let mut seed = 7;
        for n in [1, 2, 3, 5, 255, 256, 257, 4095, 32768] {
            let noise: Vec<f64> = (0..n)
                .map(|_| (lcg(&mut seed) % 2_000_000_001) as f64 - 1e9)
                .collect();
            roundtrip(&noise, 1.0);
            let steps: Vec<f64> = (0..n)
                .map(|i| if i % 97 == 0 { 1e9 } else { -1e9 })
                .collect();
            roundtrip(&steps, 1.0);
            roundtrip(&vec![0.0; n], 1.0);
        }
        // Tiny quanta push indices to the 2^53 limit, beyond it the chunk is raw.
        roundtrip(&[1e9, -1e9, 0.0], 1e-6);
        assert_eq!(roundtrip(&[1e9, 1.0], 1e-9).0, Kind::Raw);
    }

    #[test]
    fn rejects_invalid_quanta() {
        assert_eq!(encode(&[1.0], 0.0), Err(CodecError::InvalidQuantum));
        assert_eq!(encode(&[1.0], f64::NAN), Err(CodecError::InvalidQuantum));
    }

    fn seal(mut body: Vec<u8>) -> Vec<u8> {
        let checksum = crc32c(&body);
        body.extend_from_slice(&checksum.to_le_bytes());
        body
    }

    #[test]
    fn checksum_detects_every_mutation_kind() {
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
        let mut seed = 5;
        for samples in [
            (0..1000).map(|i| (i % 17) as f64).collect::<Vec<f64>>(),
            (0..1000).map(|i| (i as f64 / 9.).sin()).collect(),
            vec![0.5, -0.0, 1e9],
        ] {
            let good = encode(&samples, 1.0).unwrap();
            // Single-bit flips in the version, kind, header, payload and checksum.
            for byte in 0..good.len() {
                for bit in 0..8 {
                    let mut bad = good.clone();
                    bad[byte] ^= 1 << bit;
                    assert!(decode(&bad).is_err(), "flip {byte}:{bit}");
                    assert!(kind(&bad).is_err());
                }
            }
            // Multi-byte overwrites.
            for _ in 0..2000 {
                let mut bad = good.clone();
                let at = (lcg(&mut seed) as usize) % bad.len();
                let len = 1 + (lcg(&mut seed) as usize) % 8;
                for b in bad.iter_mut().skip(at).take(len) {
                    *b = lcg(&mut seed) as u8;
                }
                if bad != good {
                    assert!(decode(&bad).is_err());
                }
            }
            for cut in 0..good.len() {
                assert!(decode(&good[..cut]).is_err(), "truncated to {cut}");
            }
            for extra in 1..=8 {
                let mut long = good.clone();
                long.extend(std::iter::repeat_n(0u8, extra));
                assert!(decode(&long).is_err());
            }
        }
    }

    #[test]
    fn checksum_catches_mutations_that_still_decode_to_valid_samples() {
        let samples = [0.25f64, 0.5, 0.75];
        let raw = seal({
            let mut body = vec![FORMAT_VERSION, KIND_RAW, 3];
            body.extend(samples.iter().flat_map(|x| x.to_le_bytes()));
            body
        });
        let mut bad = raw.clone();
        bad[3] ^= 1; // lowest mantissa bit of the first sample
        let body = &bad[..bad.len() - CHECKSUM_BYTES];
        // Structurally the mutated body still decodes to valid, different samples ...
        assert!(decode(&seal(body.to_vec())).unwrap()[0] != samples[0]);
        // ... so only the checksum detects it.
        assert_eq!(decode(&bad), Err(CodecError::Corrupt("checksum mismatch")));
    }

    #[test]
    fn version_one_is_rejected_not_reinterpreted() {
        let mut v1 = encode(&[1.0, 2.0, 3.0], 1.0).unwrap();
        v1.truncate(v1.len() - CHECKSUM_BYTES);
        v1[0] = 1;
        assert_eq!(decode(&v1), Err(CodecError::UnsupportedVersion(1)));
        assert_eq!(kind(&v1), Err(CodecError::UnsupportedVersion(1)));
        assert_eq!(
            decode(&[3, 1, 1, 0, 0, 0, 0]),
            Err(CodecError::Corrupt("unknown format version"))
        );
    }

    #[test]
    fn structural_checks_still_apply_behind_a_valid_checksum() {
        assert!(matches!(encode(&[], 1.0), Err(CodecError::Signal(_))));
        assert!(matches!(
            encode(&[f64::INFINITY], 1.0),
            Err(CodecError::Signal(_))
        ));
        let mut raw = vec![FORMAT_VERSION, KIND_RAW, 2];
        raw.extend(f64::INFINITY.to_le_bytes());
        raw.extend(1.5f64.to_le_bytes());
        assert!(matches!(
            decode(&seal(raw.clone())),
            Err(CodecError::Signal(_))
        ));
        raw.pop();
        assert_eq!(decode(&seal(raw)), Err(CodecError::Corrupt("raw length")));
        assert_eq!(
            decode(&seal(vec![FORMAT_VERSION, 9, 1])),
            Err(CodecError::Corrupt("unknown kind"))
        );
        let grid = encode(&[1.0, 2.0, 3.0, 4.0], 1.0).unwrap();
        let mut body = grid[..grid.len() - CHECKSUM_BYTES].to_vec();
        body.push(0);
        assert!(decode(&seal(body)).is_err());
    }
}
