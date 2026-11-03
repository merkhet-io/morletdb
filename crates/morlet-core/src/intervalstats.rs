//! Count, min, max, mean and RMS of a sample stream in one pass, without buffering.
//!
//! Numerical semantics (float64 throughout):
//! - `count` is exact; `min` and `max` are exact sample values.
//! - `mean` is a compensated (Neumaier) sum divided by `count`.
//! - `rms` is `scale * sqrt(ssq / count)`, where `scale` is the largest |x| seen so far and `ssq` the
//!   compensated sum of `(x / scale)^2`, rescaled whenever `scale` grows (as LAPACK's `dlassq`): squares
//!   neither overflow nor underflow.
//! - Any NaN sample makes `min`, `max`, `mean` and `rms` NaN. An infinite sample makes `rms` infinite;
//!   `mean` then follows IEEE arithmetic (infinite, or NaN if both signs occur).
//! - An empty stream gives `count` 0 and NaN for the rest.
//!
//! Results are not bitwise equal to other summation orders (numpy's pairwise sum, for example); for
//! ordinary signals they agree to a few units in the last place.
//!
//! Per-chunk summaries: [`IntervalStats::merge`] combines accumulators of consecutive pieces, and
//! [`IntervalStats::to_summary`] / [`IntervalStats::from_summary`] store one in 66 bytes. Callers that
//! accumulate each stored chunk separately and merge the chunks in order get bitwise the same result
//! whether a chunk's accumulator was computed now from its decoded samples or earlier from the same
//! samples and stored as a summary.

/// Bytes of a stored summary ([`IntervalStats::to_summary`]).
pub const SUMMARY_BYTES: usize = 66;
/// Version byte of a stored summary.
pub const SUMMARY_VERSION: u8 = 1;

/// Accumulator; feed slices with [`IntervalStats::add`], read with [`IntervalStats::finish`].
#[derive(Clone, Debug)]
pub struct IntervalStats {
    n: u64,
    nan: bool,
    lo: f64,
    hi: f64,
    sum: f64,
    sum_c: f64,
    scale: f64,
    ssq: f64,
    ssq_c: f64,
}

impl Default for IntervalStats {
    fn default() -> Self {
        IntervalStats {
            n: 0,
            nan: false,
            lo: f64::INFINITY,
            hi: f64::NEG_INFINITY,
            sum: 0.0,
            sum_c: 0.0,
            scale: 0.0,
            ssq: 0.0,
            ssq_c: 0.0,
        }
    }
}

#[inline]
fn neumaier(sum: &mut f64, comp: &mut f64, v: f64) {
    let t = *sum + v;
    if t.is_finite() {
        *comp += if sum.abs() >= v.abs() {
            (*sum - t) + v
        } else {
            (v - t) + *sum
        };
    }
    *sum = t;
}

impl IntervalStats {
    pub fn add(&mut self, samples: &[f64]) {
        for &v in samples {
            self.n += 1;
            if v.is_nan() {
                self.nan = true;
                continue;
            }
            self.lo = self.lo.min(v);
            self.hi = self.hi.max(v);
            neumaier(&mut self.sum, &mut self.sum_c, v);
            let a = v.abs();
            if a > self.scale {
                if a.is_finite() {
                    let r = self.scale / a;
                    self.ssq = (self.ssq + self.ssq_c) * r * r;
                    self.ssq_c = 0.0;
                    neumaier(&mut self.ssq, &mut self.ssq_c, 1.0);
                }
                self.scale = a;
            } else if a > 0.0 {
                let r = a / self.scale;
                neumaier(&mut self.ssq, &mut self.ssq_c, r * r);
            }
        }
    }

    /// Adds the samples summarized by `other` (which follow this accumulator's samples). Sums are
    /// combined with the same compensated step; squares are rescaled to the larger of the two scales.
    pub fn merge(&mut self, other: &IntervalStats) {
        if other.n == 0 {
            return;
        }
        self.n += other.n;
        self.nan |= other.nan;
        self.lo = self.lo.min(other.lo);
        self.hi = self.hi.max(other.hi);
        neumaier(&mut self.sum, &mut self.sum_c, other.sum);
        neumaier(&mut self.sum, &mut self.sum_c, other.sum_c);
        if !other.scale.is_finite() || !self.scale.is_finite() {
            self.scale = self.scale.max(other.scale);
            return;
        }
        if other.scale > self.scale {
            let r = self.scale / other.scale;
            let mine = (self.ssq + self.ssq_c) * r * r;
            self.ssq = other.ssq;
            self.ssq_c = other.ssq_c;
            self.scale = other.scale;
            neumaier(&mut self.ssq, &mut self.ssq_c, mine);
        } else if other.scale > 0.0 {
            let r = other.scale / self.scale;
            neumaier(&mut self.ssq, &mut self.ssq_c, other.ssq * r * r);
            neumaier(&mut self.ssq, &mut self.ssq_c, other.ssq_c * r * r);
        }
    }

    /// Stored form of the accumulator (version 1, little-endian): `u8 1`, `u64 count`, `u8 nan`, then
    /// `f64` min, max, sum, sum compensation, scale, scaled sum of squares, its compensation.
    pub fn to_summary(&self) -> [u8; SUMMARY_BYTES] {
        let mut out = [0u8; SUMMARY_BYTES];
        out[0] = SUMMARY_VERSION;
        out[1..9].copy_from_slice(&self.n.to_le_bytes());
        out[9] = self.nan as u8;
        for (k, v) in [
            self.lo, self.hi, self.sum, self.sum_c, self.scale, self.ssq, self.ssq_c,
        ]
        .iter()
        .enumerate()
        {
            out[10 + 8 * k..18 + 8 * k].copy_from_slice(&v.to_le_bytes());
        }
        out
    }

    /// Parses [`IntervalStats::to_summary`]; `None` for another length, version or flag value.
    pub fn from_summary(bytes: &[u8]) -> Option<Self> {
        if bytes.len() != SUMMARY_BYTES || bytes[0] != SUMMARY_VERSION || bytes[9] > 1 {
            return None;
        }
        let f = |k: usize| f64::from_le_bytes(bytes[10 + 8 * k..18 + 8 * k].try_into().unwrap());
        Some(IntervalStats {
            n: u64::from_le_bytes(bytes[1..9].try_into().unwrap()),
            nan: bytes[9] == 1,
            lo: f(0),
            hi: f(1),
            sum: f(2),
            sum_c: f(3),
            scale: f(4),
            ssq: f(5),
            ssq_c: f(6),
        })
    }

    /// Number of samples accumulated.
    pub fn count(&self) -> u64 {
        self.n
    }

    /// `[count, min, max, mean, rms]`.
    pub fn finish(&self) -> [f64; 5] {
        let n = self.n as f64;
        if self.n == 0 {
            return [0.0, f64::NAN, f64::NAN, f64::NAN, f64::NAN];
        }
        if self.nan {
            return [n, f64::NAN, f64::NAN, f64::NAN, f64::NAN];
        }
        let mean = (self.sum + self.sum_c) / n;
        let rms = if self.scale == 0.0 || !self.scale.is_finite() {
            self.scale
        } else {
            self.scale * ((self.ssq + self.ssq_c) / n).sqrt()
        };
        [n, self.lo, self.hi, mean, rms]
    }
}

#[cfg(test)]
mod tests {
    use super::{IntervalStats, SUMMARY_BYTES};

    fn stats(x: &[f64]) -> [f64; 5] {
        let mut s = IntervalStats::default();
        // feed in uneven pieces: results must not depend on the chunking
        let mut i = 0;
        let mut step = 1;
        while i < x.len() {
            let j = (i + step).min(x.len());
            s.add(&x[i..j]);
            i = j;
            step = step * 3 % 4097 + 1;
        }
        s.finish()
    }

    fn exact_mean_rms(x: &[f64]) -> (f64, f64) {
        // reference with integer-scaled exact arithmetic for values on a 2^-20 grid
        let k: Vec<i128> = x
            .iter()
            .map(|v| (v * (1u64 << 20) as f64) as i128)
            .collect();
        let sum: i128 = k.iter().sum();
        let ssq: i128 = k.iter().map(|v| v * v).sum();
        let n = x.len() as f64;
        let mean = sum as f64 / n / (1u64 << 20) as f64;
        let rms = ((ssq as f64) / n).sqrt() / (1u64 << 20) as f64;
        (mean, rms)
    }

    fn lcg(seed: &mut u64) -> f64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((*seed >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }

    #[test]
    fn grid_signal_matches_exact_reference() {
        let mut seed = 7;
        let x: Vec<f64> = (0..200_000)
            .map(|_| (lcg(&mut seed) * 1000.0 * (1u64 << 20) as f64).round() / (1u64 << 20) as f64)
            .collect();
        let r = stats(&x);
        let (mean, rms) = exact_mean_rms(&x);
        assert_eq!(r[0], 200_000.0);
        assert_eq!(r[1], x.iter().cloned().fold(f64::INFINITY, f64::min));
        assert_eq!(r[2], x.iter().cloned().fold(f64::NEG_INFINITY, f64::max));
        assert!(
            ((r[3] - mean) / rms).abs() < 1e-14,
            "mean {} vs {}",
            r[3],
            mean
        );
        assert!(
            ((r[4] - rms) / rms).abs() < 1e-14,
            "rms {} vs {}",
            r[4],
            rms
        );
    }

    #[test]
    fn cancellation_large_offset_and_constant() {
        // +1e16, many small values, -1e16: naive summation loses the small ones
        let mut x = vec![1e16];
        x.extend(std::iter::repeat_n(1.0, 10_000));
        x.push(-1e16);
        let r = stats(&x);
        assert_eq!(r[3], 10_000.0 / 10_002.0);
        let c = vec![-3.25; 4097];
        let r = stats(&c);
        assert_eq!((r[1], r[2], r[3], r[4]), (-3.25, -3.25, -3.25, 3.25));
        let z = vec![0.0; 10];
        assert_eq!(stats(&z)[4], 0.0);
    }

    #[test]
    fn extreme_ranges_do_not_overflow_or_underflow() {
        let big = [1e300, -1e300, 1e300];
        let r = stats(&big);
        assert!((r[4] / 1e300 - 1.0).abs() < 1e-15);
        let tiny = [5e-324, -5e-324];
        assert_eq!(stats(&tiny)[4], 5e-324);
        let mixed = [1e-300, 1e300];
        assert!((stats(&mixed)[4] / (1e300 / 2f64.sqrt()) - 1.0).abs() < 1e-15);
    }

    #[test]
    fn nan_infinity_and_empty() {
        let r = stats(&[1.0, f64::NAN, 2.0]);
        assert_eq!(r[0], 3.0);
        assert!(r[1..].iter().all(|v| v.is_nan()));
        let r = stats(&[1.0, f64::INFINITY]);
        assert_eq!(
            (r[2], r[3], r[4]),
            (f64::INFINITY, f64::INFINITY, f64::INFINITY)
        );
        let r = stats(&[f64::NEG_INFINITY, f64::INFINITY]);
        assert!(r[3].is_nan());
        assert_eq!(r[4], f64::INFINITY);
        let r = stats(&[]);
        assert_eq!(r[0], 0.0);
        assert!(r[1..].iter().all(|v| v.is_nan()));
    }

    fn chunked(x: &[f64], size: usize, via_summary: bool) -> [f64; 5] {
        let mut total = IntervalStats::default();
        for c in x.chunks(size) {
            let mut s = IntervalStats::default();
            s.add(c);
            let s = if via_summary {
                IntervalStats::from_summary(&s.to_summary()).unwrap()
            } else {
                s
            };
            total.merge(&s);
        }
        total.finish()
    }

    #[test]
    fn merged_chunks_agree_with_one_pass_and_summaries_are_bitwise_neutral() {
        let mut seed = 7u64;
        let mut next = || {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            (seed >> 11) as f64 / (1u64 << 53) as f64
        };
        let x: Vec<f64> = (0..100_000)
            .map(|i| {
                ((i as f64) * 0.013).sin() * 40.0
                    + 3.0
                    + (next() - 0.5) * (1.0 + (i / 9000) as f64 * 50.0)
            })
            .collect();
        let one = stats(&x);
        for size in [1usize, 7, 4096, 32768] {
            let a = chunked(&x, size, false);
            let b = chunked(&x, size, true);
            assert_eq!(a.map(f64::to_bits), b.map(f64::to_bits), "size {size}");
            assert_eq!(a[0], one[0]);
            assert_eq!((a[1], a[2]), (one[1], one[2]));
            for k in [3, 4] {
                assert!(
                    (a[k] - one[k]).abs() <= 4.0 * f64::EPSILON * one[k].abs(),
                    "size {size} k {k}: {} vs {}",
                    a[k],
                    one[k]
                );
            }
        }
        // a growing scale across chunks, huge and tiny values, zeros, NaN and infinity
        for x in [
            vec![1e-300, 0.0, 1e300, -1e300, 2.0],
            vec![0.0; 10],
            vec![1.0, f64::NAN, 2.0],
            vec![1.0, f64::INFINITY, 2.0, -3.0],
        ] {
            let a = chunked(&x, 2, true);
            let one = stats(&x);
            for k in 0..5 {
                assert!(
                    a[k].to_bits() == one[k].to_bits()
                        || (a[k] - one[k]).abs() <= 4.0 * f64::EPSILON * one[k].abs(),
                    "{x:?} k {k}: {} vs {}",
                    a[k],
                    one[k]
                );
            }
        }
        let empty = IntervalStats::default();
        let mut m = IntervalStats::default();
        m.merge(&empty);
        assert_eq!(m.finish()[0], 0.0);
        assert!(IntervalStats::from_summary(&[0u8; SUMMARY_BYTES]).is_none());
        assert!(IntervalStats::from_summary(&empty.to_summary()[..65]).is_none());
    }
}
