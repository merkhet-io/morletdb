//! Chunk format version 6 (DRAFT, under study B8): float32 codings.
//!
//! A version-6 chunk holds samples that are each exactly a float32 value widened to float64. The encoder
//! writes one only for a chunk whose every sample equals its float32 rounding, and only where it is strictly
//! smaller than the chunk the configured format writes without it ([`crate::encode_with_float32`]).
//!
//! # Layout (before the CRC-32C that every chunk ends with)
//!
//! ```text
//! byte 0     6 (format version)
//! byte 1     kind: 1 = float32 grid, 3 = float32 sign/magnitude
//! varint     n, the sample count, 1..=32768
//! grid:      offset f64 LE, scale f64 LE (both finite, scale > 0)
//!            varint L, then L bytes: the index stream k (n integers)
//!            byte has_fix (0 or 1); if 1, the rest is the correction stream c (n integers)
//!            sample i = from_ord32(ord32(f32(offset + k[i] * scale)) + c[i])   (c = 0 without corrections)
//! sign/mag:  byte first sign (0 = positive, 1 = negative)
//!            varint R, the number of sign runs, 1..=n
//!            varint L, then L bytes: the magnitude stream m (n integers, each 0..=0x7fff_ffff)
//!            if R > 1, the rest is the run stream (R integers, each >= 1, summing to n); else nothing
//!            sample i = f32::from_bits(m[i] | sign(i) << 31), signs alternating run by run
//! ```
//!
//! Every stream is Grizzbit's integer stream ([`crate::decode_stream`] with LPC allowed: fixed predictors 0–4
//! or the version-5 LPC, partitioned Rice coding). `offset + k * scale` is one float64 multiply and one add
//! (no fused multiply-add), rounded to the nearest float32 (`as f32`). `ord32` maps a float32 to an integer
//! keeping order: +0 is 0, -0 is -1. A decoder rejects any value or length outside these rules, and every
//! sample is then validated as for every other version.
use crate::{
    decode_stream, encode_side_stream, encode_stream, get_varint, put_varint, CodecError, Kind,
    MAX_SAMPLES,
};

/// Chunk format version 6.
pub(crate) const VERSION: u8 = 6;
pub(crate) const KIND_GRID: u8 = 1;
pub(crate) const KIND_SIGNMAG: u8 = 3;
const MAX_MAGNITUDE: i64 = 0x7fff_ffff;

/// Whether every sample is exactly a float32 value widened (NaN never is: callers validate first).
pub(crate) fn is_float32(samples: &[f64]) -> bool {
    samples
        .iter()
        .all(|&x| ((x as f32) as f64).to_bits() == x.to_bits())
}

/// Order-preserving map of a float32 to an integer: +0 is 0, -0 is -1.
pub(crate) fn ord32(x: f32) -> i64 {
    let b = x.to_bits();
    if b >> 31 == 1 {
        -((b & 0x7fff_ffff) as i64) - 1
    } else {
        b as i64
    }
}

/// The inverse of [`ord32`]; None outside its range.
pub(crate) fn from_ord32(o: i64) -> Option<f32> {
    if o < 0 {
        let m = (o + 1).checked_neg()?;
        (m <= MAX_MAGNITUDE).then(|| f32::from_bits(m as u32 | 0x8000_0000))
    } else {
        (o <= MAX_MAGNITUDE).then(|| f32::from_bits(o as u32))
    }
}

/// The distance from `x` to the next float32 away from zero (`x` finite, as a float64).
fn ulp32(x: f64) -> f64 {
    let f = (x.abs() as f32).max(f32::MIN_POSITIVE);
    (f32::from_bits(f.to_bits() + 1) - f) as f64
}

/// The grid value of index `k` in float32: one multiply, one add, then rounding to float32.
fn grid_value(offset: f64, scale: f64, k: i64) -> f32 {
    (offset + k as f64 * scale) as f32
}

/// The smallest version-6 body for float32-exact samples (no checksum), given their `sorted_distinct` values, or
/// None when the samples are not float32-exact.
pub(crate) fn encode_body(samples: &[f64], distinct: &[f64]) -> Option<Vec<u8>> {
    if samples.is_empty() || samples.len() > MAX_SAMPLES || !is_float32(samples) {
        return None;
    }
    let signmag = encode_signmag(samples);
    Some(match encode_grid(samples, distinct) {
        Some(g) if g.len() < signmag.len() => g,
        _ => signmag,
    })
}

fn encode_signmag(samples: &[f64]) -> Vec<u8> {
    let mags: Vec<i64> = samples
        .iter()
        .map(|&x| ((x as f32).to_bits() & 0x7fff_ffff) as i64)
        .collect();
    let first = (samples[0] as f32).is_sign_negative();
    let mut runs = Vec::new();
    let (mut sign, mut len) = (first, 0i64);
    for &x in samples {
        let s = (x as f32).is_sign_negative();
        if s == sign {
            len += 1;
        } else {
            runs.push(len);
            sign = s;
            len = 1;
        }
    }
    runs.push(len);
    let mut out = vec![VERSION, KIND_SIGNMAG];
    put_varint(&mut out, samples.len() as u64);
    out.push(first as u8);
    put_varint(&mut out, runs.len() as u64);
    let mut stream = Vec::new();
    encode_stream(&mags, false, &mut stream);
    put_varint(&mut out, stream.len() as u64);
    out.extend_from_slice(&stream);
    if runs.len() > 1 {
        encode_stream(&runs, false, &mut out);
    }
    out
}

/// A float32 grid fitted to the chunk, tried only when at most three quarters of its samples are distinct: the step from the smallest gap between distinct values (or that gap
/// divided by 2..=8), accepted when nearly every distinct value lies near the grid in float32 last places,
/// then step and offset refitted by least squares over the assigned indices. The fit only affects size:
/// corrections make every sample exact. None when no grid fits or the coding is not representable.
fn encode_grid(samples: &[f64], v: &[f64]) -> Option<Vec<u8>> {
    // A grid repeats values; a chunk with more distinct values than three quarters of its samples codes smaller by sign
    // and magnitude in nearly every case (development set), so the fit is not tried (it only affects size).
    if v.len() < 3 || 4 * v.len() > 3 * samples.len() {
        return None;
    }
    let magnitude = v[0].abs().max(v[v.len() - 1].abs());
    let floor = 16.0 * ulp32(magnitude);
    let gap = v
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|&d| d > floor)
        .fold(f64::INFINITY, f64::min);
    if !gap.is_finite() {
        return None;
    }
    let x0 = v[0];
    let q = (1..=8u32).map(|m| gap / m as f64).find(|&q| {
        let near = v
            .iter()
            .filter(|&&y| {
                let k = ((y - x0) / q).round();
                ((y - x0) - k * q).abs() <= 0.1 * q + 4.0 * ulp32(y)
            })
            .count();
        q.is_normal() && near * 100 >= v.len() * 99
    })?;
    let ks: Vec<f64> = samples.iter().map(|&y| ((y - x0) / q).round()).collect();
    if ks.iter().any(|k| k.abs() > (1u64 << 52) as f64) {
        return None;
    }
    // Least squares in sample order, IEEE basic operations only.
    let n = samples.len() as f64;
    let (mut sk, mut sx, mut skk, mut skx) = (0.0f64, 0.0f64, 0.0f64, 0.0f64);
    for (&k, &x) in ks.iter().zip(samples) {
        sk += k;
        sx += x;
        skk += k * k;
        skx += k * x;
    }
    let den = n * skk - sk * sk;
    let (mut scale, mut offset) = (q, x0);
    if den != 0.0 {
        let s = (n * skx - sk * sx) / den;
        let o = (sx - s * sk) / n;
        if s.is_finite() && s > 0.0 && o.is_finite() {
            (scale, offset) = (s, o);
        }
    }
    let ints: Vec<i64> = ks.iter().map(|&k| k as i64).collect();
    let mut fix = Vec::with_capacity(samples.len());
    for (&k, &x) in ints.iter().zip(samples) {
        let g = grid_value(offset, scale, k);
        if !g.is_finite() {
            return None;
        }
        fix.push(ord32(x as f32) - ord32(g));
    }
    let mut out = vec![VERSION, KIND_GRID];
    put_varint(&mut out, samples.len() as u64);
    out.extend_from_slice(&offset.to_le_bytes());
    out.extend_from_slice(&scale.to_le_bytes());
    let mut stream = Vec::new();
    encode_stream(&ints, true, &mut stream);
    put_varint(&mut out, stream.len() as u64);
    out.extend_from_slice(&stream);
    let has_fix = fix.iter().any(|&c| c != 0);
    out.push(has_fix as u8);
    if has_fix {
        // Corrections are nearly white: coded like version 5's side streams (fixed predictors 0..=1), which
        // lost nothing in development and costs less encode time. Any predictor is valid to a decoder.
        encode_side_stream(&fix, &mut out);
    }
    Some(out)
}

/// The kind of a checksum-verified version-6 body.
pub(crate) fn kind_of(body: &[u8]) -> Result<Kind, CodecError> {
    match body[1] {
        KIND_GRID => Ok(Kind::Grid),
        KIND_SIGNMAG => Ok(Kind::Float),
        _ => Err(CodecError::Corrupt("unknown kind")),
    }
}

/// A length-prefixed stream: its bytes and the position after it.
fn prefixed<'a>(b: &'a [u8], pos: &mut usize) -> Result<&'a [u8], CodecError> {
    let len = get_varint(b, pos)?;
    let end = usize::try_from(len)
        .ok()
        .and_then(|l| pos.checked_add(l))
        .filter(|&e| e <= b.len())
        .ok_or(CodecError::Corrupt("truncated"))?;
    let s = &b[*pos..end];
    *pos = end;
    Ok(s)
}

/// Sign run lengths are each at least 1 and sum to exactly `n`, without overflow.
fn check_runs(lengths: &[i64], n: usize) -> Result<(), CodecError> {
    let mut left = n as u64;
    for &len in lengths {
        if len < 1 || len as u64 > left {
            return Err(CodecError::Corrupt("sign runs"));
        }
        left -= len as u64;
    }
    if left != 0 {
        return Err(CodecError::Corrupt("sign runs"));
    }
    Ok(())
}

/// Decodes a checksum-verified version-6 body. Samples are validated by the caller.
pub(crate) fn decode_body(b: &[u8]) -> Result<Vec<f64>, CodecError> {
    let mut pos = 2;
    let n = get_varint(b, &mut pos)?;
    if n == 0 || n > MAX_SAMPLES as u64 {
        return Err(CodecError::Corrupt("sample count"));
    }
    let n = n as usize;
    match b[1] {
        KIND_GRID => {
            let header = b
                .get(pos..pos + 16)
                .ok_or(CodecError::Corrupt("truncated"))?;
            let offset = f64::from_le_bytes(header[0..8].try_into().unwrap());
            let scale = f64::from_le_bytes(header[8..16].try_into().unwrap());
            if !offset.is_finite() || !scale.is_finite() || scale <= 0.0 {
                return Err(CodecError::Corrupt("grid header"));
            }
            pos += 16;
            let ints = decode_stream(prefixed(b, &mut pos)?, n, true)?;
            let has_fix = *b.get(pos).ok_or(CodecError::Corrupt("truncated"))?;
            pos += 1;
            let fix = match has_fix {
                0 if pos == b.len() => None,
                0 => return Err(CodecError::Corrupt("trailing data")),
                1 => Some(decode_stream(&b[pos..], n, true)?),
                _ => return Err(CodecError::Corrupt("grid header")),
            };
            let mut out = Vec::with_capacity(n);
            for (i, &k) in ints.iter().enumerate() {
                let g = grid_value(offset, scale, k);
                let x = match &fix {
                    None => g,
                    Some(c) => ord32(g)
                        .checked_add(c[i])
                        .and_then(from_ord32)
                        .ok_or(CodecError::Corrupt("correction"))?,
                };
                out.push(x as f64);
            }
            Ok(out)
        }
        KIND_SIGNMAG => {
            let first = *b.get(pos).ok_or(CodecError::Corrupt("truncated"))?;
            pos += 1;
            if first > 1 {
                return Err(CodecError::Corrupt("sign"));
            }
            let runs = get_varint(b, &mut pos)?;
            if runs == 0 || runs > n as u64 {
                return Err(CodecError::Corrupt("sign runs"));
            }
            let runs = runs as usize;
            let mags = decode_stream(prefixed(b, &mut pos)?, n, true)?;
            if mags.iter().any(|&m| !(0..=MAX_MAGNITUDE).contains(&m)) {
                return Err(CodecError::Corrupt("magnitude"));
            }
            let lengths = if runs > 1 {
                decode_stream(&b[pos..], runs, false)?
            } else if pos == b.len() {
                vec![n as i64]
            } else {
                return Err(CodecError::Corrupt("trailing data"));
            };
            check_runs(&lengths, n)?;
            let mut out = Vec::with_capacity(n);
            let mut negative = first == 1;
            for &len in &lengths {
                let sign = (negative as u32) << 31;
                let start = out.len();
                out.extend(
                    mags[start..start + len as usize]
                        .iter()
                        .map(|&m| f32::from_bits(m as u32 | sign) as f64),
                );
                negative = !negative;
            }
            Ok(out)
        }
        _ => Err(CodecError::Corrupt("unknown kind")),
    }
}

/// The coding of a version-6 chunk: (kind byte, first stream uses LPC, second stream present: corrections for a
/// grid chunk, sign runs for a sign/magnitude chunk). None for a chunk of another version or a bad checksum.
/// Golden files and development measurements only.
#[doc(hidden)]
pub fn layout(encoded: &[u8]) -> Option<(u8, bool, bool)> {
    let b = crate::verified(encoded).ok()?;
    if b[0] != VERSION {
        return None;
    }
    let mut pos = 2;
    get_varint(b, &mut pos).ok()?;
    match b[1] {
        KIND_GRID => {
            pos += 16;
            let s = prefixed(b, &mut pos).ok()?;
            Some((
                KIND_GRID,
                s.first()? >> 5 == crate::g21::PREDICTOR_LPC as u8,
                *b.get(pos)? == 1,
            ))
        }
        KIND_SIGNMAG => {
            pos += 1;
            let runs = get_varint(b, &mut pos).ok()?;
            let s = prefixed(b, &mut pos).ok()?;
            Some((
                KIND_SIGNMAG,
                s.first()? >> 5 == crate::g21::PREDICTOR_LPC as u8,
                runs > 1,
            ))
        }
        _ => None,
    }
}

#[cfg(test)]
pub(crate) fn encode_signmag_pub(x: &[f64]) -> Vec<u8> {
    encode_signmag(x)
}
#[cfg(test)]
pub(crate) fn encode_grid_pub(x: &[f64]) -> Option<Vec<u8>> {
    encode_grid(x, &crate::sorted_distinct(x))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ord32_is_inverse_and_ordered_on_samples() {
        let xs = [
            -3.0e38f32,
            -1.0,
            -f32::MIN_POSITIVE,
            -1e-45,
            -0.0,
            0.0,
            1e-45,
            f32::MIN_POSITIVE,
            1.0,
            3.0e38,
        ];
        for w in xs.windows(2) {
            assert!(ord32(w[0]) < ord32(w[1]), "{:?}", w);
        }
        for &x in &xs {
            assert_eq!(from_ord32(ord32(x)).unwrap().to_bits(), x.to_bits());
        }
        assert_eq!(ord32(0.0), 0);
        assert_eq!(ord32(-0.0), -1);
        assert!(from_ord32(MAX_MAGNITUDE + 1).is_none());
        assert!(from_ord32(-MAX_MAGNITUDE - 2).is_none());
    }
}

#[cfg(kani)]
mod proofs {
    use super::*;

    /// Every float32 bit pattern maps to an integer and back unchanged; every integer in range maps back to
    /// itself, and every integer outside it is refused.
    #[kani::proof]
    fn ord32_is_a_bijection() {
        let bits: u32 = kani::any();
        assert_eq!(
            from_ord32(ord32(f32::from_bits(bits))).unwrap().to_bits(),
            bits
        );
        let o: i64 = kani::any();
        match from_ord32(o) {
            Some(x) => assert_eq!(ord32(x), o),
            None => assert!(!(-MAX_MAGNITUDE - 1..=MAX_MAGNITUDE).contains(&o)),
        }
    }

    /// Accepted sign runs are positive and sum to n; no input panics.
    #[kani::proof]
    #[kani::unwind(5)]
    fn sign_runs_are_checked() {
        let lengths: [i64; 4] = kani::any();
        let k: usize = kani::any();
        kani::assume(k <= 4);
        let n: usize = kani::any();
        kani::assume(n <= MAX_SAMPLES);
        if check_runs(&lengths[..k], n).is_ok() {
            let mut sum = 0u64;
            for &l in &lengths[..k] {
                assert!(l >= 1);
                sum += l as u64;
            }
            assert_eq!(sum, n as u64);
        }
    }
}
