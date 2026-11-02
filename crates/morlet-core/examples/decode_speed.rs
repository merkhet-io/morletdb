//! Decode throughput of the raw codec on a grid-coded chunk (development measurement):
//!   cargo run --release -p morlet-core --example decode_speed
use morlet_core::rawcodec::{decode, encode};
use std::time::Instant;

fn main() {
    // a vibration-like signal on a 100/32768 grid: two tones plus pseudo-random noise, 4,096 samples
    let q = 100.0 / 32768.0;
    let mut seed = 1u64;
    let x: Vec<f64> = (0..4096)
        .map(|i| {
            seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            let noise = ((seed >> 40) as f64 / (1u64 << 24) as f64 - 0.5) * 0.4;
            let v = (i as f64 * 0.07).sin() + 0.3 * (i as f64 * 0.9).sin() + noise;
            (v / q).round() * q
        })
        .collect();
    let enc = encode(&x, q).unwrap();
    assert_eq!(decode(&enc).unwrap(), x);
    let reps = 2000;
    let t = Instant::now();
    let mut sink = 0.0;
    for _ in 0..reps {
        sink += decode(&enc).unwrap()[17];
    }
    let ns = t.elapsed().as_nanos() as f64 / (reps * x.len()) as f64;
    let t = Instant::now();
    let mut n = 0;
    for _ in 0..200 {
        n += encode(&x, q).unwrap().len();
    }
    let enc_ns = t.elapsed().as_nanos() as f64 / (200 * x.len()) as f64;
    println!("encode {:.1} ns/sample ({})", enc_ns, n > 0);
    println!(
        "{} bytes per chunk ({:.2} bytes/sample); decode {:.2} ns/sample ({})",
        enc.len(),
        enc.len() as f64 / x.len() as f64,
        ns,
        sink > 0.0
    );
}
