//! Calibrated compare-to-history score (`calibrated-v1`).
//!
//! Each value's relative deviation from the history median is divided by that value's
//! typical relative spread (supplied by the caller, pooled across contexts so it is stable
//! with small histories and reflects how noisy the value is). The score is the maximum of
//! these calibrated deviations over values with a defined spread. A value whose history
//! median is at or below its floor (see `values::negligible_floors`) has no defined
//! deviation, like a median of exactly zero.
//!
//! The score quantile places the query among leave-one-out scores of the history itself:
//! each history segment is scored against the others with the same spreads. It is a
//! per-context calibration: 0.99 means the query deviates more than 99% of normal history
//! segments do from their own context.

/// Median of a non-empty slice (average of the two middle values for even lengths).
fn median(v: &mut [f64]) -> f64 {
    v.sort_by(f64::total_cmp);
    let n = v.len();
    if n % 2 == 1 {
        v[n / 2]
    } else {
        (v[n / 2 - 1] + v[n / 2]) / 2.0
    }
}

/// Per value: |x - median| / |median| / spread, or None when undefined (no spread, no
/// history, or |median| at or below `floors[j]`; a missing floor counts as 0).
pub fn calibrated_deviations(
    query: &[f64],
    history: &[&[f64]],
    spreads: &[Option<f64>],
    floors: &[f64],
) -> Vec<Option<f64>> {
    (0..query.len())
        .map(|j| {
            let spread = spreads
                .get(j)
                .copied()
                .flatten()
                .filter(|s| *s > 0.0 && s.is_finite())?;
            if history.is_empty() {
                return None;
            }
            let mut col: Vec<f64> = history.iter().map(|h| h[j]).collect();
            let med = median(&mut col);
            let floor = floors.get(j).copied().unwrap_or(0.0);
            (med.abs() > floor).then(|| (query[j] - med).abs() / med.abs() / spread)
        })
        .collect()
}

pub fn score(
    query: &[f64],
    history: &[&[f64]],
    spreads: &[Option<f64>],
    floors: &[f64],
) -> Option<f64> {
    calibrated_deviations(query, history, spreads, floors)
        .into_iter()
        .flatten()
        .reduce(f64::max)
}

/// Fraction of leave-one-out history scores below the query score (ties count half).
/// None when the query score or fewer than two history scores are defined. The floors
/// stay those of the whole history.
pub fn score_quantile(
    query: &[f64],
    history: &[&[f64]],
    spreads: &[Option<f64>],
    floors: &[f64],
) -> Option<f64> {
    let q = score(query, history, spreads, floors)?;
    let loo: Vec<f64> = (0..history.len())
        .filter_map(|i| {
            let others: Vec<&[f64]> = history
                .iter()
                .enumerate()
                .filter(|(k, _)| *k != i)
                .map(|(_, h)| *h)
                .collect();
            score(history[i], &others, spreads, floors)
        })
        .collect();
    if loo.len() < 2 {
        return None;
    }
    let below = loo.iter().filter(|s| **s < q).count() as f64;
    let equal = loo.iter().filter(|s| **s == q).count() as f64;
    Some((below + 0.5 * equal) / loo.len() as f64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn calibration_down_weights_noisy_values() {
        // Value 0 is stable (spread 0.01), value 1 is noisy (spread 0.5).
        let hist: Vec<Vec<f64>> = (0..10).map(|i| vec![10.0, 3.0 + 0.1 * i as f64]).collect();
        let h: Vec<&[f64]> = hist.iter().map(|v| v.as_slice()).collect();
        let spreads = [Some(0.01), Some(0.5)];
        // A 70% jump in the noisy value scores below a 5% change in the stable one.
        let noisy = score(&[10.0, 5.95], &h, &spreads, &[]).unwrap();
        let stable = score(&[10.5, 3.45], &h, &spreads, &[]).unwrap();
        assert!(noisy < stable, "{noisy} {stable}");
        assert!((stable - 0.05 / 0.01).abs() < 1e-9);
    }

    #[test]
    fn undefined_values_are_skipped() {
        let hist = [vec![0.0, 2.0], vec![0.0, 2.0]];
        let h: Vec<&[f64]> = hist.iter().map(|v| v.as_slice()).collect();
        let d = calibrated_deviations(&[1.0, 3.0], &h, &[Some(0.1), None], &[]);
        assert_eq!(d, vec![None, None]);
        assert_eq!(
            score(&[1.0, 3.0], &h, &[Some(0.1), Some(0.1)], &[]),
            Some(5.0)
        );
        assert_eq!(score(&[1.0, 3.0], &[], &[Some(0.1), Some(0.1)], &[]), None);
    }

    #[test]
    fn negligible_medians_are_skipped() {
        // Value 0 is rounding residue around 0 (a frozen channel's std), value 1 is real.
        let hist = [vec![3e-15, 2.0], vec![4e-15, 2.0], vec![5e-15, 2.0]];
        let h: Vec<&[f64]> = hist.iter().map(|v| v.as_slice()).collect();
        let s = [Some(1.0), Some(1.0)];
        assert!(score(&[0.01, 2.2], &h, &s, &[]).unwrap() > 1e12);
        let d = calibrated_deviations(&[0.01, 2.2], &h, &s, &[1e-9, 0.0]);
        assert_eq!(d[0], None);
        assert!((d[1].unwrap() - 0.1).abs() < 1e-12);
    }

    #[test]
    fn quantile_ranks_against_leave_one_out_history() {
        let hist: Vec<Vec<f64>> = (0..20).map(|i| vec![100.0 + (i % 5) as f64]).collect();
        let h: Vec<&[f64]> = hist.iter().map(|v| v.as_slice()).collect();
        let s = [Some(0.02)];
        assert_eq!(score_quantile(&[150.0], &h, &s, &[]), Some(1.0));
        let typical = score_quantile(&[102.0], &h, &s, &[]).unwrap();
        assert!(typical < 0.5, "{typical}");
        assert_eq!(score_quantile(&[150.0], &h[..1], &s, &[]), None);
    }
}
