//! Append-only single-channel extraction with continuous, zero-based offsets.
//! A push is atomic in memory: invalid chunks or extraction failures leave state unchanged.
use crate::{morlet, normalize, validate};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

#[derive(Debug, PartialEq, Serialize)]
pub struct ChunkSegment {
    pub start_sample: u64,
    pub features: Vec<f64>,
}

/// Versioned durable state. Restore validates all invariants before use.
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Checkpoint {
    format_version: u32,
    extractor_version: String,
    rate: f64,
    window: usize,
    stride: usize,
    normalization: String,
    end: u64,
    next_start: u64,
    buffer_start: u64,
    buffer: Vec<f64>,
}

#[derive(Clone)]
pub struct ChunkExtractor {
    rate: f64,
    window: usize,
    stride: u64,
    normalization: String,
    end: u64,
    next_start: u64,
    buffer_start: u64,
    buffer: VecDeque<f64>,
}

impl ChunkExtractor {
    pub fn new(
        rate: f64,
        window: usize,
        stride: usize,
        normalization: &str,
    ) -> Result<Self, String> {
        if !rate.is_finite()
            || !(128.0..=200000.0).contains(&rate)
            || !(512..=4096).contains(&window)
            || stride == 0
            || stride > 32768
        {
            return Err("invalid chunk extractor: rate 128..200000, window 512..4096, stride 1..32768 required".into());
        }
        normalize(&[0.], normalization).map_err(|e| e.to_string())?;
        Ok(Self {
            rate,
            window,
            stride: stride as u64,
            normalization: normalization.into(),
            end: 0,
            next_start: 0,
            buffer_start: 0,
            buffer: VecDeque::new(),
        })
    }

    pub fn checkpoint(&self) -> Checkpoint {
        Checkpoint {
            format_version: 1,
            extractor_version: morlet::EXTRACTOR_VERSION.into(),
            rate: self.rate,
            window: self.window,
            stride: self.stride as usize,
            normalization: self.normalization.clone(),
            end: self.end,
            next_start: self.next_start,
            buffer_start: self.buffer_start,
            buffer: self.buffer.iter().copied().collect(),
        }
    }

    pub fn from_checkpoint(checkpoint: Checkpoint) -> Result<Self, String> {
        if checkpoint.format_version != 1
            || checkpoint.extractor_version != morlet::EXTRACTOR_VERSION
        {
            return Err("unsupported checkpoint or extractor version".into());
        }
        let mut state = Self::new(
            checkpoint.rate,
            checkpoint.window,
            checkpoint.stride,
            &checkpoint.normalization,
        )?;
        let count = if checkpoint.end < checkpoint.window as u64 {
            0
        } else {
            1 + (checkpoint.end - checkpoint.window as u64) / checkpoint.stride as u64
        };
        let expected_start = count
            .checked_mul(checkpoint.stride as u64)
            .ok_or("checkpoint offset overflow")?;
        let expected_buffer_start = expected_start.min(checkpoint.end);
        if checkpoint.next_start != expected_start
            || checkpoint.buffer_start != expected_buffer_start
            || checkpoint.buffer.len() as u64 != checkpoint.end - expected_buffer_start
            || checkpoint.buffer.len() >= checkpoint.window
        {
            return Err("invalid checkpoint offsets or tail length".into());
        }
        if !checkpoint.buffer.is_empty() {
            validate(&checkpoint.buffer).map_err(|e| e.to_string())?;
        }
        state.end = checkpoint.end;
        state.next_start = expected_start;
        state.buffer_start = expected_buffer_start;
        state.buffer = checkpoint.buffer.into();
        Ok(state)
    }

    pub fn next_sample_offset(&self) -> u64 {
        self.end
    }
    pub fn retained_samples(&self) -> usize {
        self.buffer.len()
    }

    /// Chunks must be contiguous and contain 1..32768 finite samples. Gaps,
    /// replays and overlaps are rejected; acquisition changes require a new stream.
    /// At most 256 windows may become ready per push. No padding is synthesized.
    pub fn push(
        &mut self,
        start_sample: u64,
        samples: &[f64],
    ) -> Result<Vec<ChunkSegment>, String> {
        validate(samples).map_err(|e| e.to_string())?;
        if start_sample != self.end {
            return Err(
                "chunk offset must equal the next sample offset (no gaps, overlaps or replays)"
                    .into(),
            );
        }
        let new_end = self
            .end
            .checked_add(samples.len() as u64)
            .ok_or("sample offset overflow")?;
        let first_end = self
            .next_start
            .checked_add(self.window as u64)
            .ok_or("window offset overflow")?;
        let count = if first_end > new_end {
            0
        } else {
            1 + (new_end - first_end) / self.stride
        };
        if count > 256 {
            return Err(
                "chunk would emit more than 256 windows; use smaller chunks or a larger stride"
                    .into(),
            );
        }
        let next_start = self
            .next_start
            .checked_add(count.checked_mul(self.stride).ok_or("stride overflow")?)
            .ok_or("window offset overflow")?;
        // Stage buffer changes, so numerical failures cannot advance the stream.
        let mut buffer = self.buffer.clone();
        buffer.extend(samples.iter().copied());
        let contiguous = buffer.make_contiguous();
        let mut output = Vec::with_capacity(count as usize);
        for index in 0..count {
            let start = self.next_start + index * self.stride;
            let offset = (start - self.buffer_start) as usize;
            let normalized = normalize(
                &contiguous[offset..offset + self.window],
                &self.normalization,
            )
            .map_err(|e| e.to_string())?;
            let features = morlet::extract(&normalized, self.rate)
                .map_err(|e| e.to_string())?
                .features();
            output.push(ChunkSegment {
                start_sample: start,
                features,
            });
        }
        let keep_from = next_start.min(new_end);
        buffer.drain(..(keep_from - self.buffer_start) as usize);
        self.buffer = buffer;
        self.buffer_start = keep_from;
        self.next_start = next_start;
        self.end = new_end;
        Ok(output)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn arbitrary_boundaries_match_whole_recording() {
        let samples: Vec<f64> = (0..10003)
            .map(|i| (i as f64 / 8.).sin() + 0.1 * (i as f64 / 13.).cos())
            .collect();
        for stride in [127, 256, 512, 1024] {
            let reference = crate::segments::grid(&samples, 4096., 512, stride, "zscore").unwrap();
            let mut extractor = ChunkExtractor::new(4096., 512, stride as usize, "zscore").unwrap();
            let mut output = vec![];
            let mut offset = 0;
            for size in [1, 137, 997, 3, 4096, 511, 4258] {
                let end = (offset + size).min(samples.len());
                output.extend(
                    extractor
                        .push(offset as u64, &samples[offset..end])
                        .unwrap(),
                );
                offset = end;
                assert!(extractor.retained_samples() < 512);
            }
            assert_eq!(offset, samples.len());
            assert_eq!(output.len(), reference.segments.len());
            for (actual, expected) in output.iter().zip(reference.segments) {
                assert_eq!(actual.start_sample, expected.start_sample as u64);
                assert_eq!(actual.features, expected.features);
            }
        }
    }
    #[test]
    fn long_recording_has_global_offsets_and_bounded_tail() {
        let mut extractor = ChunkExtractor::new(4096., 512, 256, "none").unwrap();
        let mut starts = vec![];
        for offset in (0..70000).step_by(1000) {
            starts.extend(
                extractor
                    .push(offset, &vec![0.; 1000])
                    .unwrap()
                    .into_iter()
                    .map(|s| s.start_sample),
            );
            assert!(extractor.retained_samples() < 512);
        }
        assert_eq!(starts, (0..=70000 - 512).step_by(256).collect::<Vec<u64>>());
        assert_eq!(extractor.next_sample_offset(), 70000);
    }
    #[test]
    fn checkpoint_roundtrip_preserves_boundary_windows() {
        let samples: Vec<f64> = (0..2048).map(|i| (i as f64 / 8.).sin()).collect();
        let mut continuous = ChunkExtractor::new(4096., 512, 256, "zscore").unwrap();
        let expected = continuous.push(0, &samples).unwrap();
        let mut restarted = ChunkExtractor::new(4096., 512, 256, "zscore").unwrap();
        let mut actual = restarted.push(0, &samples[..137]).unwrap();
        for (start, end) in [(137, 1000), (1000, 2048)] {
            let encoded = serde_json::to_vec(&restarted.checkpoint()).unwrap();
            restarted =
                ChunkExtractor::from_checkpoint(serde_json::from_slice(&encoded).unwrap()).unwrap();
            actual.extend(
                restarted
                    .push(start, &samples[start as usize..end])
                    .unwrap(),
            );
        }
        assert_eq!(actual, expected);
        let mut bad = restarted.checkpoint();
        bad.next_start += 1;
        assert!(ChunkExtractor::from_checkpoint(bad).is_err());
        let mut bad = restarted.checkpoint();
        bad.extractor_version = "future".into();
        assert!(ChunkExtractor::from_checkpoint(bad).is_err());
    }
    #[test]
    fn invalid_push_does_not_advance_state() {
        let mut extractor = ChunkExtractor::new(4096., 512, 256, "none").unwrap();
        extractor.push(0, &[1.; 137]).unwrap();
        assert!(extractor.push(138, &[1.; 10]).is_err());
        assert!(extractor.push(0, &[1.; 10]).is_err());
        assert!(extractor.push(137, &[f64::NAN; 10]).is_err());
        assert_eq!(extractor.next_sample_offset(), 137);
        assert_eq!(extractor.push(137, &[1.; 375]).unwrap()[0].start_sample, 0);
        let mut tiny = ChunkExtractor::new(4096., 512, 256, "none").unwrap();
        let samples: Vec<f64> = (0..512)
            .map(|i| if i % 2 == 0 { 1e-100 } else { -1e-100 })
            .collect();
        assert!(tiny.push(0, &samples).is_err());
        assert_eq!(tiny.next_sample_offset(), 0);
        assert_eq!(tiny.retained_samples(), 0);
        assert_eq!(tiny.push(0, &[0.; 512]).unwrap().len(), 1);
    }
}
