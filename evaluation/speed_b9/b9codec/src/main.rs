//! Study B9 harness (PROTOCOL.md section 3), based on B8's `b8codec`. Input: the codec bench example's chunk file
//! (u32 LE sample count, f64 LE quantum, the samples as f64 LE, repeated; one record per stored chunk).
//!
//! Configurations, in this fixed order: G1 = `encode_with(.., Grizzbit1)`, G1+6 = `encode_with_float32(..,
//! Grizzbit1)` (the default build), G21 = `encode_with(.., Grizzbit21)`, G21+6 = `encode_with_float32(..,
//! Grizzbit21)`. Only the grizzbit crate differs between the arms; this source is the same in each.
//!
//! `b9codec speed PASSES FILE`: single thread. Per pass and configuration, every chunk is encoded, then every chunk
//! decoded, each chunk timed on its own. Each decode is compared with the input by `to_bits` outside the timed
//! region; a mismatch aborts. One JSON line per pass: per configuration the encode and decode time (sum of the
//! per-chunk times, seconds), the speeds (samples / that time, M samples/s, unrounded), the chunk-bytes total and
//! the SHA-256 of the concatenated chunk bytes.
//!
//! `b9codec hash FILE`: the four configurations' SHA-256 of the concatenated chunk bytes (every chunk is still
//! decoded and compared by `to_bits`). `b9codec chunks FILE`: per configuration, the first 8 bytes of each chunk's
//! SHA-256 (to count differing chunks between arms). `b9codec selftest`: SHA-256 against test vectors.
use grizzbit::{decode, encode_with, encode_with_float32, Format};
use std::io::{BufReader, Read};
use std::time::Instant;

const CONFIGS: [(&str, Format, bool); 4] = [
    ("G1", Format::Grizzbit1, false),
    ("G1+6", Format::Grizzbit1, true),
    ("G21", Format::Grizzbit21, false),
    ("G21+6", Format::Grizzbit21, true),
];

fn chunks(path: &str) -> Vec<(Vec<f64>, f64)> {
    let mut r =
        BufReader::with_capacity(1 << 20, std::fs::File::open(path).expect("readable input"));
    let mut out = Vec::new();
    let mut head = [0u8; 12];
    loop {
        match r.read_exact(&mut head) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => break,
            Err(e) => panic!("read: {e}"),
        }
        let n = u32::from_le_bytes(head[..4].try_into().unwrap()) as usize;
        let q = f64::from_le_bytes(head[4..].try_into().unwrap());
        let mut b = vec![0u8; 8 * n];
        r.read_exact(&mut b).expect("whole chunk");
        let x = b
            .as_chunks::<8>()
            .0
            .iter()
            .map(|b| f64::from_le_bytes(*b))
            .collect();
        out.push((x, q));
    }
    out
}

fn exact(d: &[f64], x: &[f64]) -> bool {
    d.len() == x.len() && d.iter().zip(x).all(|(a, b)| a.to_bits() == b.to_bits())
}

fn enc(x: &[f64], q: f64, format: Format, float32: bool) -> Vec<u8> {
    if float32 {
        encode_with_float32(x, q, format)
    } else {
        encode_with(x, q, format)
    }
    .expect("valid chunk")
}

/// Every chunk of `cs` in one configuration, checked by `to_bits`.
fn coded(cs: &[(Vec<f64>, f64)], format: Format, float32: bool) -> Vec<Vec<u8>> {
    cs.iter()
        .map(|(x, q)| {
            let e = enc(x, *q, format, float32);
            assert!(exact(&decode(&e).expect("decodes"), x), "round trip");
            e
        })
        .collect()
}

fn platform() -> String {
    #[cfg(target_arch = "x86_64")]
    let isa = format!(
        ",\"x86_avx2\":{},\"x86_sse2\":{}",
        std::arch::is_x86_feature_detected!("avx2"),
        std::arch::is_x86_feature_detected!("sse2")
    );
    #[cfg(not(target_arch = "x86_64"))]
    let isa = String::new();
    format!(
        "\"arch\":\"{}\",\"os\":\"{}\"{isa}",
        std::env::consts::ARCH,
        std::env::consts::OS
    )
}

fn concat_hash(es: &[Vec<u8>]) -> (usize, String) {
    let all: Vec<u8> = es.concat();
    (all.len(), hex(&sha256(&all)))
}

fn speed(passes: usize, path: &str) {
    let cs = chunks(path);
    let n: usize = cs.iter().map(|c| c.0.len()).sum();
    for pass in 0..passes {
        let mut out = Vec::new();
        for (label, format, float32) in CONFIGS {
            let mut te = 0f64;
            let mut encoded = Vec::with_capacity(cs.len());
            for (x, q) in &cs {
                let t = Instant::now();
                let e = if float32 {
                    encode_with_float32(x, *q, format)
                } else {
                    encode_with(x, *q, format)
                };
                te += t.elapsed().as_secs_f64();
                encoded.push(e.expect("valid chunk"));
            }
            let mut td = 0f64;
            for (e, (x, _)) in encoded.iter().zip(&cs) {
                let t = Instant::now();
                let d = decode(e);
                td += t.elapsed().as_secs_f64();
                assert!(exact(&d.expect("decodable chunk"), x), "round trip");
            }
            let (bytes, sha) = concat_hash(&encoded);
            out.push(format!(
                "\"{label}\":{{\"encode_s\":{te:e},\"decode_s\":{td:e},\"encode_msamples_s\":{},\"decode_msamples_s\":{},\"bytes\":{bytes},\"sha256\":\"{sha}\"}}",
                n as f64 / te / 1e6,
                n as f64 / td / 1e6
            ));
        }
        println!(
            "{{\"file\":\"{path}\",\"samples\":{n},\"chunks\":{},\"pass\":{pass},{},{}}}",
            cs.len(),
            platform(),
            out.join(",")
        );
    }
}

fn hash(path: &str) {
    let cs = chunks(path);
    let n: usize = cs.iter().map(|c| c.0.len()).sum();
    let mut out = Vec::new();
    for (label, format, float32) in CONFIGS {
        let (bytes, sha) = concat_hash(&coded(&cs, format, float32));
        out.push(format!(
            "\"{label}\":{{\"bytes\":{bytes},\"sha256\":\"{sha}\"}}"
        ));
    }
    println!(
        "{{\"file\":\"{path}\",\"samples\":{n},\"chunks\":{},{},{}}}",
        cs.len(),
        platform(),
        out.join(",")
    );
}

fn chunk_digests(path: &str) {
    let cs = chunks(path);
    let mut out = Vec::new();
    for (label, format, float32) in CONFIGS {
        let d: Vec<String> = coded(&cs, format, float32)
            .iter()
            .map(|e| format!("\"{}\"", &hex(&sha256(e))[..16]))
            .collect();
        out.push(format!("\"{label}\":[{}]", d.join(",")));
    }
    println!(
        "{{\"file\":\"{path}\",\"chunks\":{},{}}}",
        cs.len(),
        out.join(",")
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
        Some("speed") => speed(args[1].parse().expect("passes"), &args[2]),
        Some("hash") => hash(&args[1]),
        Some("chunks") => chunk_digests(&args[1]),
        Some("selftest") => selftest(),
        _ => panic!("usage: b9codec speed PASSES FILE | hash FILE | chunks FILE | selftest"),
    }
}
