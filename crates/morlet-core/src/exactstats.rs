//! Exact merging of interval statistics (#219): the same count, min, max, mean and RMS as
//! [`IntervalStats`](crate::intervalstats::IntervalStats), for any number of pieces, independent of the
//! order and grouping in which the pieces are merged.
//!
//! Each piece (a stored chunk's summary, or the part of a chunk inside an interval) is first accumulated
//! with `intervalstats`' compensated sums. Pieces are then merged here in exact arithmetic: the piece's
//! sum and its compensation, and the exact value of its sum of squares (`scale² · (ssq + compensation)`),
//! are added into variable-length binary fixed-point accumulators, which lose nothing. The result is
//! rounded once, in [`ExactStats::finish`]. Because exact addition is associative and commutative, merging
//! chunk pieces one by one, or merging stored roll-ups of hours, days and months of them, gives bitwise the
//! same result.
//!
//! Numerical semantics of [`ExactStats::finish`] (float64):
//! - `count` is exact; `min` and `max` are exact sample values (−0 is smaller than +0).
//! - `mean` is `round(S) / count`, where S is the exact sum of the pieces' compensated sums. For a single
//!   piece this equals `intervalstats`' mean bit for bit.
//! - `rms` is `sqrt(round(Q / 4ᵏ) / count) · 2ᵏ`, where Q is the exact sum of the pieces' sums of squares
//!   and 4ᵏ brings Q near 1, so it neither overflows nor underflows.
//! - NaN and infinities follow `intervalstats`: any NaN sample makes min, max, mean and rms NaN; an infinite
//!   sample makes rms infinite and mean infinite (NaN when both signs occur).
//!
//! Stored form ([`ExactStats::to_bytes`], version 2): used for roll-ups, alongside the 66-byte version-1
//! chunk summaries, which [`ExactStats::from_summary`] reads.

use crate::intervalstats::IntervalStats;

/// Version byte of a stored roll-up.
pub const ROLLUP_VERSION: u8 = 2;

/// Bit weight of bit 0 of an accumulator: values are integers times 2^-OFFSET. Products of three float64
/// values reach down to 2^-3222 and sums up to below 2^2130, so 86 limbs of 64 bits hold every value.
const OFFSET: i32 = 3264;
const LIMBS: usize = 86;

/// An exact nonnegative binary fixed-point number: `limbs[i] · 2^(64·(base + i) − OFFSET)`.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
struct Mag {
    base: usize,
    limbs: Vec<u64>,
}

impl Mag {
    fn is_zero(&self) -> bool {
        self.limbs.iter().all(|&l| l == 0)
    }

    /// Makes limbs `lo..hi` (absolute indices) addressable.
    fn reserve(&mut self, lo: usize, hi: usize) {
        if self.limbs.is_empty() {
            self.base = lo;
            self.limbs = vec![0; hi - lo];
            return;
        }
        if lo < self.base {
            let mut v = vec![0; self.base - lo];
            v.extend_from_slice(&self.limbs);
            self.limbs = v;
            self.base = lo;
        }
        let end = self.base + self.limbs.len();
        if hi > end {
            self.limbs.resize(hi - self.base, 0);
        }
    }

    /// Adds `value · 2^bit` (value given as little-endian limbs), with carry.
    fn add_at(&mut self, value: &[u64], bit: usize) {
        let (limb, shift) = (bit / 64, bit % 64);
        let mut words = [0u64; 5];
        for (i, &v) in value.iter().enumerate() {
            words[i] |= v << shift;
            if shift > 0 {
                words[i + 1] |= v >> (64 - shift);
            }
        }
        let n = value.len() + 1;
        self.reserve(limb, limb + n + 1);
        let mut carry = 0u64;
        let mut i = limb - self.base;
        for &w in &words[..n] {
            let (s1, c1) = self.limbs[i].overflowing_add(w);
            let (s2, c2) = s1.overflowing_add(carry);
            self.limbs[i] = s2;
            carry = (c1 as u64) + (c2 as u64);
            i += 1;
        }
        while carry > 0 {
            if i == self.limbs.len() {
                self.limbs.push(0);
            }
            let (s, c) = self.limbs[i].overflowing_add(carry);
            self.limbs[i] = s;
            carry = c as u64;
            i += 1;
        }
    }

    fn add(&mut self, other: &Mag) {
        if other.limbs.is_empty() {
            return;
        }
        self.reserve(other.base, other.base + other.limbs.len() + 1);
        let mut carry = 0u64;
        let mut i = other.base - self.base;
        for &w in &other.limbs {
            let (s1, c1) = self.limbs[i].overflowing_add(w);
            let (s2, c2) = s1.overflowing_add(carry);
            self.limbs[i] = s2;
            carry = (c1 as u64) + (c2 as u64);
            i += 1;
        }
        while carry > 0 {
            if i == self.limbs.len() {
                self.limbs.push(0);
            }
            let (s, c) = self.limbs[i].overflowing_add(carry);
            self.limbs[i] = s;
            carry = c as u64;
            i += 1;
        }
    }

    /// Limb at absolute index `k` (0 outside the stored range).
    fn limb(&self, k: usize) -> u64 {
        if k < self.base {
            0
        } else {
            self.limbs.get(k - self.base).copied().unwrap_or(0)
        }
    }

    fn top(&self) -> usize {
        self.base + self.limbs.len()
    }

    fn cmp(&self, other: &Mag) -> std::cmp::Ordering {
        let hi = self.top().max(other.top());
        for k in (0..hi).rev() {
            match self.limb(k).cmp(&other.limb(k)) {
                std::cmp::Ordering::Equal => continue,
                o => return o,
            }
        }
        std::cmp::Ordering::Equal
    }

    /// `self − other`, for `self ≥ other`.
    fn sub(&self, other: &Mag) -> Mag {
        let lo = if other.limbs.is_empty() {
            self.base
        } else {
            self.base.min(other.base)
        };
        let hi = self.top();
        let mut out = Vec::with_capacity(hi - lo);
        let mut borrow = 0u64;
        for k in lo..hi {
            let (d1, b1) = self.limb(k).overflowing_sub(other.limb(k));
            let (d2, b2) = d1.overflowing_sub(borrow);
            out.push(d2);
            borrow = (b1 as u64) + (b2 as u64);
        }
        debug_assert_eq!(borrow, 0);
        Mag {
            base: lo,
            limbs: out,
        }
        .trimmed()
    }

    fn trimmed(mut self) -> Mag {
        while self.limbs.last() == Some(&0) {
            self.limbs.pop();
        }
        let lead = self.limbs.iter().take_while(|&&l| l == 0).count();
        if lead == self.limbs.len() {
            return Mag::default();
        }
        self.limbs.drain(..lead);
        self.base += lead;
        self
    }

    /// Index of the highest set bit (absolute bit position), if any.
    fn top_bit(&self) -> Option<usize> {
        for (i, &l) in self.limbs.iter().enumerate().rev() {
            if l != 0 {
                return Some(64 * (self.base + i) + 63 - l.leading_zeros() as usize);
            }
        }
        None
    }

    fn bit(&self, b: usize) -> bool {
        (self.limb(b / 64) >> (b % 64)) & 1 == 1
    }

    /// Whether any bit below `b` is set.
    fn any_below(&self, b: usize) -> bool {
        let (limb, shift) = (b / 64, b % 64);
        if limb >= self.base && shift > 0 && self.limb(limb) & ((1u64 << shift) - 1) != 0 {
            return true;
        }
        (self.base..limb.min(self.top())).any(|k| self.limb(k) != 0)
    }

    /// Bits `b ..= b + width − 1` as an integer (width ≤ 64).
    fn bits(&self, b: usize, width: usize) -> u64 {
        let mut v = 0u64;
        for i in 0..width {
            if self.bit(b + i) {
                v |= 1 << i;
            }
        }
        v
    }

    /// `round(value · 2^-shift)` to float64, round to nearest even, with subnormals and overflow to ∞.
    fn to_f64(&self, shift: i32) -> f64 {
        let Some(t) = self.top_bit() else { return 0.0 };
        // value = 1.xxx · 2^e
        let e = t as i64 - OFFSET as i64 - shift as i64;
        if e > 1023 {
            return f64::INFINITY;
        }
        // significant bits kept: 53 for normals, fewer for subnormals
        let p = if e >= -1022 { 53 } else { 53 - (-1022 - e) };
        if p <= 0 {
            // below half the smallest subnormal unless exactly at the boundary
            if p == 0 {
                // value in [2^-1075, 2^-1074): rounds to 2^-1074 if above half, ties to even (0)
                let above_half = self.any_below(t);
                return if above_half { f64::from_bits(1) } else { 0.0 };
            }
            return 0.0;
        }
        let p = p as usize;
        let low = t + 1 - p; // lowest kept bit
        let mut m = self.bits(low, p);
        let round = low > 0 && self.bit(low - 1);
        let sticky = low > 1 && self.any_below(low - 1);
        if round && (sticky || m & 1 == 1) {
            m += 1;
        }
        if e >= -1022 {
            let mut e = e;
            if m == 1 << 53 {
                m >>= 1;
                e += 1;
                if e > 1023 {
                    return f64::INFINITY;
                }
            }
            f64::from_bits((((e + 1023) as u64) << 52) | (m & ((1 << 52) - 1)))
        } else {
            // subnormal: the mantissa field is m (a carry into 2^52 gives the smallest normal)
            f64::from_bits(m)
        }
    }

    fn write(&self, out: &mut Vec<u8>) {
        let m = self.clone().trimmed();
        out.push(m.base as u8);
        out.push(m.limbs.len() as u8);
        for l in &m.limbs {
            out.extend_from_slice(&l.to_le_bytes());
        }
    }

    fn read(bytes: &[u8], at: &mut usize) -> Option<Mag> {
        let (base, len) = (*bytes.get(*at)? as usize, *bytes.get(*at + 1)? as usize);
        *at += 2;
        if base + len > LIMBS {
            return None;
        }
        let mut limbs = Vec::with_capacity(len);
        for _ in 0..len {
            limbs.push(u64::from_le_bytes(
                bytes.get(*at..*at + 8)?.try_into().ok()?,
            ));
            *at += 8;
        }
        let m = Mag { base, limbs };
        // canonical form only: no leading or trailing zero limbs
        if m.limbs.first() == Some(&0) || m.limbs.last() == Some(&0) {
            return None;
        }
        Some(m)
    }
}

/// An exact signed sum: positive and negative terms are kept apart and subtracted when read.
#[derive(Clone, Debug, Default)]
struct Exact {
    pos: Mag,
    neg: Mag,
}

/// `(mantissa, exponent)` with value = mantissa · 2^exponent, for a finite nonzero float64.
fn decompose(v: f64) -> (u64, i32) {
    let bits = v.to_bits();
    let exp = ((bits >> 52) & 0x7ff) as i32;
    let frac = bits & ((1 << 52) - 1);
    if exp == 0 {
        (frac, -1074)
    } else {
        (frac | (1 << 52), exp - 1075)
    }
}

impl Exact {
    /// Adds the float64 `v` exactly (finite).
    fn add_f64(&mut self, v: f64) {
        if v == 0.0 {
            return;
        }
        let (m, e) = decompose(v);
        let target = if v < 0.0 {
            &mut self.neg
        } else {
            &mut self.pos
        };
        target.add_at(&[m], (e + OFFSET) as usize);
    }

    /// Adds `a · a · b` exactly (finite).
    fn add_square_times(&mut self, a: f64, b: f64) {
        if a == 0.0 || b == 0.0 {
            return;
        }
        let ((ma, ea), (mb, eb)) = (decompose(a), decompose(b));
        let sq = (ma as u128) * (ma as u128); // < 2^106
        let lo = (sq as u64 as u128) * (mb as u128); // < 2^117
        let hi = (sq >> 64) * (mb as u128); // < 2^95
        let w0 = lo as u64;
        let mid = (lo >> 64) + (hi as u64 as u128);
        let w1 = mid as u64;
        let w2 = ((mid >> 64) + (hi >> 64)) as u64;
        let target = if b < 0.0 {
            &mut self.neg
        } else {
            &mut self.pos
        };
        target.add_at(&[w0, w1, w2], (2 * ea + eb + OFFSET) as usize);
    }

    fn add(&mut self, other: &Exact) {
        self.pos.add(&other.pos);
        self.neg.add(&other.neg);
    }

    /// (negative, magnitude) of the value.
    fn signed(&self) -> (bool, Mag) {
        match self.pos.cmp(&self.neg) {
            std::cmp::Ordering::Less => (true, self.neg.sub(&self.pos)),
            _ => (false, self.pos.sub(&self.neg)),
        }
    }

    fn write(&self, out: &mut Vec<u8>) {
        let (neg, mag) = self.signed();
        out.push(neg as u8);
        mag.write(out);
    }

    fn read(bytes: &[u8], at: &mut usize) -> Option<Exact> {
        let neg = *bytes.get(*at)?;
        *at += 1;
        let mag = Mag::read(bytes, at)?;
        match neg {
            0 => Some(Exact {
                pos: mag,
                neg: Mag::default(),
            }),
            1 if !mag.is_zero() => Some(Exact {
                pos: Mag::default(),
                neg: mag,
            }),
            _ => None,
        }
    }
}

fn min0(a: f64, b: f64) -> f64 {
    if a < b || (a == b && a.is_sign_negative()) {
        a
    } else {
        b
    }
}

fn max0(a: f64, b: f64) -> f64 {
    if a > b || (a == b && a.is_sign_positive()) {
        a
    } else {
        b
    }
}

/// Statistics of any number of pieces, merged exactly; see the module documentation.
#[derive(Clone, Debug)]
pub struct ExactStats {
    n: u64,
    nan: bool,
    pos_inf: bool,
    neg_inf: bool,
    sq_inf: bool,
    lo: f64,
    hi: f64,
    sum: Exact,
    sq: Exact,
}

impl Default for ExactStats {
    fn default() -> Self {
        ExactStats {
            n: 0,
            nan: false,
            pos_inf: false,
            neg_inf: false,
            sq_inf: false,
            lo: f64::INFINITY,
            hi: f64::NEG_INFINITY,
            sum: Exact::default(),
            sq: Exact::default(),
        }
    }
}

impl ExactStats {
    /// Adds one piece accumulated with `intervalstats`.
    pub fn add_piece(&mut self, p: &IntervalStats) {
        let r = p.raw();
        if r.n == 0 {
            return;
        }
        self.n += r.n;
        self.nan |= r.nan;
        self.lo = min0(self.lo, r.lo);
        self.hi = max0(self.hi, r.hi);
        if r.sum.is_nan() {
            self.pos_inf = true;
            self.neg_inf = true;
        } else if r.sum.is_infinite() || !r.sum_c.is_finite() {
            if r.sum > 0.0 || r.sum_c > 0.0 {
                self.pos_inf = true;
            } else {
                self.neg_inf = true;
            }
        } else {
            self.sum.add_f64(r.sum);
            self.sum.add_f64(r.sum_c);
        }
        if !r.scale.is_finite() {
            self.sq_inf = true;
        } else if r.scale > 0.0 {
            if r.ssq.is_finite() && r.ssq_c.is_finite() {
                self.sq.add_square_times(r.scale, r.ssq);
                self.sq.add_square_times(r.scale, r.ssq_c);
            } else {
                self.sq_inf = true;
            }
        }
    }

    /// A stored chunk summary (version 1) or roll-up (version 2) as exact statistics.
    pub fn from_summary(bytes: &[u8]) -> Option<ExactStats> {
        match bytes.first() {
            Some(1) => {
                let mut s = ExactStats::default();
                s.add_piece(&IntervalStats::from_summary(bytes)?);
                Some(s)
            }
            Some(&ROLLUP_VERSION) => ExactStats::from_bytes(bytes),
            _ => None,
        }
    }

    /// Adds the pieces of `other`; the result does not depend on the order or grouping of merges.
    pub fn merge(&mut self, other: &ExactStats) {
        if other.n == 0 {
            return;
        }
        self.n += other.n;
        self.nan |= other.nan;
        self.pos_inf |= other.pos_inf;
        self.neg_inf |= other.neg_inf;
        self.sq_inf |= other.sq_inf;
        self.lo = min0(self.lo, other.lo);
        self.hi = max0(self.hi, other.hi);
        self.sum.add(&other.sum);
        self.sq.add(&other.sq);
    }

    /// Number of samples.
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
        let mean = match (self.pos_inf, self.neg_inf) {
            (true, true) => f64::NAN,
            (true, false) => f64::INFINITY,
            (false, true) => f64::NEG_INFINITY,
            _ => {
                let (neg, mag) = self.sum.signed();
                let s = mag.to_f64(0);
                (if neg { -s } else { s }) / n
            }
        };
        let rms = if self.sq_inf {
            f64::INFINITY
        } else {
            let (neg, mag) = self.sq.signed();
            match mag.top_bit() {
                Some(t) if !neg => {
                    // Q = q · 4^k with q in [1, 4)
                    let k = (t as i32 - OFFSET).div_euclid(2);
                    let q = mag.to_f64(2 * k);
                    ldexp((q / n).sqrt(), k)
                }
                _ => 0.0,
            }
        };
        [n, self.lo, self.hi, mean, rms]
    }

    /// Stored form, version 2 (little-endian): `u8 2`, `u64 count`, `u8 flags` (NaN, +∞ and −∞ in the sum, ∞ in
    /// the squares), `f64` min and max, then the exact sum and sum of squares, each as a sign byte, the base
    /// limb, the limb count and the 64-bit limbs.
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = Vec::with_capacity(48);
        out.push(ROLLUP_VERSION);
        out.extend_from_slice(&self.n.to_le_bytes());
        out.push(
            self.nan as u8
                | (self.pos_inf as u8) << 1
                | (self.neg_inf as u8) << 2
                | (self.sq_inf as u8) << 3,
        );
        out.extend_from_slice(&self.lo.to_le_bytes());
        out.extend_from_slice(&self.hi.to_le_bytes());
        self.sum.write(&mut out);
        self.sq.write(&mut out);
        out
    }

    /// Parses [`ExactStats::to_bytes`]; `None` for anything else.
    pub fn from_bytes(bytes: &[u8]) -> Option<ExactStats> {
        if bytes.len() < 26 || bytes[0] != ROLLUP_VERSION || bytes[9] > 15 {
            return None;
        }
        let f = |a: usize| f64::from_le_bytes(bytes[a..a + 8].try_into().unwrap());
        let mut at = 26;
        let sum = Exact::read(bytes, &mut at)?;
        let sq = Exact::read(bytes, &mut at)?;
        if at != bytes.len() {
            return None;
        }
        let fl = bytes[9];
        Some(ExactStats {
            n: u64::from_le_bytes(bytes[1..9].try_into().unwrap()),
            nan: fl & 1 != 0,
            pos_inf: fl & 2 != 0,
            neg_inf: fl & 4 != 0,
            sq_inf: fl & 8 != 0,
            lo: f(10),
            hi: f(18),
            sum,
            sq,
        })
    }
}

/// `x · 2^k` for a positive normal `x` with 2^-32 ≤ x < 2, |k| ≤ 1700: exact steps keep the value normal, so
/// only the last multiplication rounds (when the result is subnormal) or overflows.
/// An exact sum of float64 terms that may be infinite or NaN: finite terms are added exactly, non-finite ones
/// only set flags (the IEEE result of adding them all).
#[derive(Clone, Debug, Default)]
struct FlaggedSum {
    exact: Exact,
    nan: bool,
    pos_inf: bool,
    neg_inf: bool,
}

impl FlaggedSum {
    fn flag(&mut self, v: f64) {
        if v.is_nan() {
            self.nan = true;
        } else if v > 0.0 {
            self.pos_inf = true;
        } else {
            self.neg_inf = true;
        }
    }

    fn add(&mut self, v: f64) {
        if v.is_finite() {
            self.exact.add_f64(v);
        } else {
            self.flag(v);
        }
    }

    /// Adds `a · b` exactly: the product of the two 53-bit significands is an exact 106-bit integer.
    fn add_product(&mut self, a: f64, b: f64) {
        if !(a.is_finite() && b.is_finite()) {
            let p = a * b; // IEEE: inf·0 is NaN, inf·x is ±inf, NaN stays NaN
            if p.is_finite() {
                return; // cannot happen for a non-finite factor, kept for clarity
            }
            self.flag(p);
            return;
        }
        if a == 0.0 || b == 0.0 {
            return;
        }
        let ((ma, ea), (mb, eb)) = (decompose(a), decompose(b));
        let p = (ma as u128) * (mb as u128); // < 2^106
        let target = if (a < 0.0) != (b < 0.0) {
            &mut self.exact.neg
        } else {
            &mut self.exact.pos
        };
        target.add_at(&[p as u64, (p >> 64) as u64], (ea + eb + OFFSET) as usize);
    }

    fn merge(&mut self, o: &FlaggedSum) {
        self.exact.add(&o.exact);
        self.nan |= o.nan;
        self.pos_inf |= o.pos_inf;
        self.neg_inf |= o.neg_inf;
    }

    /// `round(S) / n`, with the IEEE result for non-finite terms.
    fn mean(&self, n: f64) -> f64 {
        if self.nan || (self.pos_inf && self.neg_inf) {
            return f64::NAN;
        }
        if self.pos_inf {
            return f64::INFINITY;
        }
        if self.neg_inf {
            return f64::NEG_INFINITY;
        }
        let (neg, mag) = self.exact.signed();
        let s = mag.to_f64(0);
        (if neg { -s } else { s }) / n
    }

    /// `sqrt(round(Q / 4^k) / n) · 2^k` for a sum of squares Q, as in [`ExactStats::finish`].
    fn rms(&self, n: f64) -> f64 {
        if self.nan {
            return f64::NAN;
        }
        if self.pos_inf {
            return f64::INFINITY;
        }
        let (neg, mag) = self.exact.signed();
        match mag.top_bit() {
            Some(t) if !neg => {
                let k = (t as i32 - OFFSET).div_euclid(2);
                ldexp((mag.to_f64(2 * k) / n).sqrt(), k)
            }
            _ => 0.0,
        }
    }
}

/// Exact statistics of two channels sampled at the same instants (#292): count, mean(a·b), and the mean and
/// RMS of each channel. With a = voltage and b = current, mean(a·b) is the real power.
///
/// Every sum is exact: each sample and each product (as a 106-bit integer, no rounding) is added into the
/// fixed-point accumulators of [`ExactStats`], and each result is rounded once in [`CrossStats::finish`]. So the
/// result is bitwise the same however the interval is split into pieces and in whatever order they are merged.
/// Means are `round(S) / count`; RMS as in [`ExactStats::finish`]. A NaN or infinite sample gives the IEEE result
/// of the exact sums (for example ∞·0 makes mean(a·b) NaN).
#[derive(Clone, Debug, Default)]
pub struct CrossStats {
    n: u64,
    a: FlaggedSum,
    b: FlaggedSum,
    aa: FlaggedSum,
    bb: FlaggedSum,
    ab: FlaggedSum,
}

impl CrossStats {
    /// Adds samples `a[i]`, `b[i]` taken at the same instants; the slices must have equal lengths.
    pub fn add(&mut self, a: &[f64], b: &[f64]) -> Result<(), crate::SignalError> {
        if a.len() != b.len() {
            return Err(crate::SignalError::LengthMismatch);
        }
        for (&x, &y) in a.iter().zip(b) {
            self.a.add(x);
            self.b.add(y);
            self.aa.add_product(x, x);
            self.bb.add_product(y, y);
            self.ab.add_product(x, y);
        }
        self.n += a.len() as u64;
        Ok(())
    }

    /// Adds the samples of `other`; the result does not depend on the order or grouping of merges.
    pub fn merge(&mut self, other: &CrossStats) {
        self.n += other.n;
        self.a.merge(&other.a);
        self.b.merge(&other.b);
        self.aa.merge(&other.aa);
        self.bb.merge(&other.bb);
        self.ab.merge(&other.ab);
    }

    /// Number of sample pairs.
    pub fn count(&self) -> u64 {
        self.n
    }

    /// Stored form (aggregate state): `u8 1`, `u64 count`, then for a, b, a·a, b·b and a·b a flags byte (NaN, +∞,
    /// −∞) and the exact sum as in [`ExactStats::to_bytes`].
    pub fn to_bytes(&self) -> Vec<u8> {
        let mut out = vec![1u8];
        out.extend_from_slice(&self.n.to_le_bytes());
        for f in [&self.a, &self.b, &self.aa, &self.bb, &self.ab] {
            out.push(f.nan as u8 | (f.pos_inf as u8) << 1 | (f.neg_inf as u8) << 2);
            f.exact.write(&mut out);
        }
        out
    }

    /// Reads [`CrossStats::to_bytes`]; None for anything else.
    pub fn from_bytes(bytes: &[u8]) -> Option<CrossStats> {
        if bytes.first() != Some(&1) {
            return None;
        }
        let n = u64::from_le_bytes(bytes.get(1..9)?.try_into().ok()?);
        let mut at = 9;
        let mut sums = Vec::with_capacity(5);
        for _ in 0..5 {
            let flags = *bytes.get(at)?;
            if flags > 7 {
                return None;
            }
            at += 1;
            sums.push(FlaggedSum {
                exact: Exact::read(bytes, &mut at)?,
                nan: flags & 1 != 0,
                pos_inf: flags & 2 != 0,
                neg_inf: flags & 4 != 0,
            });
        }
        if at != bytes.len() {
            return None;
        }
        let mut it = sums.into_iter();
        Some(CrossStats {
            n,
            a: it.next()?,
            b: it.next()?,
            aa: it.next()?,
            bb: it.next()?,
            ab: it.next()?,
        })
    }

    /// `[count, mean(a·b), mean(a), mean(b), rms(a), rms(b)]`; NaN statistics for no samples.
    pub fn finish(&self) -> [f64; 6] {
        if self.n == 0 {
            return [0.0, f64::NAN, f64::NAN, f64::NAN, f64::NAN, f64::NAN];
        }
        let n = self.n as f64;
        [
            n,
            self.ab.mean(n),
            self.a.mean(n),
            self.b.mean(n),
            self.aa.rms(n),
            self.bb.rms(n),
        ]
    }
}

fn ldexp(x: f64, k: i32) -> f64 {
    let (mut x, mut k) = (x, k);
    while k > 900 {
        x *= 2f64.powi(900);
        k -= 900;
    }
    while k < -900 {
        x *= 2f64.powi(-900);
        k += 900;
    }
    x * 2f64.powi(k)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn bits6(x: [f64; 6]) -> [u64; 6] {
        x.map(f64::to_bits)
    }

    fn xlcg(seed: &mut u64) -> u64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *seed >> 11
    }

    /// Values over a wide exponent range, both signs, with exact zeros.
    fn wide(seed: &mut u64, n: usize) -> Vec<f64> {
        (0..n)
            .map(|_| {
                let r = xlcg(seed);
                if r.is_multiple_of(17) {
                    return 0.0;
                }
                let m = (r % (1 << 20)) as f64 / (1 << 20) as f64 + 0.5;
                let e = (xlcg(seed) % 200) as i32 - 100;
                let v = ldexp(m, e);
                if xlcg(seed).is_multiple_of(2) {
                    -v
                } else {
                    v
                }
            })
            .collect()
    }

    #[test]
    fn cross_stats_do_not_depend_on_splits_or_merge_order() {
        let mut seed = 11u64;
        let (a, b) = (wide(&mut seed, 5000), wide(&mut seed, 5000));
        let mut whole = CrossStats::default();
        whole.add(&a, &b).unwrap();
        let want = bits6(whole.finish());
        for trial in 0..20 {
            // random cut points, then merge the pieces in a shuffled order
            let mut cuts: Vec<usize> = (0..trial % 7)
                .map(|_| (xlcg(&mut seed) % 5001) as usize)
                .collect();
            cuts.extend([0, 5000]);
            cuts.sort_unstable();
            let mut pieces: Vec<CrossStats> = cuts
                .windows(2)
                .map(|w| {
                    let mut c = CrossStats::default();
                    c.add(&a[w[0]..w[1]], &b[w[0]..w[1]]).unwrap();
                    c
                })
                .collect();
            for k in (1..pieces.len()).rev() {
                pieces.swap(k, (xlcg(&mut seed) % (k as u64 + 1)) as usize);
            }
            let mut merged = CrossStats::default();
            for p in &pieces {
                merged.merge(p);
            }
            assert_eq!(bits6(merged.finish()), want, "trial {trial}");
            assert_eq!(merged.count(), 5000);
        }
    }

    #[test]
    fn cross_stats_match_an_exact_integer_reference() {
        // Values k·2^-20 with |k| < 2^30: every sum and product is an integer times 2^-40 that fits in i128.
        let mut seed = 5u64;
        let k: Vec<i64> = (0..3000)
            .map(|_| (xlcg(&mut seed) % (1 << 31)) as i64 - (1 << 30))
            .collect();
        let j: Vec<i64> = (0..3000)
            .map(|_| (xlcg(&mut seed) % (1 << 31)) as i64 - (1 << 30))
            .collect();
        let s = ldexp(1.0, -20);
        let a: Vec<f64> = k.iter().map(|&v| v as f64 * s).collect();
        let b: Vec<f64> = j.iter().map(|&v| v as f64 * s).collect();
        let mut c = CrossStats::default();
        c.add(&a, &b).unwrap();
        let [n, ab, ma, mb, ra, rb] = c.finish();
        let sab: i128 = k.iter().zip(&j).map(|(&x, &y)| x as i128 * y as i128).sum();
        let sa: i128 = k.iter().map(|&x| x as i128).sum();
        let sb: i128 = j.iter().map(|&y| y as i128).sum();
        let saa: i128 = k.iter().map(|&x| x as i128 * x as i128).sum();
        let sbb: i128 = j.iter().map(|&y| y as i128 * y as i128).sum();
        // i128 -> f64 rounds to nearest even: round(S); powers of two scale exactly
        assert_eq!(n, 3000.0);
        assert_eq!(ab.to_bits(), (ldexp(sab as f64, -40) / n).to_bits());
        assert_eq!(ma.to_bits(), (ldexp(sa as f64, -20) / n).to_bits());
        assert_eq!(mb.to_bits(), (ldexp(sb as f64, -20) / n).to_bits());
        // rms: sqrt(round(Q/4^k)/n)·2^k; Q = saa·2^-40, scaled by a power of four, rounds like saa itself
        let rms = |q: i128| {
            let t = 127 - q.leading_zeros() as i32 - 40; // top bit of Q
            let e = t.div_euclid(2);
            ldexp((ldexp(q as f64, -40 - 2 * e) / n).sqrt(), e)
        };
        assert_eq!(ra.to_bits(), rms(saa).to_bits());
        assert_eq!(rb.to_bits(), rms(sbb).to_bits());
    }

    #[test]
    fn cross_stats_are_exact_where_float_sums_cancel() {
        let big = ldexp(1.0, 53);
        let mut c = CrossStats::default();
        c.add(&[big, 1.0, -big], &[1.0, 1.0, 1.0]).unwrap();
        let [_, ab, ma, ..] = c.finish();
        assert_eq!(ab, 1.0 / 3.0); // a float sum gives (2^53 + 1) - 2^53 = 0
        assert_eq!(ma, 1.0 / 3.0);
        // products far outside float64 cancel exactly; the survivor is rounded once
        let mut c = CrossStats::default();
        c.add(&[1e300, -1e300, 1e-300], &[1e-300, 1e-300, 1e300])
            .unwrap();
        assert_eq!(c.finish()[1], (1e-300 * 1e300) / 3.0);
        let mut c = CrossStats::default();
        c.add(&[1e-300, 1e-300], &[1e-300, -1e-300]).unwrap();
        assert_eq!(c.finish()[1].to_bits(), 0f64.to_bits()); // 1e-600 - 1e-600: exactly zero
                                                             // subnormal samples: the mean is exact, the product underflows to zero
        let tiny = f64::from_bits(1);
        let mut c = CrossStats::default();
        c.add(&[tiny; 4], &[tiny; 4]).unwrap();
        let [_, ab, ma, _, ra, _] = c.finish();
        assert_eq!((ab, ma, ra), (0.0, tiny, tiny));
    }

    #[test]
    fn cross_stats_survive_their_stored_form() {
        let mut seed = 3u64;
        let (a, b) = (wide(&mut seed, 700), wide(&mut seed, 700));
        let mut c = CrossStats::default();
        c.add(&a, &b).unwrap();
        c.add(&[f64::INFINITY], &[0.0]).unwrap();
        let back = CrossStats::from_bytes(&c.to_bytes()).unwrap();
        assert_eq!(bits6(back.finish()).map(|x| x), bits6(c.finish()));
        assert_eq!(back.to_bytes(), c.to_bytes());
        let empty = CrossStats::default();
        assert_eq!(
            CrossStats::from_bytes(&empty.to_bytes()).unwrap().count(),
            0
        );
        let mut bad = c.to_bytes();
        bad.push(0);
        assert!(CrossStats::from_bytes(&bad).is_none());
        assert!(CrossStats::from_bytes(&[2]).is_none());
    }

    #[test]
    fn cross_stats_follow_ieee_for_non_finite_samples() {
        let f = |a: &[f64], b: &[f64]| {
            let mut c = CrossStats::default();
            c.add(a, b).unwrap();
            c.finish()
        };
        let r = f(&[f64::INFINITY, 1.0], &[0.0, 1.0]);
        assert!(r[1].is_nan()); // inf·0
        assert_eq!((r[2], r[4]), (f64::INFINITY, f64::INFINITY));
        let r = f(&[f64::INFINITY], &[-2.0]);
        assert_eq!(r[1], f64::NEG_INFINITY);
        let r = f(&[f64::NAN, 1.0], &[1.0, 1.0]);
        assert!(r[1].is_nan() && r[2].is_nan() && r[4].is_nan());
        assert_eq!((r[3], r[5]), (1.0, 1.0));
        assert!(CrossStats::default().add(&[1.0], &[]).is_err());
        assert_eq!(CrossStats::default().finish()[0], 0.0);
    }

    fn lcg(seed: &mut u64) -> f64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        ((*seed >> 11) as f64 / (1u64 << 53) as f64) * 2.0 - 1.0
    }

    fn piece(x: &[f64]) -> ExactStats {
        let mut p = IntervalStats::default();
        p.add(x);
        let mut s = ExactStats::default();
        s.add_piece(&p);
        s
    }

    fn bits(r: [f64; 5]) -> [u64; 5] {
        r.map(f64::to_bits)
    }

    /// Merging chunk pieces one by one, in any order, or through roll-ups of any shape (stored and read
    /// back) gives bitwise the same statistics.
    #[test]
    fn order_and_grouping_do_not_change_a_bit() {
        let mut seed = 11;
        let x: Vec<f64> = (0..300_000)
            .map(|i| {
                ((i as f64) * 0.0021).sin() * 1e3
                    + 7.0
                    + lcg(&mut seed) * (1.0 + (i / 50_000) as f64 * 1e4)
            })
            .collect();
        let chunks: Vec<ExactStats> = x.chunks(4093).map(piece).collect();
        let mut one_by_one = ExactStats::default();
        for c in &chunks {
            one_by_one.merge(c);
        }
        let want = bits(one_by_one.finish());
        let mut reversed = ExactStats::default();
        for c in chunks.iter().rev() {
            reversed.merge(c);
        }
        assert_eq!(bits(reversed.finish()), want);
        for fan in [2usize, 3, 32] {
            let mut level: Vec<ExactStats> = chunks.clone();
            while level.len() > 1 {
                level = level
                    .chunks(fan)
                    .map(|g| {
                        let mut r = ExactStats::default();
                        for s in g {
                            r.merge(s);
                        }
                        ExactStats::from_bytes(&r.to_bytes()).unwrap()
                    })
                    .collect();
            }
            assert_eq!(bits(level[0].finish()), want, "fan-out {fan}");
        }
    }

    /// One piece: mean equals `intervalstats` bit for bit, rms to a unit in the last place.
    #[test]
    fn a_single_piece_agrees_with_intervalstats() {
        let mut seed = 3;
        for scale in [1e-300, 1e-5, 1.0, 1e7, 1e300] {
            let x: Vec<f64> = (0..5000)
                .map(|_| lcg(&mut seed) * scale + scale * 0.25)
                .collect();
            let mut p = IntervalStats::default();
            p.add(&x);
            let (a, b) = (p.finish(), piece(&x).finish());
            assert_eq!(
                bits([a[0], a[1], a[2], a[3], 0.0]),
                bits([b[0], b[1], b[2], b[3], 0.0])
            );
            assert!(
                (a[4] - b[4]).abs() <= 2.0 * f64::EPSILON * a[4],
                "{scale}: {} vs {}",
                a[4],
                b[4]
            );
        }
    }

    /// On a 2^-20 grid the exact sums are known: mean and rms round once from them.
    #[test]
    fn grid_signal_matches_exact_reference() {
        let mut seed = 7;
        let k: Vec<i64> = (0..200_000)
            .map(|_| (lcg(&mut seed) * 1000.0 * (1u64 << 20) as f64).round() as i64)
            .collect();
        let x: Vec<f64> = k.iter().map(|&v| v as f64 / (1u64 << 20) as f64).collect();
        let mut s = ExactStats::default();
        for c in x.chunks(32768) {
            s.merge(&piece(c));
        }
        let r = s.finish();
        let sum: i128 = k.iter().map(|&v| v as i128).sum();
        let ssq: i128 = k.iter().map(|&v| (v as i128) * (v as i128)).sum();
        // the per-chunk compensated sums are exact here, so the exact merge is the exact sum
        assert_eq!(r[3], (sum as f64 / (1u64 << 20) as f64) / 200_000.0);
        let rms = ((ssq as f64) / 200_000.0).sqrt() / (1u64 << 20) as f64;
        assert!(((r[4] - rms) / rms).abs() < 4e-16, "{} vs {rms}", r[4]);
    }

    #[test]
    fn cancellation_extremes_zeros_nan_and_infinity() {
        // +1e16, small values in another chunk, −1e16 in a third: nothing is lost across chunks
        let mut s = piece(&[1e16]);
        s.merge(&piece(&vec![1.0; 10_000]));
        s.merge(&piece(&[-1e16]));
        assert_eq!(s.finish()[3], 10_000.0 / 10_002.0);
        let big = {
            let mut s = piece(&[1e300, -1e300]);
            s.merge(&piece(&[1e300]));
            s.finish()
        };
        assert!((big[4] / 1e300 - 1.0).abs() < 1e-15);
        let mut tiny = piece(&[5e-324]);
        tiny.merge(&piece(&[-5e-324]));
        assert_eq!(tiny.finish()[4], 5e-324);
        assert_eq!(tiny.finish()[3], 0.0);
        let mut mixed = piece(&[1e-300]);
        mixed.merge(&piece(&[1e300]));
        assert!((mixed.finish()[4] / (1e300 / 2f64.sqrt()) - 1.0).abs() < 1e-15);
        // −0 is the minimum and +0 the maximum, whatever the order
        let (mut a, mut b) = (piece(&[0.0]), piece(&[-0.0]));
        let a2 = a.clone();
        a.merge(&b);
        b.merge(&a2);
        assert_eq!(bits(a.finish()), bits(b.finish()));
        assert!(a.finish()[1].is_sign_negative() && a.finish()[2].is_sign_positive());
        assert_eq!(a.finish()[4], 0.0);
        let mut n = piece(&[1.0]);
        n.merge(&piece(&[f64::NAN]));
        assert!(n.finish()[1..].iter().all(|v| v.is_nan()));
        let mut i = piece(&[1.0, f64::INFINITY]);
        i.merge(&piece(&[2.0]));
        assert_eq!(
            (i.finish()[3], i.finish()[4]),
            (f64::INFINITY, f64::INFINITY)
        );
        i.merge(&piece(&[f64::NEG_INFINITY]));
        assert!(i.finish()[3].is_nan());
        let e = ExactStats::default().finish();
        assert_eq!(e[0], 0.0);
        assert!(e[1..].iter().all(|v| v.is_nan()));
    }

    #[test]
    fn stored_form_round_trips_and_rejects_others() {
        let mut seed = 5;
        let x: Vec<f64> = (0..10_000).map(|_| lcg(&mut seed) * 1e5).collect();
        let mut s = piece(&x[..3000]);
        s.merge(&piece(&x[3000..]));
        s.merge(&piece(&[f64::INFINITY]));
        let b = s.to_bytes();
        let r = ExactStats::from_bytes(&b).unwrap();
        assert_eq!(r.to_bytes(), b);
        assert_eq!(bits(r.finish()), bits(s.finish()));
        assert!(ExactStats::from_bytes(&b[..b.len() - 1]).is_none());
        let mut bad = b.clone();
        bad[0] = 1;
        assert!(ExactStats::from_bytes(&bad).is_none());
        let mut p = IntervalStats::default();
        p.add(&x);
        let v1 = ExactStats::from_summary(&p.to_summary()).unwrap();
        assert_eq!(bits(v1.finish()), bits(piece(&x).finish()));
        assert!(ExactStats::from_summary(&[9u8; 30]).is_none());
        let empty = ExactStats::default();
        assert_eq!(
            ExactStats::from_bytes(&empty.to_bytes()).unwrap().count(),
            0
        );
    }

    /// round(a + b) of the exact accumulator equals IEEE a + b (correctly rounded) for random pairs over the
    /// whole range, subnormals and overflow included, and a − a + b gives b exactly.
    #[test]
    fn exact_sums_round_like_ieee_addition() {
        let mut seed = 99u64;
        let mut next = || {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            seed
        };
        for _ in 0..200_000 {
            let (r1, r2, r3) = (next(), next(), next());
            let a = f64::from_bits(r1 & !(0x7ffu64 << 52) | ((r3 % 2047) << 52));
            let e = ((r3 >> 16) % 2047) as i64;
            let near = ((r1 >> 52) & 0x7ff) as i64 + ((r3 >> 40) % 120) as i64 - 60;
            let eb = if r3 & 1 == 0 { e } else { near.clamp(0, 2046) } as u64;
            let b = f64::from_bits(r2 & !(0x7ffu64 << 52) | (eb << 52));
            let want = a + b;
            let mut x = Exact::default();
            x.add_f64(a);
            x.add_f64(b);
            let (neg, m) = x.signed();
            let got = if neg { -m.to_f64(0) } else { m.to_f64(0) };
            assert!(
                got == want || (got == 0.0 && want == 0.0),
                "{a:e} + {b:e}: {got:e} vs {want:e}"
            );
            let mut y = Exact::default();
            y.add_f64(a);
            y.add_f64(-a);
            y.add_f64(b);
            let (neg, m) = y.signed();
            assert_eq!(if neg { -m.to_f64(0) } else { m.to_f64(0) }, b);
        }
    }

    /// Rounding of the exact accumulator: ties to even, subnormals and overflow.
    #[test]
    fn rounding_is_correct_at_the_edges() {
        for v in [1.0, 0.1, -3.5e-310, 5e-324, f64::MAX, 1.0 + f64::EPSILON] {
            let mut e = Exact::default();
            e.add_f64(v);
            let (neg, m) = e.signed();
            let r = m.to_f64(0);
            assert_eq!(if neg { -r } else { r }, v);
        }
        // 1 + 2^-53 is a tie: rounds to 1 (even); 1 + 2^-53 + 2^-80 rounds up
        let mut e = Exact::default();
        e.add_f64(1.0);
        e.add_f64(2f64.powi(-53));
        assert_eq!(e.signed().1.to_f64(0), 1.0);
        e.add_f64(2f64.powi(-80));
        assert_eq!(e.signed().1.to_f64(0), 1.0 + f64::EPSILON);
        let mut o = Exact::default();
        o.add_f64(f64::MAX);
        o.add_f64(f64::MAX);
        assert_eq!(o.signed().1.to_f64(0), f64::INFINITY);
        let mut s = Exact::default();
        s.add_f64(5e-324);
        s.add_f64(5e-324);
        s.add_f64(5e-324);
        assert_eq!(s.signed().1.to_f64(0), 3.0 * 5e-324);
        // squares of the extremes are exact: (2^-1074)^2 · 2^-1074 and (2^1023)^2 · 2^1023
        let mut q = Exact::default();
        q.add_square_times(5e-324, 5e-324);
        assert_eq!(q.signed().1.top_bit(), Some((-3222 + OFFSET) as usize));
        let mut q = Exact::default();
        q.add_square_times(2f64.powi(1023), 32768.0);
        assert_eq!(q.signed().1.top_bit(), Some((2061 + OFFSET) as usize));
    }
}
