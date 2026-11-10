//! Codec comparison T1 (development only): Grizzbit 1's side of the T.261 comparison.
//!
//!   cargo run --release -- grizzbit NAME FILE.f64 QUANTUM
//!       Grizzbit 1 as MorletDB stores it (grizzbit::encode per chunk, CRC included) at chunk sizes 4,096 and
//!       32,768: bytes, chunk kinds, decode check (bitwise), encode/decode wall time single thread, best of 3
//!       passes. One JSON line on stdout.
//!   cargo run --release -- ideas NAME FILE.k BITS
//!       Size estimates (not codecs: nothing is decoded) for ideas to improve the grid coding, on the integer
//!       grid indices k, per independent chunk of 4,096 and 32,768 samples (all state resets at a chunk start,
//!       as a MorletDB chunk must decode alone). Predictors x coders:
//!         predictors: fixed  = Grizzbit's fixed polynomial orders 0..4 (best per chunk);
//!                     lpc    = + per-chunk LPC of order 2, 4, 8, 16 or 32 (Levinson-Durbin, coefficients
//!                              quantized to 15 bits and charged, mean charged), best per chunk;
//!                     nlms   = + NLMS of order 16 or 32 on first differences, step 0.05 or 0.3 (charged);
//!                     blk    = fixed/lpc chosen per 1,024-sample block inside the chunk (side info charged);
//!         coders:     rice   = partitioned Rice as Grizzbit codes it (6-bit parameter per partition);
//!                     ac     = ideal adaptive arithmetic coding of a two-sided geometric (Laplacian) model
//!                              whose scale tracks the running mean |residual| (no table, rate 1/16).
//!       One JSON line on stdout: bits/sample per combination and how often each predictor won.
use std::time::Instant;

const REPS: usize = 3;
const SIZES: [usize; 2] = [4096, 32768];

fn read_f64(p: &str) -> Vec<f64> {
    std::fs::read(p)
        .expect("f64 file")
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect()
}

fn read_k(p: &str, bits: u32) -> Vec<i64> {
    let b = std::fs::read(p).expect("k file");
    if bits <= 16 {
        b.chunks_exact(2).map(|c| i16::from_le_bytes([c[0], c[1]]) as i64).collect()
    } else {
        b.chunks_exact(4).map(|c| i32::from_le_bytes(c.try_into().unwrap()) as i64).collect()
    }
}

fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

fn cmd_grizzbit(name: &str, path: &str, quantum: f64) {
    let x = read_f64(path);
    let n = x.len();
    let mut rows = Vec::new();
    for size in SIZES {
        let chunks: Vec<&[f64]> = x.chunks(size).collect();
        let enc: Vec<Vec<u8>> = chunks.iter().map(|c| grizzbit::encode(c, quantum).expect("encode")).collect();
        let bytes: usize = enc.iter().map(Vec::len).sum();
        let mut kinds = [0usize; 3];
        let mut fail = 0;
        for (e, c) in enc.iter().zip(&chunks) {
            kinds[match grizzbit::kind(e).expect("kind") {
                grizzbit::Kind::Grid => 0,
                grizzbit::Kind::Float => 1,
                grizzbit::Kind::Raw => 2,
            }] += 1;
            if !same(&grizzbit::decode(e).expect("decode"), c) {
                fail += 1;
            }
        }
        let (mut te, mut td) = (f64::MAX, f64::MAX);
        for _ in 0..REPS {
            let t = Instant::now();
            let mut s = 0usize;
            for c in &chunks {
                s += grizzbit::encode(c, quantum).unwrap().len();
            }
            te = te.min(t.elapsed().as_secs_f64());
            assert_eq!(s, bytes);
            let t = Instant::now();
            let mut m = 0usize;
            for e in &enc {
                m += grizzbit::decode(e).unwrap().len();
            }
            td = td.min(t.elapsed().as_secs_f64());
            assert_eq!(m, n);
        }
        rows.push(format!(
            "\"{size}\":{{\"bytes\":{bytes},\"chunks\":{},\"bits_per_sample\":{:.4},\"kinds\":{{\"grid\":{},\"float\":{},\"raw\":{}}},\"fail\":{fail},\"enc_s\":{te:.6},\"dec_s\":{td:.6},\"enc_msps\":{:.2},\"dec_msps\":{:.2}}}",
            chunks.len(),
            8.0 * bytes as f64 / n as f64,
            kinds[0], kinds[1], kinds[2],
            n as f64 / te / 1e6,
            n as f64 / td / 1e6
        ));
    }
    println!("{{\"name\":\"{name}\",\"n\":{n},\"quantum\":{quantum:e},{}}}", rows.join(","));
}

// ---------------------------------------------------------------- ideas (estimates)

fn zz(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}

fn fixed_pred(x: &[i64], i: usize, order: usize) -> i64 {
    match order.min(i) {
        0 => 0,
        1 => x[i - 1],
        2 => 2 * x[i - 1] - x[i - 2],
        3 => 3 * x[i - 1] - 3 * x[i - 2] + x[i - 3],
        _ => 4 * x[i - 1] - 6 * x[i - 2] + 4 * x[i - 3] - x[i - 4],
    }
}

/// Partitioned Rice cost in bits as Grizzbit codes it: 3+4 header bits, 6 bits per partition.
fn rice_bits(r: &[i64]) -> f64 {
    let u: Vec<u64> = r.iter().map(|&v| zz(v)).collect();
    let n = u.len();
    let max_p = 8u32.min(usize::BITS - 1 - n.leading_zeros());
    let mut best = f64::MAX;
    for p in 0..=max_p {
        let parts = 1usize << p;
        let mut cost = 7.0;
        for j in 0..parts {
            let (a, b) = (j * n / parts, (j + 1) * n / parts);
            let s = &u[a..b];
            // The best parameter is within one of log2(mean): try those three.
            let mean = s.iter().map(|&v| v as f64).sum::<f64>() / s.len().max(1) as f64;
            let k0 = if mean >= 1.0 { mean.log2() as u32 } else { 0 };
            let mut pc = f64::MAX;
            for k in k0.saturating_sub(1)..=(k0 + 1).min(62) {
                let c: u64 = s.iter().map(|&v| (v >> k) + 1 + k as u64).sum();
                pc = pc.min(c as f64);
            }
            cost += 6.0 + pc;
        }
        best = best.min(cost);
    }
    best
}

/// Ideal adaptive arithmetic coding cost of a two-sided geometric model with running-mean scale.
fn ac_bits(r: &[i64]) -> f64 {
    let mut m = (r.first().map_or(0, |v| v.unsigned_abs()) as f64).max(1.0);
    let mut bits = 0.0;
    for &e in r {
        let a = e.unsigned_abs() as f64;
        let mm = m.max(0.05);
        let th = ((1.0 + mm * mm).sqrt() - 1.0) / mm;
        bits += -((1.0 - th) / (1.0 + th)).log2() - a * th.log2();
        m += (a - m) / 16.0;
    }
    bits + 8.0 // model header (initial scale)
}

fn fixed_best(x: &[i64]) -> Vec<i64> {
    let mut best: Option<(u128, Vec<i64>)> = None;
    for o in 0..=4 {
        let r: Vec<i64> = (0..x.len()).map(|i| x[i] - fixed_pred(x, i, o)).collect();
        let s: u128 = r.iter().map(|&v| zz(v) as u128).sum();
        if best.as_ref().is_none_or(|b| s < b.0) {
            best = Some((s, r));
        }
    }
    best.unwrap().1
}

/// Per-chunk LPC residual of `order` (mean removed and charged, 15-bit coefficients charged).
fn lpc(x: &[i64], order: usize) -> Option<(Vec<i64>, f64)> {
    let n = x.len();
    if n <= 2 * order {
        return None;
    }
    let mean = (x.iter().sum::<i64>() as f64 / n as f64).round() as i64;
    let y: Vec<f64> = x.iter().map(|&v| (v - mean) as f64).collect();
    // Welch window for the autocorrelation only.
    let w: Vec<f64> = (0..n)
        .map(|i| {
            let t = (2.0 * i as f64 - (n - 1) as f64) / (n + 1) as f64;
            1.0 - t * t
        })
        .collect();
    let yw: Vec<f64> = y.iter().zip(&w).map(|(a, b)| a * b).collect();
    let mut ac = vec![0.0; order + 1];
    for lag in 0..=order {
        ac[lag] = (lag..n).map(|i| yw[i] * yw[i - lag]).sum();
    }
    if ac[0] <= 0.0 {
        return None;
    }
    ac[0] *= 1.0 + 1e-9;
    let mut a = vec![0.0; order + 1];
    let mut err = ac[0];
    for i in 1..=order {
        let mut k = ac[i];
        for j in 1..i {
            k -= a[j] * ac[i - j];
        }
        k /= err;
        let prev = a.clone();
        a[i] = k;
        for j in 1..i {
            a[j] = prev[j] - k * prev[i - j];
        }
        err *= 1.0 - k * k;
        if err <= 0.0 {
            return None;
        }
    }
    let cmax = a[1..].iter().fold(0f64, |m, v| m.max(v.abs()));
    let shift = (14 - (cmax.max(1e-9).log2().floor() as i32 + 1)).clamp(0, 30);
    let q: Vec<i64> = a[1..].iter().map(|c| (c * (1i64 << shift) as f64).round() as i64).collect();
    let yi: Vec<i64> = x.iter().map(|&v| v - mean).collect();
    let mut r = Vec::with_capacity(n);
    for i in 0..n {
        if i < order {
            r.push(yi[i] - fixed_pred(&yi, i, 2));
        } else {
            let mut s: i64 = 0;
            for j in 0..order {
                s += q[j] * yi[i - 1 - j];
            }
            r.push(yi[i] - (s >> shift));
        }
    }
    Some((r, 32.0 + 5.0 + 5.0 + 15.0 * order as f64))
}

/// NLMS on first differences (float64 estimate; a codec would need a bit-exact integer version).
fn nlms(x: &[i64], order: usize, mu: f64) -> Vec<i64> {
    let n = x.len();
    let mut r = Vec::with_capacity(n);
    let mut w = vec![0.0f64; order];
    let mut h = vec![0.0f64; order]; // past differences, h[0] newest
    let mut energy = 0.0;
    for i in 0..n {
        if i == 0 {
            r.push(x[0]);
            continue;
        }
        let d = (x[i] - x[i - 1]) as f64;
        let p: f64 = w.iter().zip(&h).map(|(a, b)| a * b).sum();
        let pi = p.round() as i64;
        let e = (x[i] - x[i - 1]) - pi;
        r.push(e);
        let g = mu * e as f64 / (energy + 1.0);
        for j in 0..order {
            w[j] += g * h[j];
        }
        energy += d * d - h[order - 1] * h[order - 1];
        h.rotate_right(1);
        h[0] = d;
        if energy < 0.0 {
            energy = 0.0;
        }
    }
    r
}

fn cmd_ideas(name: &str, path: &str, bits: u32) {
    let x = read_k(path, bits);
    let n = x.len();
    let mut rows = Vec::new();
    for size in SIZES {
        // [fixed_rice, fixed_ac, lpc_rice, lpc_ac, nlms_rice, nlms_ac, blk_rice, blk_ac]
        let mut tot = [0f64; 8];
        let mut wins = [0usize; 3]; // fixed, lpc, nlms (for nlms_ac)
        let mut lpc_orders = std::collections::BTreeMap::new();
        for c in x.chunks(size) {
            let f = fixed_best(c);
            let fr = rice_bits(&f) + 3.0;
            let fa = ac_bits(&f) + 3.0;
            let (mut lr, mut la) = (fr, fa);
            let mut lo = 0usize;
            for order in [2, 4, 8, 16, 32] {
                if let Some((r, side)) = lpc(c, order) {
                    let a = ac_bits(&r) + side + 3.0;
                    lr = lr.min(rice_bits(&r) + side + 3.0);
                    if a < la {
                        la = a;
                        lo = order;
                    }
                }
            }
            *lpc_orders.entry(lo).or_insert(0usize) += 1;
            let (mut nr, mut na) = (lr, la);
            let mut won = if lo == 0 { 0 } else { 1 };
            for order in [16, 32] {
                for mu in [0.05, 0.3] {
                    let r = nlms(c, order, mu);
                    nr = nr.min(rice_bits(&r) + 3.0);
                    let a = ac_bits(&r) + 3.0;
                    if a < na {
                        na = a;
                        won = 2;
                    }
                }
            }
            wins[won] += 1;
            // Block-adaptive: fixed or LPC chosen per 1,024-sample block (+3 bits per block).
            let (mut br, mut ba) = (0.0, 0.0);
            for b in c.chunks(1024) {
                let f = fixed_best(b);
                let (mut r1, mut a1) = (rice_bits(&f), ac_bits(&f));
                for order in [2, 4, 8, 16] {
                    if let Some((r, side)) = lpc(b, order) {
                        r1 = r1.min(rice_bits(&r) + side);
                        a1 = a1.min(ac_bits(&r) + side);
                    }
                }
                br += r1 + 3.0;
                ba += a1 + 3.0;
            }
            // A chunk that is a single block gains nothing from splitting.
            let br = br.min(lr);
            let ba = ba.min(la);
            for (t, v) in tot.iter_mut().zip([fr, fa, lr, la, nr, na, br, ba]) {
                *t += v;
            }
        }
        let names = ["fixed_rice", "fixed_ac", "lpc_rice", "lpc_ac", "nlms_rice", "nlms_ac", "blk_rice", "blk_ac"];
        let fields: Vec<String> = names.iter().zip(tot).map(|(k, v)| format!("\"{k}\":{:.4}", v / n as f64)).collect();
        let orders: Vec<String> = lpc_orders.iter().map(|(k, v)| format!("\"{k}\":{v}")).collect();
        rows.push(format!(
            "\"{size}\":{{{},\"wins_ac\":{{\"fixed\":{},\"lpc\":{},\"nlms\":{}}},\"lpc_order_ac\":{{{}}}}}",
            fields.join(","),
            wins[0], wins[1], wins[2],
            orders.join(",")
        ));
    }
    println!("{{\"name\":\"{name}\",\"n\":{n},{}}}", rows.join(","));
}

fn main() {
    let a: Vec<String> = std::env::args().collect();
    match a.get(1).map(String::as_str) {
        Some("grizzbit") => cmd_grizzbit(&a[2], &a[3], a[4].parse().expect("quantum")),
        Some("ideas") => cmd_ideas(&a[2], &a[3], a[4].parse().expect("bits")),
        Some("head") => {
            // Diagnostic: the first bytes of the first 4,096-sample chunk.
            let x = read_f64(&a[2]);
            let e = grizzbit::encode(&x[..4096.min(x.len())], a[3].parse().expect("quantum")).unwrap();
            println!("{} bytes: {:?}", e.len(), &e[..24.min(e.len())]);
        }
        _ => panic!("usage: grizzbit NAME FILE.f64 QUANTUM | ideas NAME FILE.k BITS"),
    }
}
