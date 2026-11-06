//! Screened exact envelope search: stored envelopes are scored by a float32 screen with a rigorous error
//! bound, and only windows that could be among the m nearest are re-scored exactly.
//!
//! The screen works on the raw (pre-z-score) envelope values decoded from the stored float16 (exact in
//! float32) and their mean, standard deviation and norm. The exact distance of a window is computed from the
//! same values, decoded and z-scored with the same arithmetic as [`crate::envelope::decode`], by
//! [`LagQuery`], so it is bitwise identical to [`crate::envelope::distance`] on the stored envelope.
//!
//! The screen computes, for every lag, the float32 dot product of the shifted query with the raw values and
//! corrects it for the window's mean and standard deviation in float64:
//! `z = (s·g − mean·Σs) / sd`, `d̃ = 1 − max z / n`. With `u = 2⁻²⁴` and `γₙ = n·u / (1 − n·u)`, the float32
//! dot product of the (float32-rounded) query with exact float32 values errs by at most
//! `(γₙ + u)·Σ|s||g| ≤ (γₙ + u)·‖q‖·‖g‖` (Cauchy–Schwarz; every shifted query is a sub-vector of q), so
//! `|d̃ − d| ≤ δ = (γₙ + u)·‖q‖·‖g‖ / (sd·n) + SLACK`, where SLACK covers the float64 corrections and the exact
//! path's own rounding.
use crate::envelope::{f16_to_f64, LagQuery};
use crate::SignalError;

/// Absolute slack added to every bound: float64 rounding of the correction terms and of the exact path,
/// orders of magnitude below it for n ≤ 4,096.
const SLACK: f64 = 1e-9;
/// Lags per screen pass, padded so the accumulators of one window stay in vector registers.
const LANES: usize = 32;

/// Relative error allowed for the moments below against `envelope::decode`'s (both are float64 sums of
/// n ≤ 3,277 terms in different orders: about n·2⁻⁵³ ≈ 4e-13 at most), used in the screen's bound.
const KAPPA: f64 = 1e-12;

/// Mean, standard deviation and norm of raw values, in float64 with eight independent accumulators
/// (vectorisable). They differ from `envelope::decode`'s sequential sums by at most KAPPA relative; the
/// screen's bound accounts for that. A standard deviation that is zero or tiny against the values is
/// returned as 0: such windows are always kept and scored exactly (decode then divides by 1).
fn moments(raw: &[f32]) -> (f64, f64, f64) {
    let n = raw.len() as f64;
    let mut acc = [0f64; 8];
    let mut sq = [0f64; 8];
    let mut big = 0f64;
    let chunks = raw.chunks_exact(8);
    let tail = chunks.remainder();
    let mut mx = [0f32; 8];
    for c in chunks {
        for j in 0..8 {
            let x = c[j] as f64;
            acc[j] += x;
            sq[j] += x * x;
            mx[j] = mx[j].max(c[j].abs());
        }
    }
    let (mut sum, mut sumsq): (f64, f64) = (acc.iter().sum(), sq.iter().sum());
    big = mx.iter().fold(big, |b, &m| b.max(m as f64));
    for &x in tail {
        sum += x as f64;
        sumsq += (x as f64) * (x as f64);
        big = big.max((x as f64).abs());
    }
    let mean = sum / n;
    let mut c2 = [0f64; 8];
    let chunks = raw.chunks_exact(8);
    let tail = chunks.remainder();
    for c in chunks {
        for j in 0..8 {
            let d = c[j] as f64 - mean;
            c2[j] += d * d;
        }
    }
    let mut css: f64 = c2.iter().sum();
    for &x in tail {
        css += (x as f64 - mean) * (x as f64 - mean);
    }
    let sd = (css / n).sqrt();
    let sd = if sd > 1e-6 * big { sd } else { 0.0 };
    (mean, sd, sumsq.sqrt())
}

/// The z-scored float64 envelope `envelope::decode` returns for these raw values.
pub fn zscored(raw: &[f32]) -> Vec<f64> {
    let mut v: Vec<f64> = raw.iter().map(|&x| x as f64).collect();
    let n = v.len() as f64;
    let mean = v.iter().sum::<f64>() / n;
    let sd = (v.iter().map(|x| (x - mean) * (x - mean)).sum::<f64>() / n).sqrt();
    let s = if sd > 0.0 { sd } else { 1.0 };
    for x in v.iter_mut() {
        *x = (*x - mean) / s;
    }
    v
}

/// Raw values of a stored envelope (version byte, then float16 little-endian), without z-scoring.
pub fn raw_values(encoded: &[u8]) -> Result<Vec<f32>, SignalError> {
    if encoded.len() < 3 || encoded.len() % 2 != 1 {
        return Err(SignalError::InvalidEnvelope);
    }
    let v: Vec<f32> = encoded[1..]
        .chunks_exact(2)
        .map(|c| f16_to_f64(u16::from_le_bytes([c[0], c[1]])) as f32)
        .collect();
    if v.iter().any(|x| !x.is_finite()) {
        return Err(SignalError::InvalidEnvelope);
    }
    Ok(v)
}

/// A query prepared for screening: shifted copies of the z-scored query for every lag in float32, laid out
/// `s[t * stride + j] = q[t + lag_j]` (zero outside), the float64 sum of every shifted copy, and the norm.
pub struct ScreenQuery {
    n: usize,
    lags: usize,
    stride: usize,
    s: Vec<f32>,
    sums: Vec<f64>,
    bound_scale: f64,
    /// KAPPA·max|Σs| / n and KAPPA·‖q‖/√n: the moments' share of the bound.
    kappa_mean: f64,
    kappa_scale: f64,
    exact: LagQuery,
}

impl ScreenQuery {
    /// Bound on |screened − exact| distance for a window with these moments; infinite for degenerate ones.
    #[inline]
    fn bound(&self, mean: f64, sd: f64, norm: f64) -> f64 {
        if sd <= 0.0 {
            return f64::INFINITY;
        }
        let n = self.n as f64;
        self.bound_scale * norm / (sd * n)
            + self.kappa_mean * mean.abs() / sd
            + self.kappa_scale
            + SLACK
    }

    pub fn new(q: &[f64], max_lag: usize) -> Option<Self> {
        let n = q.len();
        // Screening scores its survivors with a prepared LagQuery; check before allocating either matrix.
        if !LagQuery::supported(n, max_lag) {
            return None;
        }
        let lags = 2 * max_lag + 1;
        let stride = lags.div_ceil(LANES) * LANES;
        let mut s = vec![0f32; n * stride];
        let mut sums = vec![0f64; lags];
        for t in 0..n {
            for j in 0..lags {
                let i = t as isize + j as isize - max_lag as isize;
                if (0..n as isize).contains(&i) {
                    s[t * stride + j] = q[i as usize] as f32;
                    sums[j] += q[i as usize];
                }
            }
        }
        let u = (2f64).powi(-24);
        let gamma = n as f64 * u / (1.0 - n as f64 * u);
        let qnorm = q.iter().map(|x| x * x).sum::<f64>().sqrt();
        let sum_max = sums.iter().fold(0f64, |a, &b| a.max(b.abs()));
        Some(Self {
            n,
            lags,
            stride,
            s,
            sums,
            bound_scale: (gamma + u) * qnorm,
            kappa_mean: KAPPA * sum_max / n as f64,
            kappa_scale: 2.0 * KAPPA * qnorm / (n as f64).sqrt(),
            exact: LagQuery::new(q, max_lag)?,
        })
    }

    /// (screened distance, bound) for one window's raw values with their moments.
    #[inline]
    fn screen(&self, raw: &[f32], mean: f64, sd: f64, norm: f64) -> (f64, f64) {
        #[cfg(target_arch = "aarch64")]
        if self.stride == 2 * LANES {
            return self.screen_neon(raw, mean, sd, norm);
        }
        self.screen_portable(raw, mean, sd, norm)
    }

    /// NEON: fused multiply-add over 16 vector accumulators (64 lags) per row.
    #[cfg(target_arch = "aarch64")]
    #[inline]
    fn screen_neon(&self, raw: &[f32], mean: f64, sd: f64, norm: f64) -> (f64, f64) {
        use std::arch::aarch64::*;
        let sd_eff = if sd > 0.0 { sd } else { 1.0 };
        let mut acc = [0f32; 2 * LANES];
        // SAFETY: NEON is part of the aarch64 baseline; every row has `stride` = 64 floats, read in bounds.
        unsafe {
            let mut v = [vdupq_n_f32(0.0); 16];
            for (row, &g) in self.s.chunks_exact(self.stride).zip(raw) {
                let gv = vdupq_n_f32(g);
                let p = row.as_ptr();
                for (k, vk) in v.iter_mut().enumerate() {
                    *vk = vfmaq_f32(*vk, vld1q_f32(p.add(4 * k)), gv);
                }
            }
            for (k, vk) in v.iter().enumerate() {
                vst1q_f32(acc.as_mut_ptr().add(4 * k), *vk);
            }
        }
        let mut best = f64::NEG_INFINITY;
        for (j, &a) in acc.iter().enumerate().take(self.lags) {
            best = best.max((a as f64 - mean * self.sums[j]) / sd_eff);
        }
        let n = self.n as f64;
        (1.0 - best / n, self.bound(mean, sd, norm))
    }

    /// NEON for two windows: lags in two passes of 32 (8 vector accumulators per window), each loaded
    /// row of shifted queries used for both windows.
    #[cfg(target_arch = "aarch64")]
    #[inline]
    fn screen2_neon(
        &self,
        a: &[f32],
        b: &[f32],
        ma: (f64, f64, f64),
        mb: (f64, f64, f64),
    ) -> [(f64, f64); 2] {
        use std::arch::aarch64::*;
        let mut acc_a = [0f32; 2 * LANES];
        let mut acc_b = [0f32; 2 * LANES];
        // SAFETY: NEON is part of the aarch64 baseline; rows have `stride` = 64 floats, read in bounds.
        unsafe {
            for half in 0..2 {
                let mut va = [vdupq_n_f32(0.0); 8];
                let mut vb = [vdupq_n_f32(0.0); 8];
                for ((row, &ga), &gb) in self.s.chunks_exact(self.stride).zip(a).zip(b) {
                    let p = row.as_ptr().add(32 * half);
                    let (gav, gbv) = (vdupq_n_f32(ga), vdupq_n_f32(gb));
                    for k in 0..8 {
                        let r = vld1q_f32(p.add(4 * k));
                        va[k] = vfmaq_f32(va[k], r, gav);
                        vb[k] = vfmaq_f32(vb[k], r, gbv);
                    }
                }
                for k in 0..8 {
                    vst1q_f32(acc_a.as_mut_ptr().add(32 * half + 4 * k), va[k]);
                    vst1q_f32(acc_b.as_mut_ptr().add(32 * half + 4 * k), vb[k]);
                }
            }
        }
        let finish = |acc: &[f32], m: (f64, f64, f64)| {
            let sd = if m.1 > 0.0 { m.1 } else { 1.0 };
            let mut best = f64::NEG_INFINITY;
            for (j, &x) in acc.iter().enumerate().take(self.lags) {
                best = best.max((x as f64 - m.0 * self.sums[j]) / sd);
            }
            let n = self.n as f64;
            (1.0 - best / n, self.bound(m.0, m.1, m.2))
        };
        [finish(&acc_a, ma), finish(&acc_b, mb)]
    }

    #[inline]
    fn screen_portable(&self, raw: &[f32], mean: f64, sd: f64, norm: f64) -> (f64, f64) {
        let mut best = f64::NEG_INFINITY;
        let sd_eff = if sd > 0.0 { sd } else { 1.0 };
        for b0 in (0..self.stride).step_by(LANES) {
            let mut acc = [0f32; LANES];
            for (row, &g) in self.s.chunks_exact(self.stride).zip(raw) {
                let r: &[f32; LANES] = row[b0..b0 + LANES].try_into().unwrap();
                for j in 0..LANES {
                    acc[j] += r[j] * g;
                }
            }
            for (j, &a) in acc.iter().enumerate().take(self.lags.saturating_sub(b0)) {
                let z = (a as f64 - mean * self.sums[b0 + j]) / sd_eff;
                best = best.max(z);
            }
        }
        let n = self.n as f64;
        (1.0 - best / n, self.bound(mean, sd, norm))
    }

    /// Exact distance of raw values, bitwise equal to `envelope::distance` on the decoded stored envelope.
    pub fn exact(&self, raw: &[f32]) -> Result<f64, SignalError> {
        self.exact.distance(&zscored(raw))
    }
}

fn f16_table() -> &'static [f32; 65536] {
    static TABLE: std::sync::OnceLock<Box<[f32; 65536]>> = std::sync::OnceLock::new();
    TABLE.get_or_init(|| {
        let mut t = Box::new([0f32; 65536]);
        for (h, v) in t.iter_mut().enumerate() {
            *v = f16_to_f64(h as u16) as f32;
        }
        t
    })
}

/// Little-endian float16 values to float32 (exact): on aarch64 eight at a time with the FCVTL
/// instructions, otherwise (and for the tail) through a lookup table. Both give identical values.
pub fn f16_to_f32(bytes: &[u8], out: &mut Vec<f32>) {
    out.clear();
    out.reserve(bytes.len() / 2);
    #[cfg(not(target_arch = "aarch64"))]
    let done = 0;
    #[cfg(target_arch = "aarch64")]
    let done = {
        use std::arch::aarch64::*;
        use std::arch::asm;
        let groups = bytes.len() / 16;
        // SAFETY: reads 16 bytes per group inside `bytes`; writes 8 floats per group into reserved space.
        unsafe {
            let dst = out.as_mut_ptr();
            for k in 0..groups {
                let h = vld1q_u16(bytes.as_ptr().add(16 * k) as *const u16);
                let lo: float32x4_t;
                let hi: float32x4_t;
                asm!("fcvtl {lo:v}.4s, {h:v}.4h", "fcvtl2 {hi:v}.4s, {h:v}.8h",
                     h = in(vreg) h, lo = out(vreg) lo, hi = out(vreg) hi, options(pure, nomem, nostack));
                vst1q_f32(dst.add(8 * k), lo);
                vst1q_f32(dst.add(8 * k + 4), hi);
            }
            out.set_len(8 * groups);
        }
        16 * groups
    };
    let table = f16_table();
    out.extend(
        bytes[done..]
            .chunks_exact(2)
            .map(|c| table[u16::from_le_bytes([c[0], c[1]]) as usize]),
    );
}

/// Screens stored envelopes one at a time (as rows of the envelope table arrive) and keeps every window
/// that could be among the `m` nearest: the m smallest upper bounds seen so far define a threshold, and a
/// window is kept while its lower bound is at most that threshold. Screeners over disjoint parts of a
/// gallery can be merged; [`Screener::finish`] then returns a superset of the gallery's m nearest windows
/// (including every window tied with the m-th), each with its bounds.
pub struct Screener<Id> {
    query: ScreenQuery,
    m: usize,
    pending: Option<(Id, (f64, f64, f64))>,
    pending_raw: Vec<f32>,
    uppers: std::collections::BinaryHeap<OrdF64>,
    kept: Vec<(Id, f64, f64)>,
    /// Prune when `kept` grows past this. It doubles with what a prune leaves, so windows tied at the threshold
    /// (constant windows all have distance 1) cost amortized O(1) each instead of a full prune per row (#279).
    prune_at: usize,
    /// Elements visited by prunes so far (the test of #279 checks it stays linear in the rows screened).
    prune_visits: usize,
    scratch: Vec<f32>,
}

#[derive(Clone, Copy)]
struct OrdF64(f64);
impl PartialEq for OrdF64 {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other).is_eq()
    }
}
impl Eq for OrdF64 {}
impl PartialOrd for OrdF64 {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}
impl Ord for OrdF64 {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.0.total_cmp(&other.0)
    }
}

impl<Id> Screener<Id> {
    pub fn new(query: &[f64], max_lag: usize, m: usize) -> Option<Self> {
        if m == 0 {
            return None;
        }
        Some(Self {
            query: ScreenQuery::new(query, max_lag)?,
            m,
            pending: None,
            pending_raw: Vec::new(),
            uppers: std::collections::BinaryHeap::new(),
            kept: Vec::new(),
            prune_at: 4 * m + 64,
            prune_visits: 0,
            scratch: Vec::new(),
        })
    }

    fn threshold(&self) -> f64 {
        if self.uppers.len() < self.m {
            f64::INFINITY
        } else {
            self.uppers.peek().unwrap().0
        }
    }

    fn admit(&mut self, id: Id, d: f64, e: f64) {
        let (lower, upper) = (d - e, d + e);
        if self.uppers.len() < self.m {
            self.uppers.push(OrdF64(upper));
        } else if upper < self.uppers.peek().unwrap().0 {
            self.uppers.pop();
            self.uppers.push(OrdF64(upper));
        }
        if lower <= self.threshold() {
            self.kept.push((id, lower, upper));
        }
        if self.kept.len() > self.prune_at {
            self.prune();
        }
    }

    fn prune(&mut self) {
        let t = self.threshold();
        self.prune_visits += self.kept.len();
        self.kept.retain(|k| k.1 <= t);
        self.prune_at = (4 * self.m + 64).max(2 * self.kept.len());
    }

    /// Windows kept so far (a superset of the m nearest seen so far, ties included).
    pub fn kept_len(&self) -> usize {
        self.kept.len()
    }

    /// Screen one stored envelope (version byte + float16 values).
    pub fn push(&mut self, id: Id, encoded: &[u8]) -> Result<(), SignalError> {
        if encoded.len() != 1 + 2 * self.query.n || encoded[0] != 1 {
            return Err(SignalError::InvalidEnvelope);
        }
        f16_to_f32(&encoded[1..], &mut self.scratch);
        if self.scratch.iter().any(|x| !x.is_finite()) {
            return Err(SignalError::InvalidEnvelope);
        }
        let mom = moments(&self.scratch);
        #[cfg(target_arch = "aarch64")]
        if self.query.stride == 2 * LANES {
            match self.pending.take() {
                None => {
                    std::mem::swap(&mut self.pending_raw, &mut self.scratch);
                    self.pending = Some((id, mom));
                }
                Some((pid, pmom)) => {
                    let r = self
                        .query
                        .screen2_neon(&self.pending_raw, &self.scratch, pmom, mom);
                    self.admit(pid, r[0].0, r[0].1);
                    self.admit(id, r[1].0, r[1].1);
                }
            }
            return Ok(());
        }
        let (d, e) = self.query.screen(&self.scratch, mom.0, mom.1, mom.2);
        self.admit(id, d, e);
        Ok(())
    }

    fn flush(&mut self) {
        if let Some((id, m)) = self.pending.take() {
            let (d, e) = self.query.screen(&self.pending_raw, m.0, m.1, m.2);
            self.admit(id, d, e);
        }
    }

    /// Merge another screener's windows (same query) into this one.
    pub fn merge(&mut self, mut other: Self) {
        other.flush();
        self.flush();
        for u in other.uppers.into_iter() {
            if self.uppers.len() < self.m {
                self.uppers.push(u);
            } else if u.0 < self.uppers.peek().unwrap().0 {
                self.uppers.pop();
                self.uppers.push(u);
            }
        }
        self.kept.extend(other.kept);
        self.prune();
    }

    /// The kept windows (id, lower, upper) and the m smallest upper bounds, for serialization.
    pub fn into_parts(mut self) -> (Vec<(Id, f64, f64)>, Vec<f64>) {
        self.flush();
        (self.kept, self.uppers.into_iter().map(|u| u.0).collect())
    }

    /// Rebuild from [`Screener::into_parts`] (with the same query).
    pub fn from_parts(
        query: &[f64],
        max_lag: usize,
        m: usize,
        kept: Vec<(Id, f64, f64)>,
        uppers: Vec<f64>,
    ) -> Option<Self> {
        let mut s = Self::new(query, max_lag, m)?;
        s.uppers = uppers.into_iter().map(OrdF64).collect();
        while s.uppers.len() > m {
            s.uppers.pop();
        }
        s.kept = kept;
        Some(s)
    }

    /// Windows whose lower bound is at most the global threshold: a superset of the m nearest.
    pub fn finish(mut self) -> Vec<(Id, f64, f64)> {
        self.flush();
        self.prune();
        self.kept
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::envelope::{decode, distance, encode};

    /// Many windows tied at the threshold (constant windows: distance 1 to everything) are all kept, and the
    /// screen stays linear: ties used to take a full prune per row (#279).
    #[test]
    fn ties_at_the_threshold_are_kept_in_linear_time() {
        let x: Vec<f64> = (0..4096)
            .map(|t| ((t as f64) / 7.0).sin() * (1.0 + (t % 300) as f64))
            .collect();
        let q = decode(&encode(&x).unwrap()).unwrap();
        let constant = encode(&vec![0.0; 4096]).unwrap();
        let mut s = Screener::new(&q, 30, 10).unwrap();
        let n = 20_000usize;
        for i in 0..n as i64 {
            s.push(i, &constant).unwrap();
        }
        // a prune per row would visit about n * n / 2 elements; doubling keeps it below 2n
        assert!(s.prune_visits < 4 * n, "{} prune visits", s.prune_visits);
        assert_eq!(s.finish().len(), n);
    }

    fn lcg(seed: &mut u64) -> f64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        (*seed >> 11) as f64 / (1u64 << 53) as f64
    }

    /// Stored envelopes of noisy bursts at random positions (4,096-sample windows).
    fn gallery(windows: usize, seed: &mut u64) -> Vec<(i64, Vec<u8>)> {
        (0..windows)
            .map(|w| {
                let at = (lcg(seed) * 3500.0) as usize;
                let x: Vec<f64> = (0..4096)
                    .map(|t| {
                        let burst = if t >= at && t < at + 400 {
                            (-((t - at) as f64) / 80.0).exp() * ((t as f64) * 0.7).sin()
                        } else {
                            0.0
                        };
                        0.05 * (lcg(seed) - 0.5) + burst
                    })
                    .collect();
                (w as i64 * 2048, encode(&x).unwrap())
            })
            .collect()
    }

    #[test]
    fn screeners_split_and_merged_keep_the_m_nearest() {
        let mut seed = 21;
        let g = gallery(700, &mut seed);
        let q = decode(&g[42].1).unwrap();
        let mut exact: Vec<(f64, i64)> = g
            .iter()
            .map(|(s, e)| (distance(&q, &decode(e).unwrap(), 30).unwrap(), *s))
            .collect();
        exact.sort_by(|a, b| a.0.total_cmp(&b.0).then(a.1.cmp(&b.1)));
        for m in [1usize, 7, 100, 200] {
            // three "workers" over interleaved parts, merged in an arbitrary order
            let mut parts: Vec<Screener<i64>> =
                (0..3).map(|_| Screener::new(&q, 30, m).unwrap()).collect();
            for (k, (s, e)) in g.iter().enumerate() {
                parts[k % 3].push(*s, e).unwrap();
            }
            let (kept, uppers) = parts.pop().unwrap().into_parts();
            let mut merged = Screener::from_parts(&q, 30, m, kept, uppers).unwrap();
            for p in parts {
                merged.merge(p);
            }
            let have: std::collections::HashSet<i64> =
                merged.finish().iter().map(|k| k.0).collect();
            let cut = exact[m - 1].0;
            for &(d, s) in exact.iter().filter(|x| x.0 <= cut) {
                assert!(have.contains(&s), "m {m}: window {s} at {d} missing");
            }
            assert!(have.len() <= m + 50, "m {m}: {} kept", have.len());
        }
    }

    #[test]
    fn hardware_and_table_conversion_agree_on_every_finite_float16() {
        let bytes: Vec<u8> = (0..=u16::MAX)
            .filter(|h| (h >> 10) & 0x1f != 0x1f) // finite values only
            .flat_map(|h| h.to_le_bytes())
            .collect();
        let mut out = Vec::new();
        f16_to_f32(&bytes, &mut out);
        for (k, c) in bytes.chunks_exact(2).enumerate() {
            let h = u16::from_le_bytes([c[0], c[1]]);
            assert_eq!(
                out[k].to_bits(),
                (f16_to_f64(h) as f32).to_bits(),
                "{h:#06x}"
            );
        }
    }

    /// Screened distance and bound of one stored envelope, through the same path the screener takes.
    fn screened(sq: &ScreenQuery, enc: &[u8]) -> (f64, f64) {
        let mut raw = Vec::new();
        f16_to_f32(&enc[1..], &mut raw);
        let m = moments(&raw);
        sq.screen(&raw, m.0, m.1, m.2)
    }

    #[test]
    fn exact_distances_are_bitwise_equal_and_the_bound_holds() {
        let mut seed = 5;
        let g = gallery(300, &mut seed);
        let q = decode(&g[17].1).unwrap();
        for lag in [0usize, 5, 30] {
            let sq = ScreenQuery::new(&q, lag).unwrap();
            for (_, enc) in &g {
                let want = distance(&q, &decode(enc).unwrap(), lag).unwrap();
                assert_eq!(
                    sq.exact(&raw_values(enc).unwrap()).unwrap().to_bits(),
                    want.to_bits()
                );
                let (d, e) = screened(&sq, enc);
                assert!(
                    e.is_finite() && (d - want).abs() <= e,
                    "bound violated: {d} vs {want} ± {e}"
                );
            }
        }
        #[cfg(target_arch = "aarch64")]
        {
            // the paired kernel gives the same screened distances as the single one
            let sq = ScreenQuery::new(&q, 30).unwrap();
            let (mut a, mut b) = (Vec::new(), Vec::new());
            f16_to_f32(&g[1].1[1..], &mut a);
            f16_to_f32(&g[2].1[1..], &mut b);
            let r = sq.screen2_neon(&a, &b, moments(&a), moments(&b));
            assert_eq!(r[0], screened(&sq, &g[1].1));
            assert_eq!(r[1], screened(&sq, &g[2].1));
        }
    }

    #[test]
    fn degenerate_and_offset_windows_are_kept_or_bounded() {
        let mut seed = 13;
        let mut g = gallery(60, &mut seed);
        let q = decode(&g[4].1).unwrap();
        // a constant window (sd 0), a nearly constant one, and one with a large offset and small spread
        let flat = encode(&vec![0.0; 4096]).unwrap();
        g.push((1_000_000, flat));
        let offset: Vec<f64> = (0..4096)
            .map(|t| 50.0 + 1e-3 * ((t as f64) * 0.01).sin() + 1e-4 * lcg(&mut seed))
            .collect();
        g.push((1_002_048, encode(&offset).unwrap()));
        let sq = ScreenQuery::new(&q, 30).unwrap();
        for (_, enc) in &g {
            let want = distance(&q, &decode(enc).unwrap(), 30).unwrap();
            let (d, e) = screened(&sq, enc);
            assert!(
                !(e.is_finite()) || (d - want).abs() <= e,
                "bound violated: {d} vs {want} ± {e}"
            );
        }
        let flat_raw = raw_values(&g[60].1).unwrap();
        assert_eq!(moments(&flat_raw).1, 0.0, "a constant window is degenerate");
        // degenerate windows are always kept, whatever m is
        let mut sc = Screener::new(&q, 30, 1).unwrap();
        for (s, e) in &g {
            sc.push(*s, e).unwrap();
        }
        let kept: Vec<i64> = sc.finish().iter().map(|k| k.0).collect();
        assert!(kept.contains(&1_000_000) && kept.contains(&g[4].0));
        let mut sc = Screener::new(&q, 30, 1).unwrap();
        assert!(sc.push(0, &g[0].1[..10]).is_err());
        assert!(Screener::<i64>::new(&q, 30, 0).is_none());
    }

    #[test]
    fn screening_feasibility_matches_preparation_for_every_window() {
        // Every supported window size and lag fraction: ScreenQuery::new succeeds exactly when
        // LagQuery::supported says so; otherwise the direct distance still scores (the SQL fallback).
        let mut unsupported = Vec::new();
        for w in [512usize, 1024, 2048, 4096, 8192, 16384, 32768] {
            let samples: Vec<f64> = (0..w)
                .map(|t| ((t as f64) * 0.013).sin() * (1.0 + (t % 97) as f64 / 97.0))
                .collect();
            let q = crate::envelope::envelope(&samples).unwrap();
            for f in [0.0, 0.075, 0.1, 0.25, 0.5] {
                let lag = (f * q.len() as f64).floor() as usize;
                let ok = LagQuery::supported(q.len(), lag);
                assert_eq!(
                    ScreenQuery::new(&q, lag).is_some(),
                    ok,
                    "window {w}, fraction {f}"
                );
                if !ok {
                    unsupported.push((w, f));
                    assert!(
                        distance(&q, &q, lag).is_ok(),
                        "direct distance, window {w}, fraction {f}"
                    );
                }
            }
        }
        assert_eq!(
            unsupported,
            vec![(16384, 0.5), (32768, 0.1), (32768, 0.25), (32768, 0.5)]
        );
    }
}
