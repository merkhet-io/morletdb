//! The LPC prediction kernel of chunk format version 5 (and of version 6, which reuses its predictor):
//! the wrapping dot product `Σ_j h[j] · c[j]` of a history window `h` and the coefficients `c`, both of
//! `P` lanes (`P` = 8, 16, 24 or 32; the coefficients are zero-padded at the old end), followed by the
//! arithmetic right shift. Prediction for `x[i]` is `dot(x[i - P..i], c) >> shift`.
//!
//! **Semantics.** Every implementation computes the same integer: the sum and the products are taken in
//! two's-complement arithmetic modulo 2^64 (Rust's `wrapping_mul` and `wrapping_add` on `i64`), and only
//! then shifted. Addition modulo 2^64 is associative and commutative, so any grouping or lane order gives
//! the same sum; the shift is applied once, to that sum, exactly as the format defines it.
//!
//! **Kernels.** [`Isa::Scalar`] is the original kernel (four interleaved wrapping sums), the only one
//! compiled under Kani and on targets without a SIMD kernel. The SIMD kernels have no 64 × 64-bit
//! multiply (neither NEON nor SSE2/AVX2 has one), so each product is built from 32-bit halves: with `a = a_hi · 2^32 + a_lo` and
//! `c = c_hi · 2^32 + c_lo` (all halves unsigned),
//!
//! `a · c ≡ a_lo · c_lo + 2^32 · (a_lo · c_hi + a_hi · c_lo)  (mod 2^64)`,
//!
//! since the `a_hi · c_hi · 2^64` term vanishes. `a_lo · c_lo` is a full 32 × 32 → 64-bit unsigned
//! product, and of the cross terms only their sum modulo 2^32 matters (it is shifted left by 32), so the
//! cross terms are accumulated over all lanes first and shifted once. Nothing depends on the coefficients
//! being small (they are at most 15 bits in the format): the identity holds for any i64.
//!
//! - [`Isa::Neon`] (aarch64, always present): `vmlaq_u32` accumulates the cross terms in 32-bit lanes
//!   against coefficients with their halves swapped, `vmlal_u32` the low products.
//! - [`Isa::Avx2`] (x86_64, detected at run time) and [`Isa::Sse2`] (x86_64 baseline): `pmuludq` gives
//!   `a_lo · c_lo`, `a_lo · c_hi` and `a_hi · c_lo`, accumulated in 64-bit lanes.
//!
//! **Decoder.** In [`restore`] each prediction needs the values just restored. The SIMD kernels take the
//! newest four lanes (`x[i - 4..i]`) from registers and load only the older ones, so a vector load never
//! reads a value still in flight from the previous iteration's store.
use crate::zigzag;

/// Which kernel computes the dot product.
// Outside tests each target constructs only the variants `Isa::detect` returns.
#[allow(dead_code)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Isa {
    Scalar,
    #[cfg(all(target_arch = "aarch64", not(kani)))]
    Neon,
    #[cfg(all(target_arch = "x86_64", not(kani)))]
    Sse2,
    #[cfg(all(target_arch = "x86_64", not(kani)))]
    Avx2,
}

impl Isa {
    /// The fastest kernel this CPU runs; [`Isa::Scalar`] under Kani (the proofs cover the scalar kernel).
    #[inline]
    pub(crate) fn detect() -> Self {
        #[cfg(all(target_arch = "aarch64", not(kani)))]
        let isa = Isa::Neon;
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        let isa = if std::arch::is_x86_feature_detected!("avx2") {
            Isa::Avx2
        } else {
            Isa::Sse2
        };
        #[cfg(any(kani, not(any(target_arch = "aarch64", target_arch = "x86_64"))))]
        let isa = Isa::Scalar;
        isa
    }

    /// Every kernel this CPU can run (for the equivalence tests).
    #[cfg(test)]
    pub(crate) fn all_available() -> Vec<Self> {
        #[allow(unused_mut)]
        let mut v = vec![Isa::Scalar];
        #[cfg(all(target_arch = "aarch64", not(kani)))]
        v.push(Isa::Neon);
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        {
            v.push(Isa::Sse2);
            if std::arch::is_x86_feature_detected!("avx2") {
                v.push(Isa::Avx2);
            }
        }
        v
    }
}

const fn check_width<const P: usize>() {
    assert!(
        P.is_multiple_of(4) && P >= 8 && P <= 32,
        "LPC window must be 8, 16, 24 or 32"
    );
}

/// `r[i] = zigzag(x[i] - (dot(x[i - P..i], c) >> shift))` for `i` in `from..x.len()`; `from >= P`
/// unless the range is empty.
pub(crate) fn residuals<const P: usize>(
    isa: Isa,
    x: &[i64],
    c: &[i64; P],
    shift: u32,
    r: &mut [u64],
    from: usize,
) {
    const { check_width::<P>() };
    match isa {
        Isa::Scalar => residuals_with(x, r, from, shift, |h: &[i64; P]| scalar::dot(h, c)),
        // SAFETY (all three): the kernel's target feature is present. NEON is part of the aarch64
        // baseline and SSE2 of the x86_64 one; `Isa::Avx2` is only produced by `Isa::detect` and
        // `Isa::all_available` after `is_x86_feature_detected!("avx2")`.
        #[cfg(all(target_arch = "aarch64", not(kani)))]
        Isa::Neon => unsafe { neon::residuals(x, c, shift, r, from) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Sse2 => unsafe { x86::residuals_sse2(x, c, shift, r, from) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Avx2 => unsafe { x86::residuals_avx2(x, c, shift, r, from) },
    }
}

/// Inverts [`residuals`] in place: `x[i] += dot(x[i - P..i], c) >> shift` for `i` in `from..x.len()`, in
/// order; `from >= P` unless the range is empty.
pub(crate) fn restore<const P: usize>(
    isa: Isa,
    x: &mut [i64],
    c: &[i64; P],
    shift: u32,
    from: usize,
) {
    const { check_width::<P>() };
    match isa {
        Isa::Scalar => {
            for i in from..x.len() {
                let h: &[i64; P] = x[i - P..i].try_into().unwrap();
                let pred = scalar::dot(h, c) >> shift;
                x[i] = x[i].wrapping_add(pred);
            }
        }
        // SAFETY: as in `residuals`, the kernel's target feature is present.
        #[cfg(all(target_arch = "aarch64", not(kani)))]
        Isa::Neon => unsafe { neon::restore(x, c, shift, from) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Sse2 => unsafe { x86::restore_sse2(x, c, shift, from) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Avx2 => unsafe { x86::restore_avx2(x, c, shift, from) },
    }
}

/// The dot product of one window with the given kernel (for the equivalence tests). With `head`, only
/// the lanes the decoder loads (all but the newest four).
#[cfg(test)]
pub(crate) fn dot_with<const P: usize>(isa: Isa, h: &[i64; P], c: &[i64; P], head: bool) -> i64 {
    const { check_width::<P>() };
    #[allow(unused_variables)]
    let groups = P / 4 - head as usize;
    match isa {
        Isa::Scalar => {
            let mut cc = *c;
            if head {
                cc[P - 4..].fill(0);
            }
            scalar::dot(h, &cc)
        }
        // SAFETY: as in `residuals`, the kernel's target feature is present.
        #[cfg(all(target_arch = "aarch64", not(kani)))]
        Isa::Neon => unsafe { neon::dot_once(h, c, groups) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Sse2 => unsafe { x86::dot_once_sse2(h, c, groups) },
        #[cfg(all(target_arch = "x86_64", not(kani)))]
        Isa::Avx2 => unsafe { x86::dot_once_avx2(h, c, groups) },
    }
}

#[inline(always)]
fn residuals_with<const P: usize>(
    x: &[i64],
    r: &mut [u64],
    from: usize,
    shift: u32,
    dot: impl Fn(&[i64; P]) -> i64,
) {
    for i in from..x.len() {
        let h: &[i64; P] = x[i - P..i].try_into().unwrap();
        r[i] = zigzag(x[i].wrapping_sub(dot(h) >> shift));
    }
}

/// The decoder loop of the SIMD kernels: `head(h)` sums lanes `0..P - 4` of the window, and the newest
/// four lanes (`c[P - 4..]` times `x[i - 4..i]`) are added from registers. Same wrapping sum, in another
/// grouping.
#[cfg(all(any(target_arch = "aarch64", target_arch = "x86_64"), not(kani)))]
#[inline(always)]
fn restore_with<const P: usize>(
    x: &mut [i64],
    c: &[i64; P],
    from: usize,
    shift: u32,
    head: impl Fn(&[i64; P]) -> i64,
) {
    let n = x.len();
    if from >= n {
        return;
    }
    let (c1, c2, c3, c4) = (c[P - 1], c[P - 2], c[P - 3], c[P - 4]);
    let (mut h1, mut h2, mut h3, mut h4) = (x[from - 1], x[from - 2], x[from - 3], x[from - 4]);
    for i in from..n {
        let h: &[i64; P] = x[i - P..i].try_into().unwrap();
        let s = head(h)
            .wrapping_add(c1.wrapping_mul(h1))
            .wrapping_add(c2.wrapping_mul(h2))
            .wrapping_add(c3.wrapping_mul(h3))
            .wrapping_add(c4.wrapping_mul(h4));
        let v = x[i].wrapping_add(s >> shift);
        x[i] = v;
        (h4, h3, h2, h1) = (h3, h2, h1, v);
    }
}

mod scalar {
    /// The wrapping dot product in four interleaved partial sums.
    #[inline(always)]
    pub(super) fn dot<const P: usize>(h: &[i64; P], c: &[i64; P]) -> i64 {
        let mut acc = [0i64; 4];
        for j in 0..P {
            acc[j % 4] = acc[j % 4].wrapping_add(h[j].wrapping_mul(c[j]));
        }
        acc[0]
            .wrapping_add(acc[1])
            .wrapping_add(acc[2])
            .wrapping_add(acc[3])
    }
}

#[cfg(all(target_arch = "aarch64", not(kani)))]
mod neon {
    use std::arch::aarch64::*;

    /// Coefficients for groups of four lanes: `swap[2g]`, `swap[2g + 1]` hold lanes `4g..4g + 4` as
    /// `[c_hi, c_lo]` 32-bit pairs (halves swapped), `lo[g]` their four low halves.
    struct Coefs {
        swap: [uint32x4_t; 16],
        lo: [uint32x4_t; 8],
    }

    impl Coefs {
        #[target_feature(enable = "neon")]
        #[inline]
        fn new<const P: usize>(c: &[i64; P]) -> Self {
            let mut swap = [0u32; 64];
            let mut lo = [0u32; 32];
            for (j, &v) in c.iter().enumerate() {
                let v = v as u64;
                swap[2 * j] = (v >> 32) as u32;
                swap[2 * j + 1] = v as u32;
                lo[j] = v as u32;
            }
            Self {
                // SAFETY: each load reads four u32 inside `swap` (64 elements) or `lo` (32 elements).
                swap: std::array::from_fn(|k| unsafe { vld1q_u32(swap.as_ptr().add(4 * k)) }),
                lo: std::array::from_fn(|g| unsafe { vld1q_u32(lo.as_ptr().add(4 * g)) }),
            }
        }
    }

    /// The wrapping sum of `h[j] · c[j]` over lanes `0..4 · groups` (`groups <= P / 4`).
    #[target_feature(enable = "neon")]
    #[inline]
    fn dot<const P: usize>(h: &[i64; P], k: &Coefs, groups: usize) -> i64 {
        assert!(groups <= P / 4);
        let p = h.as_ptr() as *const u64;
        let (mut cross0, mut cross1) = (vdupq_n_u32(0), vdupq_n_u32(0));
        let (mut lo0, mut lo1) = (vdupq_n_u64(0), vdupq_n_u64(0));
        for g in 0..groups {
            // SAFETY: lanes 4g..4g + 4 are inside `h` because g < groups <= P / 4.
            let (a, b) = unsafe { (vld1q_u64(p.add(4 * g)), vld1q_u64(p.add(4 * g + 2))) };
            let (a, b) = (vreinterpretq_u32_u64(a), vreinterpretq_u32_u64(b));
            // [a_lo·c_hi, a_hi·c_lo] per lane, modulo 2^32
            cross0 = vmlaq_u32(cross0, a, k.swap[2 * g]);
            cross1 = vmlaq_u32(cross1, b, k.swap[2 * g + 1]);
            // the low halves of the four lanes, then a_lo·c_lo in full
            let l = vuzp1q_u32(a, b);
            lo0 = vmlal_u32(lo0, vget_low_u32(l), vget_low_u32(k.lo[g]));
            lo1 = vmlal_high_u32(lo1, l, k.lo[g]);
        }
        let cross = vpaddlq_u32(vaddq_u32(cross0, cross1));
        let sum = vaddq_u64(vaddq_u64(lo0, lo1), vshlq_n_u64::<32>(cross));
        vaddvq_u64(sum) as i64
    }

    #[target_feature(enable = "neon")]
    pub(super) fn residuals<const P: usize>(
        x: &[i64],
        c: &[i64; P],
        shift: u32,
        r: &mut [u64],
        from: usize,
    ) {
        let k = Coefs::new(c);
        super::residuals_with(x, r, from, shift, |h: &[i64; P]| dot(h, &k, P / 4))
    }

    #[target_feature(enable = "neon")]
    pub(super) fn restore<const P: usize>(x: &mut [i64], c: &[i64; P], shift: u32, from: usize) {
        let k = Coefs::new(c);
        super::restore_with(x, c, from, shift, |h: &[i64; P]| dot(h, &k, P / 4 - 1))
    }

    #[cfg(test)]
    #[target_feature(enable = "neon")]
    pub(super) fn dot_once<const P: usize>(h: &[i64; P], c: &[i64; P], groups: usize) -> i64 {
        dot(h, &Coefs::new(c), groups)
    }
}

#[cfg(all(target_arch = "x86_64", not(kani)))]
mod x86 {
    use std::arch::x86_64::*;

    /// Coefficients as stored (`pmuludq` reads the low half of each lane) and their high halves.
    struct Coefs {
        c: [i64; 32],
        hi: [i64; 32],
    }

    impl Coefs {
        #[inline(always)]
        fn new<const P: usize>(c: &[i64; P]) -> Self {
            let mut k = Coefs {
                c: [0; 32],
                hi: [0; 32],
            };
            for (j, &v) in c.iter().enumerate() {
                k.c[j] = v;
                k.hi[j] = ((v as u64) >> 32) as i64;
            }
            k
        }
    }

    #[target_feature(enable = "sse2")]
    #[inline]
    fn hsum128(v: __m128i) -> i64 {
        let s = _mm_add_epi64(v, _mm_unpackhi_epi64(v, v));
        _mm_cvtsi128_si64(s)
    }

    /// The wrapping sum of `h[j] · c[j]` over lanes `0..4 · groups` (`groups <= P / 4`), two lanes per
    /// vector.
    #[target_feature(enable = "sse2")]
    #[inline]
    fn dot_sse2<const P: usize>(h: &[i64; P], k: &Coefs, groups: usize) -> i64 {
        assert!(groups <= P / 4);
        let (p, pc, ph) = (h.as_ptr(), k.c.as_ptr(), k.hi.as_ptr());
        let (mut lo, mut cross) = (_mm_setzero_si128(), _mm_setzero_si128());
        for v in 0..2 * groups {
            // SAFETY: lanes 2v..2v + 2 lie inside `h` (2v + 2 <= 4 · groups <= P) and inside `k.c`
            // and `k.hi` (32 lanes, P <= 32); the loads are unaligned.
            let (a, c, ch) = unsafe {
                (
                    _mm_loadu_si128(p.add(2 * v) as *const __m128i),
                    _mm_loadu_si128(pc.add(2 * v) as *const __m128i),
                    _mm_loadu_si128(ph.add(2 * v) as *const __m128i),
                )
            };
            lo = _mm_add_epi64(lo, _mm_mul_epu32(a, c));
            cross = _mm_add_epi64(cross, _mm_mul_epu32(a, ch));
            cross = _mm_add_epi64(cross, _mm_mul_epu32(_mm_srli_epi64::<32>(a), c));
        }
        hsum128(_mm_add_epi64(lo, _mm_slli_epi64::<32>(cross)))
    }

    /// As [`dot_sse2`], four lanes per vector.
    #[target_feature(enable = "avx2")]
    #[inline]
    fn dot_avx2<const P: usize>(h: &[i64; P], k: &Coefs, groups: usize) -> i64 {
        assert!(groups <= P / 4);
        let (p, pc, ph) = (h.as_ptr(), k.c.as_ptr(), k.hi.as_ptr());
        let (mut lo, mut cross) = (_mm256_setzero_si256(), _mm256_setzero_si256());
        for g in 0..groups {
            // SAFETY: lanes 4g..4g + 4 lie inside `h` (g < groups <= P / 4) and inside `k.c` and
            // `k.hi` (32 lanes, P <= 32); the loads are unaligned.
            let (a, c, ch) = unsafe {
                (
                    _mm256_loadu_si256(p.add(4 * g) as *const __m256i),
                    _mm256_loadu_si256(pc.add(4 * g) as *const __m256i),
                    _mm256_loadu_si256(ph.add(4 * g) as *const __m256i),
                )
            };
            lo = _mm256_add_epi64(lo, _mm256_mul_epu32(a, c));
            cross = _mm256_add_epi64(cross, _mm256_mul_epu32(a, ch));
            cross = _mm256_add_epi64(cross, _mm256_mul_epu32(_mm256_srli_epi64::<32>(a), c));
        }
        let s = _mm256_add_epi64(lo, _mm256_slli_epi64::<32>(cross));
        hsum128(_mm_add_epi64(
            _mm256_castsi256_si128(s),
            _mm256_extracti128_si256::<1>(s),
        ))
    }

    #[target_feature(enable = "sse2")]
    pub(super) fn residuals_sse2<const P: usize>(
        x: &[i64],
        c: &[i64; P],
        shift: u32,
        r: &mut [u64],
        from: usize,
    ) {
        let k = Coefs::new(c);
        super::residuals_with(x, r, from, shift, |h: &[i64; P]| dot_sse2(h, &k, P / 4))
    }

    #[target_feature(enable = "sse2")]
    pub(super) fn restore_sse2<const P: usize>(
        x: &mut [i64],
        c: &[i64; P],
        shift: u32,
        from: usize,
    ) {
        let k = Coefs::new(c);
        super::restore_with(x, c, from, shift, |h: &[i64; P]| dot_sse2(h, &k, P / 4 - 1))
    }

    #[target_feature(enable = "avx2")]
    pub(super) fn residuals_avx2<const P: usize>(
        x: &[i64],
        c: &[i64; P],
        shift: u32,
        r: &mut [u64],
        from: usize,
    ) {
        let k = Coefs::new(c);
        super::residuals_with(x, r, from, shift, |h: &[i64; P]| dot_avx2(h, &k, P / 4))
    }

    #[target_feature(enable = "avx2")]
    pub(super) fn restore_avx2<const P: usize>(
        x: &mut [i64],
        c: &[i64; P],
        shift: u32,
        from: usize,
    ) {
        let k = Coefs::new(c);
        super::restore_with(x, c, from, shift, |h: &[i64; P]| dot_avx2(h, &k, P / 4 - 1))
    }

    #[cfg(test)]
    #[target_feature(enable = "sse2")]
    pub(super) fn dot_once_sse2<const P: usize>(h: &[i64; P], c: &[i64; P], groups: usize) -> i64 {
        dot_sse2(h, &Coefs::new(c), groups)
    }

    #[cfg(test)]
    #[target_feature(enable = "avx2")]
    pub(super) fn dot_once_avx2<const P: usize>(h: &[i64; P], c: &[i64; P], groups: usize) -> i64 {
        dot_avx2(h, &Coefs::new(c), groups)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn lcg(seed: &mut u64) -> u64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *seed
    }

    /// The format's definition, term by term in coefficient order.
    fn reference(h: &[i64], c: &[i64]) -> i64 {
        h.iter()
            .zip(c)
            .fold(0i64, |acc, (&x, &c)| acc.wrapping_add(x.wrapping_mul(c)))
    }

    const EDGES: [i64; 20] = [
        0,
        1,
        -1,
        2,
        -2,
        i64::MAX,
        i64::MIN,
        i64::MAX - 1,
        i64::MIN + 1,
        i64::MAX / 2,
        i64::MIN / 2,
        1 << 32,
        -(1 << 32),
        (1 << 32) - 1,
        -(1 << 32) + 1,
        u32::MAX as i64,
        (1 << 31) - 1,
        -(1 << 31),
        (1 << 14) - 1,
        -(1 << 14),
    ];

    fn edge_or_random(seed: &mut u64) -> i64 {
        let r = lcg(seed);
        match r % 4 {
            0 => EDGES[(r >> 8) as usize % EDGES.len()],
            1 => (r >> 2) as i64,
            2 => ((r >> 20) % 65536) as i64 - 32768,
            _ => lcg(seed) as i64,
        }
    }

    /// Every kernel, full and head-only, equals the definition for window `h` and coefficients `c`.
    fn check<const P: usize>(h: &[i64; P], c: &[i64; P], what: &str) {
        let full = reference(h, c);
        let head = reference(&h[..P - 4], &c[..P - 4]);
        for isa in Isa::all_available() {
            assert_eq!(dot_with(isa, h, c, false), full, "{isa:?} full, {what}");
            assert_eq!(dot_with(isa, h, c, true), head, "{isa:?} head, {what}");
        }
    }

    /// Order `order` (1..=32) zero-padded at the old end to the window it runs in, as `Lpc::padded` does.
    fn padded<const P: usize>(coefs: &[i64]) -> [i64; P] {
        let mut c = [0i64; P];
        c[P - coefs.len()..].copy_from_slice(coefs);
        c
    }

    fn window_for(order: usize) -> usize {
        match order {
            0..=8 => 8,
            9..=16 => 16,
            17..=24 => 24,
            _ => 32,
        }
    }

    fn check_order(order: usize, coefs: &[i64], hist: &[i64], what: &str) {
        match window_for(order) {
            8 => check::<8>(hist[..8].try_into().unwrap(), &padded(coefs), what),
            16 => check::<16>(hist[..16].try_into().unwrap(), &padded(coefs), what),
            24 => check::<24>(hist[..24].try_into().unwrap(), &padded(coefs), what),
            _ => check::<32>(hist[..32].try_into().unwrap(), &padded(coefs), what),
        }
    }

    /// Exhaustive over orders 1..=32 and every pair of extreme values: all coefficients one extreme, all
    /// history another, plus each extreme in a single lane (so a wrong lane mapping shows).
    #[test]
    fn kernels_match_the_definition_on_extremes() {
        for order in 1..=32 {
            for &cv in &EDGES {
                for &hv in &EDGES {
                    let coefs = vec![cv; order];
                    let hist = [hv; 32];
                    check_order(
                        order,
                        &coefs,
                        &hist,
                        &format!("order {order} c {cv} h {hv}"),
                    );
                }
            }
            for lane in 0..32 {
                for &v in &EDGES {
                    let coefs: Vec<i64> = (0..order).map(|j| j as i64 - 7).collect();
                    let mut hist = [3i64; 32];
                    hist[lane] = v;
                    check_order(
                        order,
                        &coefs,
                        &hist,
                        &format!("order {order} h[{lane}] {v}"),
                    );
                    let mut coefs = vec![1i64; order];
                    coefs[lane % order] = v;
                    let hist: Vec<i64> = (0..32).map(|j| (j as i64) << 33 | 5).collect();
                    check_order(
                        order,
                        &coefs,
                        &hist,
                        &format!("order {order} c[{lane}] {v}"),
                    );
                }
            }
        }
    }

    /// Many random windows and coefficients, mixing the format's 15-bit coefficients with arbitrary i64
    /// ones (the kernels do not rely on the bound) and values that make the sum wrap.
    #[test]
    fn kernels_match_the_definition_on_random_windows() {
        let mut seed = 0x6772_697a_7a62_6974;
        for case in 0..200_000 {
            let order = 1 + case % 32;
            let small = case % 3 != 0;
            let coefs: Vec<i64> = (0..order)
                .map(|_| {
                    if small {
                        crate::g21::sign_extend(lcg(&mut seed) >> 7, 15)
                    } else {
                        edge_or_random(&mut seed)
                    }
                })
                .collect();
            let hist: Vec<i64> = (0..32).map(|_| edge_or_random(&mut seed)).collect();
            check_order(order, &coefs, &hist, &format!("case {case}"));
        }
    }

    fn streams<const P: usize>(seed: &mut u64, cases: usize) {
        for case in 0..cases {
            let order = 1 + (lcg(seed) as usize) % P.min(32);
            let order = order.max(if P == 8 { 1 } else { P - 7 });
            let coefs: Vec<i64> = (0..order)
                .map(|_| crate::g21::sign_extend(lcg(seed) >> 7, 15))
                .collect();
            let c = padded::<P>(&coefs);
            let shift = (lcg(seed) % 32) as u32;
            let n = (lcg(seed) % 400) as usize;
            let x: Vec<i64> = (0..n).map(|_| edge_or_random(seed)).collect();
            let from = P.min(n);
            let mut want = vec![0u64; n];
            residuals::<P>(Isa::Scalar, &x, &c, shift, &mut want, from);
            let mut back: Vec<i64> = x.clone();
            for i in from..n {
                back[i] = crate::unzigzag(want[i]);
            }
            for isa in Isa::all_available() {
                let mut r = vec![0u64; n];
                residuals::<P>(isa, &x, &c, shift, &mut r, from);
                assert_eq!(
                    r[from..],
                    want[from..],
                    "{isa:?} residuals P {P} case {case}"
                );
                let mut y = back.clone();
                restore::<P>(isa, &mut y, &c, shift, from);
                assert_eq!(y, x, "{isa:?} restore P {P} case {case}");
            }
        }
    }

    /// Whole streams: every kernel's residuals equal the scalar kernel's, and every kernel's decoder loop
    /// (with its register tail) restores the values, for all four windows and every shift.
    #[test]
    fn kernels_agree_on_streams() {
        let mut seed = 99;
        streams::<8>(&mut seed, 500);
        streams::<16>(&mut seed, 500);
        streams::<24>(&mut seed, 500);
        streams::<32>(&mut seed, 500);
    }

    #[test]
    fn detected_kernel_is_available() {
        // (in the test log: which kernels the equivalence tests above compared on this CPU)
        eprintln!(
            "LPC kernels: {:?}, detected {:?}",
            Isa::all_available(),
            Isa::detect()
        );
        assert!(Isa::all_available().contains(&Isa::detect()));
    }
}
