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
    let rms = (samples.iter().map(|v| v * v).sum::<f64>() / n).sqrt();
    let peak = samples.iter().fold(0.0f64, |a, v| a.max(v.abs()));
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
        m2.sqrt(),
        rms,
        p75,
        peak,
        if rms > 0.0 { peak / rms } else { 0.0 },
        if m2 > 0.0 { m4 / (m2 * m2) } else { 0.0 },
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

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
