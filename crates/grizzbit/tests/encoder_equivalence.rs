//! Byte-for-byte equivalence of the Grizzbit encoder with the encoder it was moved from.
//!
//! `testdata/grizzbit/encoder-equivalence.bin` holds, for every case in `cases()`, what the chunk
//! encoder of morlet-core 0.3.8 (`morlet_core::rawcodec`, before the codec became the `grizzbit`
//! crate) returned: the encoded bytes, or the error message. The file was written once from that
//! encoder and is never regenerated: Grizzbit 1 is the chunk format versions 2 and 3, unchanged, so
//! the current encoder must reproduce every record exactly.
//!
//! Record layout: one status byte (0 = encoded, 1 = error), a little-endian u32 length, then the
//! encoded bytes or the UTF-8 error message.
use grizzbit::{decode, encode};
use std::path::PathBuf;

fn path() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../testdata/grizzbit/encoder-equivalence.bin")
}

/// A smooth, platform-independent wave in [0, 1]: a parabola per period, from integer division and
/// products only (exactly rounded in IEEE 754, unlike `sin`, whose last bit differs between libms).
fn smooth(i: usize, period: usize) -> f64 {
    let p = (i % period) as f64 / period as f64;
    4.0 * p * (1.0 - p)
}

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

fn ulp_up(x: f64, n: u64) -> f64 {
    f64::from_bits(x.to_bits() + n)
}

/// (samples, quantum): every kind, both grid modes, offsets, corrections, edge values, lengths and errors.
fn cases() -> Vec<(Vec<f64>, f64)> {
    let mut s = 0x6772_697a_7a62_6974;
    let mut out: Vec<(Vec<f64>, f64)> = Vec::new();
    let lengths = [
        1usize, 2, 3, 4, 5, 7, 8, 15, 16, 17, 63, 64, 65, 255, 256, 1000, 4096,
    ];
    for &n in &lengths {
        // Integer counts: random walk plus a smooth wave, on quantum 1.
        let mut d = 0i64;
        let counts: Vec<f64> = (0..n)
            .map(|i| {
                d += (lcg(&mut s) % 21) as i64 - 10;
                (d + ((smooth(i, 82) - 0.5) * 1000.) as i64) as f64
            })
            .collect();
        out.push((counts.clone(), 1.0));
        // The same counts scaled by a calibrated, a dyadic and a decimal step.
        let cal = 100.0 / 32768.0 * (1.0 - 7e-10);
        out.push((counts.iter().map(|k| k * cal).collect(), 100.0 / 32768.0));
        out.push((
            counts.iter().map(|k| k * (1.0 / 1024.0)).collect(),
            1.0 / 1024.0,
        ));
        out.push((
            counts
                .iter()
                .map(|k| format!("{:.3}", k / 1000.).parse().unwrap())
                .collect(),
            0.001,
        ));
        // Offset (EDF-like) grid, and the same with last-place noise.
        let step = 417.0 / 4095.0;
        let edf: Vec<f64> = counts
            .iter()
            .map(|k| (k.clamp(-2048.0, 2047.0) + 2048.0) * 417.0 / 4095.0 - 208.0)
            .collect();
        out.push((edf.clone(), step));
        out.push((
            edf.iter()
                .enumerate()
                .map(|(i, &x)| if i % 3 == 0 { ulp_up(x, 1) } else { x })
                .collect(),
            step,
        ));
        // Shifted decimals: off the plain grid by a constant.
        out.push((counts.iter().map(|k| 0.0004 + k / 1000.).collect(), 0.001));
        // Smooth floats (float kind) and white noise (raw kind).
        out.push((
            (0..n)
                .map(|i| smooth(i, 232) * 3.7 + 11.0)
                .collect(),
            1.0,
        ));
        out.push(((0..n).map(|_| (unit(&mut s) - 0.5) * 2e9).collect(), 1.0));
        out.push(((0..n).map(|_| (unit(&mut s) - 0.5) * 1e-3).collect(), 1e-6));
        // Mostly on the grid with a few far-off samples.
        out.push((
            counts
                .iter()
                .enumerate()
                .map(|(i, &k)| if i % 11 == 5 { k + 0.37 } else { k })
                .collect(),
            1.0,
        ));
        // Constant.
        out.push((vec![42.0; n], 1.0));
    }
    // Edge values.
    out.push((vec![0.0], 1.0));
    out.push((vec![-0.0], 1.0));
    out.push((vec![1.0, -0.0, 2.0, -0.0, 0.0, -3.0], 1.0));
    out.push((vec![-0.0; 9], 0.5));
    out.push((vec![1e9, -1e9, 0.0, 1.0], 1.0));
    out.push((vec![1e9, -1e9, 1e9, -1e9], 1e-6));
    out.push((vec![f64::MIN_POSITIVE, 5e-324, -5e-324, 0.0], 1.0));
    out.push((vec![5e-324, 1e-320, 2e-310], 5e-324));
    out.push((vec![0.1, 0.2, 0.3, 0.7], 0.1));
    out.push((vec![1.0 / 3.0, 2.0 / 3.0, 1.0, 4.0 / 3.0], 1.0 / 3.0));
    out.push((vec![0.25, 0.5, 0.75], 0.25));
    out.push((vec![3.0, 6.0, 9.0, 12.0], 3.0));
    out.push((vec![1e-300, 2e-300, 3e-300], 1e-300));
    out.push((vec![123_456_789.0, 123_456_790.0], 1e-9));
    out.push((vec![0.5, 1.5, 2.5], 1e300));
    // Maximum length, grid and float.
    out.push((
        (0..32768)
            .map(|i| ((i * 37) % 1001) as f64 - 500.0)
            .collect(),
        1.0,
    ));
    out.push((
        (0..32768).map(|i| smooth(i, 6283) + 2.0).collect(),
        1.0,
    ));
    // Errors: no samples, too many, non-finite or too large samples, bad quanta.
    out.push((vec![], 1.0));
    out.push((vec![0.0; 32769], 1.0));
    out.push((vec![1.0, f64::NAN], 1.0));
    out.push((vec![f64::INFINITY], 1.0));
    out.push((vec![f64::NEG_INFINITY], 1.0));
    out.push((vec![1.000_000_001e9], 1.0));
    out.push((vec![1.0], 0.0));
    out.push((vec![1.0], -1.0));
    out.push((vec![1.0], f64::NAN));
    out.push((vec![1.0], f64::INFINITY));
    out
}

fn records() -> Vec<u8> {
    let mut out = Vec::new();
    for (samples, quantum) in cases() {
        let (status, bytes) = match encode(&samples, quantum) {
            Ok(enc) => {
                let back = decode(&enc).unwrap();
                assert_eq!(back.len(), samples.len());
                assert!(back
                    .iter()
                    .zip(&samples)
                    .all(|(a, b)| a.to_bits() == b.to_bits()));
                (0u8, enc)
            }
            Err(e) => (1u8, e.to_string().into_bytes()),
        };
        out.push(status);
        out.extend_from_slice(&(bytes.len() as u32).to_le_bytes());
        out.extend_from_slice(&bytes);
    }
    out
}

#[test]
fn encoder_reproduces_the_frozen_bytes() {
    let frozen = std::fs::read(path()).expect("testdata/grizzbit/encoder-equivalence.bin");
    let now = records();
    assert_eq!(now.len(), frozen.len(), "record stream length differs");
    assert!(
        now == frozen,
        "encoded bytes differ from the frozen encoder output"
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
