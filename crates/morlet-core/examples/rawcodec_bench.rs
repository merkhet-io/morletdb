//! Encodes chunk files and reports bits/sample, grid/raw chunk counts and throughput.
//! Input records: u32 LE sample count, f64 LE quantum, then the samples as f64 LE.
//!
//! Options, before the files:
//! - `--grizzbit2`: encode with Grizzbit 2 (`encode_with(.., Format::Grizzbit2)`: a version-4 pco chunk where
//!   that is strictly smaller than Grizzbit 1) instead of Grizzbit 1 (`encode`, the default). The line then
//!   also reports `"format"` and `"pco_chunks"`; without the option the output is exactly as before.
//! - `--per-chunk`: also report every chunk's format version, kind and encoded bytes, in input order, as
//!   `"chunks":[[version,"kind",bytes],...]`.
use morlet_core::rawcodec::{decode, encode_with, kind, Format, Kind};
use std::time::Instant;

fn main() {
    let mut format = Format::Grizzbit1;
    let mut per_chunk = false;
    let mut paths = Vec::new();
    for arg in std::env::args().skip(1) {
        match arg.as_str() {
            "--grizzbit2" => format = Format::Grizzbit2,
            "--per-chunk" => per_chunk = true,
            _ => paths.push(arg),
        }
    }
    for path in paths {
        let bytes = std::fs::read(&path).expect("readable input");
        let (mut pos, mut samples, mut encoded) = (0, 0usize, 0usize);
        let (mut grid, mut float, mut raw, mut pco) = (0, 0, 0, 0);
        let (mut enc_s, mut dec_s) = (0f64, 0f64);
        let mut chunks = Vec::new();
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
            let e = encode_with(&x, quantum, format).expect("valid chunk");
            enc_s += t.elapsed().as_secs_f64();
            let t = Instant::now();
            let d = decode(&e).expect("decodable chunk");
            dec_s += t.elapsed().as_secs_f64();
            assert!(
                d.len() == x.len() && d.iter().zip(&x).all(|(a, b)| a.to_bits() == b.to_bits())
            );
            let k = kind(&e).unwrap();
            match k {
                Kind::Grid => grid += 1,
                Kind::Float => float += 1,
                Kind::Raw => raw += 1,
                Kind::Pco => pco += 1,
            }
            if per_chunk {
                chunks.push(format!("[{},\"{}\",{}]", e[0], k.name(), e.len()));
            }
            samples += n;
            encoded += e.len();
        }
        let g2 = if format == Format::Grizzbit2 {
            format!(",\"format\":\"{}\",\"pco_chunks\":{pco}", format.name())
        } else {
            String::new()
        };
        let list = if per_chunk {
            format!(",\"chunks\":[{}]", chunks.join(","))
        } else {
            String::new()
        };
        println!(
            "{{\"file\":\"{path}\",\"samples\":{samples},\"bytes\":{encoded},\"bits_per_sample\":{:.4},\"grid_chunks\":{grid},\"float_chunks\":{float},\"raw_chunks\":{raw},\"encode_msamples_s\":{:.1},\"decode_msamples_s\":{:.1}{g2}{list}}}",
            8.0 * encoded as f64 / samples as f64,
            samples as f64 / enc_s / 1e6,
            samples as f64 / dec_s / 1e6
        );
    }
}
