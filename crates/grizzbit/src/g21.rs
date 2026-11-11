//! Grizzbit 2.1, DEVELOPMENT BUILD: chunk format version 5 (#377).
//!
//! Not a stable format and not written by [`encode`](crate::encode): only
//! [`encode_with`](crate::encode_with)`(.., `[`Format::Grizzbit21`](crate::Format::Grizzbit21)`)` writes it,
//! and only for a chunk where it is strictly smaller than the Grizzbit 1 chunk. Every version-5 chunk is
//! decoded by [`decode`](crate::decode). It builds on Grizzbit 1's grid path (versions 2 and 3), not on the
//! unshipped Grizzbit 2: version 4 was that format's pco chunk kind, which failed its frozen study (B5);
//! the number 4 is never reused.
//!
//! Version 5 has the grid kind only, with two additions to a Grizzbit 1 grid chunk:
//!
//! 1. **Corrections once per grid index.** Grizzbit 1 stores a last-place correction for every sample.
//!    When the value is a function of its grid index (calibrated float64 such as `k * a * b`), version 5
//!    stores one correction per distinct index, in order of first occurrence, plus sparse exceptions for
//!    samples that differ from their index's entry. A chunk whose few corrections are scattered (negative
//!    zeros on a decimal grid) stores the exceptions only.
//! 2. **Per-chunk LPC.** Beside the fixed predictors 0–4, a stream may use a linear predictor of order
//!    1–32 with quantised integer coefficients (precision up to 15 bits, right shift up to 31), as in
//!    FLAC's LPC subframes; the residuals use the same partitioned Rice coder.
//!
//! # Layout (version 5)
//!
//! All integers little-endian; `varint` is unsigned LEB128.
//!
//! | Field | Size | Meaning |
//! |---|---|---|
//! | version | 1 byte | `5` |
//! | kind | 1 byte | `1` (grid; the only kind in version 5) |
//! | n | varint | sample count, 1..32768 |
//! | flags | u8 | bit 0: an offset follows the scale; bits 1–2: correction mode (below); bits 3–7: zero |
//! | mode | u8 | `0`: `x = k * scale`; `1`: `x = k / scale` (as version 2) |
//! | scale | f64 | finite, > 0 |
//! | offset | f64 | only with flag bit 0; finite; the value is then `offset + (k * scale or k / scale)` |
//! | index length | varint | byte length of the index stream |
//! | index stream | — | `n` grid indices, a version-5 stream (below) |
//! | corrections | — | per correction mode, below; takes every byte up to the checksum |
//! | crc | 4 bytes | CRC-32C of every preceding byte |
//!
//! Correction modes (corrections are wrapping differences of order-preserving integers, as in version 2,
//! applied as `from_ordered(ordered(grid value) + c)`):
//!
//! - `0` none: no bytes.
//! - `1` per sample: one stream of `n` corrections (version 2's layout).
//! - `2` table: an exception block, then one stream of `D` corrections, where `D` is the number of
//!   distinct indices in the chunk and entry `j` belongs to the `j`-th distinct index in order of first
//!   occurrence. A sample's correction is its index's entry unless the sample is an exception.
//! - `3` exceptions only: an exception block with at least one exception and nothing after it; every
//!   other sample's correction is 0.
//!
//! An exception block is `m` (varint, 0..=n); if `m > 0`, a varint byte length and a stream of `m` gaps
//! (the first is the first position, each further one is `position - previous position - 1`, all ≥ 0,
//! positions < n), then a varint byte length and a stream of the `m` corrections of those samples (they
//! replace the table entry).
//!
//! A version-5 stream is a Grizzbit 1 stream (predictor 3 bits, partition order 4 bits, per partition a
//! 6-bit Rice parameter or escape, residuals, zero padding), where predictor `7` means LPC. Its parameters
//! follow the partition order: order − 1 (5 bits, order 1..=32), precision − 1 (4 bits, precision
//! 1..=15), shift (5 bits, 0..=31), then `order` coefficients of `precision` bits each in two's
//! complement, the first multiplying `x[i−1]`. Prediction, in signed 64-bit two's-complement wrapping
//! arithmetic with no floating point: for `i < order`, the fixed order-2 predictor of version 2 (so
//! `min(2, i)`); from `i = order` on, `(Σ_{j=1..order} c_j · x[i−j]) >> shift`, an arithmetic shift.
//! Predictors 5 and 6 are invalid. Every stream in a version-5 chunk (indices, corrections, table, gaps)
//! has this form; the encoder uses LPC only for the index stream.
//!
//! The encoder computes the coefficients in floating point (Welch-windowed autocorrelation,
//! Levinson-Durbin, then FLAC-style quantisation with error feedback), using only IEEE basic operations,
//! so its output does not depend on a platform's libm. The decoder uses integers only.
//!
//! # Encoder search (development default)
//!
//! - **LPC:** one order is coded exactly, the one with the smallest code length estimated from the
//!   Levinson prediction errors ([`LpcSearch::Estimated`]). It is kept only if it beats the best fixed
//!   predictor. Development measurement (evaluation/codec_g21) found the other strategies within
//!   0.01 bits/sample and slower.
//! - **Reuse:** the index and per-sample correction streams Grizzbit 1 already wrote are reused, and
//!   correction variants whose lower-bound size cannot win are skipped.
//! - **Side streams** (table, exception gaps and values) try fixed predictors 0 and 1 only.
//!
//! **Determinism:** every floating-point step is an IEEE basic operation in a fixed order: no fused
//! multiply-add (Rust never contracts `a * b + c` unless `mul_add` is called), no libm, and each
//! autocorrelation lag summed in sample order. Encoder output is therefore the same bytes on every
//! platform. CI's golden-file check on Linux reproduces the version-5 goldens written on macOS/aarch64.
use super::*;

/// Predictor field value for LPC in a version-5 stream.
pub(crate) const PREDICTOR_LPC: u64 = 7;
/// Highest fixed predictor order the encoder tries for side streams (table, exceptions).
pub(crate) const SIDE_MAX_ORDER: usize = 1;
/// Highest LPC order.
pub(crate) const MAX_LPC_ORDER: usize = 32;
const MAX_LPC_PRECISION: u32 = 15;
const MAX_LPC_SHIFT: u32 = 31;
/// Fixed predictor order used for the first `order` samples of an LPC stream.
const LPC_WARMUP_ORDER: usize = 2;
/// Bits of LPC side information besides the coefficients: order, precision and shift.
const LPC_HEADER_BITS: u128 = 5 + 4 + 5;
/// The encoder tries LPC only on streams at least this long ...
const LPC_MIN_SAMPLES: usize = 64;
/// ... and with every value below this magnitude (no overflow in the prediction sum of real signals;
/// wrapping keeps any stream exact, this only avoids hopeless candidates).
const LPC_MAX_MAGNITUDE: u64 = 1 << 40;

const FLAG_OFFSET: u8 = 1;
const FLAGS_KNOWN: u8 = 0b111;
pub(crate) const FIX_NONE: u8 = 0;
pub(crate) const FIX_SAMPLES: u8 = 1;
pub(crate) const FIX_TABLE: u8 = 2;
pub(crate) const FIX_EXCEPTIONS: u8 = 3;

/// A quantised linear predictor: `rev[p - 1 - j]` multiplies `x[i - 1 - j]`, so the history window
/// `x[i - p..i]` and `rev` line up element by element.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct Lpc {
    rev: Vec<i64>,
    precision: u32,
    shift: u32,
}

impl Lpc {
    fn order(&self) -> usize {
        self.rev.len()
    }

    fn side_bits(&self) -> u128 {
        LPC_HEADER_BITS + self.order() as u128 * self.precision as u128
    }

    pub(crate) fn write(&self, w: &mut BitWriter) {
        w.put(self.order() as u64 - 1, 5);
        w.put(self.precision as u64 - 1, 4);
        w.put(self.shift as u64, 5);
        for &c in self.rev.iter().rev() {
            w.put(c as u64, self.precision);
        }
    }

    pub(crate) fn read(rd: &mut BitReader) -> Result<Self, CodecError> {
        let order = rd.get(5)? as usize + 1;
        let precision = rd.get(4)? as u32 + 1;
        let shift = rd.get(5)? as u32;
        if precision > MAX_LPC_PRECISION {
            return Err(CodecError::Corrupt("lpc header"));
        }
        let mut rev = Vec::with_capacity(order);
        for _ in 0..order {
            rev.push(sign_extend(rd.get(precision)?, precision));
        }
        rev.reverse();
        Ok(Self {
            rev,
            precision,
            shift,
        })
    }

    /// The prediction for `x[i]`, `i >= order`, from `hist = x[i - order..i]`.
    #[inline(always)]
    fn predict(&self, hist: &[i64]) -> i64 {
        dot(hist, &self.rev) >> self.shift
    }

    /// Residuals of `x` under this predictor, zigzag-mapped, into `r`.
    fn residuals(&self, x: &[i64], r: &mut [u64]) {
        let p = self.order();
        let warm = p.min(x.len());
        for i in 0..warm {
            r[i] = zigzag(x[i].wrapping_sub(predict(x, i, LPC_WARMUP_ORDER)));
        }
        match p {
            0..=8 => self.residuals_padded::<8>(x, r, warm),
            9..=16 => self.residuals_padded::<16>(x, r, warm),
            17..=24 => self.residuals_padded::<24>(x, r, warm),
            _ => self.residuals_padded::<32>(x, r, warm),
        }
    }

    /// The coefficients zero-padded at the old end to `P`: `c[P - 1]` multiplies `x[i - 1]`. A zero
    /// coefficient adds exactly zero, so a window of `P` gives the same prediction as one of `order`.
    fn padded<const P: usize>(&self) -> [i64; P] {
        let mut c = [0i64; P];
        c[P - self.order()..].copy_from_slice(&self.rev);
        c
    }

    fn residuals_padded<const P: usize>(&self, x: &[i64], r: &mut [u64], from: usize) {
        let p = self.order();
        let n = x.len();
        let start = P.min(n).max(from);
        for i in from..start {
            r[i] = zigzag(x[i].wrapping_sub(self.predict(&x[i - p..i])));
        }
        let c = self.padded::<P>();
        for i in start..n {
            let h: &[i64; P] = x[i - P..i].try_into().unwrap();
            r[i] = zigzag(x[i].wrapping_sub(dot_fixed(h, &c) >> self.shift));
        }
    }

    /// Inverts [`Lpc::residuals`] in place: `x` holds the residuals (unzigzagged) on entry and the
    /// values on return.
    pub(crate) fn restore(&self, x: &mut [i64]) {
        let p = self.order();
        let warm = p.min(x.len());
        for i in 0..warm {
            x[i] = x[i].wrapping_add(predict(x, i, LPC_WARMUP_ORDER));
        }
        match p {
            0..=8 => self.restore_padded::<8>(x, warm),
            9..=16 => self.restore_padded::<16>(x, warm),
            17..=24 => self.restore_padded::<24>(x, warm),
            _ => self.restore_padded::<32>(x, warm),
        }
    }

    fn restore_padded<const P: usize>(&self, x: &mut [i64], from: usize) {
        let p = self.order();
        let n = x.len();
        let start = P.min(n).max(from);
        for i in from..start {
            let pred = self.predict(&x[i - p..i]);
            x[i] = x[i].wrapping_add(pred);
        }
        let c = self.padded::<P>();
        for i in start..n {
            let h: &[i64; P] = x[i - P..i].try_into().unwrap();
            let pred = dot_fixed(h, &c) >> self.shift;
            x[i] = x[i].wrapping_add(pred);
        }
    }

    /// The straightforward predictor, as the format defines it (an oracle for the unrolled kernels).
    #[cfg(any(test, kani))]
    pub(crate) fn predict_at(&self, x: &[i64], i: usize) -> i64 {
        let p = self.order();
        if i < p {
            return predict(x, i, LPC_WARMUP_ORDER);
        }
        let mut acc = 0i64;
        for j in 1..=p {
            // coefficient c_j (multiplying x[i - j]) is rev[p - j]
            acc = acc.wrapping_add(self.rev[p - j].wrapping_mul(x[i - j]));
        }
        acc >> self.shift
    }

    #[cfg(any(test, kani))]
    pub(crate) fn new(coefs: Vec<i64>, precision: u32, shift: u32) -> Self {
        let mut rev = coefs;
        rev.reverse();
        Self {
            rev,
            precision,
            shift,
        }
    }
}

/// [`dot`] over a fixed-length window, with four independent wrapping sums (wrapping addition is
/// associative and commutative, so the result is the same integer).
#[inline(always)]
fn dot_fixed<const P: usize>(h: &[i64; P], c: &[i64; P]) -> i64 {
    sum4::<P>(|j| h[j].wrapping_mul(c[j]))
}

/// The wrapping sum of `term(0..P)` in four interleaved partial sums.
#[inline(always)]
fn sum4<const P: usize>(term: impl Fn(usize) -> i64) -> i64 {
    let mut acc = [0i64; 4];
    for j in 0..P {
        acc[j % 4] = acc[j % 4].wrapping_add(term(j));
    }
    acc[0]
        .wrapping_add(acc[1])
        .wrapping_add(acc[2])
        .wrapping_add(acc[3])
}

/// Wrapping dot product: the same integers on every platform, whatever the evaluation order.
#[inline(always)]
fn dot(a: &[i64], b: &[i64]) -> i64 {
    a.iter()
        .zip(b)
        .fold(0i64, |acc, (&x, &c)| acc.wrapping_add(x.wrapping_mul(c)))
}

/// The low `bits` bits of `v` as a two's-complement integer.
pub(crate) fn sign_extend(v: u64, bits: u32) -> i64 {
    ((v << (64 - bits)) as i64) >> (64 - bits)
}

/// Quantisation precision for a stream of `n` values (as FLAC scales it with block size).
fn precision_for(n: usize) -> u32 {
    match n {
        0..=2304 => 11,
        2305..=4608 => 12,
        4609..=9216 => 13,
        9217..=18432 => 14,
        _ => MAX_LPC_PRECISION,
    }
}

/// log2 to within about 0.09, from the float's exponent and a linear mantissa (no libm).
fn log2_approx(v: f64) -> f64 {
    if v.is_nan() || v < f64::MIN_POSITIVE {
        return -1100.0;
    }
    let b = v.to_bits();
    let e = ((b >> 52) & 0x7ff) as i64 - 1023;
    let m = f64::from_bits((b & ((1u64 << 52) - 1)) | (1023u64 << 52));
    e as f64 + (m - 1.0)
}

/// FLAC-style quantisation of real coefficients `a` (a[j] multiplies x[i-1-j]) to `precision` bits with
/// error feedback. None if the coefficients are all zero, not finite, or too large for a shift >= 0.
fn quantize(a: &[f64], precision: u32) -> Option<Lpc> {
    let cmax = a.iter().fold(0.0f64, |m, &c| m.max(c.abs()));
    if !cmax.is_finite() || cmax < f64::MIN_POSITIVE {
        return None;
    }
    // floor(log2(cmax)) from the exponent of a normal float.
    let e = ((cmax.to_bits() >> 52) & 0x7ff) as i32 - 1023;
    // cmax * 2^shift lies in [2^(precision-2), 2^(precision-1)).
    let shift = (precision as i32 - 2 - e).min(MAX_LPC_SHIFT as i32);
    if shift < 0 {
        return None;
    }
    let scale = f64::from_bits(((shift + 1023) as u64) << 52);
    let qmax = (1i64 << (precision - 1)) - 1;
    let qmin = -(1i64 << (precision - 1));
    let mut err = 0.0f64;
    let mut q = Vec::with_capacity(a.len());
    for &c in a {
        err += c * scale;
        let v = (err.round() as i64).clamp(qmin, qmax);
        err -= v as f64;
        q.push(v);
    }
    if q.iter().all(|&v| v == 0) {
        return None;
    }
    q.reverse();
    Some(Lpc {
        rev: q,
        precision,
        shift: shift as u32,
    })
}

/// The Welch window of length `n`: 1 - t^2, t = (i - c) / (c + 1), c = (n - 1) / 2.
fn welch(n: usize) -> impl Iterator<Item = f64> {
    let c = (n - 1) as f64 / 2.0;
    (0..n).map(move |i| {
        let t = (i as f64 - c) / (c + 1.0);
        1.0 - t * t
    })
}

/// Real LPC coefficients for orders 1..=`pmax` by Levinson-Durbin on the Welch-windowed autocorrelation
/// of `x`, with each order's prediction error. Stops early when the error stops being positive.
fn levinson(x: &[i64], pmax: usize) -> Vec<(Vec<f64>, f64)> {
    let w: Vec<f64> = x
        .iter()
        .zip(welch(x.len()))
        .map(|(&v, w)| v as f64 * w)
        .collect();
    let r = autocorrelation(&w, pmax);
    let mut out = Vec::with_capacity(pmax);
    if !r[0].is_finite() || r[0] <= 0.0 {
        return out;
    }
    let mut a = vec![0.0f64; pmax + 1];
    let mut err = r[0];
    for p in 1..=pmax {
        let mut acc = r[p];
        for j in 1..p {
            acc -= a[j] * r[p - j];
        }
        let k = acc / err;
        if !k.is_finite() {
            break;
        }
        let prev = a.clone();
        a[p] = k;
        for j in 1..p {
            a[j] = prev[j] - k * prev[p - j];
        }
        err *= 1.0 - k * k;
        if err.is_nan() || err <= 0.0 {
            break;
        }
        out.push((a[1..=p].to_vec(), err));
    }
    out
}

/// Which LPC orders the encoder codes exactly (the format allows any order 1..=32; this only trades
/// encode time against size). Development knob: `DEFAULT` is what [`Format::Grizzbit21`] uses.
#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LpcSearch {
    /// The order with the smallest estimated code length, its half and its double (the first development
    /// build).
    Around,
    /// Only the order with the smallest estimated code length.
    Estimated,
    /// The estimated order and the highest order.
    EstimatedAndMax,
    /// A fixed set of orders (each capped at n / 8), no estimate.
    Fixed(&'static [usize]),
    /// [`LpcSearch::Estimated`], skipped when the estimate does not beat the fixed predictor's size by
    /// at least this many thousandths of a bit per sample.
    EstimatedSkip(u32),
}

impl LpcSearch {
    pub const DEFAULT: Self = Self::Estimated;
}

/// Floating point in the LPC fit: only IEEE 754 basic operations (+, -, *, / and comparisons), in a fixed
/// order, so every platform computes the same coefficients and the encoder's output is the same bytes
/// everywhere. Rust never contracts `a * b + c` into a fused multiply-add unless `mul_add` is called,
/// which this module does not; vectorising the lag loop below keeps each lag's sum in sample order.
/// No libm function (log, exp, cos, pow) is used: [`log2_approx`] and the Welch window are built from
/// basic operations. The decoder uses no floating point in prediction at all.
fn autocorrelation(w: &[f64], pmax: usize) -> Vec<f64> {
    const P: usize = MAX_LPC_ORDER;
    let n = w.len();
    let mut r = [0.0f64; P + 1];
    // Every lag is its own accumulator, summed in increasing sample order (as sum over j of
    // w[j] * w[j - lag]); the lags are independent, so the inner loop runs them side by side.
    let head = (pmax + 1).min(n);
    for j in 0..head {
        for lag in 0..=j.min(pmax) {
            r[lag] += w[j] * w[j - lag];
        }
    }
    if pmax == P {
        for j in head..n {
            let wj = w[j];
            let hist: &[f64; P + 1] = w[j - P..=j].try_into().unwrap();
            for lag in 0..=P {
                r[lag] += wj * hist[P - lag];
            }
        }
    } else {
        for j in head..n {
            for lag in 0..=pmax {
                r[lag] += w[j] * w[j - lag];
            }
        }
    }
    r[..=pmax].to_vec()
}

/// The LPC stream for `x` if one codes smaller than `fixed_cost` (the best fixed predictor's estimated
/// size in bits, partitions only) once its side information is charged; None otherwise.
pub(crate) fn lpc_stream(x: &[i64], fixed_cost: u128, search: LpcSearch) -> Option<Vec<u8>> {
    let n = x.len();
    let max_porder = max_partition_order(n);
    let mut r = vec![0u64; n];
    let mut scratch = Scratch::default();
    let (model, porder) = best_lpc(x, fixed_cost, &mut r, max_porder, &mut scratch, search)?;
    Some(write_lpc_stream(&model, &r, porder))
}

/// The LPC predictor that codes `x` smallest among the orders `search` tries, if it beats `fixed_cost`.
/// On `Some`, `r` holds its residuals; on `None`, `r` is clobbered.
pub(crate) fn best_lpc(
    x: &[i64],
    fixed_cost: u128,
    r: &mut [u64],
    max_porder: u32,
    scratch: &mut Scratch,
    search: LpcSearch,
) -> Option<(Lpc, u32)> {
    let n = x.len();
    if n < LPC_MIN_SAMPLES || x.iter().any(|v| v.unsigned_abs() >= LPC_MAX_MAGNITUDE) {
        return None;
    }
    let pmax = MAX_LPC_ORDER.min(n / 8);
    let models = levinson(x, pmax);
    if models.is_empty() {
        return None;
    }
    let precision = precision_for(n);
    // Order by estimated code length: n/2 * log2(error) + side information.
    let estimate =
        |p: usize, err: f64| 0.5 * n as f64 * log2_approx(err) + (p as u32 * precision) as f64;
    let (best_p, best_est) = models
        .iter()
        .enumerate()
        .map(|(i, (_, e))| (i + 1, estimate(i + 1, *e)))
        .fold((0, f64::INFINITY), |b, c| if c.1 < b.1 { c } else { b });
    if best_p == 0 {
        return None;
    }
    let mut orders: Vec<usize> = match search {
        LpcSearch::Around => vec![best_p, (best_p * 2).min(models.len()), best_p.div_ceil(2)],
        LpcSearch::Estimated => vec![best_p],
        LpcSearch::EstimatedAndMax => vec![best_p, models.len()],
        LpcSearch::Fixed(set) => set.iter().map(|&p| p.min(models.len())).collect(),
        LpcSearch::EstimatedSkip(milli) => {
            // Absolute size estimate: Rice on a Laplacian residual of variance err / sum(w^2) costs
            // about log2(sigma) + 1.25 bits per sample.
            let wsum: f64 = welch(n).map(|w| w * w).sum();
            let err = models[best_p - 1].1 / wsum;
            let est = n as f64 * (0.5 * log2_approx(err) + 1.25) + best_est
                - 0.5 * n as f64 * log2_approx(models[best_p - 1].1);
            let bar = fixed_cost as f64 - milli as f64 / 1000.0 * n as f64;
            if est >= bar {
                return None;
            }
            vec![best_p]
        }
    };
    orders.retain(|&p| p >= 1);
    orders.sort_unstable();
    orders.dedup();
    let mut best: Option<(u128, Lpc, u32)> = None;
    // Whether `r` still holds the best candidate's residuals (it does when that was the last one tried).
    let mut r_is_best = false;
    for p in orders {
        let Some(model) = quantize(&models[p - 1].0, precision) else {
            continue;
        };
        model.residuals(x, r);
        let (cost, porder) = partition_search(r, max_porder, scratch);
        let total = cost + model.side_bits();
        let bar = best.as_ref().map_or(fixed_cost, |b| b.0);
        r_is_best = total < bar;
        if r_is_best {
            best = Some((total, model, porder));
        }
    }
    let (_, model, porder) = best?;
    if !r_is_best {
        model.residuals(x, r);
    }
    Some((model, porder))
}

/// For each sample, the number of its grid index among the chunk's distinct indices in order of first
/// occurrence, and the number of distinct indices. Worst case O(n log n) for any input.
pub(crate) fn first_occurrence_slots(ints: &[i64]) -> (Vec<u32>, usize) {
    let n = ints.len();
    let (lo, hi) = ints
        .iter()
        .fold((i64::MAX, i64::MIN), |(lo, hi), &k| (lo.min(k), hi.max(k)));
    let span = (hi as i128 - lo as i128) as u128;
    if span < (4 * n + 1024) as u128 {
        let mut map = vec![u32::MAX; span as usize + 1];
        let mut d = 0u32;
        let slots = ints
            .iter()
            .map(|&k| {
                let e = &mut map[k.wrapping_sub(lo) as u64 as usize];
                if *e == u32::MAX {
                    *e = d;
                    d += 1;
                }
                *e
            })
            .collect();
        return (slots, d as usize);
    }
    let mut idx: Vec<u32> = (0..n as u32).collect();
    idx.sort_unstable_by_key(|&i| (ints[i as usize], i));
    // Groups of equal index; each group's first element is its first occurrence.
    let mut group = vec![0u32; n];
    let mut firsts: Vec<u32> = Vec::new();
    for (j, &i) in idx.iter().enumerate() {
        if j == 0 || ints[idx[j - 1] as usize] != ints[i as usize] {
            firsts.push(i);
        }
        group[i as usize] = firsts.len() as u32 - 1;
    }
    let mut by_first: Vec<u32> = (0..firsts.len() as u32).collect();
    by_first.sort_unstable_by_key(|&g| firsts[g as usize]);
    let mut rank = vec![0u32; firsts.len()];
    for (r, &g) in by_first.iter().enumerate() {
        rank[g as usize] = r as u32;
    }
    (
        group.iter().map(|&g| rank[g as usize]).collect(),
        firsts.len(),
    )
}

/// The most frequent correction of each slot (ties: the smallest).
fn majority(slots: &[u32], d: usize, fix: &[i64]) -> Vec<i64> {
    let mut table = vec![0i64; d];
    let mut seen = vec![false; d];
    let mut function = true;
    for (&s, &c) in slots.iter().zip(fix) {
        let s = s as usize;
        if !seen[s] {
            seen[s] = true;
            table[s] = c;
        } else if table[s] != c {
            function = false;
        }
    }
    if function {
        return table;
    }
    let mut pairs: Vec<(u32, i64)> = slots.iter().copied().zip(fix.iter().copied()).collect();
    pairs.sort_unstable();
    let mut i = 0;
    while i < pairs.len() {
        let s = pairs[i].0;
        let mut best = (0usize, 0i64);
        while i < pairs.len() && pairs[i].0 == s {
            let c = pairs[i].1;
            let mut count = 0;
            while i < pairs.len() && pairs[i] == (s, c) {
                count += 1;
                i += 1;
            }
            if count > best.0 {
                best = (count, c);
            }
        }
        table[s as usize] = best.1;
    }
    table
}

fn put_exceptions(out: &mut Vec<u8>, positions: &[usize], fix: &[i64]) {
    put_varint(out, positions.len() as u64);
    if positions.is_empty() {
        return;
    }
    let mut prev: Option<usize> = None;
    let gaps: Vec<i64> = positions
        .iter()
        .map(|&p| {
            let g = p - prev.map_or(0, |q| q + 1);
            prev = Some(p);
            g as i64
        })
        .collect();
    let values: Vec<i64> = positions.iter().map(|&p| fix[p]).collect();
    for stream in [gaps, values] {
        let mut s = Vec::new();
        encode_side_stream(&stream, &mut s);
        put_varint(out, s.len() as u64);
        out.extend(s);
    }
}

/// The smallest correction section for `fix`: none, per sample, or (with `sparse`) table plus exceptions
/// or exceptions only. Per sample wins ties, so a chunk changes only when a new coding is smaller.
///
/// `per_sample` is Grizzbit 1's correction stream for `fix` (empty when every correction is 0). A
/// variant is skipped, never changing the result, when a lower bound of its size is not smaller: every
/// value in a stream costs at least one bit and a nonzero one at least two (a Rice code of a value >= 1
/// with parameter k takes at least k + 1 bits, and at least 2 when k is 0), so `m` exceptions with
/// nonzero corrections cost at least 3m bits and a table of `d` entries at least d bits.
fn corrections(ints: &[i64], fix: &[i64], per_sample: &[u8], sparse: bool) -> (u8, Vec<u8>) {
    if per_sample.is_empty() {
        return (FIX_NONE, Vec::new());
    }
    let mut best = (FIX_SAMPLES, per_sample.to_vec());
    if !sparse {
        return best;
    }
    let m = fix.iter().filter(|&&c| c != 0).count();
    if 3 * m < 8 * best.1.len() {
        let nonzero: Vec<usize> = (0..fix.len()).filter(|&i| fix[i] != 0).collect();
        let mut exceptions = Vec::new();
        put_exceptions(&mut exceptions, &nonzero, fix);
        if exceptions.len() < best.1.len() {
            best = (FIX_EXCEPTIONS, exceptions);
        }
    }
    let (slots, d) = first_occurrence_slots(ints);
    if d >= 8 * best.1.len() {
        return best;
    }
    let table = majority(&slots, d, fix);
    if table.iter().any(|&c| c != 0) {
        let off: Vec<usize> = (0..fix.len())
            .filter(|&i| fix[i] != table[slots[i] as usize])
            .collect();
        let mut bytes = Vec::new();
        put_exceptions(&mut bytes, &off, fix);
        encode_side_stream(&table, &mut bytes);
        if bytes.len() < best.1.len() {
            best = (FIX_TABLE, bytes);
        }
    }
    best
}

/// The version-5 body (no checksum) of a grid coding the Grizzbit 1 encoder computed.
pub(crate) fn encode_plan(n: usize, plan: &Plan, features: Grizzbit21Features) -> Vec<u8> {
    let (fix_mode, fix_bytes) = corrections(
        &plan.ints,
        &plan.fix,
        &plan.fix_stream,
        features.corrections,
    );
    let mut out = vec![GRIZZBIT21_VERSION, KIND_GRID];
    put_varint(&mut out, n as u64);
    out.push(
        if plan.grid.offset.is_some() {
            FLAG_OFFSET
        } else {
            0
        } | (fix_mode << 1),
    );
    out.push(plan.grid.mode);
    out.extend_from_slice(&plan.grid.scale.to_le_bytes());
    if let Some(o) = plan.grid.offset {
        out.extend_from_slice(&o.to_le_bytes());
    }
    // Grizzbit 1's index stream, unless an LPC stream beats its fixed predictor.
    let lpc = features
        .lpc
        .then(|| lpc_stream(&plan.ints, plan.ints_cost, features.search))
        .flatten();
    let stream = lpc.as_deref().unwrap_or(&plan.ints_stream);
    put_varint(&mut out, stream.len() as u64);
    out.extend_from_slice(stream);
    out.extend(fix_bytes);
    out
}

/// A varint length, then that many bytes.
fn take<'a>(bytes: &'a [u8], pos: &mut usize) -> Result<&'a [u8], CodecError> {
    let len = get_varint(bytes, pos)?;
    let end = pos
        .checked_add(usize::try_from(len).map_err(|_| CodecError::Corrupt("length"))?)
        .filter(|&e| e <= bytes.len())
        .ok_or(CodecError::Corrupt("truncated"))?;
    let s = &bytes[*pos..end];
    *pos = end;
    Ok(s)
}

/// An exception block: strictly increasing positions below `n` and their corrections.
fn read_exceptions(
    bytes: &[u8],
    pos: &mut usize,
    n: usize,
) -> Result<(Vec<usize>, Vec<i64>), CodecError> {
    let m = get_varint(bytes, pos)?;
    if m > n as u64 {
        return Err(CodecError::Corrupt("exception count"));
    }
    let m = m as usize;
    if m == 0 {
        return Ok((Vec::new(), Vec::new()));
    }
    let gaps = decode_stream(take(bytes, pos)?, m, true)?;
    let values = decode_stream(take(bytes, pos)?, m, true)?;
    Ok((positions_from_gaps(&gaps, n)?, values))
}

/// Exception positions from their gaps: the first gap is the first position, each further one the distance
/// to the previous position minus one. Every gap must be >= 0 and every position < `n`, so the positions are
/// strictly increasing and index the chunk's samples.
pub(crate) fn positions_from_gaps(gaps: &[i64], n: usize) -> Result<Vec<usize>, CodecError> {
    let mut positions = Vec::with_capacity(gaps.len());
    let mut next = 0u64;
    for &g in gaps {
        if g < 0 {
            return Err(CodecError::Corrupt("exception position"));
        }
        // next <= n <= 2^15 and g < 2^63, so the sum cannot overflow.
        let p = next + g as u64;
        if p >= n as u64 {
            return Err(CodecError::Corrupt("exception position"));
        }
        positions.push(p as usize);
        next = p + 1;
    }
    Ok(positions)
}

/// The flags byte of a version-5 chunk: (offset present, correction mode). Bits 3-7 must be zero.
pub(crate) fn parse_flags(flags: u8) -> Result<(bool, u8), CodecError> {
    if flags & !FLAGS_KNOWN != 0 {
        return Err(CodecError::Corrupt("grid header"));
    }
    Ok((flags & FLAG_OFFSET != 0, flags >> 1))
}

/// Decodes a checksum-verified version-5 body (`kind_of` accepted it: version 5, grid kind).
pub(crate) fn decode_body(b: &[u8]) -> Result<Vec<f64>, CodecError> {
    let mut pos = 2;
    let n = get_varint(b, &mut pos)?;
    if n == 0 || n > MAX_SAMPLES as u64 {
        return Err(CodecError::Corrupt("sample count"));
    }
    let n = n as usize;
    let flags = *b.get(pos).ok_or(CodecError::Corrupt("truncated"))?;
    pos += 1;
    let (shifted, fix_mode) = parse_flags(flags)?;
    let size = if shifted { 17 } else { 9 };
    let header = b
        .get(pos..pos + size)
        .ok_or(CodecError::Corrupt("truncated"))?;
    let mode = header[0];
    let scale = f64::from_le_bytes(header[1..9].try_into().unwrap());
    let offset = shifted.then(|| f64::from_le_bytes(header[9..17].try_into().unwrap()));
    if mode > MODE_DIV
        || !scale.is_finite()
        || scale <= 0.0
        || offset.is_some_and(|o| !o.is_finite())
    {
        return Err(CodecError::Corrupt("grid header"));
    }
    let grid = Grid {
        mode,
        scale,
        offset,
    };
    pos += size;
    let ints = decode_stream(take(b, &mut pos)?, n, true)?;
    let rest = &b[pos..];
    let fixed = |k: i64, c: i64| float_from_ordered(ordered_bits(grid.value(k)).wrapping_add(c));
    let (mut values, (positions, corrections)) = match fix_mode {
        FIX_NONE => {
            if !rest.is_empty() {
                return Err(CodecError::Corrupt("trailing data"));
            }
            (
                ints.iter().map(|&k| grid.value(k)).collect(),
                Default::default(),
            )
        }
        FIX_SAMPLES => {
            let fix = decode_stream(rest, n, true)?;
            let values: Vec<f64> = ints.iter().zip(fix).map(|(&k, c)| fixed(k, c)).collect();
            (values, Default::default())
        }
        FIX_TABLE => {
            let mut p = 0;
            let exceptions = read_exceptions(rest, &mut p, n)?;
            let (slots, d) = first_occurrence_slots(&ints);
            let table = decode_stream(&rest[p..], d, true)?;
            let values: Vec<f64> = ints
                .iter()
                .zip(slots)
                .map(|(&k, s)| fixed(k, table[s as usize]))
                .collect();
            (values, exceptions)
        }
        _ => {
            let mut p = 0;
            let exceptions = read_exceptions(rest, &mut p, n)?;
            if exceptions.0.is_empty() {
                return Err(CodecError::Corrupt("exception count"));
            }
            if p != rest.len() {
                return Err(CodecError::Corrupt("trailing data"));
            }
            (ints.iter().map(|&k| grid.value(k)).collect(), exceptions)
        }
    };
    // An exception's correction replaces the table entry (or the implied 0).
    for (i, c) in positions.into_iter().zip(corrections) {
        values[i] = fixed(ints[i], c);
    }
    Ok(values)
}

/// Which of version 5's codings a chunk uses: (correction mode, index stream uses LPC). None for a
/// chunk of another version or one that does not decode. Development measurements only.
#[doc(hidden)]
pub fn layout(encoded: &[u8]) -> Option<(u8, bool)> {
    let b = verified(encoded).ok()?;
    if b[0] != GRIZZBIT21_VERSION || b[1] != KIND_GRID {
        return None;
    }
    let mut pos = 2;
    get_varint(b, &mut pos).ok()?;
    let flags = *b.get(pos)?;
    pos += 1 + if flags & FLAG_OFFSET != 0 { 17 } else { 9 };
    let stream = take(b, &mut pos).ok()?;
    Some((flags >> 1, stream.first()? >> 5 == PREDICTOR_LPC as u8))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lcg(seed: &mut u64) -> u64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *seed >> 11
    }

    /// The unrolled kernels (zero-padded windows, four partial sums) give the format's prediction, for
    /// every order, extreme coefficients and shifts, and values that overflow the sum.
    #[test]
    fn unrolled_lpc_matches_the_definition() {
        let mut seed = 77u64;
        for case in 0..400 {
            let order = 1 + case % MAX_LPC_ORDER;
            let precision = 1 + (lcg(&mut seed) % 15) as u32;
            let shift = (lcg(&mut seed) % 32) as u32;
            let coefs: Vec<i64> = (0..order)
                .map(|_| sign_extend(lcg(&mut seed), precision))
                .collect();
            let model = Lpc::new(coefs, precision, shift);
            let n = 1 + (lcg(&mut seed) % 300) as usize;
            let big = case % 3 == 0;
            let x: Vec<i64> = (0..n)
                .map(|_| {
                    if big {
                        (lcg(&mut seed) << 11) as i64
                    } else {
                        (lcg(&mut seed) % 2001) as i64 - 1000
                    }
                })
                .collect();
            let mut r = vec![0u64; n];
            model.residuals(&x, &mut r);
            for i in 0..n {
                assert_eq!(
                    unzigzag(r[i]),
                    x[i].wrapping_sub(model.predict_at(&x, i)),
                    "case {case} i {i}"
                );
            }
            let mut back: Vec<i64> = r.iter().map(|&u| unzigzag(u)).collect();
            model.restore(&mut back);
            assert_eq!(back, x, "case {case}");
        }
    }

    #[test]
    fn coefficients_roundtrip_through_the_bit_stream() {
        let mut seed = 3u64;
        for _ in 0..200 {
            let order = 1 + (lcg(&mut seed) as usize % MAX_LPC_ORDER);
            let precision = 1 + (lcg(&mut seed) % 15) as u32;
            let shift = (lcg(&mut seed) % 32) as u32;
            let coefs: Vec<i64> = (0..order)
                .map(|_| sign_extend(lcg(&mut seed), precision))
                .collect();
            let model = Lpc::new(coefs, precision, shift);
            let mut w = BitWriter::default();
            model.write(&mut w);
            let bytes = w.finish();
            let mut rd = BitReader::new(&bytes);
            assert_eq!(Lpc::read(&mut rd).unwrap(), model);
        }
        // precision 16 (field value 15) is invalid
        let mut w = BitWriter::default();
        w.put(0, 5);
        w.put(15, 4);
        w.put(0, 5);
        w.put(0, 16);
        let bytes = w.finish();
        assert_eq!(
            Lpc::read(&mut BitReader::new(&bytes)),
            Err(CodecError::Corrupt("lpc header"))
        );
    }

    fn slots_reference(ints: &[i64]) -> (Vec<u32>, usize) {
        let mut seen: Vec<i64> = Vec::new();
        let slots = ints
            .iter()
            .map(|k| match seen.iter().position(|s| s == k) {
                Some(p) => p as u32,
                None => {
                    seen.push(*k);
                    seen.len() as u32 - 1
                }
            })
            .collect();
        (slots, seen.len())
    }

    /// Both slot paths (dense map for small spans, sort otherwise) number indices by first occurrence.
    #[test]
    fn slots_follow_first_occurrence_on_both_paths() {
        let mut seed = 11u64;
        for case in 0..300 {
            let n = 1 + (lcg(&mut seed) % 2000) as usize;
            let spread: i64 = match case % 4 {
                0 => 3,
                1 => 500,
                2 => 1 << 40,
                _ => i64::MAX,
            };
            let ints: Vec<i64> = (0..n)
                .map(|_| match lcg(&mut seed) % 7 {
                    0 => i64::MIN,
                    1 => i64::MAX,
                    _ => ((lcg(&mut seed) << 11) as i64) % spread,
                })
                .collect();
            assert_eq!(
                first_occurrence_slots(&ints),
                slots_reference(&ints),
                "case {case}"
            );
        }
    }

    #[test]
    fn majority_takes_the_most_frequent_correction() {
        let slots = [0, 1, 0, 0, 1, 2, 1];
        let fix = [5, -1, 7, 7, -1, 0, 3];
        assert_eq!(majority(&slots, 3, &fix), vec![7, -1, 0]);
        // a function of the slot: the first value, no sorting
        assert_eq!(majority(&[0, 1, 0], 2, &[4, 2, 4]), vec![4, 2]);
        // ties: the smallest
        assert_eq!(majority(&[0, 0], 1, &[3, -3]), vec![-3]);
    }

    #[test]
    fn quantisation_fits_the_precision_and_tracks_the_coefficients() {
        let a = [1.8, -0.9, 0.05];
        for precision in 3..=15 {
            let m = quantize(&a, precision).unwrap();
            let lim = 1i64 << (precision - 1);
            assert!(m.rev.iter().all(|&c| -lim <= c && c < lim));
            if precision >= 8 {
                let s = (1u64 << m.shift) as f64;
                for (q, &c) in m.rev.iter().rev().zip(&a) {
                    assert!((*q as f64 / s - c).abs() < 4.0 / s, "{precision}");
                }
            }
        }
        assert!(quantize(&[0.0, 0.0], 12).is_none());
        assert!(quantize(&[f64::NAN], 12).is_none());
        // too large for any shift >= 0 at this precision
        assert!(quantize(&[1e6], 4).is_none());
    }

    #[test]
    fn log2_approx_is_close() {
        for &(v, want) in &[
            (1.0, 0.0),
            (2.0, 1.0),
            (1024.0, 10.0),
            (0.25, -2.0),
            (3.0, 1.585),
        ] {
            assert!((log2_approx(v) - want).abs() < 0.09, "{v}");
        }
        assert!(log2_approx(0.0) < -1000.0);
    }

    /// LPC is chosen for a resonant signal and the stream decodes; without LPC the bytes are
    /// Grizzbit 1's stream coder's.
    #[test]
    fn lpc_stream_wins_on_a_resonance_and_roundtrips() {
        let mut seed = 5u64;
        let (mut y1, mut y2) = (0.0f64, 0.0f64);
        let x: Vec<i64> = (0..4096)
            .map(|_| {
                let e = (lcg(&mut seed) % 201) as f64 - 100.0;
                let y = 1.9 * y1 - 0.95 * y2 + e;
                y2 = y1;
                y1 = y;
                y.round() as i64
            })
            .collect();
        let (mut fixed, mut with_lpc, mut g1) = (Vec::new(), Vec::new(), Vec::new());
        encode_stream(&x, false, &mut fixed);
        encode_stream(&x, true, &mut with_lpc);
        encode_ints(&x, &mut g1);
        assert_eq!(fixed, g1);
        assert!(with_lpc.len() < fixed.len());
        assert_eq!(with_lpc[0] >> 5, PREDICTOR_LPC as u8);
        assert_eq!(decode_stream(&with_lpc, x.len(), true).unwrap(), x);
        // a Grizzbit 1 reader refuses predictor 7
        assert_eq!(
            decode_stream(&with_lpc, x.len(), false),
            Err(CodecError::Corrupt("predictor header"))
        );
    }
}

/// Bounded model checking (Kani) of version 5's integer arithmetic. Bounds: docs/verification.md style.
#[cfg(kani)]
mod proofs {
    use super::*;

    /// The flags byte: accepted exactly when bits 3-7 are zero, giving the offset bit and a correction mode
    /// 0..=3.
    #[kani::proof]
    fn version5_flags_are_checked() {
        let flags: u8 = kani::any();
        match parse_flags(flags) {
            Ok((offset, mode)) => {
                assert!(flags < 8);
                assert_eq!(offset, flags & 1 == 1);
                assert!(mode <= 3 && mode == flags >> 1);
            }
            Err(e) => {
                assert!(flags >= 8);
                // (messages are compared by length: CBMC cannot bound a string comparison)
                assert!(matches!(e, CodecError::Corrupt(m) if m.len() == "grid header".len()));
            }
        }
    }

    /// Exception positions from any gaps: never a panic or overflow, and when accepted, strictly increasing
    /// and below n (so every exception indexes a sample of the chunk).
    #[kani::proof]
    #[kani::unwind(6)]
    fn exception_positions_are_in_range() {
        let gaps: [i64; 4] = kani::any();
        let m: usize = kani::any();
        kani::assume(m <= 4);
        let n: usize = kani::any();
        kani::assume(n >= 1 && n <= MAX_SAMPLES);
        if let Ok(p) = positions_from_gaps(&gaps[..m], n) {
            assert_eq!(p.len(), m);
            for k in 0..m {
                assert!(p[k] < n);
                assert!(gaps[k] >= 0);
                if k > 0 {
                    assert_eq!(p[k], p[k - 1] + 1 + gaps[k] as usize);
                }
            }
        }
    }

    /// Kinds by version: version 5 has the grid kind only; every other kind byte under version 5 is refused,
    /// and version 4 (the unshipped Grizzbit 2) is refused as an unknown version before its checksum is read.
    #[kani::proof]
    fn version5_kind_dispatch() {
        let kind: u8 = kani::any();
        match kind_of(&[GRIZZBIT21_VERSION, kind]) {
            Ok(k) => {
                assert_eq!(kind, KIND_GRID);
                assert_eq!(k, Kind::Grid);
            }
            Err(e) => {
                assert!(kind != KIND_GRID);
                assert!(matches!(e, CodecError::Corrupt(m) if m.len() == "unknown kind".len()));
            }
        }
        let rest: [u8; 6] = kani::any();
        let mut chunk = [4u8; 7];
        chunk[1..].copy_from_slice(&rest);
        assert!(matches!(
            verified(&chunk),
            Err(CodecError::Corrupt(m)) if m.len() == "unknown format version".len()
        ));
    }

    /// A version-5 chunk whose checksum does not match is refused before its body is parsed.
    #[kani::proof]
    fn version5_checksum_is_checked_first() {
        let chunk: [u8; 7] = kani::any();
        kani::assume(chunk[0] == GRIZZBIT21_VERSION);
        let body = &chunk[..3];
        match verified(&chunk) {
            Ok(b) => {
                assert!(crc32c(body).to_le_bytes() == chunk[3..]);
                assert!(b.len() == 3 && b.as_ptr() == chunk.as_ptr());
            }
            Err(e) => {
                assert!(crc32c(body).to_le_bytes() != chunk[3..]);
                assert!(
                    matches!(e, CodecError::Corrupt(m) if m.len() == "checksum mismatch".len())
                );
            }
        }
    }

    /// Keep the smaller: the version-5 candidate replaces Grizzbit 1's bytes only if strictly shorter.
    #[kani::proof]
    #[kani::unwind(5)]
    fn keep_smaller_keeps_grizzbit1_unless_strictly_smaller() {
        let (a, b): ([u8; 3], [u8; 3]) = (kani::any(), kani::any());
        let (la, lb): (usize, usize) = (kani::any(), kani::any());
        kani::assume(la <= 3 && lb <= 3);
        let kept = keep_smaller(a[..la].to_vec(), b[..lb].to_vec());
        if lb < la {
            assert_eq!(kept, b[..lb].to_vec());
        } else {
            assert_eq!(kept, a[..la].to_vec());
        }
    }

    /// The LPC prediction in wrapping i64 equals the exact (i128) sum shifted right, on bounded inputs: values
    /// below the encoder's 2^40 limit and coefficients of up to 15 bits, order 2, any shift. So for every
    /// stream the encoder writes, the wrapping arithmetic never wraps.
    #[kani::proof]
    fn lpc_prediction_matches_wide_reference() {
        let x: [i64; 2] = kani::any();
        let c: [i64; 2] = kani::any();
        for k in 0..2 {
            kani::assume(x[k] > -(1i64 << 40) && x[k] < (1i64 << 40));
            kani::assume(c[k] >= -(1i64 << 14) && c[k] < (1i64 << 14));
        }
        let shift: u32 = kani::any();
        kani::assume(shift <= MAX_LPC_SHIFT);
        let model = Lpc::new(c.to_vec(), MAX_LPC_PRECISION, shift);
        let hist = [x[0], x[1], 0];
        let wide = (c[0] as i128 * x[1] as i128 + c[1] as i128 * x[0] as i128) >> shift;
        assert_eq!(model.predict_at(&hist, 2) as i128, wide);
    }

    /// The LPC decoder on any residuals and any coefficients of the allowed precision: no panic or overflow
    /// check fires (all arithmetic wraps), and the unrolled kernel restores exactly the values whose
    /// residuals the format's definition gives, through the warm-up and the first kernel position.
    #[kani::proof]
    #[kani::unwind(10)]
    fn lpc_kernel_restores_reference_residuals() {
        let x: [i64; 9] = kani::any();
        let c: [i64; 2] = kani::any();
        for &v in &c {
            kani::assume(v >= -(1i64 << 14) && v < (1i64 << 14));
        }
        let shift: u32 = kani::any();
        kani::assume(shift <= MAX_LPC_SHIFT);
        let model = Lpc::new(c.to_vec(), MAX_LPC_PRECISION, shift);
        let mut r = [0i64; 9];
        for i in 0..9 {
            r[i] = x[i].wrapping_sub(model.predict_at(&x, i));
        }
        model.restore(&mut r);
        assert_eq!(r, x);
    }

    /// Every coefficient a writer can store (a `precision`-bit two's-complement integer) reads back as
    /// itself, for every precision the format allows.
    #[kani::proof]
    fn coefficient_sign_extension_roundtrips() {
        let precision: u32 = kani::any();
        kani::assume((1..=MAX_LPC_PRECISION).contains(&precision));
        let c: i64 = kani::any();
        kani::assume(-(1i64 << (precision - 1)) <= c && c < (1i64 << (precision - 1)));
        let stored = (c as u64) & ((1u64 << precision) - 1);
        assert_eq!(sign_extend(stored, precision), c);
        // and any stored bits give a value in range
        let bits: u64 = kani::any();
        let v = sign_extend(bits & ((1u64 << precision) - 1), precision);
        assert!(-(1i64 << (precision - 1)) <= v && v < (1i64 << (precision - 1)));
    }

    /// Decoding adds back exactly the LPC prediction the encoder subtracted, for any history, any
    /// coefficients of the allowed precision and any shift (wrapping on both sides), order 2, at the
    /// warm-up and the steady-state positions.
    #[kani::proof]
    fn lpc_residuals_invert() {
        let x: [i64; 3] = kani::any();
        let c: [i64; 2] = kani::any();
        for &v in &c {
            kani::assume(-(1i64 << 14) <= v && v < (1i64 << 14));
        }
        let shift: u32 = kani::any();
        kani::assume(shift <= MAX_LPC_SHIFT);
        let model = Lpc::new(c.to_vec(), MAX_LPC_PRECISION, shift);
        let i: usize = kani::any();
        kani::assume(i < 3);
        let residual = zigzag(x[i].wrapping_sub(model.predict_at(&x, i)));
        assert_eq!(
            unzigzag(residual).wrapping_add(model.predict_at(&x[..i], i)),
            x[i]
        );
    }
}
