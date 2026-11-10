//! Grizzbit 2 (chunk format version 4, study B5 gate 1): edge cases through the version-4 coder alone and
//! through the keep-the-smaller encoder. Every decode is compared by `to_bits`; the encoder is deterministic.
use grizzbit::{
    decode, encode, encode_version4, encode_with, kind, Format, Kind, MAX_AMPLITUDE, MAX_SAMPLES,
    PCO_VERSION,
};

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

fn unit(seed: &mut u64) -> f64 {
    lcg(seed) as f64 / 9_007_199_254_740_992.0
}

fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

/// (name, samples, quantum)
fn cases() -> Vec<(&'static str, Vec<f64>, f64)> {
    let mut s = 0x6235_6772_697a_7a32;
    let mut out: Vec<(&'static str, Vec<f64>, f64)> = vec![
        ("n=1", vec![0.123456789], 1.0),
        ("n=1 zero", vec![0.0], 1.0),
        ("n=1 negative zero", vec![-0.0], 1.0),
        ("n=1 max", vec![MAX_AMPLITUDE], 1.0),
        ("n=1 denormal", vec![5e-324], 1.0),
        ("constant", vec![3.25; 4096], 1.0),
        ("constant off grid", vec![0.1; 4096], 1.0),
        ("constant max length", vec![-7.5e8; MAX_SAMPLES], 1.0),
        (
            "signed zeros",
            (0..4096)
                .map(|i| if i % 3 == 0 { -0.0 } else { 0.0 })
                .collect(),
            1.0,
        ),
        (
            "signed zeros and values",
            (0..1000)
                .map(|i| match i % 4 {
                    0 => -0.0,
                    1 => 0.0,
                    2 => i as f64 * 0.37,
                    _ => -(i as f64) * 0.37,
                })
                .collect(),
            1.0,
        ),
        (
            "denormals",
            (0..4096)
                .map(|i| f64::from_bits(1 + (i as u64 * 7919) % 1_000_000))
                .collect(),
            1.0,
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
        (
            "plus minus 1e9",
            (0..4096)
                .map(|i| if i % 2 == 0 { 1e9 } else { -1e9 })
                .collect(),
            1.0,
        ),
        (
            "alternating extremes",
            (0..4096)
                .map(|i| match i % 4 {
                    0 => MAX_AMPLITUDE,
                    1 => 5e-324,
                    2 => -MAX_AMPLITUDE,
                    _ => -0.0,
                })
                .collect(),
            1.0,
        ),
        (
            "extremes with ulp neighbours",
            (0..2000)
                .map(|i| {
                    let x = f64::from_bits(MAX_AMPLITUDE.to_bits() - (i as u64 % 5));
                    if i % 2 == 0 {
                        x
                    } else {
                        -x
                    }
                })
                .collect(),
            1.0,
        ),
    ];
    let walk = |seed: &mut u64, n: usize, scale: f64| -> Vec<f64> {
        let mut x = 0.0f64;
        (0..n)
            .map(|_| {
                x += (unit(seed) - 0.5) * scale;
                x
            })
            .collect()
    };
    out.push(("float walk max length", walk(&mut s, MAX_SAMPLES, 1.0), 1.0));
    out.push(("float walk 4096", walk(&mut s, 4096, 0.01), 1.0));
    out.push((
        "float32 held as float64",
        walk(&mut s, 4096, 3.0)
            .into_iter()
            .map(|v| v as f32 as f64)
            .collect(),
        1.0,
    ));
    out.push((
        "float32 held as float64, max length",
        walk(&mut s, MAX_SAMPLES, 0.1)
            .into_iter()
            .map(|v| v as f32 as f64)
            .collect(),
        1.0,
    ));
    // Near-grid: decimals with a few values a few ulps off, and decimals carried as products (0.1 * k).
    out.push((
        "near grid decimals",
        (0..4096)
            .map(|i| {
                let k = (lcg(&mut s) % 20_000) as i64 - 10_000;
                let x = k as f64 / 100.0;
                if i % 17 == 0 {
                    f64::from_bits(x.to_bits() ^ 1)
                } else {
                    x
                }
            })
            .collect(),
        0.01,
    ));
    out.push((
        "near grid products",
        (0..4096)
            .map(|_| ((lcg(&mut s) % 100_000) as f64) * 0.1)
            .collect(),
        1.0,
    ));
    out.push((
        "near grid within a thousandth",
        (0..4096)
            .map(|_| ((lcg(&mut s) % 4000) as f64) * 0.25 + (unit(&mut s) - 0.5) * 1e-4)
            .collect(),
        0.25,
    ));
    // Random bit patterns within the accepted range (finite, |x| <= 1e9), plus the full exponent range.
    out.push((
        "random bits within range",
        (0..4096)
            .map(|_| loop {
                let x = f64::from_bits(lcg(&mut s) << 11 ^ lcg(&mut s));
                if x.is_finite() && x.abs() <= MAX_AMPLITUDE {
                    break x;
                }
            })
            .collect(),
        1.0,
    ));
    out.push((
        "random magnitudes",
        (0..4096)
            .map(|_| {
                let e = (lcg(&mut s) % 60) as i32 - 50;
                (unit(&mut s) - 0.5) * 2f64.powi(e) * 1.7
            })
            .collect(),
        1.0,
    ));
    out.push((
        "integer counts",
        walk(&mut s, 4096, 2000.0)
            .into_iter()
            .map(f64::round)
            .collect(),
        1.0,
    ));
    out
}

#[test]
fn version4_round_trips_every_edge_case_bit_for_bit() {
    for (name, x, _) in cases() {
        let c = encode_version4(&x).unwrap_or_else(|e| panic!("{name}: {e}"));
        assert_eq!(c[0], PCO_VERSION, "{name}");
        assert_eq!(kind(&c), Ok(Kind::Pco), "{name}");
        assert!(same(&decode(&c).unwrap(), &x), "{name}: round trip");
        assert_eq!(encode_version4(&x).unwrap(), c, "{name}: deterministic");
    }
}

#[test]
fn keep_the_smaller_round_trips_every_edge_case_bit_for_bit() {
    let mut kept = 0;
    for (name, x, q) in cases() {
        let g1 = encode(&x, q).unwrap();
        let v4 = encode_version4(&x).unwrap();
        let g2 = encode_with(&x, q, Format::Grizzbit2).unwrap();
        if v4.len() < g1.len() {
            assert_eq!(g2, v4, "{name}: the smaller pco chunk is kept");
            kept += 1;
        } else {
            assert_eq!(g2, g1, "{name}: Grizzbit 1 is kept, byte for byte");
        }
        assert!(same(&decode(&g2).unwrap(), &x), "{name}: round trip");
        assert_eq!(
            encode_with(&x, q, Format::Grizzbit2).unwrap(),
            g2,
            "{name}: deterministic"
        );
        assert_eq!(encode_with(&x, q, Format::Grizzbit1).unwrap(), g1, "{name}");
    }
    // Both outcomes occur in this set.
    assert!(kept > 0 && kept < cases().len(), "kept {kept}");
}

#[test]
fn invalid_chunks_are_refused_by_both_formats() {
    let too_long = vec![1.0; MAX_SAMPLES + 1];
    for x in [
        vec![],
        too_long,
        vec![f64::NAN],
        vec![1.0, f64::INFINITY],
        vec![-f64::INFINITY],
        vec![MAX_AMPLITUDE * (1.0 + f64::EPSILON)],
    ] {
        assert_eq!(
            encode_with(&x, 1.0, Format::Grizzbit2),
            encode(&x, 1.0),
            "{:?}",
            &x[..x.len().min(2)]
        );
        assert!(encode_version4(&x).is_err());
    }
    assert_eq!(
        encode_with(&[1.0], 0.0, Format::Grizzbit2),
        encode(&[1.0], 0.0)
    );
}
