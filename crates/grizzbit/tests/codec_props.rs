//! Property tests for the chunk codec: the layer between the Kani proofs (all inputs,
//! tiny sizes) and fuzzing (unbounded time). See docs/verification.md.
use grizzbit::{decode, encode};
use grizzbit::{MAX_AMPLITUDE, MAX_SAMPLES};
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

fn quantum() -> impl Strategy<Value = f64> {
    prop_oneof![
        Just(1.0),
        Just(0.001),
        Just(100.0 / 32768.0),
        Just(417.0 / 4095.0),
        (-9i32..=6).prop_map(|e| 10f64.powi(e)),
        (1u32..=1_000_000, 1u32..=1_000_000).prop_map(|(a, b)| a as f64 / b as f64),
        (1u64..0x7FF0_0000_0000_0000).prop_map(f64::from_bits),
    ]
}

/// Chunks on, near and off the grids real data uses: counts, decimals, offset grids,
/// last-place noise, edge values and arbitrary finite floats within range.
fn chunk() -> impl Strategy<Value = (Vec<f64>, f64)> {
    (quantum(), -997i32..=997, 1usize..=4096).prop_flat_map(|(q, off, n)| {
        let offset = off as f64 * q / 997.0;
        let value = prop_oneof![
            4 => (-40_000i64..=40_000).prop_map(move |k| k as f64 * q),
            2 => (-40_000i64..=40_000).prop_map(move |k| offset + k as f64 * q),
            2 => (-40_000i64..=40_000, -3i64..=3).prop_map(move |(k, d)| {
                f64::from_bits(((k as f64 * q).to_bits() as i64).wrapping_add(d) as u64)
            }),
            1 => prop::sample::select(vec![0.0, -0.0, 5e-324, -5e-324, f64::MIN_POSITIVE, 1e9, -1e9]),
            1 => -MAX_AMPLITUDE..=MAX_AMPLITUDE,
            1 => any::<u64>().prop_map(f64::from_bits),
        ];
        (prop::collection::vec(value, n), Just(q))
    })
}

fn valid(x: &[f64]) -> bool {
    !x.is_empty()
        && x.len() <= MAX_SAMPLES
        && x.iter().all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE)
}

proptest! {
    #![proptest_config(ProptestConfig { cases: 500, ..ProptestConfig::default() })]

    #[test]
    fn valid_chunks_roundtrip_bit_for_bit((samples, q) in chunk()) {
        match encode(&samples, q) {
            Ok(bytes) => {
                prop_assert!(valid(&samples));
                prop_assert!(bytes.len() <= 2 + 3 + 8 * samples.len() + 4);
                let back = decode(&bytes).unwrap();
                prop_assert_eq!(back.len(), samples.len());
                for (a, b) in back.iter().zip(&samples) {
                    prop_assert_eq!(a.to_bits(), b.to_bits());
                }
            }
            Err(_) => prop_assert!(!valid(&samples)),
        }
    }

    /// Any change to a valid encoding, resealed so it passes the checksum, either fails
    /// to decode or decodes to valid samples; it never panics.
    #[test]
    fn resealed_mutations_never_panic(
        (samples, q) in chunk(),
        edits in prop::collection::vec((any::<prop::sample::Index>(), any::<u8>(), 0u8..3), 1..8),
    ) {
        let Ok(good) = encode(&samples, q) else { return Ok(()) };
        let mut body = good[..good.len() - 4].to_vec();
        for (at, byte, op) in edits {
            let i = at.index(body.len().max(1));
            match op {
                0 if i < body.len() => body[i] = byte,
                1 => body.insert(i.min(body.len()), byte),
                _ if body.len() > 1 && i < body.len() => { body.remove(i); }
                _ => {}
            }
        }
        if let Ok(x) = decode(&seal(&body)) {
            prop_assert!(valid(&x));
        }
    }

    #[test]
    fn arbitrary_bytes_never_panic(bytes in prop::collection::vec(any::<u8>(), 0..512), sealed in any::<bool>()) {
        let input = if sealed { seal(&bytes) } else { bytes };
        if let Ok(x) = decode(&input) {
            prop_assert!(valid(&x));
        }
    }
}
