//! Exact envelope scoring: the prepared query (`LagQuery`) against the direct form (`distance`), on the machine it
//! runs on. Both must give bitwise the same distance; the bench checks that for every window and reports the time
//! per stored window. Envelopes are decoded from float16 like stored ones and z-scored, so values are realistic.
//!
//!   cargo run --release -p morlet-core --example envelope_score_bench [windows]
use morlet_core::envelope::{decode, distance, encode, LagQuery};
use std::time::Instant;

fn noise(seed: &mut u64, n: usize) -> Vec<f64> {
    (0..n)
        .map(|i| {
            *seed = seed
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            let r = ((*seed >> 11) as f64) / ((1u64 << 53) as f64) - 0.5;
            r + (i as f64 / 37.0).sin()
        })
        .collect()
}

fn main() {
    let count: usize = std::env::args().nth(1).map_or(400, |v| v.parse().unwrap());
    let mut seed = 7u64;
    // Window sizes 4096 and 16384 at the default max_lag_fraction 0.075 (N1 used 16384).
    for window in [4096usize, 16384] {
        let stored: Vec<Vec<f64>> = (0..count)
            .map(|_| decode(&encode(&noise(&mut seed, window)).unwrap()).unwrap())
            .collect();
        let q = decode(&encode(&noise(&mut seed, window)).unwrap()).unwrap();
        let lag = (0.075 * q.len() as f64).floor() as usize;
        let t = Instant::now();
        let p = LagQuery::new(&q, lag).expect("supported");
        let prep = t.elapsed().as_secs_f64();
        let t = Instant::now();
        let a: Vec<f64> = stored.iter().map(|g| p.distance(g).unwrap()).collect();
        let prepared = t.elapsed().as_secs_f64();
        let t = Instant::now();
        let b: Vec<f64> = stored
            .iter()
            .map(|g| distance(&q, g, lag).unwrap())
            .collect();
        let direct = t.elapsed().as_secs_f64();
        assert!(
            a.iter().zip(&b).all(|(x, y)| x.to_bits() == y.to_bits()),
            "forms disagree"
        );
        println!(
            "window {window}: {} values, lag {lag}, matrix {:.2} MB; per stored window: prepared {:.1} us \
             (+{:.1} ms once), direct {:.1} us; bitwise equal on {count}",
            q.len(),
            (q.len() * (2 * lag + 1).div_ceil(16) * 16 * 8) as f64 / 1e6,
            prepared / count as f64 * 1e6,
            prep * 1e3,
            direct / count as f64 * 1e6
        );
    }
}
