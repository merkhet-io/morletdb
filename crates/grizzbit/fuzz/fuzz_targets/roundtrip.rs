//! Structured chunks into `encode`: every valid chunk decodes bit for bit, every
//! invalid one (empty, too long, NaN, infinite, beyond 1e9) or invalid quantum is
//! rejected, and the encoding is never larger than plain float64 plus its header.
#![no_main]
use arbitrary::{Arbitrary, Unstructured};
use libfuzzer_sys::fuzz_target;
use grizzbit::{
    decode, encode, encode_version5, encode_version6, encode_with, encode_with_float32, CodecError, Format,
    FLOAT32_VERSION, GRIZZBIT21_VERSION,
    MAX_AMPLITUDE, MAX_SAMPLES,
};
mod common;

#[derive(Debug)]
struct Chunk {
    samples: Vec<f64>,
    quantum: f64,
}

/// Samples near the grids real data sits on: integer counts, decimals, dyadic and
/// calibrated steps, EDF-style affine grids, off by a few ulps, plus edge values and
/// raw bit patterns.
fn sample(u: &mut Unstructured, step: f64, offset: f64) -> arbitrary::Result<f64> {
    let k = i64::from(u.int_in_range(-40_000i32..=40_000)?);
    Ok(match u.int_in_range(0u8..=9)? {
        0 => k as f64 * step,
        1 => k as f64 / (1.0 / step).round().max(1.0),
        2 => offset + k as f64 * step,
        3 => (k as f64 + 2048.0) * (step * 4095.0) / 4095.0 + offset,
        4 => {
            let x = k as f64 * step;
            let d = i64::from(u.int_in_range(-3i8..=3)?);
            f64::from_bits((x.to_bits() as i64).wrapping_add(d) as u64)
        }
        5 => *u.choose(&[
            0.0,
            -0.0,
            5e-324,
            -5e-324,
            f64::MIN_POSITIVE,
            1e9,
            -1e9,
            1e-300,
        ])?,
        6 => f64::from_bits(u.arbitrary()?),
        7 => (k as f64 * 0.37).sin() * 1e3,
        8 => *u.choose(&[
            f64::NAN,
            f64::INFINITY,
            -f64::INFINITY,
            1e9 * (1.0 + f64::EPSILON),
        ])?,
        _ => k as f64,
    })
}

impl<'a> Arbitrary<'a> for Chunk {
    fn arbitrary(u: &mut Unstructured<'a>) -> arbitrary::Result<Self> {
        let quantum = match u.int_in_range(0u8..=7)? {
            0 => 1.0,
            1 => 0.001,
            2 => 100.0 / 32768.0,
            3 => 417.0 / 4095.0,
            4 => f64::from_bits(u.arbitrary()?),
            5 => *u.choose(&[0.0, -1.0, f64::NAN, f64::INFINITY, 5e-324, 1e-9, 1e300])?,
            6 => 10f64.powi(i32::from(u.int_in_range(-9i8..=6)?)),
            _ => {
                f64::from(u.int_in_range(1u16..=u16::MAX)?)
                    / f64::from(u.int_in_range(1u16..=u16::MAX)?)
            }
        };
        let step = if quantum.is_finite() && quantum > 0.0 {
            quantum
        } else {
            1.0
        };
        let offset = f64::from(u.int_in_range(-1000i16..=1000)?) * step / 997.0;
        let n = u
            .int_in_range(0usize..=MAX_SAMPLES + 1)?
            .min(u.len() / 2 + 1);
        let samples = (0..n)
            .map(|_| sample(u, step, offset))
            .collect::<arbitrary::Result<_>>()?;
        Ok(Self { samples, quantum })
    }
}

fuzz_target!(|c: Chunk| {
    let valid_samples = !c.samples.is_empty()
        && c.samples.len() <= MAX_SAMPLES
        && c.samples
            .iter()
            .all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE);
    let valid_quantum = c.quantum.is_finite() && c.quantum > 0.0;
    match encode(&c.samples, c.quantum) {
        Ok(bytes) => {
            assert!(valid_samples && valid_quantum);
            // Version, kind, varint count (<= 3 bytes), float64 payload, checksum.
            assert!(bytes.len() <= 1 + 1 + 3 + 8 * c.samples.len() + 4);
            let back = decode(&bytes).expect("decodes");
            assert_eq!(back.len(), c.samples.len());
            for (a, b) in back.iter().zip(&c.samples) {
                assert_eq!(a.to_bits(), b.to_bits());
            }
            // Grizzbit 2.1 (study B6 build): exact too, never larger, and Grizzbit 1 byte for byte unless it
            // writes a strictly smaller version-5 chunk.
            let g21 = encode_with(&c.samples, c.quantum, Format::Grizzbit21).expect("encodes");
            if g21[0] == GRIZZBIT21_VERSION {
                assert!(g21.len() < bytes.len());
            } else {
                assert_eq!(g21, bytes);
            }
            let back = decode(&g21).expect("decodes");
            assert_eq!(back.len(), c.samples.len());
            for (a, b) in back.iter().zip(&c.samples) {
                assert_eq!(a.to_bits(), b.to_bits());
            }
            // Study B8: the same samples rounded to float32, through the default server encoder (version 6
            // only where strictly smaller, else the format's own chunk byte for byte) and the version-6 coder
            // alone, are exact too.
            let f: Vec<f64> = c.samples.iter().map(|&v| v as f32 as f64).collect();
            if f.iter().all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE) {
                for format in [Format::Grizzbit1, Format::Grizzbit21] {
                    let base = encode_with(&f, c.quantum, format).expect("encodes");
                    let got = encode_with_float32(&f, c.quantum, format).expect("encodes");
                    if got[0] == FLOAT32_VERSION {
                        assert!(got.len() < base.len());
                    } else {
                        assert_eq!(got, base);
                    }
                    let back = decode(&got).expect("decodes");
                    assert!(back.iter().zip(&f).all(|(a, b)| a.to_bits() == b.to_bits()));
                }
                let v6 = encode_version6(&f).expect("encodes").expect("float32 samples");
                let back = decode(&v6).expect("decodes");
                assert_eq!(back.len(), f.len());
                assert!(back.iter().zip(&f).all(|(a, b)| a.to_bits() == b.to_bits()));
            }
            // The version-5 coder alone, kept or not, decodes bit for bit too.
            if let Some(v5) = encode_version5(&c.samples, c.quantum).expect("encodes") {
                assert_eq!(v5[0], GRIZZBIT21_VERSION);
                let back = decode(&v5).expect("decodes");
                assert_eq!(back.len(), c.samples.len());
                for (a, b) in back.iter().zip(&c.samples) {
                    assert_eq!(a.to_bits(), b.to_bits());
                }
            }
        }
        Err(CodecError::InvalidQuantum) => assert!(valid_samples && !valid_quantum),
        Err(CodecError::Signal(_)) => assert!(!valid_samples),
        Err(e) => panic!("unexpected encode error {e:?}"),
    }
});
