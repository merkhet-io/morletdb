//! Study B5 harness (PROTOCOL.md sections 3 and 4A). Input: the codec bench example's chunk file (u32 LE sample
//! count, f64 LE quantum, the samples as f64 LE, repeated).
//!
//! `b5codec storage FILE`: every chunk coded as G1 (`encode_with(.., Format::Grizzbit1)`, i.e. `encode`) and as G2
//! (`encode_with(.., Format::Grizzbit2)`); both decoded and compared with the input by `to_bits` (a mismatch
//! aborts); per chunk `[g1 version, g1 kind, g1 bytes, g2 version, g2 kind, g2 bytes, g2 bytes == g1 bytes]`.
//!
//! `b5codec speed PASSES FILE`: single thread; per pass, per format, all chunks encoded, then all of that format's
//! chunks decoded; each figure is total samples / total time over all chunks (timed per chunk, as the bench
//! example does). Reports every pass and the best (highest) of the passes per figure.
use grizzbit::{decode, encode_with, kind, Format};
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
    let (mut samples, mut b1, mut b2) = (0usize, 0usize, 0usize);
    for (x, q) in chunks(path) {
        let e1 = encode_with(&x, q, Format::Grizzbit1).expect("valid chunk");
        let e2 = encode_with(&x, q, Format::Grizzbit2).expect("valid chunk");
        assert!(exact(&decode(&e1).expect("G1 decodes"), &x), "G1 round trip");
        assert!(exact(&decode(&e2).expect("G2 decodes"), &x), "G2 round trip");
        let (k1, k2) = (kind(&e1).unwrap().name(), kind(&e2).unwrap().name());
        rows.push(format!(
            "[{},\"{k1}\",{},{},\"{k2}\",{},{}]",
            e1[0],
            e1.len(),
            e2[0],
            e2.len(),
            u8::from(e1 == e2)
        ));
        samples += x.len();
        b1 += e1.len();
        b2 += e2.len();
    }
    println!(
        "{{\"file\":\"{path}\",\"samples\":{samples},\"g1_bytes\":{b1},\"g2_bytes\":{b2},\"exact\":true,\"chunks\":[{}]}}",
        rows.join(",")
    );
}

fn speed(passes: usize, path: &str) {
    let cs = chunks(path);
    let n: usize = cs.iter().map(|c| c.0.len()).sum();
    let mut out = Vec::new();
    for (label, format) in [("G1", Format::Grizzbit1), ("G2", Format::Grizzbit2)] {
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
    println!("{{\"file\":\"{path}\",\"samples\":{n},\"passes\":{passes},{}}}", out.join(","));
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args.first().map(String::as_str) {
        Some("storage") => storage(&args[1]),
        Some("speed") => speed(args[1].parse().expect("passes"), &args[2]),
        _ => panic!("usage: b5codec storage FILE | b5codec speed PASSES FILE"),
    }
}
