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
//!
//! Profiling aids (#394; DEVELOPMENT ONLY, nothing here is a measurement to quote):
//!
//!   g21_bench prof decode|encode ARM FILE QUANTUM CHUNK SECONDS
//!     encodes every chunk once with ARM (g1, corr, lpc, g21, or f32: `encode_with_float32` over 2.1), checks it decodes bit for bit, then only decodes
//!     (or only encodes) the same chunks in a loop for about SECONDS, so a sampling profiler (`sample PID 15`)
//!     sees a steady state. Prints the pid first and the rate of each pass to stderr.
//!   g21_bench phases FILE QUANTUM CHUNK REPS
//!     a differential phase breakdown with the public API only: decode time per sample of each arm (best of
//!     REPS), plus the CRC-32C pass and the `validate` pass every decode does, and each arm's chunk layouts.
use grizzbit::{
    decode, encode, encode_experimental, grizzbit21_layout, Grizzbit21Features, LpcSearch,
};
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

/// The LPC order-selection strategies compared in `strategies` mode (both ideas on).
const STRATEGIES: [&str; 8] = [
    "around",
    "estimated",
    "est+max",
    "fixed-8-16-32",
    "fixed-12-32",
    "fixed-32",
    "skip-20",
    "skip-50",
];

fn strategy(name: &str) -> Option<LpcSearch> {
    Some(match name {
        "around" => LpcSearch::Around,
        "estimated" => LpcSearch::Estimated,
        "est+max" => LpcSearch::EstimatedAndMax,
        "fixed-8-16-32" => LpcSearch::Fixed(&[8, 16, 32]),
        "fixed-12-32" => LpcSearch::Fixed(&[12, 32]),
        "fixed-32" => LpcSearch::Fixed(&[32]),
        "skip-20" => LpcSearch::EstimatedSkip(20),
        "skip-50" => LpcSearch::EstimatedSkip(50),
        _ => return None,
    })
}

fn arm(
    name: &str,
    chunks: &[&[f64]],
    q: f64,
    reps: usize,
    g1: Option<&[Vec<u8>]>,
) -> (String, Vec<Vec<u8>>) {
    let features = |corrections, lpc, search| Grizzbit21Features {
        corrections,
        lpc,
        search,
    };
    let search = strategy(name);
    let enc_one = |c: &[f64]| -> Vec<u8> {
        match name {
            "g1" => encode(c, q).unwrap(),
            "corr" => encode_experimental(c, q, features(true, false, LpcSearch::DEFAULT)).unwrap(),
            "lpc" => encode_experimental(c, q, features(false, true, LpcSearch::DEFAULT)).unwrap(),
            "g21" => grizzbit::encode_with(c, q, grizzbit::Format::Grizzbit21).unwrap(),
            _ => encode_experimental(c, q, features(true, true, search.unwrap())).unwrap(),
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
    if a[1] == "prof" {
        prof(&a[2..]);
        return;
    }
    if a[1] == "phases" {
        phases(&a[2..]);
        return;
    }
    if a[1] == "loop" {
        // Profiling aid: g21_bench loop FILE QUANTUM CHUNK PASSES encodes with Grizzbit 2.1 only.
        let x = read_series(&a[2]);
        let q = a[3].parse::<f64>().unwrap();
        let size = a[4].parse::<usize>().unwrap();
        let mut s = 0usize;
        for _ in 0..a[5].parse::<usize>().unwrap() {
            for c in x.chunks(size) {
                s += grizzbit::encode_with(c, q, grizzbit::Format::Grizzbit21)
                    .unwrap()
                    .len();
            }
        }
        println!("{s}");
        return;
    }
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
    let arms: Vec<&str> = if std::env::var("G21_STRATEGIES").is_ok() {
        STRATEGIES.to_vec()
    } else {
        vec!["corr", "lpc", "g21"]
    };
    for name in arms {
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

fn encode_arm(name: &str, c: &[f64], q: f64) -> Vec<u8> {
    let f = |corrections, lpc| Grizzbit21Features {
        corrections,
        lpc,
        search: LpcSearch::DEFAULT,
    };
    match name {
        "g1" => encode(c, q).unwrap(),
        "corr" => encode_experimental(c, q, f(true, false)).unwrap(),
        "lpc" => encode_experimental(c, q, f(false, true)).unwrap(),
        "g21" => grizzbit::encode_with(c, q, grizzbit::Format::Grizzbit21).unwrap(),
        "f32" => grizzbit::encode_with_float32(c, q, grizzbit::Format::Grizzbit21).unwrap(),
        _ => panic!("arm must be g1, corr, lpc, g21 or f32"),
    }
}

fn prof(a: &[String]) {
    let (what, name, path) = (a[0].as_str(), a[1].as_str(), &a[2]);
    let q = a[3].parse::<f64>().unwrap();
    let size = a[4].parse::<usize>().unwrap();
    let secs = a[5].parse::<f64>().unwrap();
    let x = read_series(path);
    let chunks: Vec<&[f64]> = x.chunks(size).collect();
    let enc: Vec<Vec<u8>> = chunks.iter().map(|c| encode_arm(name, c, q)).collect();
    for (e, c) in enc.iter().zip(&chunks) {
        assert!(same(&decode(e).unwrap(), c), "does not decode bit for bit");
    }
    let mut versions = [0usize; 8];
    for e in &enc {
        versions[e[0] as usize & 7] += 1;
    }
    eprintln!("pid {}", std::process::id());
    eprintln!("chunks by format version (index = version): {versions:?}");
    let start = Instant::now();
    let mut passes = 0usize;
    let mut sink = 0usize;
    while start.elapsed().as_secs_f64() < secs {
        let t = Instant::now();
        if what == "decode" {
            for e in &enc {
                sink += decode(std::hint::black_box(e)).unwrap().len();
            }
        } else {
            for c in &chunks {
                sink += encode_arm(name, std::hint::black_box(c), q).len();
            }
        }
        passes += 1;
        if passes.is_multiple_of(20) {
            eprintln!(
                "pass {passes}: {:.2} M samples/s",
                x.len() as f64 / t.elapsed().as_secs_f64() / 1e6
            );
        }
    }
    let total = start.elapsed().as_secs_f64();
    println!(
        "{what} {name}: {passes} passes, {:.2} M samples/s mean (sink {sink})",
        (passes * x.len()) as f64 / total / 1e6
    );
}

/// Best-of-`reps` nanoseconds per sample of `f` over `n` samples.
fn ns_per_sample(n: usize, reps: usize, mut f: impl FnMut() -> usize) -> f64 {
    let mut best = f64::MAX;
    for _ in 0..reps {
        let t = Instant::now();
        std::hint::black_box(f());
        best = best.min(t.elapsed().as_secs_f64());
    }
    best * 1e9 / n as f64
}

fn phases(a: &[String]) {
    let path = &a[0];
    let q = a[1].parse::<f64>().unwrap();
    let size = a[2].parse::<usize>().unwrap();
    let reps = a[3].parse::<usize>().unwrap();
    let x = read_series(path);
    let n = x.len();
    let chunks: Vec<&[f64]> = x.chunks(size).collect();
    let mut rows = Vec::new();
    for name in ["g1", "corr", "lpc", "g21"] {
        let enc: Vec<Vec<u8>> = chunks.iter().map(|c| encode_arm(name, c, q)).collect();
        let mut layout = [0usize; 4];
        let mut lpc = 0usize;
        for (e, c) in enc.iter().zip(&chunks) {
            assert!(same(&decode(e).unwrap(), c));
            if let Some((mode, l)) = grizzbit21_layout(e) {
                layout[mode as usize] += 1;
                lpc += l as usize;
            }
        }
        let bytes: usize = enc.iter().map(Vec::len).sum();
        let dec = ns_per_sample(n, reps, || {
            enc.iter().map(|e| decode(e).unwrap().len()).sum()
        });
        let crc = ns_per_sample(n, reps, || {
            enc.iter().map(|e| grizzbit::crc32c(e) as usize).sum()
        });
        let enc_ns = ns_per_sample(n, reps.min(3), || {
            chunks.iter().map(|c| encode_arm(name, c, q).len()).sum()
        });
        rows.push(format!(
            "{name:>5}: {:.3} bits/sample, decode {dec:.2} ns/sample (crc {crc:.2}), encode {enc_ns:.1} ns/sample, \
             v5 layouts none/per-sample/table/exceptions {}/{}/{}/{}, LPC {lpc}",
            8.0 * bytes as f64 / n as f64,
            layout[0],
            layout[1],
            layout[2],
            layout[3],
        ));
    }
    let val = ns_per_sample(n, reps, || {
        chunks
            .iter()
            .map(|c| grizzbit::validate(c).is_ok() as usize)
            .sum()
    });
    let copy = ns_per_sample(n, reps, || chunks.iter().map(|c| c.to_vec().len()).sum());
    println!("{path}: {n} samples, {} chunks of {size}", chunks.len());
    for r in rows {
        println!("{r}");
    }
    println!("validate {val:.2} ns/sample, Vec<f64> copy {copy:.2} ns/sample");
}
