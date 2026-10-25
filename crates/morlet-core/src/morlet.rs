//! Numerical port of the Go `morlet-energy-v1` extractor.
use crate::{validate, SignalError};
use serde::Serialize;
use std::f64::consts::PI;

pub const EXTRACTOR_VERSION: &str = "morlet-energy-v1";
/// Identical kernels, bands, pooling and features to `morlet-energy-v1`, over the
/// disjoint rate domain [1,128) Hz. Band centers stay fs/64..0.4fs, so physical
/// coverage scales with the rate (100 Hz: 1.56..40 Hz). A separate version keeps
/// v1's frozen contract intact and makes every low-rate representation explicit.
pub const LOWRATE_EXTRACTOR_VERSION: &str = "morlet-energy-lowrate-v1";
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

/// Experimental localized cells. Coordinates describe coefficient centers;
/// support radius describes additional raw dependency inside the parent window.
#[derive(Debug, Clone, Serialize)]
pub struct EnergyCell {
    pub band: usize,
    pub start_sample: usize,
    pub end_sample: usize,
    pub support_radius: usize,
    pub energy: f64,
}

/// Canonical version string, or an error for unknown representations.
pub fn extractor_version(name: &str) -> Result<&'static str, SignalError> {
    [EXTRACTOR_VERSION, LOWRATE_EXTRACTOR_VERSION]
        .into_iter()
        .find(|version| *version == name)
        .ok_or(SignalError::UnknownExtractor)
}

/// Rejects rates outside the version's domain. The domains do not overlap.
pub fn validate_rate(version: &str, sample_rate_hz: f64) -> Result<(), SignalError> {
    match extractor_version(version)? {
        EXTRACTOR_VERSION if (128.0..=200_000.0).contains(&sample_rate_hz) => Ok(()),
        EXTRACTOR_VERSION => Err(SignalError::InvalidSampleRate),
        _ if (1.0..128.0).contains(&sample_rate_hz) => Ok(()),
        _ => Err(SignalError::InvalidLowSampleRate),
    }
}

/// Extract integer-coordinate cells without changing the frozen signature format.
pub fn extract_with_cells(
    samples: &[f64],
    sample_rate_hz: f64,
) -> Result<(Signature, Vec<EnergyCell>), SignalError> {
    extract_versioned_with_cells(samples, sample_rate_hz, EXTRACTOR_VERSION)
}

pub fn extract_versioned_with_cells(
    samples: &[f64],
    sample_rate_hz: f64,
    version: &str,
) -> Result<(Signature, Vec<EnergyCell>), SignalError> {
    let mut cells = Vec::new();
    let signature = extract_internal(
        samples,
        sample_rate_hz,
        version,
        |band, start, end, radius, energy| {
            cells.push(EnergyCell {
                band,
                start_sample: start,
                end_sample: end,
                support_radius: radius,
                energy,
            });
        },
    )?;
    Ok((signature, cells))
}

/// Twelve log-spaced bands fs/64..0.4fs, omega0=6, support +/-3 scale,
/// DC-corrected, complex L2-normalized kernels. Only valid coefficients are used.
/// Tokens pool consecutive blocks of up to 128 valid coefficients per band.
pub fn extract(samples: &[f64], sample_rate_hz: f64) -> Result<Signature, SignalError> {
    extract_versioned(samples, sample_rate_hz, EXTRACTOR_VERSION)
}

/// Same transform as `extract`, under an explicitly selected rate-domain version.
pub fn extract_versioned(
    samples: &[f64],
    sample_rate_hz: f64,
    version: &str,
) -> Result<Signature, SignalError> {
    extract_internal(samples, sample_rate_hz, version, |_, _, _, _, _| {})
}

fn extract_internal(
    samples: &[f64],
    sample_rate_hz: f64,
    version: &str,
    mut emit: impl FnMut(usize, usize, usize, usize, f64),
) -> Result<Signature, SignalError> {
    let version = extractor_version(version)?;
    validate(samples)?;
    if samples.len() < MIN_SAMPLES {
        return Err(SignalError::MorletLength);
    }
    // NaN is outside every domain.
    validate_rate(version, sample_rate_hz)?;
    let n = samples.len();
    // Keep Go's accumulation order as part of this version's numerical contract.
    let mean = samples.iter().map(|v| v / n as f64).sum::<f64>();
    let x: Vec<f64> = samples.iter().map(|v| v - mean).collect();
    let mut result = Signature {
        version,
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
            emit(band, start, end, radius, energy / (end - start) as f64);
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
    fn integer_cells_preserve_signature_and_support() {
        let samples = tone(1., 0.);
        let (signature, cells) = extract_with_cells(&samples, 4096.).unwrap();
        let expected = extract(&samples, 4096.).unwrap();
        assert_eq!(signature.features(), expected.features());
        assert_eq!(cells.len(), signature.tokens.len());
        for (cell, token) in cells.iter().zip(&signature.tokens) {
            assert_eq!(cell.start_sample as f64 / 4096., token.offset_seconds);
            assert_eq!(cell.energy, token.energy);
            assert!(cell.start_sample >= cell.support_radius);
            assert!(cell.end_sample + cell.support_radius <= samples.len());
            assert!(cell.end_sample > cell.start_sample);
        }
    }

    #[test]
    fn lowrate_version_has_disjoint_domain_and_identical_transform() {
        let samples: Vec<f64> = (0..2048)
            .map(|i| (i as f64 / 5.).sin() * (-((i as f64 - 900.) / 120.).powi(2)).exp())
            .collect();
        let low = extract_versioned(&samples, 100., LOWRATE_EXTRACTOR_VERSION).unwrap();
        let reference = extract(&samples, 6400.).unwrap();
        assert_eq!(low.version, LOWRATE_EXTRACTOR_VERSION);
        assert!((low.frequencies_hz[0] - 100. / 64.).abs() < 1e-12);
        assert!((low.frequencies_hz[11] - 40.).abs() < 1e-9);
        // Kernels depend only on fs/f, so sample-coordinate energies are unchanged.
        for (a, b) in low.features().iter().zip(reference.features()) {
            assert!((a - b).abs() < 1e-9 * (1. + b.abs()));
        }
        let (_, low_cells) =
            extract_versioned_with_cells(&samples, 100., LOWRATE_EXTRACTOR_VERSION).unwrap();
        let (_, cells) = extract_with_cells(&samples, 6400.).unwrap();
        assert_eq!(low_cells.len(), cells.len());
        for rate in [1., 20., 100., 127.999] {
            assert!(extract_versioned(&[0.; 512], rate, LOWRATE_EXTRACTOR_VERSION).is_ok());
            assert_eq!(
                extract(&[0.; 512], rate).unwrap_err(),
                SignalError::InvalidSampleRate
            );
        }
        for rate in [f64::NAN, 0., 0.999, 128., 4096.] {
            assert_eq!(
                extract_versioned(&[0.; 512], rate, LOWRATE_EXTRACTOR_VERSION).unwrap_err(),
                SignalError::InvalidLowSampleRate
            );
        }
        assert_eq!(
            extract_versioned(&[0.; 512], 100., "morlet-energy-v2").unwrap_err(),
            SignalError::UnknownExtractor
        );
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
