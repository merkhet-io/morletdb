//! Study B6 harness (PROTOCOL.md sections 3, 4A and 4B). Input: the codec bench example's chunk file (u32 LE sample
//! count, f64 LE quantum, the samples as f64 LE, repeated; one record per stored chunk).
//!
//! `b6codec storage FILE`: every chunk coded as G1 (`encode_with(.., Format::Grizzbit1)`, i.e. `encode`) and as G21
//! (`encode_with(.., Format::Grizzbit21)`); both decoded and compared with the input by `to_bits` (a mismatch aborts);
//! per chunk `[g1 version, g1 kind, g1 bytes, g21 version, g21 kind, g21 bytes, g21 bytes == g1 bytes, v5 correction
//! mode, v5 lpc]` (the last two -1 for a chunk that is not version 5).
//!
//! `b6codec speed PASSES FILE`: single thread; per pass, per format, all chunks encoded, then all of that format's
//! chunks decoded; each figure is total samples / total time over all chunks (timed per chunk, as the bench example
//! does). Reports every pass and the best (highest) of the passes per figure.
//!
//! `b6codec hash FILE`: section 4B. Every chunk coded as G21, decoded and compared by `to_bits`; reports the SHA-256 of
//! the input's concatenated G21 chunk bytes, their total length, the number of version-5 chunks, and one pass of
//! encode and decode speed (for the edge box; no criterion). SHA-256 is implemented here (FIPS 180-4) so the program
//! has no dependency but the grizzbit crate and builds offline in any container; `b6codec selftest` checks it
//! against the standard test vectors.
//!
//! `b6codec dump FILE OUT`: writes the concatenated G21 chunk bytes to OUT (for cross-checking `hash` in tests).
use grizzbit::{decode, encode_with, grizzbit21_layout, kind, Format};
use std::time::Instant;

fn chunks(path: &str) -> Vec<(Vec<f64>, f64)> {
    let bytes = std::fs::read(path).expect("readable input");
    let (mut pos, mut out) = (0, Vec::new());
    while pos < bytes.len() {
        let n = u32::from_le_bytes(bytes[pos..pos + 4].try_into().unwrap()) as usize;
        let q = f64::from_le_bytes(bytes[pos + 4..pos + 12].try_into().unwrap());
        pos += 12;
        let x = bytes[pos..pos + 8 * n]
            .chunks_exact(8)
            .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
            .collect();
        pos += 8 * n;
        out.push((x, q));
    }
    out
}

fn exact(d: &[f64], x: &[f64]) -> bool {
    d.len() == x.len() && d.iter().zip(x).all(|(a, b)| a.to_bits() == b.to_bits())
}

fn storage(path: &str) {
    let mut rows = Vec::new();
    let (mut samples, mut b1, mut b21) = (0usize, 0usize, 0usize);
    for (x, q) in chunks(path) {
        let e1 = encode_with(&x, q, Format::Grizzbit1).expect("valid chunk");
        let e2 = encode_with(&x, q, Format::Grizzbit21).expect("valid chunk");
        assert!(exact(&decode(&e1).expect("G1 decodes"), &x), "G1 round trip");
        assert!(exact(&decode(&e2).expect("G21 decodes"), &x), "G21 round trip");
        let (k1, k2) = (kind(&e1).unwrap().name(), kind(&e2).unwrap().name());
        let (mode, lpc) = grizzbit21_layout(&e2).map_or((-1, -1), |(m, l)| (m as i32, l as i32));
        rows.push(format!(
            "[{},\"{k1}\",{},{},\"{k2}\",{},{},{mode},{lpc}]",
            e1[0],
            e1.len(),
            e2[0],
            e2.len(),
            u8::from(e1 == e2)
        ));
        samples += x.len();
        b1 += e1.len();
        b21 += e2.len();
    }
    println!(
        "{{\"file\":\"{path}\",\"samples\":{samples},\"g1_bytes\":{b1},\"g21_bytes\":{b21},\"exact\":true,\"chunks\":[{}]}}",
        rows.join(",")
    );
}

fn speed(passes: usize, path: &str) {
    let cs = chunks(path);
    let n: usize = cs.iter().map(|c| c.0.len()).sum();
    let mut out = Vec::new();
    for (label, format) in [("G1", Format::Grizzbit1), ("G21", Format::Grizzbit21)] {
        let (mut enc, mut dec) = (Vec::new(), Vec::new());
        for _ in 0..passes {
            let (mut te, mut td) = (0f64, 0f64);
            let mut encoded = Vec::with_capacity(cs.len());
            for (x, q) in &cs {
                let t = Instant::now();
                let e = encode_with(x, *q, format).expect("valid chunk");
                te += t.elapsed().as_secs_f64();
                encoded.push(e);
            }
            for (e, (x, _)) in encoded.iter().zip(&cs) {
                let t = Instant::now();
                let d = decode(e).expect("decodable chunk");
                td += t.elapsed().as_secs_f64();
                assert!(exact(&d, x), "round trip");
            }
            enc.push(n as f64 / te / 1e6);
            dec.push(n as f64 / td / 1e6);
        }
        let best = |v: &[f64]| v.iter().cloned().fold(0f64, f64::max);
        let list = |v: &[f64]| v.iter().map(|s| format!("{s:.2}")).collect::<Vec<_>>().join(",");
        out.push(format!(
            "\"{label}\":{{\"encode_msamples_s\":{:.2},\"decode_msamples_s\":{:.2},\"encode_passes\":[{}],\"decode_passes\":[{}]}}",
            best(&enc),
            best(&dec),
            list(&enc),
            list(&dec)
        ));
    }
    println!(
        "{{\"file\":\"{path}\",\"samples\":{n},\"passes\":{passes},{}}}",
        out.join(",")
    );
}

/// The concatenated G21 chunk bytes of every chunk (each decoded and compared by `to_bits`), with timings.
fn encode_all(path: &str) -> (Vec<u8>, usize, usize, f64, f64) {
    let cs = chunks(path);
    let n: usize = cs.iter().map(|c| c.0.len()).sum();
    let (mut te, mut td, mut v5) = (0f64, 0f64, 0usize);
    let mut all = Vec::new();
    for (x, q) in &cs {
        let t = Instant::now();
        let e = encode_with(x, *q, Format::Grizzbit21).expect("valid chunk");
        te += t.elapsed().as_secs_f64();
        let t = Instant::now();
        let d = decode(&e).expect("decodable chunk");
        td += t.elapsed().as_secs_f64();
        assert!(exact(&d, x), "round trip");
        v5 += usize::from(e[0] == grizzbit::GRIZZBIT21_VERSION);
        all.extend_from_slice(&e);
    }
    (all, n, v5, te, td)
}

fn hash(path: &str) {
    let (all, n, v5, te, td) = encode_all(path);
    println!(
        "{{\"file\":\"{path}\",\"samples\":{n},\"bytes\":{},\"v5_chunks\":{v5},\"sha256\":\"{}\",\"exact\":true,\"encode_msamples_s\":{:.2},\"decode_msamples_s\":{:.2},\"arch\":\"{}\",\"os\":\"{}\"}}",
        all.len(),
        hex(&sha256(&all)),
        n as f64 / te / 1e6,
        n as f64 / td / 1e6,
        std::env::consts::ARCH,
        std::env::consts::OS
    );
}

fn hex(b: &[u8]) -> String {
    b.iter().map(|x| format!("{x:02x}")).collect()
}

/// SHA-256 (FIPS 180-4).
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
    for block in msg.chunks_exact(64) {
        let mut w = [0u32; 64];
        for (i, word) in block.chunks_exact(4).enumerate() {
            w[i] = u32::from_be_bytes(word.try_into().unwrap());
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

fn selftest() {
    let cases: [(&[u8], &str); 3] = [
        (
            b"",
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        ),
        (
            b"abc",
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
        ),
        (
            b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq",
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1",
        ),
    ];
    for (input, want) in cases {
        assert_eq!(hex(&sha256(input)), want);
    }
    let million = vec![b'a'; 1_000_000];
    assert_eq!(
        hex(&sha256(&million)),
        "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
    );
    println!("{{\"selftest\":\"ok\"}}");
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("storage") => storage(&args[1]),
        Some("speed") => speed(args[1].parse().expect("passes"), &args[2]),
        Some("hash") => hash(&args[1]),
        Some("dump") => std::fs::write(&args[2], encode_all(&args[1]).0).expect("writable output"),
        Some("selftest") => selftest(),
        _ => panic!(
            "usage: b6codec storage FILE | speed PASSES FILE | hash FILE | dump FILE OUT | selftest"
        ),
    }
}
