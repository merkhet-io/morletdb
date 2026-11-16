//! Byte identity on a synthetic corpus (study B9, gate 5, evaluation/speed_b9/PROTOCOL.md): a deterministic corpus,
//! generated here from fixed seeds (no file under testdata/), is encoded in the four configurations that ship (G1,
//! G1+6, G21, G21+6), plus Grizzbit 2.1 restricted to each fixed LPC order 1..=32; the SHA-256 of each
//! configuration's concatenated chunk bytes must equal what `9361aba` (main with #507, before #394's speed work)
//! writes. Every chunk is also decoded and compared by `to_bits`.
//!
//! The corpus uses IEEE 754 basic operations only (no libm), so it is the same on every platform. It covers tones,
//! chirps and noise on integer, decimal and offset grids; chunks whose LPC order spans 1..=32 (every order is
//! asserted); wide index spans that take the open-addressing slot path, and dense ones; per-sample, table and
//! exception corrections; float32-exact chunks of both version-6 kinds; and float chunks. The test asserts that
//! each of these codings occurs, so the hashes cover them.
use grizzbit::{
    decode, encode_experimental, encode_with, encode_with_float32, float32_layout,
    grizzbit21_layout, Format, Grizzbit21Features, LpcSearch, FLOAT32_VERSION, GRIZZBIT21_VERSION,
};
use std::collections::BTreeSet;

/// The SHA-256 of each configuration's concatenated chunk bytes, as `9361aba` writes them (computed by running
/// this file against a `git archive` of `9361aba`).
const EXPECTED: [(&str, &str); 5] = [
    (
        "G1",
        "b138e4024387a1960d46781ecf0170a46d1436453da019f1f55cd99b45270a4f",
    ),
    (
        "G1+6",
        "f1a9f646b472fe9dd6d779b0b8bfd98ef1f123705a39759f4e758ae712deccaa",
    ),
    (
        "G21",
        "8e0169f102edb7c3a1d85d58dae36264046b8f10681844e79b376c25d150fbd0",
    ),
    (
        "G21+6",
        "d7def72606bd593df1bcf633585945b6dbef9cb5e4ab41a44711a73ffd1861cd",
    ),
    (
        "G21 fixed LPC orders",
        "fda01ac57c3ce2d9aabd44669067d23c4ca9df2be011c67967c49d7e1b9fd4cc",
    ),
];

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

/// Uniform in [0, 1) from 53 bits.
fn unit(seed: &mut u64) -> f64 {
    lcg(seed) as f64 / (1u64 << 53) as f64
}

/// Sum of 4 uniforms, centred: roughly normal, sd about 0.58.
fn noise(seed: &mut u64) -> f64 {
    unit(seed) + unit(seed) + unit(seed) + unit(seed) - 2.0
}

use std::f64::consts::PI;

/// sin from basic operations only: reduction to [-pi, pi], then an odd polynomial (accurate to about 1e-6, which is
/// enough for a test signal; what matters is that every platform computes the same bits).
fn sin_b(x: f64) -> f64 {
    let t = x - (x / (2.0 * PI)).round() * (2.0 * PI);
    let t2 = t * t;
    let mut p = 1.0 / 6_227_020_800.0;
    p = p * -t2 + 1.0 / 39_916_800.0;
    p = p * t2 - 1.0 / 362_880.0;
    p = p * t2 + 1.0 / 5040.0;
    p = p * t2 - 1.0 / 120.0;
    p = p * t2 + 1.0 / 6.0;
    t - t * t2 * p
}

/// An AR(p) process of integers: p resonant poles near the unit circle (product of second-order sections, plus a
/// real pole for odd p), driven by small noise.
fn ar(order: usize, n: usize, seed: &mut u64, gain: f64) -> Vec<i64> {
    // coefficients of prod (1 - 2 r cos w z^-1 + r^2 z^-2) (1 - r z^-1)
    let mut poly = vec![1.0f64];
    let mut k = 0;
    while poly.len() - 1 + 2 <= order {
        let r = 0.97 - 0.004 * k as f64;
        let w = 0.05 + 0.19 * k as f64;
        let c = sin_b(w + PI / 2.0);
        let sec = [1.0, -2.0 * r * c, r * r];
        let mut next = vec![0.0; poly.len() + 2];
        for (i, a) in poly.iter().enumerate() {
            for (j, b) in sec.iter().enumerate() {
                next[i + j] += a * b;
            }
        }
        poly = next;
        k += 1;
    }
    if poly.len() - 1 < order {
        let mut next = vec![0.0; poly.len() + 1];
        for (i, a) in poly.iter().enumerate() {
            next[i] += a;
            next[i + 1] -= 0.9 * a;
        }
        poly = next;
    }
    let mut y = vec![0.0f64; n + order];
    let mut out = Vec::with_capacity(n);
    for i in order..n + order {
        let mut v = gain * noise(seed);
        for j in 1..=order {
            v -= poly[j] * y[i - j];
        }
        // keep it bounded whatever the poles do
        y[i] = v.clamp(-1e6, 1e6);
        out.push(y[i].round() as i64);
    }
    out
}

/// A random walk of grid indices with steps in -s..=s.
fn walk(n: usize, seed: &mut u64, s: u64) -> Vec<i64> {
    let mut k = 0i64;
    (0..n)
        .map(|_| {
            k += (lcg(seed) % (2 * s + 1)) as i64 - s as i64;
            k
        })
        .collect()
}

fn f32x(v: f64) -> f64 {
    v as f32 as f64
}

/// Where the dense/outlier/wide-span chunks start in the corpus (after 4 tones, 3 chirps, 2 offset-grid chunks
/// and 32 AR processes).
const WIDE_SPANS: usize = 41;

/// (samples, quantum) per chunk.
fn corpus() -> Vec<(Vec<f64>, f64)> {
    let mut s = 0x0b9c_0de5_u64;
    let mut out: Vec<(Vec<f64>, f64)> = Vec::new();
    let n = 4096;
    // Tones and noise on an integer grid (counts).
    for (f, a) in [
        (0.013, 2000.0),
        (0.0711, 31.0),
        (0.25, 8000.0),
        (0.0013, 1.0e6),
    ] {
        let x = (0..n)
            .map(|i| (a * sin_b(2.0 * PI * f * i as f64) + 3.0 * noise(&mut s)).round())
            .collect();
        out.push((x, 1.0));
    }
    // Chirps on a decimal grid (three decimals, quantum 0.001: k / 1000 decodes exactly).
    for (f0, f1, a) in [(0.001, 0.2, 5.0), (0.05, 0.45, 0.4), (0.0005, 0.01, 120.0)] {
        let x = (0..n)
            .map(|i| {
                let t = i as f64 / n as f64;
                let phase = 2.0 * PI * n as f64 * (f0 * t + (f1 - f0) * t * t / 2.0);
                ((a * sin_b(phase) + 0.01 * noise(&mut s)) * 1000.0).round() / 1000.0
            })
            .collect();
        out.push((x, 0.001));
    }
    // Noise and a tone on an offset grid (an EDF-like calibration: (k + 2048) * 417 / 4095 - 208).
    for amp in [40.0, 900.0] {
        let x = (0..n)
            .map(|i| {
                let k = (amp * sin_b(0.002 * i as f64) + 25.0 * noise(&mut s)).round();
                (k + 2048.0) * 417.0 / 4095.0 - 208.0
            })
            .collect();
        out.push((x, 417.0 / 4095.0));
    }
    // AR processes of orders 1..=32 (the encoder's own order choice; integer grid), at several lengths.
    for order in 1..=32 {
        let len = [4096, 4096, 2048, 333][order % 4];
        let x = ar(order, len, &mut s, 40.0)
            .into_iter()
            .map(|k| k as f64)
            .collect();
        out.push((x, 1.0));
    }
    // Dense and wide index spans with calibrated values (table corrections): a dense walk; the same walk with one
    // outlier index (span >= 4n + 1024, the open-addressing path); distinct indices spread over a wide span.
    assert_eq!(out.len(), WIDE_SPANS);
    let cal = 0.000_688_781_737_8;
    let dense = walk(n, &mut s, 6);
    out.push((
        dense
            .iter()
            .map(|&k| k as f64 * cal * 1.000_000_7)
            .collect(),
        cal,
    ));
    let mut outlier = dense.clone();
    outlier[1777] = 900_000_000;
    out.push((
        outlier
            .iter()
            .map(|&k| k as f64 * cal * 1.000_000_7)
            .collect(),
        cal,
    ));
    // 300 distinct indices about 10^6 apart, each repeated (span far beyond 4n + 1024)
    let wide: Vec<i64> = (0..n)
        .map(|_| (lcg(&mut s) % 300) as i64 * 1_000_003 - 150_000_000)
        .collect();
    out.push((
        wide.iter().map(|&k| k as f64 * cal * 1.000_000_7).collect(),
        cal,
    ));
    // Per-sample corrections: every sample a few ulps off a calibrated step; LPC with them on a resonance.
    let x = walk(n, &mut s, 9)
        .iter()
        .map(|&k| {
            let v = k as f64 * (100.0 / 32768.0);
            if v == 0.0 {
                v
            } else {
                f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 5) as i64 - 2) as u64)
            }
        })
        .collect();
    out.push((x, 100.0 / 32768.0));
    let x = ar(6, n, &mut s, 30.0)
        .iter()
        .map(|&k| {
            let v = k as f64 * (100.0 / 32768.0);
            if v == 0.0 {
                v
            } else {
                f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 3) as i64 - 1) as u64)
            }
        })
        .collect();
    out.push((x, 100.0 / 32768.0));
    // Exceptions: sparse negative zeros on a decimal grid; sparse one-ulp flips over table corrections.
    let x = walk(n, &mut s, 40)
        .iter()
        .enumerate()
        .map(|(i, &k)| {
            let k = k.rem_euclid(200);
            if k == 0 && i % 2 == 0 {
                -0.0
            } else {
                k as f64 / 1000.0
            }
        })
        .collect();
    out.push((x, 0.001));
    let mut x: Vec<f64> = walk(n, &mut s, 6)
        .iter()
        .map(|&k| k as f64 * cal * 1.000_000_7)
        .collect();
    for (i, v) in x.iter_mut().enumerate() {
        if i % 97 == 5 && *v != 0.0 {
            *v = f64::from_bits(v.to_bits() ^ 1);
        }
    }
    out.push((x, cal));
    let x = (0..512)
        .map(|i| f64::from_bits((i as f64).to_bits() + u64::from(i % 4 < 3)))
        .collect();
    out.push((x, 1.0));
    // Float32-exact chunks: version 6's grid kind (controller decimals rounded to float32) and its sign/magnitude
    // kind (float32 noise crossing zero); an LPC-friendly float32 resonance on a grid.
    for len in [4096, 1000, 17] {
        let x = (0..len)
            .map(|i| f32x(((sin_b(0.01 * i as f64) * 1500.0).round() / 1000.0) - 0.7))
            .collect();
        out.push((x, 1.0));
        let x = (0..len)
            .map(|i| f32x(300.0 * sin_b(0.031 * i as f64) + noise(&mut s) * 0.37))
            .collect();
        out.push((x, 1.0));
    }
    let x = ar(4, n, &mut s, 20.0)
        .iter()
        .map(|&k| f32x(0.25 + k as f64 * 0.0023413))
        .collect();
    out.push((x, 1.0));
    // Float chunks: a smooth float64 signal off any grid, and random float64 bit patterns in range.
    let x = (0..n)
        .map(|i| 1.0e-3 * sin_b(0.0123 * i as f64) + 1.0e-9 * noise(&mut s))
        .collect();
    out.push((x, 1.0));
    let x = (0..2000)
        .map(|_| loop {
            let v = f64::from_bits(lcg(&mut s) << 11 | lcg(&mut s) >> 42);
            if v.is_finite() && v.abs() <= 1e9 {
                break v;
            }
        })
        .collect();
    out.push((x, 1.0));
    // Short and edge chunks.
    out.push((vec![0.0, -0.0, 1.0, -1.0e9, 1.0e9], 1.0));
    out.push((vec![42.0], 1.0));
    out
}

fn exact(d: &[f64], x: &[f64]) -> bool {
    d.len() == x.len() && d.iter().zip(x).all(|(a, b)| a.to_bits() == b.to_bits())
}

/// The LPC order of a version-5 grid chunk's index stream, if that stream uses LPC.
fn lpc_order(chunk: &[u8]) -> Option<usize> {
    let b = &chunk[..chunk.len() - 4];
    if b[0] != GRIZZBIT21_VERSION || b[1] != 1 {
        return None;
    }
    let mut pos = 2;
    while b[pos] & 0x80 != 0 {
        pos += 1;
    }
    pos += 1;
    let flags = b[pos];
    pos += 1 + if flags & 1 != 0 { 17 } else { 9 };
    let (mut len, mut shift) = (0usize, 0);
    loop {
        let v = b[pos];
        pos += 1;
        len |= ((v & 0x7f) as usize) << shift;
        shift += 7;
        if v & 0x80 == 0 {
            break;
        }
    }
    let s = &b[pos..pos + len];
    let w = (s[0] as u16) << 8 | s[1] as u16;
    (w >> 13 == 7).then_some(((w >> 4) & 31) as usize + 1)
}

static ORDERS: [usize; 32] = [
    1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
    27, 28, 29, 30, 31, 32,
];

#[test]
fn corpus_bytes_match_the_baseline() {
    let cs = corpus();
    let mut all: [Vec<u8>; 5] = Default::default();
    let mut g21_modes = BTreeSet::new();
    let mut g21_orders = BTreeSet::new();
    let mut fixed_orders = BTreeSet::new();
    let mut v6_kinds = BTreeSet::new();
    let mut g1_versions = BTreeSet::new();
    let mut table_modes = Vec::new();
    for (i, (x, q)) in cs.iter().enumerate() {
        let coded = [
            encode_with(x, *q, Format::Grizzbit1).unwrap(),
            encode_with_float32(x, *q, Format::Grizzbit1).unwrap(),
            encode_with(x, *q, Format::Grizzbit21).unwrap(),
            encode_with_float32(x, *q, Format::Grizzbit21).unwrap(),
        ];
        for (acc, e) in all.iter_mut().zip(&coded) {
            assert!(exact(&decode(e).unwrap(), x), "round trip");
            acc.extend_from_slice(e);
        }
        g1_versions.insert((coded[0][0], coded[0][1]));
        if let Some((mode, lpc)) = grizzbit21_layout(&coded[2]) {
            if (WIDE_SPANS..WIDE_SPANS + 3).contains(&i) {
                table_modes.push(mode);
            }
            g21_modes.insert(mode);
            if lpc {
                g21_orders.extend(lpc_order(&coded[2]));
            }
        }
        for e in [&coded[1], &coded[3]] {
            if e[0] == FLOAT32_VERSION {
                v6_kinds.extend(float32_layout(e).map(|l| l.0));
            }
        }
    }
    // Grizzbit 2.1 restricted to one LPC order: every order 1..=32 is written and decoded.
    for order in &ORDERS {
        let order = *order;
        let mut s = 0x5eed_0000 + order as u64;
        let x: Vec<f64> = ar(32, 4096, &mut s, 40.0)
            .into_iter()
            .map(|k| k as f64)
            .collect();
        let f = Grizzbit21Features {
            corrections: true,
            lpc: true,
            search: LpcSearch::Fixed(std::slice::from_ref(&ORDERS[order - 1])),
        };
        let e = encode_experimental(&x, 1.0, f).unwrap();
        assert!(exact(&decode(&e).unwrap(), &x), "round trip, order {order}");
        fixed_orders.extend(lpc_order(&e));
        all[4].extend_from_slice(&e);
    }
    eprintln!(
        "corpus: {} chunks; G1 (version, kind) {g1_versions:?}; G21 correction modes {g21_modes:?}, LPC orders \
         chosen {g21_orders:?}; version-6 kinds {v6_kinds:?}; fixed LPC orders written {fixed_orders:?}",
        cs.len()
    );
    // The corpus covers what it claims.
    assert_eq!(g21_modes, BTreeSet::from([0, 1, 2, 3]), "correction modes");
    // dense, one outlier (open addressing), wide distinct indices: all three use the table
    assert_eq!(
        table_modes,
        [2, 2, 2],
        "table corrections over dense and wide spans"
    );
    assert_eq!(fixed_orders, (1..=32).collect(), "fixed LPC orders");
    assert!(g21_orders.len() >= 8, "LPC orders chosen: {g21_orders:?}");
    assert_eq!(v6_kinds, BTreeSet::from([1, 3]), "version-6 kinds");
    let names = ["G1", "G1+6", "G21", "G21+6", "G21 fixed LPC orders"];
    let got: Vec<String> = all.iter().map(|b| hex(&sha256(b))).collect();
    for ((name, sha), (want_name, want)) in names.iter().zip(&got).zip(EXPECTED) {
        eprintln!("{name}: {sha}");
        assert_eq!(*name, want_name);
        assert_eq!(sha, want, "{name}: chunk bytes differ from 9361aba's");
    }
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// SHA-256 (FIPS 180-4), checked against the standard test vectors below.
fn sha256(data: &[u8]) -> [u8; 32] {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4,
        0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe,
        0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f,
        0x4a7484aa, 0x5cb0a9dc, 0x76f988da, 0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7,
        0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc,
        0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
        0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070, 0x19a4c116,
        0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7,
        0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab,
        0x5be0cd19,
    ];
    let mut msg = data.to_vec();
    let bit_len = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bit_len.to_be_bytes());
    for block in msg.as_chunks::<64>().0 {
        let mut w = [0u32; 64];
        for (i, word) in block.as_chunks::<4>().0.iter().enumerate() {
            w[i] = u32::from_be_bytes(*word);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16]
                .wrapping_add(s0)
                .wrapping_add(w[i - 7])
                .wrapping_add(s1);
        }
        let mut v = h;
        for i in 0..64 {
            let s1 = v[4].rotate_right(6) ^ v[4].rotate_right(11) ^ v[4].rotate_right(25);
            let ch = (v[4] & v[5]) ^ (!v[4] & v[6]);
            let t1 = v[7]
                .wrapping_add(s1)
                .wrapping_add(ch)
                .wrapping_add(K[i])
                .wrapping_add(w[i]);
            let s0 = v[0].rotate_right(2) ^ v[0].rotate_right(13) ^ v[0].rotate_right(22);
            let maj = (v[0] & v[1]) ^ (v[0] & v[2]) ^ (v[1] & v[2]);
            let t2 = s0.wrapping_add(maj);
            v = [
                t1.wrapping_add(t2),
                v[0],
                v[1],
                v[2],
                v[3].wrapping_add(t1),
                v[4],
                v[5],
                v[6],
            ];
        }
        for i in 0..8 {
            h[i] = h[i].wrapping_add(v[i]);
        }
    }
    let mut out = [0u8; 32];
    for (i, x) in h.iter().enumerate() {
        out[4 * i..4 * i + 4].copy_from_slice(&x.to_be_bytes());
    }
    out
}

#[test]
fn sha256_matches_the_test_vectors() {
    for (input, want) in [
        (
            &b""[..],
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        (
            &b"abc"[..],
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        (
            &b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"[..],
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
    ] {
        assert_eq!(hex(&sha256(input)), want);
    }
    assert_eq!(
        hex(&sha256(&vec![b'a'; 1_000_000])),
        "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
    );
}
