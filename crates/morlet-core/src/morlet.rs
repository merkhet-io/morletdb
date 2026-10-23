//! Numerical port of the Go `morlet-energy-v1` extractor.
use crate::{validate, SignalError};
use serde::Serialize;
use std::f64::consts::PI;

pub const EXTRACTOR_VERSION: &str = "morlet-energy-v1";
pub const BANDS: usize = 12;
pub const MIN_SAMPLES: usize = 512;

#[derive(Debug, Clone, Serialize)]
pub struct Token {
    pub offset_seconds: f64,
    pub duration_seconds: f64,
    pub frequency_hz: f64,
    pub energy: f64,
}

#[derive(Debug, Clone, Serialize)]
pub struct Signature {
    pub version: &'static str,
    pub frequencies_hz: [f64; BANDS],
    pub energy: [f64; BANDS],
    /// AC RMS, unlike the standalone raw RMS primitive.
    pub rms: f64,
    pub peak: f64,
    pub crest_factor: f64,
    pub kurtosis: f64,
    pub tokens: Vec<Token>,
}

impl Signature {
    /// Twelve log1p energies followed by log1p AC RMS, crest factor and kurtosis.
    pub fn features(&self) -> Vec<f64> {
        self.energy
            .iter()
            .copied()
            .chain([self.rms, self.crest_factor, self.kurtosis])
            .map(f64::ln_1p)
            .collect()
    }
    pub fn distance(&self, other: &Self) -> f64 {
        let a = self.features();
        let b = other.features();
        (a.iter().zip(b).map(|(x, y)| (x - y).powi(2)).sum::<f64>() / a.len() as f64).sqrt()
    }
}

/// Twelve log-spaced bands fs/64..0.4fs, omega0=6, support +/-3 scale,
/// DC-corrected, complex L2-normalized kernels. Only valid coefficients are used.
/// Tokens pool consecutive blocks of up to 128 valid coefficients per band.
pub fn extract(samples: &[f64], sample_rate_hz: f64) -> Result<Signature, SignalError> {
    validate(samples)?;
    if samples.len() < MIN_SAMPLES {
        return Err(SignalError::MorletLength);
    }
    if !sample_rate_hz.is_finite() || !(128.0..=200_000.0).contains(&sample_rate_hz) {
        return Err(SignalError::InvalidSampleRate);
    }
    let n = samples.len();
    // Keep Go's accumulation order as part of this version's numerical contract.
    let mean = samples.iter().map(|v| v / n as f64).sum::<f64>();
    let x: Vec<f64> = samples.iter().map(|v| v - mean).collect();
    let mut result = Signature {
        version: EXTRACTOR_VERSION,
        frequencies_hz: [0.; BANDS],
        energy: [0.; BANDS],
        rms: 0.,
        peak: 0.,
        crest_factor: 0.,
        kurtosis: 0.,
        tokens: vec![],
    };
    let mut sum2 = 0.0;
    let mut sum4 = 0.0;
    for &value in &x {
        result.peak = result.peak.max(value.abs());
        sum2 += value * value;
        sum4 += value.powi(4);
    }
    result.rms = (sum2 / n as f64).sqrt();
    if result.rms > 0.0 {
        result.crest_factor = result.peak / result.rms;
        result.kurtosis = (sum4 / n as f64) / result.rms.powi(4);
    }
    for band in 0..BANDS {
        let frequency = sample_rate_hz / 64.0
            * ((sample_rate_hz * 0.4) / (sample_rate_hz / 64.0)).powf(band as f64 / 11.0);
        result.frequencies_hz[band] = frequency;
        let scale = 6.0 * sample_rate_hz / (2.0 * PI * frequency);
        let radius = (3.0 * scale).ceil() as usize;
        let size = 2 * radius + 1;
        let mut re = vec![0.0; size];
        let mut im = vec![0.0; size];
        let (mut mean_re, mut mean_im) = (0.0, 0.0);
        for k in 0..size {
            let t = (k as f64 - radius as f64) / scale;
            let gaussian = (-t * t / 2.0).exp();
            re[k] = gaussian * (6.0 * t).cos();
            im[k] = gaussian * (6.0 * t).sin();
            mean_re += re[k];
            mean_im += im[k];
        }
        let mut norm = 0.0;
        for k in 0..size {
            re[k] -= mean_re / size as f64;
            im[k] -= mean_im / size as f64;
            norm += re[k] * re[k] + im[k] * im[k];
        }
        norm = norm.sqrt();
        for k in 0..size {
            re[k] /= norm;
            im[k] /= norm;
        }
        let (mut total, mut count) = (0.0, 0usize);
        for start in (radius..n - radius).step_by(128) {
            let end = (start + 128).min(n - radius);
            let mut energy = 0.0;
            for i in start..end {
                let (mut real, mut imaginary) = (0.0, 0.0);
                for k in 0..size {
                    real += x[i + k - radius] * re[k];
                    imaginary += x[i + k - radius] * im[k];
                }
                energy += real * real + imaginary * imaginary;
            }
            total += energy;
            count += end - start;
            result.tokens.push(Token {
                offset_seconds: start as f64 / sample_rate_hz,
                duration_seconds: (end - start) as f64 / sample_rate_hz,
                frequency_hz: frequency,
                energy: energy / (end - start) as f64,
            });
        }
        result.energy[band] = total / count as f64;
    }
    if result.features().iter().any(|v| !v.is_finite()) {
        return Err(SignalError::NumericalFailure);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn tone(amplitude: f64, dc: f64) -> Vec<f64> {
        (0..2048)
            .map(|i| dc + amplitude * (2. * PI * 400. * i as f64 / 4096.).sin())
            .collect()
    }
    #[test]
    fn localization_scaling_and_dc() {
        let a = extract(&tone(1., 0.), 4096.).unwrap();
        let peak = a
            .energy
            .iter()
            .enumerate()
            .max_by(|a, b| a.1.total_cmp(b.1))
            .unwrap()
            .0;
        assert!((a.frequencies_hz[peak] / 400.).ln().abs() < 0.2);
        let b = extract(&tone(2., 0.), 4096.).unwrap();
        for i in 0..BANDS {
            assert!((b.energy[i] - 4. * a.energy[i]).abs() < 1e-9);
        }
        assert!(a.distance(&extract(&tone(1., 100.), 4096.).unwrap()) < 1e-10);
    }
    #[test]
    fn token_energy_and_boundaries() {
        let s = extract(&tone(1., 0.)[..777], 4096.).unwrap();
        for (band, f) in s.frequencies_hz.iter().enumerate() {
            let mut energy = 0.;
            let mut duration = 0.;
            let mut last = -1.;
            for t in s.tokens.iter().filter(|t| t.frequency_hz == *f) {
                assert!(t.offset_seconds > last);
                assert!(t.offset_seconds + t.duration_seconds <= 777. / 4096.);
                assert!(t.duration_seconds > 0. && t.duration_seconds <= 128. / 4096.);
                last = t.offset_seconds;
                energy += t.energy * t.duration_seconds;
                duration += t.duration_seconds;
            }
            assert!((energy / duration - s.energy[band]).abs() < 1e-9);
        }
    }
    #[test]
    fn invalid_and_silence() {
        assert_eq!(
            extract(&[0.; 511], 4096.).unwrap_err(),
            SignalError::MorletLength
        );
        for rate in [f64::NAN, f64::INFINITY, 0., 127., 200001.] {
            assert_eq!(
                extract(&[0.; 512], rate).unwrap_err(),
                SignalError::InvalidSampleRate
            );
        }
        assert!(extract(&[f64::NAN; 512], 4096.).is_err());
        assert!(extract(&[0.; 32769], 4096.).is_err());
        assert!(extract(&[3.; 512], 4096.)
            .unwrap()
            .features()
            .iter()
            .all(|v| *v == 0.));
        assert!(extract(&[0.; 512], 128.).is_ok());
        assert!(extract(&[0.; 512], 200000.).is_ok());
        let tiny: Vec<f64> = (0..512)
            .map(|i| if i % 2 == 0 { 1e-100 } else { -1e-100 })
            .collect();
        assert_eq!(
            extract(&tiny, 4096.).unwrap_err(),
            SignalError::NumericalFailure
        );
    }
}
