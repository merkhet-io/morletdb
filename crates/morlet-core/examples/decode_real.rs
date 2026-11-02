//! Encode/decode throughput on real samples (development measurement):
//!   cargo run --release -p morlet-core --example decode_real -- FILE.f64 QUANTUM
use morlet_core::rawcodec::{decode, encode, kind};
use std::time::Instant;

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let bytes = std::fs::read(&args[1]).unwrap();
    let q: f64 = args[2].parse().unwrap();
    let x: Vec<f64> = bytes
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    let chunks: Vec<Vec<u8>> = x.chunks(4096).map(|c| encode(c, q).unwrap()).collect();
    for (c, e) in x.chunks(4096).zip(&chunks) {
        assert_eq!(&decode(e).unwrap()[..], c);
    }
    let total: usize = chunks.iter().map(|c| c.len()).sum();
    let reps: usize = std::env::var("REPS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(50);
    let t = Instant::now();
    let mut n = 0;
    for _ in 0..reps {
        for e in &chunks {
            n += decode(e).unwrap().len();
        }
    }
    let dec = t.elapsed().as_nanos() as f64 / n as f64;
    let t = Instant::now();
    let mut m = 0;
    let enc_reps: usize = std::env::var("EREPS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(5);
    for _ in 0..enc_reps {
        for c in x.chunks(4096) {
            m += encode(c, q).unwrap().len();
        }
    }
    let enc = t.elapsed().as_nanos() as f64 / (enc_reps * x.len()) as f64;
    println!(
        "{}: kind {:?}, {:.2} bytes/sample, decode {:.2} ns/sample, encode {:.1} ns/sample ({})",
        args[1].rsplit('/').next().unwrap(),
        kind(&chunks[0]).unwrap(),
        total as f64 / x.len() as f64,
        dec,
        enc,
        m > 0
    );
}
