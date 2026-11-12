//! `simple-v1`: length-independent scalar values of one context segment (plus its
//! duration), the representation compared by compare-to-history.
//!
//! Values, in order: duration_s, mean, std (population), rms (including DC),
//! p75 (linear interpolation, as numpy's default percentile), peak_abs,
//! crest (peak_abs / rms), kurtosis (Pearson, of the centred samples).
//! The 75th percentile reflects a level during cutting without entry/exit detection; it
//! and the standard deviation were the informative values in evaluation C1.
use crate::SignalError;

pub const VALUES_VERSION: &str = "simple-v1";
pub const VALUE_NAMES: [&str; 8] = [
    "duration_s",
    "mean",
    "std",
    "rms",
    "p75",
    "peak_abs",
    "crest",
    "kurtosis",
];
/// Values in channel units, which scale with the signal level; duration_s is in seconds,
/// crest and kurtosis are dimensionless.
pub const IN_CHANNEL_UNITS: [bool; 8] = [false, true, true, true, true, true, false, false];
/// Index of rms, the level that channel-unit values are judged against.
pub const RMS: usize = 3;
/// A typical (median) channel-unit value at or below this fraction of the typical rms is
/// treated as zero. Rounding residue is far smaller: a constant window's std comes out
/// about 1e-13 of its level, not 0, because its mean is a rounded sum (at most 2^20
/// samples, 2^-33 relative). Real resolution is far larger: a 24-bit converter
/// resolves 6e-8 of full scale.
pub const NEGLIGIBLE_FRACTION: f64 = 1e-9;

/// Per value, the largest typical value treated as zero, given the typical rms (`level`)
/// of the same history. Comparisons skip a value whose typical value is at or below its
/// floor, as they skip an exact zero: a relative deviation from rounding residue is noise.
pub fn negligible_floors(level: f64) -> [f64; 8] {
    let floor = if level.is_finite() {
        level.abs() * NEGLIGIBLE_FRACTION
    } else {
        0.0
    };
    IN_CHANNEL_UNITS.map(|scaled| if scaled { floor } else { 0.0 })
}

/// Value-comparison rules: `simple-v1` treats a history median as zero only at rounding residue
/// (`negligible_floors`); `simple-v2` also at a few steps of a quantised history (`quantised_step`,
/// study V2, #387). The stored values are the same under both.
pub const RULES_V1: &str = "simple-v1";
pub const RULES_V2: &str = "simple-v2";
/// simple-v2: a history median within this many steps of zero is treated as zero (V2, frozen).
pub const QUANTISED_STEPS: f64 = 2.0;
/// Relative tolerance for a gap to count as an integer multiple of the step (V2, frozen).
const STEP_TOLERANCE: f64 = 1e-6;
/// Largest multiple of the step a gap may be (V2, frozen).
const MAX_MULTIPLE: f64 = 1e6;

/// The step of a quantised history of one value (V2): with at least 3 distinct finite values,
/// the smallest positive gap between neighbouring distinct values, if every such gap is an integer
/// multiple of it (relative tolerance 1e-6, at most 10^6 steps); otherwise None, and the value is
/// compared as in simple-v1.
pub fn quantised_step(values: &[f64]) -> Option<f64> {
    let mut u: Vec<f64> = values.iter().copied().filter(|v| v.is_finite()).collect();
    u.sort_by(|a, b| a.total_cmp(b));
    u.dedup();
    if u.len() < 3 {
        return None;
    }
    let gaps: Vec<f64> = u
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|g| *g > 0.0)
        .collect();
    let step = gaps.iter().copied().fold(f64::INFINITY, f64::min);
    if !(step.is_finite() && step > 0.0) {
        return None;
    }
    for g in &gaps {
        let m = g / step;
        if m > MAX_MULTIPLE || (m - m.round()).abs() > STEP_TOLERANCE * m {
            return None;
        }
    }
    Some(step)
}

/// The simple-v2 floor of one value: simple-v1's floor, raised to `QUANTISED_STEPS` steps when the
/// history is quantised.
pub fn v2_floor(v1_floor: f64, history: &[f64]) -> f64 {
    match quantised_step(history) {
        Some(step) => v1_floor.max(QUANTISED_STEPS * step),
        None => v1_floor,
    }
}

/// Longest segment summarized in one call (2^20 samples, about 41 s at 25.6 kHz).
pub const MAX_SEGMENT_SAMPLES: usize = 1 << 20;

pub fn simple(samples: &[f64], sample_rate_hz: f64) -> Result<Vec<f64>, SignalError> {
    if samples.is_empty() || samples.len() > MAX_SEGMENT_SAMPLES {
        return Err(SignalError::SegmentLength);
    }
    if samples.iter().any(|v| !v.is_finite() || v.abs() > 1e9) {
        return Err(SignalError::InvalidSample);
    }
    if !(sample_rate_hz.is_finite() && sample_rate_hz > 0.0) {
        return Err(SignalError::InvalidSampleRate);
    }
    let n = samples.len() as f64;
    let mean = samples.iter().sum::<f64>() / n;
    let m2 = samples.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n;
    let m4 = samples.iter().map(|v| (v - mean).powi(4)).sum::<f64>() / n;
    let peak = samples.iter().fold(0.0f64, |a, v| a.max(v.abs()));
    // Tiny amplitudes underflow squares and fourth powers: for [-1e-100, 1e-100],
    // m4 and m2^2 are both 0 and kurtosis would be 0/0. There the moments are taken
    // on values divided by their largest magnitude. Where m2^2 (or peak^2) is a
    // normal float, m4 >= m2^2 is too and the plain formulas are used, bit for bit;
    // std keeps sqrt(m2) wherever m2 itself is normal.
    let (std, kurtosis) = if m2 * m2 >= f64::MIN_POSITIVE {
        (m2.sqrt(), m4 / (m2 * m2))
    } else {
        let s = samples.iter().fold(0.0f64, |a, v| a.max((v - mean).abs()));
        if s > 0.0 {
            let c2 = samples
                .iter()
                .map(|v| ((v - mean) / s).powi(2))
                .sum::<f64>()
                / n;
            let c4 = samples
                .iter()
                .map(|v| ((v - mean) / s).powi(4))
                .sum::<f64>()
                / n;
            let std = if m2 >= f64::MIN_POSITIVE {
                m2.sqrt()
            } else {
                s * c2.sqrt()
            };
            (std, c4 / (c2 * c2))
        } else {
            (0.0, 0.0)
        }
    };
    let rms = if peak * peak >= f64::MIN_POSITIVE || peak == 0.0 {
        (samples.iter().map(|v| v * v).sum::<f64>() / n).sqrt()
    } else {
        peak * (samples.iter().map(|v| (v / peak).powi(2)).sum::<f64>() / n).sqrt()
    };
    let mut sorted = samples.to_vec();
    sorted.sort_by(f64::total_cmp);
    let pos = 0.75 * (sorted.len() - 1) as f64;
    let (lo, frac) = (pos.floor() as usize, pos - pos.floor());
    let p75 = if lo + 1 < sorted.len() {
        sorted[lo] + frac * (sorted[lo + 1] - sorted[lo])
    } else {
        sorted[lo]
    };
    Ok(vec![
        n / sample_rate_hz,
        mean,
        std,
        rms,
        p75,
        peak,
        if rms > 0.0 { peak / rms } else { 0.0 },
        kurtosis,
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quantised_p75_near_zero_is_zero_under_v2() {
        // V2 S1: p75 history alternating between 0 and -1 step (0.0383), and a few other steps.
        let step = 0.0383;
        let hist = [0.0, -step, 0.0, -step, -2.0 * step, 0.0, -step, step];
        assert_eq!(quantised_step(&hist), Some(step));
        assert_eq!(v2_floor(0.0, &hist), 2.0 * step);
        // the median of this history is -step/2 or so; it lies within the floor, so it is zero
        let mut h = hist.to_vec();
        h.sort_by(|a, b| a.total_cmp(b));
        let med = (h[3] + h[4]) / 2.0;
        assert!(med.abs() <= v2_floor(0.0, &hist));
    }

    #[test]
    fn continuous_values_have_no_step() {
        // V2 S2: smooth values keep simple-v1's floor.
        let hist: Vec<f64> = (0..200)
            .map(|i| ((i as f64) * 0.7311).sin() * 1.3 + 0.01 * i as f64)
            .collect();
        assert_eq!(quantised_step(&hist), None);
        assert_eq!(v2_floor(1e-9, &hist), 1e-9);
        assert_eq!(quantised_step(&[1.0, 2.0]), None); // fewer than 3 distinct values
        assert_eq!(quantised_step(&[0.0, 0.0, 0.0, 0.0]), None);
        assert_eq!(quantised_step(&[0.0, 1.0, 1.0003]), None); // 1 is not a multiple of 0.0003
    }

    #[test]
    fn step_tolerates_float_rounding_of_a_grid() {
        // calibrated values o + k*s rounded to float: gaps are multiples of s within 1e-6
        let s = 0.1;
        let hist: Vec<f64> = [3, 7, 4, 12, 5, 9]
            .iter()
            .map(|k| -0.3 + *k as f64 * s)
            .collect();
        let st = quantised_step(&hist).unwrap();
        assert!((st - s).abs() < 1e-12);
    }

    #[test]
    fn known_values() {
        let v = simple(&[1.0, 2.0, 3.0, 4.0], 2.0).unwrap();
        assert_eq!(v[0], 2.0);
        assert_eq!(v[1], 2.5);
        assert!((v[2] - 1.25f64.sqrt()).abs() < 1e-15);
        assert!((v[3] - 7.5f64.sqrt()).abs() < 1e-15);
        assert_eq!(v[4], 3.25); // numpy.percentile([1,2,3,4], 75)
        assert_eq!(v[5], 4.0);
        assert!((v[6] - 4.0 / 7.5f64.sqrt()).abs() < 1e-15);
        assert!((v[7] - 1.64).abs() < 1e-12);
        assert_eq!(VALUE_NAMES.len(), v.len());
    }

    /// The formulas before tiny amplitudes were handled; the bitwise reference.
    fn plain(samples: &[f64]) -> [f64; 3] {
        let n = samples.len() as f64;
        let mean = samples.iter().sum::<f64>() / n;
        let m2 = samples.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / n;
        let m4 = samples.iter().map(|v| (v - mean).powi(4)).sum::<f64>() / n;
        let rms = (samples.iter().map(|v| v * v).sum::<f64>() / n).sqrt();
        [m2.sqrt(), rms, if m2 > 0.0 { m4 / (m2 * m2) } else { 0.0 }]
    }

    #[test]
    fn tiny_amplitudes_give_finite_scale_free_values() {
        // Issue #51: m4 and m2^2 underflow to 0 here; Pearson kurtosis is exactly 1.
        let v = simple(&[-1e-100, 1e-100], 1.0).unwrap();
        assert_eq!(v[7], 1.0);
        assert_eq!(v[2], 1e-100);
        let wave: Vec<f64> = (0..4096)
            .map(|i| (i as f64 / 37.).sin() + 0.3 * (i as f64 / 5.).cos())
            .collect();
        let base = simple(&wave, 100.0).unwrap();
        for scale in [1e-50, 1e-100, 1e-160, 1e-200, 1e-300, 1e-310] {
            let x: Vec<f64> = wave.iter().map(|v| v * scale).collect();
            let v = simple(&x, 100.0).unwrap();
            assert!(v.iter().all(|x| x.is_finite()), "{scale}: {v:?}");
            // Subnormal inputs keep few significant bits; the values still describe the shape.
            let tol = if scale < 1e-300 { 1e-3 } else { 1e-9 };
            assert!(
                (v[7] / base[7] - 1.0).abs() < tol,
                "{scale}: kurtosis {} vs {}",
                v[7],
                base[7]
            );
            assert!(
                (v[6] / base[6] - 1.0).abs() < tol,
                "{scale}: crest {} vs {}",
                v[6],
                base[6]
            );
            assert!((v[2] / (base[2] * scale) - 1.0).abs() < tol, "{scale}: std");
            assert!((v[3] / (base[3] * scale) - 1.0).abs() < tol, "{scale}: rms");
        }
        // A constant tiny window's std is rounding residue, as at ordinary levels, and
        // stays below the negligible floor; an all-zero window keeps exact zeros.
        let v = simple(&[1e-300; 64], 1.0).unwrap();
        assert!(v.iter().all(|x| x.is_finite()) && v[2] <= negligible_floors(v[RMS])[2]);
        assert_eq!(simple(&[0.0; 64], 1.0).unwrap()[2..8], [0.0; 6]);
    }

    #[test]
    fn ordinary_amplitudes_are_unchanged_bit_for_bit() {
        let mut seed = 0x2545_f491_4f6c_dd1du64;
        for scale in [1e-70, 1e-20, 1e-3, 1.0, 1e3, 1e8] {
            for len in [1, 2, 3, 100, 4096] {
                let x: Vec<f64> = (0..len)
                    .map(|_| {
                        seed ^= seed << 13;
                        seed ^= seed >> 7;
                        seed ^= seed << 17;
                        ((seed >> 11) as f64 / (1u64 << 53) as f64 - 0.5) * scale
                    })
                    .collect();
                let v = simple(&x, 1.0).unwrap();
                let [std, rms, kurtosis] = plain(&x);
                assert_eq!(
                    [v[2].to_bits(), v[3].to_bits(), v[7].to_bits()],
                    [std.to_bits(), rms.to_bits(), kurtosis.to_bits()],
                    "{scale} {len}"
                );
            }
        }
    }

    #[test]
    fn constant_and_limits() {
        let v = simple(&[0.0; 10], 1.0).unwrap();
        assert_eq!(v[6], 0.0);
        assert_eq!(v[7], 0.0);
        assert_eq!(simple(&[5.0], 1.0).unwrap()[4], 5.0);
        assert!(simple(&[], 1.0).is_err());
        assert!(simple(&[f64::NAN], 1.0).is_err());
        assert!(simple(&[1.0], 0.0).is_err());
        assert!(simple(&vec![0.0; MAX_SEGMENT_SAMPLES + 1], 1.0).is_err());
        assert!(simple(&vec![0.0; 40000], 1.0).is_ok());
    }

    #[test]
    fn constant_window_residue_is_below_the_floor() {
        // A constant non-zero window's std is rounding residue, not 0.
        for (x, n) in [(0.1, 2000), (101325.3, 3600), (-7.3, MAX_SEGMENT_SAMPLES)] {
            let v = simple(&vec![x; n], 1.0).unwrap();
            let floors = negligible_floors(v[RMS]);
            assert!(
                v[2] <= floors[2],
                "{x} {n}: std {} floor {}",
                v[2],
                floors[2]
            );
            assert!(floors[2] > 0.0);
        }
        assert_eq!(
            negligible_floors(2.0),
            [0.0, 2e-9, 2e-9, 2e-9, 2e-9, 2e-9, 0.0, 0.0]
        );
        assert_eq!(negligible_floors(f64::NAN), [0.0; 8]);
        // A real 24-bit signal stays above it.
        let v = simple(
            &(0..4096)
                .map(|i| 1.0 + 6e-8 * ((i % 2) as f64))
                .collect::<Vec<_>>(),
            1.0,
        )
        .unwrap();
        assert!(v[2] > negligible_floors(v[RMS])[2]);
    }
}
