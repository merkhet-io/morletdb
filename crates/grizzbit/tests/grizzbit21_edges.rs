//! Grizzbit 2.1 (chunk format version 5, study B6 gate 1): edge cases through the keep-the-smaller encoder
//! (`encode_with(.., Format::Grizzbit21)`), through each of its two codings alone, and through the version-5
//! coder alone (`encode_version5`, kept or not). Every decode is compared by `to_bits`; every encoding is done
//! twice and must be the same bytes.
use grizzbit::{
    decode, encode, encode_experimental, encode_version5, encode_with, grizzbit21_layout, kind,
    Format, Grizzbit21Features, Kind, LpcSearch, GRIZZBIT21_VERSION, MAX_AMPLITUDE, MAX_SAMPLES,
};

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

/// Uniform in [0, 1).
fn unit(seed: &mut u64) -> f64 {
    lcg(seed) as f64 / 9_007_199_254_740_992.0
}

fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

fn counts(ks: impl Iterator<Item = i64>) -> Vec<f64> {
    ks.map(|k| k as f64).collect()
}

/// (name, samples, quantum)
fn cases() -> Vec<(&'static str, Vec<f64>, f64)> {
    let mut s = 0x6232_316b_6565_7032;
    let n = 4096;
    let tau = std::f64::consts::TAU;
    let calibrated = 0.000_688_781_737_8 * 1.000_000_7;
    let mut walk = 0i64;
    let small_walk: Vec<i64> = (0..n)
        .map(|_| {
            walk += (lcg(&mut s) % 7) as i64 - 3;
            walk
        })
        .collect();
    let mut walk = 0i64;
    let wide_walk: Vec<i64> = (0..n)
        .map(|_| {
            walk += (lcg(&mut s) % 4001) as i64 - 2000;
            walk
        })
        .collect();
    vec![
        // n = 1 and n = 32,768
        ("n=1", vec![0.123456789], 1.0),
        ("n=1 count", vec![42.0], 1.0),
        ("n=1 zero", vec![0.0], 1.0),
        ("n=1 negative zero", vec![-0.0], 1.0),
        ("n=1 max", vec![MAX_AMPLITUDE], 1.0),
        ("n=1 min", vec![-MAX_AMPLITUDE], 1.0),
        ("n=1 denormal", vec![5e-324], 1.0),
        ("max length constant", vec![-7.5e8; MAX_SAMPLES], 1.0),
        (
            "max length tone",
            counts(
                (0..MAX_SAMPLES).map(|i| (3000.0 * (tau * i as f64 / 97.3).sin()).round() as i64),
            ),
            1.0,
        ),
        (
            "max length calibrated walk",
            {
                let mut k = 0i64;
                (0..MAX_SAMPLES)
                    .map(|_| {
                        k += (lcg(&mut s) % 61) as i64 - 30;
                        k as f64 * calibrated
                    })
                    .collect()
            },
            0.000_688_781_737_8,
        ),
        // constants and signed zeros
        ("constant", vec![3.25; n], 1.0),
        ("constant off grid", vec![0.1; n], 1.0),
        ("constant zero", vec![0.0; n], 1.0),
        ("constant negative zero", vec![-0.0; n], 1.0),
        (
            "signed zeros",
            (0..n)
                .map(|i| if i % 3 == 0 { -0.0 } else { 0.0 })
                .collect(),
            1.0,
        ),
        (
            "signed zeros on a decimal grid",
            small_walk
                .iter()
                .enumerate()
                .map(|(i, &k)| {
                    if k == 0 && i % 2 == 0 {
                        -0.0
                    } else {
                        k as f64 / 1000.0
                    }
                })
                .collect(),
            0.001,
        ),
        // denormals and +-1e9
        (
            "denormals",
            (0..n)
                .map(|i| f64::from_bits(1 + (i as u64 * 7919) % 1_000_000))
                .collect(),
            1.0,
        ),
        (
            "denormal grid",
            (0..n).map(|i| (i % 50) as f64 * 5e-324).collect(),
            5e-324,
        ),
        (
            "denormals signed",
            (0..512)
                .map(|i| {
                    let v = f64::from_bits(1 + i as u64);
                    if i % 2 == 0 {
                        v
                    } else {
                        -v
                    }
                })
                .collect(),
            1.0,
        ),
        // alternating extremes
        (
            "alternating extremes",
            (0..n)
                .map(|i| if i % 2 == 0 { 1e9 } else { -1e9 })
                .collect(),
            1.0,
        ),
        (
            "extremes and zero",
            (0..n).map(|i| [1e9, 0.0, -1e9, -0.0][i % 4]).collect(),
            1.0,
        ),
        (
            "extremes with ulp neighbours",
            (0..n)
                .map(|i| {
                    f64::from_bits(1e9f64.to_bits() - (i as u64 % 3))
                        * if i % 2 == 0 { 1.0 } else { -1.0 }
                })
                .collect(),
            1.0,
        ),
        // grid data: multiplier and offset grids, few and many distinct indices
        (
            "calibrated few indices",
            small_walk.iter().map(|&k| k as f64 * calibrated).collect(),
            0.000_688_781_737_8,
        ),
        (
            "calibrated many indices",
            wide_walk.iter().map(|&k| k as f64 * calibrated).collect(),
            0.000_688_781_737_8,
        ),
        (
            "dyadic grid",
            small_walk
                .iter()
                .map(|&k| k as f64 * (100.0 / 32768.0))
                .collect(),
            100.0 / 32768.0,
        ),
        (
            "decimal grid",
            wide_walk.iter().map(|&k| k as f64 / 1000.0).collect(),
            0.001,
        ),
        (
            "offset grid (EDF)",
            small_walk
                .iter()
                .map(|&k| ((k % 2000) as f64 + 2048.0) * 417.0 / 4095.0 - 208.0)
                .collect(),
            417.0 / 4095.0,
        ),
        (
            "offset grid, many indices",
            wide_walk
                .iter()
                .map(|&k| 0.25 + (k % 3_000_000) as f64 * 0.003)
                .collect(),
            0.003,
        ),
        (
            "near grid mixed corrections",
            wide_walk
                .iter()
                .map(|&k| {
                    let v = k as f64 * (100.0 / 32768.0);
                    if v == 0.0 {
                        v
                    } else {
                        f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 3) as i64 - 1) as u64)
                    }
                })
                .collect(),
            100.0 / 32768.0,
        ),
        // LPC: pure tones, chirps, white noise, steps
        (
            "pure tone counts",
            counts((0..n).map(|i| (12000.0 * (tau * i as f64 / 37.1).sin()).round() as i64)),
            1.0,
        ),
        (
            "two tones calibrated",
            (0..n)
                .map(|i| {
                    let k = (4000.0 * (tau * i as f64 / 64.0).sin()
                        + 900.0 * (tau * i as f64 / 9.7).sin())
                    .round();
                    k * calibrated
                })
                .collect(),
            0.000_688_781_737_8,
        ),
        (
            "chirp counts",
            counts((0..n).map(|i| {
                let t = i as f64 / n as f64;
                (8000.0 * (tau * (5.0 * t + 400.0 * t * t)).sin()).round() as i64
            })),
            1.0,
        ),
        (
            "white noise counts",
            counts((0..n).map(|_| (lcg(&mut s) % 65536) as i64 - 32768)),
            1.0,
        ),
        (
            "steps",
            counts((0..n).map(|i| ((i / 300) as i64 % 5) * 1000 - 2000)),
            1.0,
        ),
        (
            "steps with noise",
            counts((0..n).map(|i| ((i / 300) as i64 % 5) * 1000 + (lcg(&mut s) % 9) as i64 - 4)),
            1.0,
        ),
        // random bit patterns within the accepted range
        (
            "random bit patterns",
            (0..n)
                .map(|_| loop {
                    let v = f64::from_bits(lcg(&mut s) << 11 | lcg(&mut s));
                    if v.is_finite() && v.abs() <= MAX_AMPLITUDE {
                        break v;
                    }
                })
                .collect(),
            1.0,
        ),
        (
            "random magnitudes",
            (0..n).map(|_| (unit(&mut s) - 0.5) * 2e9).collect(),
            1.0,
        ),
    ]
}

const ARMS: [Grizzbit21Features; 2] = [
    Grizzbit21Features {
        corrections: true,
        lpc: false,
        search: LpcSearch::DEFAULT,
    },
    Grizzbit21Features {
        corrections: false,
        lpc: true,
        search: LpcSearch::DEFAULT,
    },
];

#[test]
fn edge_cases_through_keep_the_smaller_and_the_version5_coder() {
    let mut kept_v5 = 0;
    for (name, x, q) in cases() {
        assert!(
            x.iter().all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE),
            "{name}"
        );
        let g1 = encode(&x, q).unwrap();
        let g21 = encode_with(&x, q, Format::Grizzbit21).unwrap();
        assert_eq!(
            g21,
            encode_with(&x, q, Format::Grizzbit21).unwrap(),
            "{name}: deterministic"
        );
        if g21[0] == GRIZZBIT21_VERSION {
            kept_v5 += 1;
            assert!(
                g21.len() < g1.len(),
                "{name}: version 5 kept but not smaller"
            );
            assert_eq!(kind(&g21).unwrap(), Kind::Grid, "{name}");
        } else {
            assert_eq!(g21, g1, "{name}: not Grizzbit 1's bytes");
        }
        assert!(
            same(&decode(&g21).unwrap(), &x),
            "{name}: keep-the-smaller round trip"
        );
        for arm in ARMS {
            let c = encode_experimental(&x, q, arm).unwrap();
            assert!(
                c == g1 || (c[0] == GRIZZBIT21_VERSION && c.len() < g1.len()),
                "{name}: {arm:?}"
            );
            assert!(same(&decode(&c).unwrap(), &x), "{name}: {arm:?} round trip");
        }
        // The version-5 coder alone, kept or not.
        if let Some(v5) = encode_version5(&x, q).unwrap() {
            assert_eq!(v5[0], GRIZZBIT21_VERSION, "{name}");
            assert!(grizzbit21_layout(&v5).is_some(), "{name}");
            assert!(
                same(&decode(&v5).unwrap(), &x),
                "{name}: version-5 round trip"
            );
            assert_eq!(
                Some(v5),
                encode_version5(&x, q).unwrap(),
                "{name}: deterministic"
            );
        }
    }
    assert!(kept_v5 >= 10, "only {kept_v5} cases keep a version-5 chunk");
}

/// Invalid inputs give the same errors through every encoder.
#[test]
fn invalid_inputs_are_refused_alike() {
    for (x, q) in [
        (vec![], 1.0),
        (vec![f64::NAN], 1.0),
        (vec![1.0, f64::INFINITY], 1.0),
        (vec![MAX_AMPLITUDE * 1.0000001], 1.0),
        (vec![0.0; MAX_SAMPLES + 1], 1.0),
        (vec![1.0], 0.0),
        (vec![1.0], f64::NAN),
        (vec![1.0], -1.0),
    ] {
        let want = encode(&x, q);
        assert!(want.is_err());
        assert_eq!(encode_with(&x, q, Format::Grizzbit21), want);
        assert_eq!(encode_version5(&x, q).map(|_| ()), want.map(|_| ()));
    }
}
