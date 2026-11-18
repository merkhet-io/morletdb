//! Encodes chunk files and reports bits/sample, grid/raw chunk counts and throughput.
//! Input records: u32 LE sample count, f64 LE quantum, then the samples as f64 LE.
//!
//! Options, before the files:
//! - `--grizzbit21`: encode with Grizzbit 2.1 (`encode_with(.., Format::Grizzbit21)`: a version-5 chunk where
//!   that is strictly smaller than Grizzbit 1) instead of Grizzbit 1 (`encode`, the default). The line then
//!   also reports `"format"` and `"v5_chunks"`; without the option the output is exactly as before.
//! - `--float32` (study B8): encode with `encode_with_float32(.., format)`, a version-6 chunk for float32 samples
//!   where that is strictly smaller. The line then also reports `"float32":true`, `"v6_grid_chunks"` and
//!   `"v6_signmag_chunks"`; without the option the output is exactly as before.
//! - `--per-chunk`: also report every chunk's format version, kind, encoded bytes and, for a version-5 chunk,
//!   its correction mode (0 none, 1 per sample, 2 table, 3 exceptions only) and whether its index stream uses
//!   LPC (1) or not (0), in input order, as `"chunks":[[version,"kind",bytes,mode,lpc],...]`; mode and lpc
//!   are -1 for chunks of other versions.
use morlet_core::rawcodec::{
    decode, encode_with, encode_with_float32, float32_layout, grizzbit21_layout, kind, Format, Kind,
};
use std::time::Instant;

fn main() {
    let mut format = Format::Grizzbit1;
    let mut per_chunk = false;
    let mut float32 = false;
    let mut paths = Vec::new();
    for arg in std::env::args().skip(1) {
        match arg.as_str() {
            "--grizzbit21" => format = Format::Grizzbit21,
            "--per-chunk" => per_chunk = true,
            "--float32" => float32 = true,
            _ => paths.push(arg),
        }
    }
    for path in paths {
        let bytes = std::fs::read(&path).expect("readable input");
        let (mut pos, mut samples, mut encoded) = (0, 0usize, 0usize);
        let (mut grid, mut float, mut raw, mut v5) = (0, 0, 0, 0);
        let (mut v6_grid, mut v6_signmag) = (0usize, 0usize);
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
            let e = if float32 {
                encode_with_float32(&x, quantum, format)
            } else {
                encode_with(&x, quantum, format)
            }
            .expect("valid chunk");
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
            }
            match float32_layout(&e) {
                Some((1, ..)) => v6_grid += 1,
                Some(_) => v6_signmag += 1,
                None => {}
            }
            let layout = grizzbit21_layout(&e);
            v5 += layout.is_some() as usize;
            if per_chunk {
                let (mode, lpc) = layout.map_or((-1, -1), |(m, l)| (m as i32, l as i32));
                chunks.push(format!(
                    "[{},\"{}\",{},{mode},{lpc}]",
                    e[0],
                    k.name(),
                    e.len()
                ));
            }
            samples += n;
            encoded += e.len();
        }
        let g21 = if format == Format::Grizzbit21 {
            format!(",\"format\":\"grizzbit21\",\"v5_chunks\":{v5}")
        } else {
            String::new()
        };
        let f32s = if float32 {
            format!(
                ",\"float32\":true,\"v6_grid_chunks\":{v6_grid},\"v6_signmag_chunks\":{v6_signmag}"
            )
        } else {
            String::new()
        };
        let list = if per_chunk {
            format!(",\"chunks\":[{}]", chunks.join(","))
        } else {
            String::new()
        };
        println!(
            "{{\"file\":\"{path}\",\"samples\":{samples},\"bytes\":{encoded},\"bits_per_sample\":{:.4},\"grid_chunks\":{grid},\"float_chunks\":{float},\"raw_chunks\":{raw},\"encode_msamples_s\":{:.1},\"decode_msamples_s\":{:.1}{g21}{f32s}{list}}}",
            8.0 * encoded as f64 / samples as f64,
            samples as f64 / enc_s / 1e6,
            samples as f64 / dec_s / 1e6
        );
    }
}
