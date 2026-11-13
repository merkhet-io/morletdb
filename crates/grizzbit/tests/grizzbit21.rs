//! Grizzbit 2.1 (DEVELOPMENT BUILD, chunk format version 5): round trips, edge cases, never larger than
//! Grizzbit 1, Grizzbit 1 output untouched, and malformed version-5 chunks rejected without a panic.
use grizzbit::{
    decode, encode, encode_experimental, encode_with, grizzbit21_layout, kind, CodecError, Format,
    Grizzbit21Features, Kind, GRIZZBIT21_VERSION, MAX_AMPLITUDE, MAX_SAMPLES,
};
use proptest::prelude::*;

const FIX_NONE: u8 = 0;
const FIX_SAMPLES: u8 = 1;
const FIX_TABLE: u8 = 2;
const FIX_EXCEPTIONS: u8 = 3;

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

fn crc32c(bytes: &[u8]) -> u32 {
    let mut c = !0u32;
    for &b in bytes {
        c ^= b as u32;
        for _ in 0..8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0x82F6_3B78
            } else {
                c >> 1
            };
        }
    }
    !c
}

fn seal(body: &[u8]) -> Vec<u8> {
    let mut out = body.to_vec();
    out.extend_from_slice(&crc32c(body).to_le_bytes());
    out
}

const ARMS: [Grizzbit21Features; 3] = [
    Grizzbit21Features {
        corrections: true,
        lpc: false,
        search: grizzbit::LpcSearch::DEFAULT,
    },
    Grizzbit21Features {
        corrections: false,
        lpc: true,
        search: grizzbit::LpcSearch::DEFAULT,
    },
    Grizzbit21Features::ALL,
];

/// Every 2.1 arm decodes bit for bit, is never larger than Grizzbit 1, and is Grizzbit 1 byte for
/// byte unless it is version 5 and strictly smaller. Returns the full 2.1 chunk.
fn check(samples: &[f64], q: f64) -> Vec<u8> {
    let g1 = encode(samples, q).unwrap();
    assert_eq!(encode_with(samples, q, Format::Grizzbit1).unwrap(), g1);
    let mut full = Vec::new();
    for arm in ARMS {
        let c = encode_experimental(samples, q, arm).unwrap();
        let back = decode(&c).unwrap();
        assert_eq!(back.len(), samples.len());
        for (i, (a, b)) in back.iter().zip(samples).enumerate() {
            assert_eq!(a.to_bits(), b.to_bits(), "{arm:?}: sample {i}");
        }
        if c[0] == GRIZZBIT21_VERSION {
            assert!(c.len() < g1.len(), "{arm:?}: version 5 not smaller");
            assert_eq!(kind(&c).unwrap(), Kind::Grid);
            let (mode, lpc) = grizzbit21_layout(&c).unwrap();
            assert!(arm.corrections || mode <= FIX_SAMPLES, "{arm:?}");
            assert!(arm.lpc || !lpc, "{arm:?}");
        } else {
            assert_eq!(c, g1, "{arm:?}");
            assert!(grizzbit21_layout(&c).is_none());
        }
        if arm == Grizzbit21Features::ALL {
            assert_eq!(c, encode_with(samples, q, Format::Grizzbit21).unwrap());
            full = c;
        }
    }
    full
}

/// Counts scaled in two float64 steps, so the value is a function of the index but a few ulps off the
/// best single-step grid: the shape of the calibrated channels the table is for.
fn calibrated(ks: &[i64]) -> Vec<f64> {
    ks.iter()
        .map(|&k| k as f64 * 0.000_688_781_737_8 * 1.000_000_7)
        .collect()
}

fn walk(n: usize, seed: &mut u64, step: i64) -> Vec<i64> {
    let mut k = 0i64;
    (0..n)
        .map(|_| {
            k += (lcg(seed) % (2 * step as u64 + 1)) as i64 - step;
            k
        })
        .collect()
}

/// A noisy resonance in integers: what per-chunk LPC is for.
fn resonance(n: usize, seed: &mut u64) -> Vec<i64> {
    let (mut y1, mut y2) = (0.0f64, 0.0f64);
    (0..n)
        .map(|_| {
            let e = (lcg(seed) % 41) as f64 - 20.0;
            let y = 1.95 * y1 - 0.97 * y2 + e;
            y2 = y1;
            y1 = y;
            y.round()
        })
        .map(|y| y as i64)
        .collect()
}

#[test]
fn edge_cases_roundtrip() {
    let mut s = 1u64;
    let cases: Vec<(Vec<f64>, f64)> = vec![
        (vec![0.0], 1.0),
        (vec![-0.0], 1.0),
        (vec![1e9], 1.0),
        (vec![-1e9, 1e9, 0.0, -0.0], 1.0),
        (vec![3.25; 4096], 1.0),
        (vec![-0.0; 4096], 1.0),
        (vec![5e-324, -5e-324, f64::MIN_POSITIVE, 0.0], 1.0),
        ((0..4096).map(|i| (i % 7) as f64 * 5e-324).collect(), 5e-324),
        (
            (0..MAX_SAMPLES)
                .map(|i| ((i * 37) % 1001) as f64 - 500.0)
                .collect(),
            1.0,
        ),
        (
            resonance(MAX_SAMPLES, &mut s)
                .iter()
                .map(|&k| k as f64)
                .collect(),
            1.0,
        ),
        (
            calibrated(&walk(MAX_SAMPLES, &mut s, 30)),
            0.000_688_781_737_8,
        ),
        (
            (0..4096)
                .map(|i| if i % 2 == 0 { 1e9 } else { -1e9 })
                .collect(),
            1.0,
        ),
    ];
    for (x, q) in &cases {
        check(x, *q);
    }
}

#[test]
fn corrections_once_per_index_use_the_table() {
    let mut s = 2u64;
    let ks = walk(4096, &mut s, 6);
    let x = calibrated(&ks);
    let c = check(&x, 0.000_688_781_737_8);
    let (mode, _) = grizzbit21_layout(&c).expect("version 5");
    assert_eq!(mode, FIX_TABLE);
    assert!(c.len() * 10 < encode(&x, 0.000_688_781_737_8).unwrap().len() * 9);
}

#[test]
fn chunks_where_the_index_does_not_fix_the_value_stay_exact() {
    let mut s = 3u64;
    let ks = walk(4096, &mut s, 6);
    // Some indices carry two different last places: the table keeps the majority, exceptions the rest.
    let mut x = calibrated(&ks);
    for (i, v) in x.iter_mut().enumerate() {
        if i % 97 == 5 {
            *v = f64::from_bits(v.to_bits() ^ 1);
        }
    }
    let c = check(&x, 0.000_688_781_737_8);
    assert_eq!(grizzbit21_layout(&c).map(|l| l.0), Some(FIX_TABLE));
    // Every sample independently off by -1, 0 or +1 ulp: no function at all.
    let noisy: Vec<f64> = ks
        .iter()
        .map(|&k| {
            let v = k as f64 * (100.0 / 32768.0);
            if v == 0.0 {
                return v;
            }
            f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 3) as i64 - 1) as u64)
        })
        .collect();
    check(&noisy, 100.0 / 32768.0);
    // Near-grid values with mixed corrections and an offset grid.
    let edf: Vec<f64> = ks
        .iter()
        .map(|&k| (k as f64 + 2048.0) * 417.0 / 4095.0 - 208.0)
        .collect();
    check(&edf, 417.0 / 4095.0);
}

#[test]
fn negative_zeros_on_a_grid_are_sparse_exceptions() {
    let mut s = 4u64;
    let x: Vec<f64> = walk(4096, &mut s, 40)
        .iter()
        .enumerate()
        .map(|(i, &k)| {
            let k = k % 200;
            let v = k as f64 / 1000.0;
            if k == 0 && i % 2 == 0 {
                -0.0
            } else {
                v
            }
        })
        .collect();
    assert!(x.iter().any(|v| v.to_bits() == (-0.0f64).to_bits()));
    let c = check(&x, 0.001);
    assert_eq!(grizzbit21_layout(&c).map(|l| l.0), Some(FIX_EXCEPTIONS));
}

#[test]
fn lpc_wins_on_resonant_counts() {
    let mut s = 5u64;
    let x: Vec<f64> = resonance(4096, &mut s).iter().map(|&k| k as f64).collect();
    let c = check(&x, 1.0);
    assert_eq!(grizzbit21_layout(&c), Some((FIX_NONE, true)));
    // LPC with per-sample corrections: every sample a few ulps off a calibrated step.
    let y: Vec<f64> = resonance(4096, &mut s)
        .iter()
        .map(|&k| {
            let v = k as f64 * (100.0 / 32768.0);
            f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 5) as i64 - 2) as u64)
        })
        .collect();
    let c = check(&y, 100.0 / 32768.0);
    assert_eq!(grizzbit21_layout(&c).map(|l| l.1), Some(true));
    let _ = FIX_SAMPLES;
}

#[test]
fn off_grid_chunks_keep_grizzbit_1() {
    let mut s = 6u64;
    let x: Vec<f64> = (0..1000)
        .map(|_| (lcg(&mut s) as f64 / 9e15 - 0.5) * 1e3)
        .collect();
    let c = check(&x, 1.0);
    assert_ne!(c[0], GRIZZBIT21_VERSION);
}

/// Version 5 header and section bodies that a writer never produces are refused, never a panic.
#[test]
fn malformed_version5_chunks_are_rejected() {
    let mut s = 7u64;
    let x = calibrated(&walk(512, &mut s, 6));
    let good = encode_with(&x, 0.000_688_781_737_8, Format::Grizzbit21).unwrap();
    assert_eq!(good[0], GRIZZBIT21_VERSION);
    let body = &good[..good.len() - 4];
    let flags_at = 2 + 2; // version, kind, varint 512 (2 bytes)
                          // unknown flag bits
    for bit in 3..8 {
        let mut b = body.to_vec();
        b[flags_at] |= 1 << bit;
        assert_eq!(decode(&seal(&b)), Err(CodecError::Corrupt("grid header")));
    }
    // every correction mode against this body: an error or the same samples, never a panic
    for mode in 0..4u8 {
        let mut b = body.to_vec();
        b[flags_at] = (b[flags_at] & 1) | (mode << 1);
        if let Ok(v) = decode(&seal(&b)) {
            assert_eq!(v.len(), x.len());
        }
    }
    // mode 3 with zero exceptions
    let mut b = vec![GRIZZBIT21_VERSION, 1, 1, FIX_EXCEPTIONS << 1, 1];
    b.extend(1.0f64.to_le_bytes());
    b.extend([1, 0b0000_0000, 0]); // index stream: order 0, porder 0, then a Rice parameter byte
    b.push(0); // m = 0
    assert!(decode(&seal(&b)).is_err());
    // grid kinds other than 1 and version 4
    let mut b = body.to_vec();
    b[1] = 2;
    assert_eq!(decode(&seal(&b)), Err(CodecError::Corrupt("unknown kind")));
    let mut b = body.to_vec();
    b[0] = 4;
    assert_eq!(
        decode(&seal(&b)),
        Err(CodecError::Corrupt("unknown format version"))
    );
    // trailing byte, truncation
    let mut b = body.to_vec();
    b.push(0);
    assert!(decode(&seal(&b)).is_err());
    for cut in 1..body.len() {
        assert!(decode(&seal(&body[..cut])).is_err() || cut == body.len());
    }
}

fn quantum() -> impl Strategy<Value = f64> {
    prop_oneof![
        Just(1.0),
        Just(0.001),
        Just(100.0 / 32768.0),
        Just(0.000_688_781_737_8),
        Just(417.0 / 4095.0),
        (-9i32..=6).prop_map(|e| 10f64.powi(e)),
        (1u64..0x7FF0_0000_0000_0000).prop_map(f64::from_bits),
    ]
}

/// Integer-valued sequences (walks, resonances, constants) mapped onto grids in the ways real data is:
/// exact, scaled in two steps (corrections a function of the index), offset, ulp noise, negative zeros.
fn chunk() -> impl Strategy<Value = (Vec<f64>, f64)> {
    (quantum(), 1usize..=4096, any::<u64>(), 0u8..6, 0u8..5).prop_map(|(q, n, seed, shape, map)| {
        let mut s = seed | 1;
        let ks: Vec<i64> = match shape {
            0 => walk(n, &mut s, 20),
            1 => resonance(n, &mut s),
            2 => vec![(seed % 1000) as i64 - 500; n],
            3 => (0..n).map(|_| (lcg(&mut s) % 50) as i64 - 25).collect(),
            4 => walk(n, &mut s, 3000),
            _ => (0..n).map(|_| lcg(&mut s) as i64).collect(),
        };
        let x = ks
            .iter()
            .map(|&k| {
                let v = match map {
                    0 => k as f64 * q,
                    1 => k as f64 * q * 1.000_000_7,
                    2 => 0.37 * q + k as f64 * q,
                    3 => f64::from_bits(
                        ((k as f64 * q).to_bits() as i64 + (lcg(&mut s) % 3) as i64 - 1) as u64,
                    ),
                    _ => {
                        if k == 0 && lcg(&mut s).is_multiple_of(2) {
                            -0.0
                        } else {
                            k as f64 * q
                        }
                    }
                };
                if v.is_finite() && v.abs() <= MAX_AMPLITUDE {
                    v
                } else {
                    0.0
                }
            })
            .collect();
        (x, q)
    })
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 300, ..ProptestConfig::default() })]

    #[test]
    fn grizzbit21_roundtrips_and_never_grows((samples, q) in chunk()) {
        check(&samples, q);
    }

    /// Any change to a valid version-5 encoding, resealed, fails or decodes to valid samples.
    #[test]
    fn resealed_version5_mutations_never_panic(
        (samples, q) in chunk(),
        edits in prop::collection::vec((any::<prop::sample::Index>(), any::<u8>(), 0u8..3), 1..8),
    ) {
        let good = encode_with(&samples, q, Format::Grizzbit21).unwrap();
        let mut body = good[..good.len() - 4].to_vec();
        body[0] = GRIZZBIT21_VERSION;
        for (at, byte, op) in edits {
            let i = at.index(body.len().max(1));
            match op {
                0 if i > 0 && i < body.len() => body[i] = byte,
                1 if i > 0 => body.insert(i.min(body.len()), byte),
                _ if body.len() > 2 && i > 0 && i < body.len() => { body.remove(i); }
                _ => {}
            }
        }
        if let Ok(x) = decode(&seal(&body)) {
            prop_assert!(!x.is_empty() && x.len() <= MAX_SAMPLES);
            prop_assert!(x.iter().all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE));
        }
    }
}
