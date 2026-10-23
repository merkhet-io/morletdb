//! Bounded, versioned window representations for transactional persistence.
use crate::{morlet, normalize, validate};
use serde::Serialize;

#[derive(Debug, Serialize)]
pub struct Segment {
    pub start_sample: usize,
    pub features: Vec<f64>,
}

#[derive(Debug, Serialize)]
pub struct Grid {
    pub version: &'static str,
    pub window_size: usize,
    pub stride: usize,
    pub normalization: String,
    pub sample_count: usize,
    pub sample_rate_hz: f64,
    pub segments: Vec<Segment>,
}

pub fn grid(
    samples: &[f64],
    rate: f64,
    window: i32,
    stride: i32,
    mode: &str,
) -> Result<Grid, String> {
    validate(samples).map_err(|e| e.to_string())?;
    if samples.len() < 512 || !rate.is_finite() || !(128.0..=200000.0).contains(&rate) {
        return Err("recordings require 512..32768 samples and a rate in [128,200000]".into());
    }
    if !(512..=4096).contains(&window) || stride < 1 {
        return Err("segment window must be 512..4096 and stride must be positive".into());
    }
    // Validate normalization even when no complete windows fit.
    normalize(&samples[..1], mode).map_err(|e| e.to_string())?;
    let window = window as usize;
    let stride = stride as usize;
    let count = if samples.len() < window {
        0
    } else {
        1 + (samples.len() - window) / stride
    };
    if count > 256 {
        return Err("segment grid exceeds 256 windows; increase stride".into());
    }
    let mut segments = Vec::with_capacity(count);
    for index in 0..count {
        let start_sample = index * stride;
        let normalized = normalize(&samples[start_sample..start_sample + window], mode)
            .map_err(|e| e.to_string())?;
        let features = morlet::extract(&normalized, rate)
            .map_err(|e| e.to_string())?
            .features();
        segments.push(Segment {
            start_sample,
            features,
        });
    }
    Ok(Grid {
        version: morlet::EXTRACTOR_VERSION,
        window_size: window,
        stride,
        normalization: mode.into(),
        sample_count: samples.len(),
        sample_rate_hz: rate,
        segments,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn grid_matches_independent_extraction() {
        let samples: Vec<f64> = (0..1025).map(|i| (i as f64 / 8.).sin()).collect();
        let grid = grid(&samples, 4096., 512, 256, "zscore").unwrap();
        assert_eq!(grid.segments.len(), 3);
        assert_eq!(grid.segments[2].start_sample, 512);
        assert_eq!(
            grid.segments[2].features,
            morlet::extract(&normalize(&samples[512..1024], "zscore").unwrap(), 4096.)
                .unwrap()
                .features()
        );
        assert_eq!(grid.sample_count, 1025);
    }
    #[test]
    fn empty_and_bounded_grids() {
        assert!(grid(&vec![0.; 512], 4096., 1024, 256, "none")
            .unwrap()
            .segments
            .is_empty());
        assert!(grid(&vec![0.; 8192], 4096., 512, 1, "none")
            .unwrap_err()
            .contains("256 windows"));
        assert!(grid(&vec![0.; 512], 4096., 1024, 256, "invalid").is_err());
        assert!(grid(&vec![0.; 512], f64::NAN, 512, 256, "none").is_err());
    }
}
