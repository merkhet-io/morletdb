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
}
