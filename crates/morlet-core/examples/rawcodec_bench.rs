//! Encodes chunk files and reports bits/sample, grid/raw chunk counts and throughput.
//! Input records: u32 LE sample count, f64 LE quantum, then the samples as f64 LE.
use morlet_core::rawcodec::{decode, encode, kind, Kind};
use std::time::Instant;

fn main() {
    for path in std::env::args().skip(1) {
        let bytes = std::fs::read(&path).expect("readable input");
        let (mut pos, mut samples, mut encoded) = (0, 0usize, 0usize);
        let (mut grid, mut float, mut raw) = (0, 0, 0);
        let (mut enc_s, mut dec_s) = (0f64, 0f64);
        while pos < bytes.len() {
            let n = u32::from_le_bytes(bytes[pos..pos + 4].try_into().unwrap()) as usize;
            let quantum = f64::from_le_bytes(bytes[pos + 4..pos + 12].try_into().unwrap());
            pos += 12;
            let x: Vec<f64> = bytes[pos..pos + 8 * n]
                .chunks_exact(8)
                .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
                .collect();
            pos += 8 * n;
            let t = Instant::now();
            let e = encode(&x, quantum).expect("valid chunk");
            enc_s += t.elapsed().as_secs_f64();
            let t = Instant::now();
            let d = decode(&e).expect("decodable chunk");
            dec_s += t.elapsed().as_secs_f64();
            assert!(d.iter().zip(&x).all(|(a, b)| a.to_bits() == b.to_bits()));
            match kind(&e).unwrap() {
                Kind::Grid => grid += 1,
                Kind::Float => float += 1,
                Kind::Raw => raw += 1,
            }
            samples += n;
            encoded += e.len();
        }
        println!(
            "{{\"file\":\"{path}\",\"samples\":{samples},\"bytes\":{encoded},\"bits_per_sample\":{:.4},\"grid_chunks\":{grid},\"float_chunks\":{float},\"raw_chunks\":{raw},\"encode_msamples_s\":{:.1},\"decode_msamples_s\":{:.1}}}",
            8.0 * encoded as f64 / samples as f64,
            samples as f64 / enc_s / 1e6,
            samples as f64 / dec_s / 1e6
        );
    }
}
