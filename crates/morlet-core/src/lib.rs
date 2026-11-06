//! Portable signal primitives and the versioned Morlet extractor.
pub mod chunks;
pub mod compare;
pub mod envelope;
pub mod envelope_screen;
pub mod intervalstats;
pub mod morlet;
pub mod rawcodec;
pub mod segments;
pub mod transfer;
pub mod values;
use std::fmt;

pub const MAX_SAMPLES: usize = 32_768;
pub const MAX_AMPLITUDE: f64 = 1e9;

#[derive(Debug, PartialEq, Eq)]
pub enum SignalError {
    InvalidLength,
    InvalidSample,
    LengthMismatch,
    InvalidNormalization,
    MorletLength,
    InvalidSampleRate,
    InvalidLowSampleRate,
    UnknownExtractor,
    NumericalFailure,
    EnvelopeLength,
    SegmentLength,
    InvalidEnvelope,
}
impl fmt::Display for SignalError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidLength => "waveform must contain 1..32768 samples",
            Self::InvalidSample => "samples must be finite and within +/-1e9",
            Self::LengthMismatch => "waveforms must have equal lengths",
            Self::MorletLength => "Morlet extraction requires 512..32768 samples",
            Self::InvalidSampleRate => "sample_rate_hz must be finite and in [128,200000]",
            Self::InvalidLowSampleRate => {
                "morlet-energy-lowrate-v1 requires a finite sample_rate_hz in [1,128)"
            }
            Self::UnknownExtractor => {
                "extractor must be morlet-energy-v1 or morlet-energy-lowrate-v1"
            }
            Self::NumericalFailure => "signal produces non-finite features at this numeric scale",
            Self::SegmentLength => "segments must contain 1..1048576 samples",
            Self::EnvelopeLength => "envelope windows must be a power of two in 512..32768 samples",
            Self::InvalidEnvelope => "invalid stored envelope",
            Self::InvalidNormalization => "normalization must be none, demean or zscore",
        })
    }
}
impl std::error::Error for SignalError {}

pub fn validate(samples: &[f64]) -> Result<(), SignalError> {
    if samples.is_empty() || samples.len() > MAX_SAMPLES {
        return Err(SignalError::InvalidLength);
    }
    if samples
        .iter()
        .any(|v| !v.is_finite() || v.abs() > MAX_AMPLITUDE)
    {
        return Err(SignalError::InvalidSample);
    }
    Ok(())
}

/// Raw RMS, including DC. Unlike the Go extractor's AC RMS.
pub fn rms(samples: &[f64]) -> Result<f64, SignalError> {
    validate(samples)?;
    Ok(root_mean_square(samples))
}

/// Root mean square of finite values, robust to underflow and overflow (#228). The direct sum of squares is used
/// whenever it is a normal, finite number, so every ordinary input gives bit-identical results to the plain
/// formula (stored envelopes and golden files do not change); only when squaring underflows or overflows is the
/// sum taken over values scaled by their largest magnitude. Zero for all-zero (and empty) input.
pub(crate) fn root_mean_square(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let n = values.len() as f64;
    let direct = values.iter().map(|v| v * v).sum::<f64>();
    if direct.is_finite() && direct >= f64::MIN_POSITIVE {
        return (direct / n).sqrt();
    }
    let peak = values.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
    if peak == 0.0 {
        return 0.0;
    }
    peak * (values.iter().map(|v| (v / peak) * (v / peak)).sum::<f64>() / n).sqrt()
}

/// Population standard deviation for zscore. Constant inputs become zeros.
pub fn normalize(samples: &[f64], mode: &str) -> Result<Vec<f64>, SignalError> {
    validate(samples)?;
    if mode == "none" {
        return Ok(samples.to_vec());
    }
    if mode != "demean" && mode != "zscore" {
        return Err(SignalError::InvalidNormalization);
    }
    // Use a shifted mean so exactly constant inputs center exactly to zero.
    let origin = samples[0];
    let mean_delta = samples.iter().map(|v| v - origin).sum::<f64>() / samples.len() as f64;
    let mut output: Vec<f64> = samples.iter().map(|v| (v - origin) - mean_delta).collect();
    if mode == "zscore" {
        let scale = root_mean_square(&output);
        if scale > 0.0 {
            output.iter_mut().for_each(|v| *v /= scale);
        }
    }
    Ok(output)
}

/// Aligned sample RMSE; not the Go Morlet-feature distance and not subsequence search.
pub fn distance(left: &[f64], right: &[f64], mode: &str) -> Result<f64, SignalError> {
    if left.len() != right.len() {
        return Err(SignalError::LengthMismatch);
    }
    let left = normalize(left, mode)?;
    let right = normalize(right, mode)?;
    let difference: Vec<f64> = left.iter().zip(&right).map(|(a, b)| a - b).collect();
    Ok(root_mean_square(&difference))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn raw_rms_includes_dc() {
        assert_eq!(rms(&[3., 4.]).unwrap(), (12.5_f64).sqrt());
        assert_eq!(rms(&[2., 2.]).unwrap(), 2.);
    }
    #[test]
    fn tiny_and_accepted_finite_signals_keep_their_shape() {
        // #228: squaring 1e-200 underflows; normalization must stay scale-invariant across accepted amplitudes.
        let normal = distance(&[1., -1.], &[-1., 1.], "zscore").unwrap();
        assert_eq!(normal, 2.0);
        let x: Vec<f64> = (0..512).map(|i| (i as f64 * 0.13).sin()).collect();
        let y: Vec<f64> = (0..512).map(|i| (i as f64 * 0.21).cos()).collect();
        let base = normalize(&x, "zscore").unwrap();
        let d = distance(&x, &y, "zscore").unwrap();
        for scale in [1e-300, 1e-200, 1e-150, 1e-30, 1e8] {
            let a = distance(&[scale, -scale], &[-scale, scale], "zscore").unwrap();
            assert!((a - 2.0).abs() < 1e-12, "scale {scale}: {a}");
            let xs: Vec<f64> = x.iter().map(|v| v * scale).collect();
            let ys: Vec<f64> = y.iter().map(|v| v * scale).collect();
            let z = normalize(&xs, "zscore").unwrap();
            assert!(
                z.iter().zip(&base).all(|(p, q)| (p - q).abs() < 1e-9),
                "scale {scale}"
            );
            assert!(
                (distance(&xs, &ys, "zscore").unwrap() - d).abs() < 1e-9,
                "scale {scale}"
            );
            let r = rms(&xs).unwrap() / rms(&x).unwrap();
            assert!((r / scale - 1.0).abs() < 1e-12, "scale {scale}");
            assert!(distance(&xs, &ys, "none").unwrap() > 0.0, "scale {scale}");
        }
        // constant input still centres to exact zeros
        assert_eq!(normalize(&[1e-200; 64], "zscore").unwrap(), vec![0.; 64]);
        assert_eq!(rms(&[0.0; 8]).unwrap(), 0.0);
    }
    #[test]
    fn normalization_semantics() {
        let x = [1., 2., 3., 4.];
        let y = [12., 14., 16., 18.];
        assert!(distance(&x, &y, "zscore").unwrap() < 1e-12);
        assert!(distance(&x, &y, "none").unwrap() > 1.);
        assert_eq!(normalize(&[0.1; 100], "zscore").unwrap(), vec![0.; 100]);
        assert_eq!(normalize(&x, "demean").unwrap(), vec![-1.5, -0.5, 0.5, 1.5]);
    }
    #[test]
    fn rejects_invalid_inputs() {
        assert_eq!(rms(&[]), Err(SignalError::InvalidLength));
        assert_eq!(rms(&[f64::NAN]), Err(SignalError::InvalidSample));
        assert_eq!(rms(&[f64::INFINITY]), Err(SignalError::InvalidSample));
        assert_eq!(rms(&[1e10]), Err(SignalError::InvalidSample));
        assert_eq!(
            distance(&[1.], &[1., 2.], "none"),
            Err(SignalError::LengthMismatch)
        );
        assert_eq!(
            normalize(&[1.], "unknown"),
            Err(SignalError::InvalidNormalization)
        );
    }
}
