//! Structured chunks into `encode`: every valid chunk decodes bit for bit, every
//! invalid one (empty, too long, NaN, infinite, beyond 1e9) or invalid quantum is
//! rejected, and the encoding is never larger than plain float64 plus its header.
//! The same chunks go through Grizzbit 2's keep-the-smaller encoder (`encode_with`,
//! `Format::Grizzbit2`): its output is Grizzbit 1's bytes, or a version-4 chunk with
//! strictly fewer bytes, and decodes bit for bit; the version-4 (pco) chunk of every
//! valid chunk, kept or not, decodes bit for bit too.
#![no_main]
use arbitrary::{Arbitrary, Unstructured};
use libfuzzer_sys::fuzz_target;
use grizzbit::{
    decode, encode, encode_version4, encode_with, CodecError, Format, MAX_AMPLITUDE, MAX_SAMPLES,
    PCO_VERSION,
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

fn assert_decodes_to(bytes: &[u8], samples: &[f64]) {
    let back = decode(bytes).expect("decodes");
    assert_eq!(back.len(), samples.len());
    for (a, b) in back.iter().zip(samples) {
        assert_eq!(a.to_bits(), b.to_bits());
    }
}

fuzz_target!(|c: Chunk| {
    let valid_samples = !c.samples.is_empty()
        && c.samples.len() <= MAX_SAMPLES
        && c.samples
            .iter()
            .all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE);
    let valid_quantum = c.quantum.is_finite() && c.quantum > 0.0;
    let grizzbit1 = encode(&c.samples, c.quantum);
    match &grizzbit1 {
        Ok(bytes) => {
            assert!(valid_samples && valid_quantum);
            // Version, kind, varint count (<= 3 bytes), float64 payload, checksum.
            assert!(bytes.len() <= 1 + 1 + 3 + 8 * c.samples.len() + 4);
            assert_decodes_to(bytes, &c.samples);
        }
        Err(CodecError::InvalidQuantum) => assert!(valid_samples && !valid_quantum),
        Err(CodecError::Signal(_)) => assert!(!valid_samples),
        Err(e) => panic!("unexpected encode error {e:?}"),
    }
    // Grizzbit 2: the same errors, or Grizzbit 1's bytes, or a strictly smaller version-4 chunk.
    let grizzbit2 = encode_with(&c.samples, c.quantum, Format::Grizzbit2);
    match (&grizzbit1, &grizzbit2) {
        (Ok(g1), Ok(g2)) => {
            if g2 != g1 {
                assert_eq!(g2[0], PCO_VERSION);
                assert!(g2.len() < g1.len());
            }
            assert_decodes_to(g2, &c.samples);
        }
        (Err(a), Err(b)) => assert_eq!(a, b),
        _ => panic!("Grizzbit 1 and 2 disagree on validity"),
    }
    match encode_version4(&c.samples) {
        Ok(v4) => {
            assert!(valid_samples);
            assert_decodes_to(&v4, &c.samples);
        }
        Err(CodecError::Signal(_)) => assert!(!valid_samples),
        Err(e) => panic!("unexpected version-4 encode error {e:?}"),
    }
});
