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
    use super::IntervalStats;

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
}
