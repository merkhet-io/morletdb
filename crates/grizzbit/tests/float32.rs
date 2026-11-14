//! Chunk format version 6 (float32 codings, study B8): round trips through the keep-the-smaller
//! encoder and through the version-6 coding alone, edge cases, and the decoder's rejection of malformed
//! chunks. Gate 1 of evaluation/storage_b8/PROTOCOL.md.
use grizzbit::{
    decode, encode_version6, encode_with, encode_with_float32, kind, Format, Kind, FLOAT32_VERSION,
    MAX_SAMPLES,
};
use proptest::prelude::*;

/// The CRC-32C the codec appends, reimplemented only to reseal mutated bodies.
fn seal(body: &[u8]) -> Vec<u8> {
    let mut c = !0u32;
    for &b in body {
        c ^= b as u32;
        for _ in 0..8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0x82F6_3B78
            } else {
                c >> 1
            };
        }
    }
    let mut out = body.to_vec();
    out.extend_from_slice(&(!c).to_le_bytes());
    out
}

fn same_bits(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

/// Every check a stored chunk gets: keep-the-smaller holds, both codings round-trip bit for bit.
fn check(x: &[f64]) {
    for format in [Format::Grizzbit1, Format::Grizzbit21] {
        let base = encode_with(x, 1.0, format).unwrap();
        let got = encode_with_float32(x, 1.0, format).unwrap();
        if got[0] == FLOAT32_VERSION {
            assert!(
                got.len() < base.len(),
                "version 6 kept without being smaller"
            );
        } else {
            assert_eq!(
                got, base,
                "a chunk that is not version 6 must be the format's own chunk"
            );
        }
        assert!(same_bits(&decode(&got).unwrap(), x));
    }
    match encode_version6(x).unwrap() {
        Some(v6) => {
            assert_eq!(v6[0], FLOAT32_VERSION);
            assert!(same_bits(&decode(&v6).unwrap(), x));
            assert!(matches!(kind(&v6).unwrap(), Kind::Grid | Kind::Float));
        }
        None => assert!(x.iter().any(|&v| (v as f32) as f64 != v)),
    }
}

fn f32s(v: &[f32]) -> Vec<f64> {
    v.iter().map(|&x| x as f64).collect()
}

#[test]
fn edge_cases() {
    let max = 1.0e9f32; // the largest accepted magnitude is exactly a float32 value
    assert_eq!(max as f64, 1e9);
    let cases: Vec<Vec<f64>> = vec![
        vec![1.5],
        vec![-0.0],
        vec![0.0],
        vec![0.0; MAX_SAMPLES],
        vec![-0.0; 100],
        f32s(&[0.0, -0.0, 0.0, -0.0, 0.0]),
        f32s(&[
            1e-45,
            -1e-45,
            f32::MIN_POSITIVE,
            -f32::MIN_POSITIVE,
            0.0,
            -0.0,
        ]),
        f32s(&[max, -max, max, -max]),
        (0..MAX_SAMPLES)
            .map(|i| if i % 2 == 0 { 1.25 } else { -1.25 })
            .collect(),
        (0..MAX_SAMPLES)
            .map(|i| ((i as f32) * 0.001).sin() as f64)
            .collect(),
        (0..4096)
            .map(|i| (0.3 + (i % 97) as f64 * 0.001) as f32 as f64)
            .collect(),
        (0..4096)
            .map(|i| ((i % 200) as f64 * 5e-6 - 5e-4) as f32 as f64)
            .collect(),
        (0..4096)
            .map(|i| (17.25 + (i % 31) as f64 * 0.159) as f32 as f64)
            .collect(),
        (0..4096)
            .map(|i| (i * 7919 % 4093) as f64 - 2000.0)
            .collect(),
        (0..4096).map(|i| (i as f64 * 1e-3) as f32 as f64).collect(),
    ];
    for c in &cases {
        check(c);
    }
    // One sample that is not a float32 value: never version 6.
    let mut x = cases[9].clone();
    x[100] = 0.1;
    assert!(encode_version6(&x).unwrap().is_none());
    for format in [Format::Grizzbit1, Format::Grizzbit21] {
        assert_eq!(
            encode_with_float32(&x, 1.0, format).unwrap(),
            encode_with(&x, 1.0, format).unwrap()
        );
    }
}

#[test]
fn signed_zero_runs_and_one_sign() {
    let x = f32s(&[-0.0, -0.0, -1.0, -2.0, -0.0]);
    let v6 = encode_version6(&x).unwrap().unwrap();
    assert!(same_bits(&decode(&v6).unwrap(), &x));
}

#[test]
fn float32_grids_beat_today_on_rounded_decimals() {
    // Decimals rounded to float32 (as controller data is): version 6 must win and be exact.
    let x: Vec<f64> = (0..4096)
        .map(|i| ((i as f64 * 0.37).sin() * 3.0 * 1e6).round() / 1e6)
        .map(|v| v as f32 as f64)
        .collect();
    let got = encode_with_float32(&x, 1.0, Format::Grizzbit1).unwrap();
    assert_eq!(got[0], FLOAT32_VERSION);
    assert!(got.len() * 2 < encode_with(&x, 1.0, Format::Grizzbit1).unwrap().len());
    assert!(same_bits(&decode(&got).unwrap(), &x));
}

fn body_of(c: &[u8]) -> Vec<u8> {
    c[..c.len() - 4].to_vec()
}

#[test]
fn malformed_chunks_are_rejected() {
    let signmag = encode_version6(
        &(0..5)
            .map(|i| ((i as f64 * 1.7).sin() * 1.234) as f32 as f64)
            .collect::<Vec<_>>(),
    )
    .unwrap()
    .unwrap();
    let grid = encode_version6(
        &(0..120)
            .map(|i| (0.3 + (i * 7 % 40) as f64 * 0.001) as f32 as f64)
            .collect::<Vec<_>>(),
    )
    .unwrap()
    .unwrap();
    assert_eq!(kind(&signmag).unwrap(), Kind::Float);
    assert_eq!(kind(&grid).unwrap(), Kind::Grid);
    for c in [&signmag, &grid] {
        let b = body_of(c);
        // unknown kind, zero samples, too many samples, trailing byte, truncation
        let mut k = b.clone();
        k[1] = 2;
        assert!(decode(&seal(&k)).is_err());
        let mut z = b.clone();
        z[2] = 0;
        assert!(decode(&seal(&z)).is_err());
        let mut t = b.clone();
        t.push(0);
        assert!(decode(&seal(&t)).is_err());
        for cut in 3..b.len() {
            assert!(decode(&seal(&b[..cut])).is_err(), "truncated at {cut}");
        }
        // every single-bit flip, resealed, either fails or decodes to valid samples, never panics
        for i in 1..b.len() {
            for bit in 0..8 {
                let mut m = b.clone();
                m[i] ^= 1 << bit;
                if let Ok(x) = decode(&seal(&m)) {
                    assert!(x.iter().all(|v| v.is_finite()));
                }
            }
        }
        // a flipped byte without resealing fails the checksum
        let mut f = c.to_vec();
        f[3] ^= 0x40;
        assert!(decode(&f).is_err());
    }
    // sign/magnitude: first sign must be 0 or 1; run count within 1..=n
    let b = body_of(&signmag);
    let mut s = b.clone();
    s[3] = 2;
    assert!(decode(&seal(&s)).is_err());
    let mut r = b.clone();
    r[4] = 0;
    assert!(decode(&seal(&r)).is_err());
    let mut r = b.clone();
    r[4] = 6;
    assert!(decode(&seal(&r)).is_err());
    // grid: non-finite or non-positive scale, has_fix > 1
    let b = body_of(&grid);
    for scale in [0.0f64, -1.0, f64::INFINITY, f64::NAN] {
        let mut g = b.clone();
        g[11..19].copy_from_slice(&scale.to_le_bytes());
        assert!(decode(&seal(&g)).is_err());
    }
}

fn float32_chunk() -> impl Strategy<Value = Vec<f64>> {
    let bits = (0u32..=u32::MAX)
        .prop_map(f32::from_bits)
        .prop_filter("in range", |x| x.is_finite() && x.abs() <= 1e9);
    prop_oneof![
        // arbitrary float32 bit patterns in range
        proptest::collection::vec(bits, 1..=512).prop_map(|v| f32s(&v)),
        // smooth signals crossing zero
        (
            1usize..=4096,
            0.001f64..0.5,
            0.01f64..1000.0,
            -10.0f64..10.0
        )
            .prop_map(|(n, w, a, o)| {
                (0..n)
                    .map(|i| ((i as f64 * w).sin() * a + o) as f32 as f64)
                    .collect()
            }),
        // rounded decimal and offset grids, with float32 rounding
        (1usize..=4096, 1u32..=7, -1000i32..=1000, 1u32..=5000).prop_map(|(n, d, off, span)| {
            let q = 10f64.powi(-(d as i32));
            (0..n)
                .map(|i| ((off as f64 + (i as u32 * 7919 % span) as f64) * q) as f32 as f64)
                .collect()
        }),
        // ADC-like step with offset, rounded to float32
        (1usize..=4096, 1e-6f64..1.0, -100.0f64..100.0).prop_map(|(n, s, o)| {
            (0..n)
                .map(|i| (o + ((i * 31 % 2048) as f64 - 1024.0) * s) as f32 as f64)
                .collect()
        }),
    ]
}

proptest! {
    #![proptest_config(ProptestConfig::with_cases(256))]
    #[test]
    fn version6_round_trips(x in float32_chunk()) {
        check(&x);
    }
}
