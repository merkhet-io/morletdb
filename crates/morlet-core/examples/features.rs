//! JSONL adapter for evaluation. Production extraction stays in morlet-core.
use morlet_core::{morlet, normalize};
use serde::Deserialize;
use std::io::{self, BufRead};

#[derive(Deserialize)]
struct Input {
    samples: Vec<f64>,
    sample_rate_hz: f64,
    normalization: String,
    #[serde(default = "default_extractor")]
    extractor_version: String,
}

fn default_extractor() -> String {
    morlet::EXTRACTOR_VERSION.into()
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    for line in io::stdin().lock().lines() {
        let input: Input = serde_json::from_str(&line?)?;
        let samples = normalize(&input.samples, &input.normalization)?;
        let (signature, cells) = morlet::extract_versioned_with_cells(
            &samples,
            input.sample_rate_hz,
            &input.extractor_version,
        )?;
        // Research baseline: retain four coarse temporal bins of token energy.
        // Tokens contribute by center time and duration, including short tails.
        // This is intentionally not a new production extractor/version.
        let mut energy = vec![0.0; morlet::BANDS * 4];
        let mut weight = vec![0.0; morlet::BANDS * 4];
        let duration = samples.len() as f64 / input.sample_rate_hz;
        for token in &signature.tokens {
            let band = signature
                .frequencies_hz
                .iter()
                .position(|f| *f == token.frequency_hz)
                .unwrap();
            let bin = (((token.offset_seconds + token.duration_seconds / 2.0) / duration * 4.0)
                as usize)
                .min(3);
            let index = band * 4 + bin;
            energy[index] += token.energy * token.duration_seconds;
            weight[index] += token.duration_seconds;
        }
        let mut temporal: Vec<f64> = energy
            .iter()
            .zip(&weight)
            .map(|(e, w)| if *w > 0.0 { (e / w).ln_1p() } else { 0.0 })
            .collect();
        temporal.extend(signature.features().into_iter().skip(morlet::BANDS));
        println!(
            "{}",
            serde_json::json!({
                "version": signature.version,
                "morlet": signature.features(),
                "energy_cells_experimental": cells,
                "morlet_temporal_experimental": temporal,
            })
        );
    }
    Ok(())
}
