//! Portable signal primitives and the versioned Morlet extractor.
pub mod chunks;
pub mod envelope;
pub mod morlet;
pub mod rawcodec;
pub mod segments;
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
    Ok((samples.iter().map(|v| v * v).sum::<f64>() / samples.len() as f64).sqrt())
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
        let scale = (output.iter().map(|v| v * v).sum::<f64>() / output.len() as f64).sqrt();
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
    Ok((left
        .iter()
        .zip(&right)
        .map(|(a, b)| (a - b).powi(2))
        .sum::<f64>()
        / left.len() as f64)
        .sqrt())
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
