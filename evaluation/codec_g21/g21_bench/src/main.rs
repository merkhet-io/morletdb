//! Grizzbit 2.1 development measurement (DEVELOPMENT ONLY, not a frozen study).
//!
//!   g21_bench NAME FILE QUANTUM CHUNK REPS
//!
//! FILE is little-endian float64 (`.f64`) or a text file with one number per line (`.csv`, every line that
//! parses as a number, in order; others skipped, as B4 reads the Elf files). The series is cut into chunks of
//! CHUNK samples and each chunk encoded four ways:
//!   g1    grizzbit::encode (Grizzbit 1, what MorletDB stores today)
//!   corr  Grizzbit 2.1 with corrections once per grid index / sparse exceptions only
//!   lpc   Grizzbit 2.1 with per-chunk LPC only
//!   g21   Grizzbit 2.1 with both (grizzbit::encode_with(.., Format::Grizzbit21))
//! Every chunk of every arm is decoded and compared bit for bit (a mismatch aborts), and no 2.1 chunk may be
//! larger than its Grizzbit 1 chunk. Sizes include every chunk's header and CRC-32C. Speed: single thread,
//! best of REPS passes over all chunks (encode, then decode). One JSON line on stdout.
use grizzbit::{decode, encode, encode_experimental, grizzbit21_layout, Grizzbit21Features};
use std::time::Instant;

fn read_series(path: &str) -> Vec<f64> {
    if path.ends_with(".csv") {
        std::fs::read_to_string(path)
            .expect("csv")
            .lines()
            .filter_map(|l| l.trim().parse::<f64>().ok())
            .collect()
    } else {
        std::fs::read(path)
            .expect("f64 file")
            .chunks_exact(8)
            .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
            .collect()
    }
}

fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

fn arm(
    name: &str,
    chunks: &[&[f64]],
    q: f64,
    reps: usize,
    g1: Option<&[Vec<u8>]>,
) -> (String, Vec<Vec<u8>>) {
    let enc_one = |c: &[f64]| -> Vec<u8> {
        match name {
            "g1" => encode(c, q).unwrap(),
            "corr" => encode_experimental(
                c,
                q,
                Grizzbit21Features {
                    corrections: true,
                    lpc: false,
                },
            )
            .unwrap(),
            "lpc" => encode_experimental(
                c,
                q,
                Grizzbit21Features {
                    corrections: false,
                    lpc: true,
                },
            )
            .unwrap(),
            _ => grizzbit::encode_with(c, q, grizzbit::Format::Grizzbit21).unwrap(),
        }
    };
    let enc: Vec<Vec<u8>> = chunks.iter().map(|c| enc_one(c)).collect();
    let n: usize = chunks.iter().map(|c| c.len()).sum();
    let bytes: usize = enc.iter().map(Vec::len).sum();
    let mut v5 = 0usize;
    let mut fix_modes = [0usize; 4];
    let mut lpc = 0usize;
    let mut v5_samples = 0usize;
    for (i, (e, c)) in enc.iter().zip(chunks).enumerate() {
        assert!(
            same(&decode(e).expect("decodes"), c),
            "{name}: chunk {i} does not decode bit for bit"
        );
        if let Some(g1) = g1 {
            assert!(
                e.len() <= g1[i].len(),
                "{name}: chunk {i} larger than Grizzbit 1"
            );
        }
        if let Some((mode, uses_lpc)) = grizzbit21_layout(e) {
            v5 += 1;
            v5_samples += c.len();
            fix_modes[mode as usize] += 1;
            lpc += uses_lpc as usize;
        }
    }
    let (mut te, mut td) = (f64::MAX, f64::MAX);
    for _ in 0..reps {
        let t = Instant::now();
        let mut s = 0usize;
        for c in chunks {
            s += enc_one(c).len();
        }
        te = te.min(t.elapsed().as_secs_f64());
        assert_eq!(s, bytes);
        let t = Instant::now();
        let mut m = 0usize;
        for e in &enc {
            m += decode(e).unwrap().len();
        }
        td = td.min(t.elapsed().as_secs_f64());
        assert_eq!(m, n);
    }
    let row = format!(
        "\"{name}\":{{\"bytes\":{bytes},\"bits_per_sample\":{:.4},\"v5_chunks\":{v5},\"v5_samples\":{v5_samples},\"fix_mode\":{{\"none\":{},\"per_sample\":{},\"table\":{},\"exceptions\":{}}},\"lpc_chunks\":{lpc},\"enc_msps\":{:.2},\"dec_msps\":{:.2}}}",
        8.0 * bytes as f64 / n as f64,
        fix_modes[0],
        fix_modes[1],
        fix_modes[2],
        fix_modes[3],
        n as f64 / te / 1e6,
        n as f64 / td / 1e6,
    );
    (row, enc)
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    let (name, path, q, size, reps) = (
        &a[1],
        &a[2],
        a[3].parse::<f64>().unwrap(),
        a[4].parse::<usize>().unwrap(),
        a[5].parse::<usize>().unwrap(),
    );
    let x = read_series(path);
    let chunks: Vec<&[f64]> = x.chunks(size).collect();
    let (g1_row, g1) = arm("g1", &chunks, q, reps, None);
    let mut rows = vec![g1_row];
    for name in ["corr", "lpc", "g21"] {
        rows.push(arm(name, &chunks, q, reps, Some(&g1)).0);
    }
    let kinds = g1.iter().fold([0usize; 3], |mut k, e| {
        k[match grizzbit::kind(e).unwrap() {
            grizzbit::Kind::Grid => 0,
            grizzbit::Kind::Float => 1,
            grizzbit::Kind::Raw => 2,
        }] += 1;
        k
    });
    println!(
        "{{\"name\":\"{name}\",\"n\":{},\"chunk\":{size},\"chunks\":{},\"quantum\":{q:e},\"g1_kinds\":{{\"grid\":{},\"float\":{},\"raw\":{}}},{}}}",
        x.len(),
        chunks.len(),
        kinds[0],
        kinds[1],
        kinds[2],
        rows.join(",")
    );
}
