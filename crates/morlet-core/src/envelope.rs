//! `xc-envelope-f16-v1`: a materialized envelope representation for search-by-example.
//!
//! The definition reproduces the envelope evaluated in R1/R1b
//! (`evaluation/decision/timing.py::envelope`, stored as float16):
//! 1. z-score the window (population standard deviation; a constant window gives zeros);
//! 2. Hilbert magnitude via the full-length FFT analytic signal (as `scipy.signal.hilbert`);
//! 3. moving average over 50 samples, aligned as `numpy.convolve(.., mode="same")`;
//! 4. keep every 10th sample, starting at 0 (`ceil(n / 10)` values);
//! 5. z-score again (a constant envelope is only centred).
//!
//! Parameters are in samples, so the representation is rate-invariant in sample
//! coordinates, like the Morlet extractor. Windows are powers of two in 512..=32768.
//! Stored values are IEEE binary16 (round to nearest even), decoded and z-scored again
//! before matching. Matching is `1 - max` over lags of the biased cross-correlation.
use crate::{rawcodec, validate, SignalError};

pub const ENVELOPE_VERSION: &str = "xc-envelope-f16-v1";
const SMOOTH: usize = 50;
const DECIMATE: usize = 10;
const HEADER: [u8; 1] = [1];

pub fn valid_window(n: usize) -> bool {
    (512..=32768).contains(&n) && n.is_power_of_two()
}

/// Envelope at full precision (used for queries).
pub fn envelope(samples: &[f64]) -> Result<Vec<f64>, SignalError> {
    validate(samples)?;
    if !valid_window(samples.len()) {
        return Err(SignalError::EnvelopeLength);
    }
    let n = samples.len();
    // As evaluation/retrieval.py::normalize("zscore"): shift by the first sample, then centre.
    let shifted: Vec<f64> = samples.iter().map(|v| v - samples[0]).collect();
    let mean = shifted.iter().sum::<f64>() / n as f64;
    let centred: Vec<f64> = shifted.iter().map(|v| v - mean).collect();
    let scale = (centred.iter().map(|v| v * v).sum::<f64>() / n as f64).sqrt();
    let x: Vec<f64> = if scale > 0.0 {
        centred.iter().map(|v| v / scale).collect()
    } else {
        vec![0.0; n]
    };
    let mut re = x;
    let mut im = vec![0.0; n];
    fft(&mut re, &mut im, false);
    for k in 1..n / 2 {
        re[k] *= 2.0;
        im[k] *= 2.0;
    }
    for k in n / 2 + 1..n {
        re[k] = 0.0;
        im[k] = 0.0;
    }
    fft(&mut re, &mut im, true);
    let magnitude: Vec<f64> = re.iter().zip(&im).map(|(a, b)| a.hypot(*b)).collect();
    // numpy "same": out[i] = sum of input[i-25 ..= i+24] / 50, zeros outside.
    let mut env = Vec::with_capacity(n.div_ceil(DECIMATE));
    for i in (0..n).step_by(DECIMATE) {
        let lo = i.saturating_sub(SMOOTH / 2);
        let hi = (i + SMOOTH / 2 - 1).min(n - 1);
        // Each term is weighted by the kernel value 1/50 (= numpy's ones(50)/50).
        let w = 1.0 / SMOOTH as f64;
        let acc: f64 = (lo..=hi).rev().map(|j| magnitude[j] * w).sum();
        env.push(acc);
    }
    Ok(zscore(env))
}

fn zscore(v: Vec<f64>) -> Vec<f64> {
    let n = v.len() as f64;
    let mean = v.iter().sum::<f64>() / n;
    let sd = (v.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n).sqrt();
    let s = if sd > 0.0 { sd } else { 1.0 };
    v.iter().map(|x| (x - mean) / s).collect()
}

/// Stored form: one version byte, then binary16 little-endian values.
pub fn encode(samples: &[f64]) -> Result<Vec<u8>, SignalError> {
    let env = envelope(samples)?;
    let mut out = Vec::with_capacity(1 + 2 * env.len());
    out.extend_from_slice(&HEADER);
    for v in env {
        out.extend_from_slice(&f64_to_f16(v).to_le_bytes());
    }
    Ok(out)
}

pub fn decode(bytes: &[u8]) -> Result<Vec<f64>, SignalError> {
    if bytes.len() < 3 || bytes[0] != HEADER[0] || bytes.len() % 2 != 1 {
        return Err(SignalError::InvalidEnvelope);
    }
    let v: Vec<f64> = bytes[1..]
        .chunks_exact(2)
        .map(|c| f16_to_f64(u16::from_le_bytes([c[0], c[1]])))
        .collect();
    if !valid_window_values(v.len()) || v.iter().any(|x| !x.is_finite()) {
        return Err(SignalError::InvalidEnvelope);
    }
    Ok(zscore(v))
}

/// Stored envelopes of the windows `first, first + stride, ...` that end at or before `end`
/// (at most `max_windows`), from contiguous encoded raw chunks `(start_sample, encoded)` that
/// cover them. Each chunk is decoded once; every envelope is `encode` of the same samples
/// that reading the interval returns, so the bytes equal the per-window path.
pub fn encode_windows(
    chunks: &[(i64, &[u8])],
    first: i64,
    end: i64,
    window: usize,
    stride: usize,
    max_windows: usize,
) -> Result<Vec<(i64, Vec<u8>)>, SignalError> {
    let Some(&(base, _)) = chunks.first() else {
        return Ok(Vec::new());
    };
    let mut samples: Vec<f64> = Vec::new();
    for &(start, encoded) in chunks {
        if start != base + samples.len() as i64 {
            return Err(SignalError::LengthMismatch);
        }
        samples.extend(rawcodec::decode(encoded).map_err(|_| SignalError::LengthMismatch)?);
    }
    let mut out = Vec::new();
    let mut s = first;
    while s + window as i64 <= end && out.len() < max_windows {
        if s < base || (s - base) as usize + window > samples.len() {
            return Err(SignalError::LengthMismatch);
        }
        let a = (s - base) as usize;
        out.push((s, encode(&samples[a..a + window])?));
        s += stride as i64;
    }
    Ok(out)
}

fn valid_window_values(m: usize) -> bool {
    (9..=15).any(|p| (1usize << p).div_ceil(DECIMATE) == m)
}

/// `1 - max_{|lag| <= max_lag} (1/n) sum_t q[t + lag] g[t]`, over the overlap.
pub fn distance(q: &[f64], g: &[f64], max_lag: usize) -> Result<f64, SignalError> {
    if q.len() != g.len() || q.is_empty() || max_lag >= q.len() {
        return Err(SignalError::LengthMismatch);
    }
    let n = q.len();
    // Positive lags pair q[t + lag] with g[t]; negative lags pair g[t + lag] with q[t].
    let best = max_lagged_sum(q, g, 0, max_lag).max(max_lagged_sum(g, q, 1, max_lag));
    Ok(1.0 - best / n as f64)
}

/// Zero-padded shift of a query envelope for probe search: `p[t] = q[t + lag]` where
/// `0 <= t + lag < n`, else 0. The inner product of the probe with a stored envelope `g`
/// is exactly the lag term of `distance`, `sum_t q[t + lag] g[t]` over the overlap, so the
/// best score over all |lag| <= max_lag equals `n * (1 - distance)`.
pub fn shifted(q: &[f64], lag: isize) -> Result<Vec<f64>, SignalError> {
    let n = q.len();
    if n == 0 || lag.unsigned_abs() >= n {
        return Err(SignalError::LengthMismatch);
    }
    let mut p = vec![0.0; n];
    if lag >= 0 {
        let l = lag as usize;
        p[..n - l].copy_from_slice(&q[l..]);
    } else {
        let l = lag.unsigned_abs();
        p[l..].copy_from_slice(&q[..n - l]);
    }
    Ok(p)
}

const LAG_BLOCK: usize = 8;

/// max over lag in first..=last of sum_{t < n - lag} a[t + lag] * b[t].
///
/// Lags are scored LAG_BLOCK at a time with one accumulator each, so the loop
/// vectorizes across lags. Every accumulator still adds its products in ascending t,
/// starting from the same neutral element as `Iterator::sum`, so each lagged sum is
/// bitwise identical to summing it on its own.
fn max_lagged_sum(a: &[f64], b: &[f64], first: usize, last: usize) -> f64 {
    let n = a.len();
    let zero: f64 = std::iter::empty::<f64>().sum();
    let mut best = f64::NEG_INFINITY;
    let mut l0 = first;
    while l0 <= last {
        let k = LAG_BLOCK.min(last + 1 - l0);
        let mut acc = [zero; LAG_BLOCK];
        // Products valid for every lag in the block, then each lag's own tail.
        let common = n - (l0 + k - 1);
        if k == LAG_BLOCK {
            for t in 0..common {
                let bt = b[t];
                let w = &a[t + l0..t + l0 + LAG_BLOCK];
                for j in 0..LAG_BLOCK {
                    acc[j] += w[j] * bt;
                }
            }
        } else {
            for t in 0..common {
                for j in 0..k {
                    acc[j] += a[t + l0 + j] * b[t];
                }
            }
        }
        for (j, s) in acc.iter_mut().enumerate().take(k) {
            for t in common..n - l0 - j {
                *s += a[t + l0 + j] * b[t];
            }
            best = best.max(*s);
        }
        l0 += k;
    }
    best
}

/// In-place iterative radix-2 FFT; `inverse` includes the 1/n scaling.
fn fft(re: &mut [f64], im: &mut [f64], inverse: bool) {
    let n = re.len();
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j |= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let sign = if inverse { 1.0 } else { -1.0 };
    let mut len = 2;
    while len <= n {
        let half = len / 2;
        for k in 0..half {
            let (s, c) = (sign * 2.0 * std::f64::consts::PI * k as f64 / len as f64).sin_cos();
            for start in (0..n).step_by(len) {
                let (a, b) = (start + k, start + k + half);
                let tr = re[b] * c - im[b] * s;
                let ti = re[b] * s + im[b] * c;
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
            }
        }
        len <<= 1;
    }
    if inverse {
        for i in 0..n {
            re[i] /= n as f64;
            im[i] /= n as f64;
        }
    }
}

/// IEEE 754 binary16, round to nearest even, directly from f64 (as numpy's astype).
pub fn f64_to_f16(v: f64) -> u16 {
    let bits = v.to_bits();
    let sign = ((bits >> 48) & 0x8000) as u16;
    let exp = ((bits >> 52) & 0x7ff) as i32;
    let mant = bits & 0x000f_ffff_ffff_ffff;
    if exp == 0x7ff {
        return sign | 0x7c00 | if mant != 0 { 0x200 } else { 0 };
    }
    let e = exp - 1023 + 15;
    if e >= 31 {
        return sign | 0x7c00;
    }
    // Significand with the implicit bit, as a 53-bit integer.
    let full = if exp == 0 { mant } else { mant | (1u64 << 52) };
    // Number of low bits to drop: 42 for normal halves, more for subnormals.
    let shift = if e <= 0 { 42 + (1 - e) } else { 42 } as u32;
    if shift > 53 + 10 {
        return sign;
    }
    let kept = if shift >= 64 { 0 } else { full >> shift };
    let rem = if shift >= 64 {
        full
    } else {
        full & ((1u64 << shift) - 1)
    };
    let halfway = 1u64 << (shift - 1);
    let mut r = kept;
    if rem > halfway || (rem == halfway && kept & 1 == 1) {
        r += 1;
    }
    if e <= 0 {
        // Subnormal (r may carry into the smallest normal, which encodes correctly).
        sign | r as u16
    } else {
        // r includes the implicit bit at position 10; a carry may bump the exponent.
        let value = ((e as u64) << 10) + (r - (1 << 10));
        if value >= 0x7c00 {
            sign | 0x7c00
        } else {
            sign | value as u16
        }
    }
}

pub fn f16_to_f64(h: u16) -> f64 {
    let sign = if h & 0x8000 != 0 { -1.0 } else { 1.0 };
    let exp = ((h >> 10) & 0x1f) as i32;
    let mant = (h & 0x3ff) as f64;
    sign * match exp {
        0 => mant * 2f64.powi(-24),
        31 if mant == 0.0 => f64::INFINITY,
        31 => f64::NAN,
        _ => (1.0 + mant / 1024.0) * 2f64.powi(exp - 15),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn f16_matches_ieee_examples() {
        for (v, h) in [
            (0.0, 0x0000u16),
            (-0.0, 0x8000),
            (1.0, 0x3c00),
            (-2.0, 0xc000),
            (65504.0, 0x7bff),
            (65520.0, 0x7c00),
            (1e9, 0x7c00),
            (0.333_333_333_333, 0x3555),
            (6.103_515_625e-5, 0x0400),
            (5.960_464_477_539_063e-8, 0x0001),
            (2.0f64.powi(-25), 0x0000),
            (1.0 + 2.0f64.powi(-11), 0x3c00),
            (1.0 + 3.0 * 2.0f64.powi(-11), 0x3c02),
        ] {
            assert_eq!(f64_to_f16(v), h, "{v}");
        }
        for h in (0..0x7c00u16).step_by(7) {
            assert_eq!(f64_to_f16(f16_to_f64(h)), h);
        }
    }

    #[test]
    fn fft_roundtrip_and_tone() {
        let n = 1024;
        let x: Vec<f64> = (0..n)
            .map(|i| (i as f64 * 0.37).sin() + 0.1 * i as f64 / n as f64)
            .collect();
        let (mut re, mut im) = (x.clone(), vec![0.0; n]);
        fft(&mut re, &mut im, false);
        fft(&mut re, &mut im, true);
        for (a, b) in re.iter().zip(&x) {
            assert!((a - b).abs() < 1e-12);
        }
        assert!(im.iter().all(|v| v.abs() < 1e-12));
    }

    #[test]
    fn envelope_shape_and_validation() {
        let x: Vec<f64> = (0..4096)
            .map(|i| ((i as f64) / 7.0).sin() * (1.0 + (i / 1000) as f64))
            .collect();
        let env = envelope(&x).unwrap();
        assert_eq!(env.len(), 410);
        let mean = env.iter().sum::<f64>() / 410.0;
        let var = env.iter().map(|v| (v - mean) * (v - mean)).sum::<f64>() / 410.0;
        assert!(mean.abs() < 1e-12 && (var - 1.0).abs() < 1e-12);
        assert!(envelope(&x[..4000]).is_err());
        assert!(envelope(&[1.0; 256]).is_err());
        assert_eq!(envelope(&[3.0; 512]).unwrap(), vec![0.0; 52]);
    }

    #[test]
    fn stored_form_roundtrips_and_rejects_corruption() {
        let x: Vec<f64> = (0..2048)
            .map(|i| ((i * 7919) % 101) as f64 - 50.0)
            .collect();
        let b = encode(&x).unwrap();
        assert_eq!(b.len(), 1 + 2 * 205);
        let d = decode(&b).unwrap();
        let full = envelope(&x).unwrap();
        assert!(d.iter().zip(&full).all(|(a, b)| (a - b).abs() < 5e-3));
        assert!(decode(&b[..b.len() - 2]).is_err());
        let mut bad = b.clone();
        bad[0] = 2;
        assert!(decode(&bad).is_err());
        let mut inf = b.clone();
        inf[1] = 0x00;
        inf[2] = 0x7c;
        assert!(decode(&inf).is_err());
    }

    #[test]
    fn matches_the_evaluated_python_definition() {
        let fixtures: serde_json::Value = serde_json::from_str(include_str!(
            "../../../testdata/xc-envelope-f16-v1/fixtures.json"
        ))
        .unwrap();
        for (name, case) in fixtures["cases"].as_object().unwrap() {
            let x: Vec<f64> = case["samples"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_f64().unwrap())
                .collect();
            let expected: Vec<f64> = case["envelope"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_f64().unwrap())
                .collect();
            let env = envelope(&x).unwrap();
            assert_eq!(env.len(), expected.len(), "{name}");
            let worst = env
                .iter()
                .zip(&expected)
                .map(|(a, b)| (a - b).abs())
                .fold(0.0, f64::max);
            assert!(worst < 1e-9, "{name}: {worst}");
            let hex: String = encode(&x).unwrap()[1..]
                .iter()
                .map(|b| format!("{b:02x}"))
                .collect();
            assert_eq!(hex, case["f16_hex"].as_str().unwrap(), "{name}");
        }
    }

    #[test]
    fn distance_is_shift_tolerant() {
        let x: Vec<f64> = (0..4096)
            .map(|i| (-((i as f64 - 2000.0) / 150.0).powi(2)).exp() * ((i as f64) / 3.0).sin())
            .collect();
        let mut shifted = vec![0.0; 4096];
        shifted[100..].copy_from_slice(&x[..3996]);
        let (a, b) = (envelope(&x).unwrap(), envelope(&shifted).unwrap());
        assert!(distance(&a, &a, 30).unwrap().abs() < 1e-12);
        let aligned = distance(&a, &b, 30).unwrap();
        let unaligned = distance(&a, &b, 0).unwrap();
        assert!(
            aligned < 0.05 && unaligned > aligned + 0.1,
            "{aligned} {unaligned}"
        );
        assert!(distance(&a, &b[..400], 30).is_err());
    }

    #[test]
    fn shifted_probes_reproduce_every_lag_term_and_the_distance() {
        let mut seed = 7u64;
        let mut next = || {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (seed >> 11) as f64 / (1u64 << 53) as f64 - 0.5
        };
        let q: Vec<f64> = (0..410).map(|_| next()).collect();
        let g: Vec<f64> = (0..410).map(|_| next()).collect();
        let n = q.len();
        let mut best = f64::NEG_INFINITY;
        for lag in -30isize..=30 {
            let p = shifted(&q, lag).unwrap();
            let dot: f64 = p.iter().zip(&g).map(|(a, b)| a * b).sum();
            let term: f64 = if lag >= 0 {
                (0..n - lag as usize)
                    .map(|t| q[t + lag as usize] * g[t])
                    .sum()
            } else {
                let l = lag.unsigned_abs();
                (0..n - l).map(|t| g[t + l] * q[t]).sum()
            };
            assert!((dot - term).abs() < 1e-12, "lag {lag}");
            best = best.max(dot);
        }
        assert!((1.0 - best / n as f64 - distance(&q, &g, 30).unwrap()).abs() < 1e-12);
        assert_eq!(shifted(&q, 0).unwrap(), q);
        assert!(
            shifted(&q, 410).is_err() && shifted(&q, -410).is_err() && shifted(&[], 0).is_err()
        );
    }

    #[test]
    fn batched_windows_equal_per_window_encoding() {
        let x: Vec<f64> = (0..20_000)
            .map(|i| ((i * 7919 % 211) as f64 - 105.0) + ((i as f64) / 40.0).sin() * 300.0)
            .collect();
        let sizes = [4096usize, 3000, 4096, 5000, 3808];
        let mut chunks = vec![];
        let mut at = 0usize;
        for n in sizes {
            chunks.push((
                at as i64,
                crate::rawcodec::encode(&x[at..at + n], 1.0).unwrap(),
            ));
            at += n;
        }
        let refs: Vec<(i64, &[u8])> = chunks.iter().map(|(s, e)| (*s, e.as_slice())).collect();
        let got = encode_windows(&refs, 2048, 20_000, 4096, 2048, 100).unwrap();
        let expected: Vec<i64> = (1..)
            .map(|k| k * 2048)
            .take_while(|s| s + 4096 <= 20_000)
            .collect();
        assert_eq!(got.iter().map(|(s, _)| *s).collect::<Vec<_>>(), expected);
        for (s, env) in &got {
            let a = *s as usize;
            assert_eq!(env, &encode(&x[a..a + 4096]).unwrap());
        }
        assert_eq!(
            encode_windows(&refs, 0, 20_000, 4096, 2048, 3)
                .unwrap()
                .len(),
            3
        );
        assert!(encode_windows(&refs[1..], 0, 20_000, 4096, 2048, 3).is_err()); // window before the chunks
        let gap = [refs[0], refs[2]];
        assert!(encode_windows(&gap, 0, 20_000, 4096, 2048, 10).is_err()); // not contiguous
    }

    /// The scoring loop before lags were blocked; kept as the bitwise reference.
    fn distance_one_lag_at_a_time(q: &[f64], g: &[f64], max_lag: usize) -> f64 {
        let n = q.len();
        let mut best = f64::NEG_INFINITY;
        for lag in 0..=max_lag {
            let pos: f64 = (0..n - lag).map(|t| q[t + lag] * g[t]).sum();
            best = best.max(pos);
            if lag > 0 {
                let neg: f64 = (0..n - lag).map(|t| q[t] * g[t + lag]).sum();
                best = best.max(neg);
            }
        }
        1.0 - best / n as f64
    }

    #[test]
    fn blocked_lags_are_bitwise_identical() {
        let mut state = 0x9e37_79b9_7f4a_7c15u64;
        let mut next = || {
            state ^= state << 13;
            state ^= state >> 7;
            state ^= state << 17;
            (state >> 11) as f64 / (1u64 << 53) as f64 * 6.0 - 3.0
        };
        for n in [52, 103, 410, 820, 3277] {
            for _ in 0..20 {
                let q: Vec<f64> = (0..n).map(|_| next()).collect();
                let g: Vec<f64> = (0..n).map(|_| next()).collect();
                for max_lag in [0, 1, 7, 8, 9, 15, 16, 17, 30, 61, n / 2, n - 1]
                    .into_iter()
                    .filter(|&l| l < n)
                {
                    let got = distance(&q, &g, max_lag).unwrap();
                    let want = distance_one_lag_at_a_time(&q, &g, max_lag);
                    assert_eq!(got.to_bits(), want.to_bits(), "n {n} max_lag {max_lag}");
                }
            }
        }
        let zeros = vec![0.0; 410];
        assert_eq!(
            distance(&zeros, &zeros, 30).unwrap(),
            distance_one_lag_at_a_time(&zeros, &zeros, 30)
        );
    }
}
