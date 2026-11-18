//! Study B8, S6: byte-for-byte determinism of the version-6 (float32) encoder across platforms.
//!
//! `testdata/grizzbit/float32-equivalence.bin` holds, for every case in `cases()`, what
//! `encode_with_float32(.., Grizzbit1)`, `encode_with_float32(.., Grizzbit21)` and `encode_version6` return. It was
//! written once on macOS/arm64 from the B8 build and is never regenerated; CI and the B8 platform runs check that every
//! platform reproduces it. Inputs use only integer arithmetic and exactly rounded IEEE operations (no libm), rounded
//! to float32.
//!
//! Record layout per case and encoder: a little-endian u32 length, then the encoded bytes (length 0: no version-6
//! coding).
use grizzbit::{decode, encode_version6, encode_with_float32, Format};
use std::path::PathBuf;

fn path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../testdata/grizzbit/float32-equivalence.bin")
}

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

/// A parabola per period in [0, 1], exactly rounded (no libm).
fn smooth(i: usize, period: usize) -> f64 {
    let p = (i % period) as f64 / period as f64;
    4.0 * p * (1.0 - p)
}

fn f(x: f64) -> f64 {
    x as f32 as f64
}

fn cases() -> Vec<Vec<f64>> {
    let mut seed = 2026u64;
    let mut out: Vec<Vec<f64>> = Vec::new();
    for &n in &[1usize, 2, 3, 17, 100, 1000, 2206, 4096] {
        // a force-like signal crossing zero, with float32 noise
        out.push(
            (0..n)
                .map(|i| f((smooth(i, 97) - 0.5) * 300.0 + (lcg(&mut seed) % 2001) as f64 * 1e-4))
                .collect(),
        );
        // controller decimals rounded to float32 (a float32 grid)
        out.push(
            (0..n)
                .map(|i| f(((smooth(i, 251) * 3000.0).round() / 1000.0) - 0.7))
                .collect(),
        );
        // one sign, smooth
        out.push(
            (0..n)
                .map(|i| f(50.0 + smooth(i, 61) * 20.0 + (lcg(&mut seed) % 1000) as f64 * 1e-3))
                .collect(),
        );
    }
    // the longest chunk
    out.push(
        (0..32768)
            .map(|i| f((smooth(i, 97) - 0.5) * 300.0 + (lcg(&mut seed) % 2001) as f64 * 1e-4))
            .collect(),
    );
    // signed zeros, subnormals, extremes within the accepted range
    out.push(vec![
        0.0,
        -0.0,
        0.0,
        -0.0,
        1e-45f32 as f64,
        -(1e-45f32 as f64),
        1e9,
        -1e9,
    ]);
    out.push(
        (0..600)
            .map(|i| {
                if i % 3 == 0 {
                    -0.0
                } else {
                    f((lcg(&mut seed) % 100) as f64 * 1e-5)
                }
            })
            .collect(),
    );
    // jumping two-digit decimals; a staircase; a resonance on a grid
    out.push(
        (0..2206)
            .map(|_| f(((lcg(&mut seed) % 300) as f64 - 150.0) / 100.0 - 0.7))
            .collect(),
    );
    out.push(
        (0..4096)
            .map(|i| f(0.3 + (i / 16 % 97) as f64 * 0.001))
            .collect(),
    );
    let (mut y1, mut y2) = (0.0f64, 0.0f64);
    out.push(
        (0..4096)
            .map(|_| {
                let e = (lcg(&mut seed) % 41) as f64 - 20.0;
                let y = 1.95 * y1 - 0.97 * y2 + e;
                y2 = y1;
                y1 = y;
                f(0.25 + y.round() * 0.0023413)
            })
            .collect(),
    );
    // random float32 bit patterns in range
    out.push(
        (0..1000)
            .map(|_| loop {
                let v = f32::from_bits(lcg(&mut seed) as u32);
                if v.is_finite() && v.abs() <= 1e9 {
                    break v as f64;
                }
            })
            .collect(),
    );
    out
}

fn records() -> Vec<u8> {
    let mut out = Vec::new();
    let mut put = |bytes: &[u8], x: &[f64]| {
        if !bytes.is_empty() {
            let back = decode(bytes).unwrap();
            assert!(
                back.len() == x.len()
                    && back.iter().zip(x).all(|(a, b)| a.to_bits() == b.to_bits())
            );
        }
        out.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
        out.extend_from_slice(bytes);
    };
    for x in cases() {
        put(
            &encode_with_float32(&x, 1.0, Format::Grizzbit1).unwrap(),
            &x,
        );
        put(
            &encode_with_float32(&x, 1.0, Format::Grizzbit21).unwrap(),
            &x,
        );
        put(&encode_version6(&x).unwrap().unwrap_or_default(), &x);
    }
    out
}

#[test]
fn float32_encoder_reproduces_the_frozen_bytes() {
    let frozen = std::fs::read(path()).expect("testdata/grizzbit/float32-equivalence.bin");
    let now = records();
    assert_eq!(now.len(), frozen.len(), "record stream length differs");
    assert!(
        now == frozen,
        "encoded bytes differ from the frozen float32 encoder output"
    );
}

/// Writes the file only if it is missing: `GRIZZBIT_WRITE_EQUIVALENCE=1 cargo test ... -- --ignored`.
#[test]
#[ignore]
fn write_frozen_bytes() {
    if std::env::var_os("GRIZZBIT_WRITE_EQUIVALENCE").is_some() && !path().exists() {
        std::fs::write(path(), records()).unwrap();
    }
}
