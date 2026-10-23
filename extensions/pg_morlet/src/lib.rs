use pgrx::prelude::*;
use pgrx::JsonB;

pgrx::pg_module_magic!(name, version);

fn checked_samples(input: Vec<Option<f64>>) -> Vec<f64> {
    input
        .into_iter()
        .map(|v| v.unwrap_or_else(|| pgrx::error!("NULL waveform elements are unsupported")))
        .collect()
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_rms(samples: Vec<Option<f64>>) -> f64 {
    morlet_core::rms(&checked_samples(samples)).unwrap_or_else(|e| pgrx::error!("{}", e))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_normalize(samples: Vec<Option<f64>>, mode: &str) -> Vec<f64> {
    morlet_core::normalize(&checked_samples(samples), mode)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_distance(
    left_samples: Vec<Option<f64>>,
    right_samples: Vec<Option<f64>>,
    mode: &str,
) -> f64 {
    morlet_core::distance(
        &checked_samples(left_samples),
        &checked_samples(right_samples),
        mode,
    )
    .unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// Version of the numerical representation, independent of extension version.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_extractor_version() -> &'static str {
    morlet_core::morlet::EXTRACTOR_VERSION
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_signature(samples: Vec<Option<f64>>, sample_rate_hz: f64) -> JsonB {
    let signature = morlet_core::morlet::extract(&checked_samples(samples), sample_rate_hz)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    JsonB(serde_json::to_value(signature).unwrap_or_else(|e| pgrx::error!("{}", e)))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_features(samples: Vec<Option<f64>>, sample_rate_hz: f64) -> Vec<f64> {
    morlet_core::morlet::extract(&checked_samples(samples), sample_rate_hz)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
        .features()
}

/// Distinct from morlet_distance: distance between spectral/statistical features.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_feature_distance(
    left_samples: Vec<Option<f64>>,
    right_samples: Vec<Option<f64>>,
    sample_rate_hz: f64,
) -> f64 {
    let left = checked_samples(left_samples);
    let right = checked_samples(right_samples);
    if left.len() != right.len() {
        pgrx::error!("waveforms must have equal lengths");
    }
    let left = morlet_core::morlet::extract(&left, sample_rate_hz)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    let right = morlet_core::morlet::extract(&right, sample_rate_hz)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    left.distance(&right)
}

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;
    use pgrx::JsonB;
    #[pg_test]
    fn go_fixture_through_sql() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../../testdata/morlet-energy-v1/tone.json"))
                .unwrap();
        let values = fixture["capture"]["samples"]
            .as_array()
            .unwrap()
            .iter()
            .map(|v| v.as_f64().unwrap().to_string())
            .collect::<Vec<_>>()
            .join(",");
        let signature = Spi::get_one::<JsonB>(&format!(
            "SELECT morlet_signature(ARRAY[{values}]::float8[],4096.)"
        ))
        .unwrap()
        .unwrap();
        assert_eq!(signature.0["version"], fixture["signature"]["version"]);
        assert_eq!(
            signature.0["tokens"].as_array().unwrap().len(),
            fixture["signature"]["tokens"].as_array().unwrap().len()
        );
        let features = Spi::get_one::<Vec<f64>>(&format!(
            "SELECT morlet_features(ARRAY[{values}]::float8[],4096.)"
        ))
        .unwrap()
        .unwrap();
        assert_eq!(features.len(), 15);
        for (actual, expected) in features.iter().zip(fixture["features"].as_array().unwrap()) {
            let expected = expected.as_f64().unwrap();
            assert!((actual - expected).abs() <= 1e-10 + 1e-9 * expected.abs());
        }
        assert_eq!(Spi::get_one::<f64>(&format!("SELECT morlet_feature_distance(ARRAY[{values}]::float8[],ARRAY[{values}]::float8[],4096.)")).unwrap().unwrap(),0.);
        assert!(
            Spi::get_one::<JsonB>("SELECT morlet_signature(NULL::float8[],4096.)")
                .unwrap()
                .is_none()
        );
        assert!(Spi::get_one::<Vec<f64>>(
            "SELECT morlet_features(array_fill(0.::float8,ARRAY[512]),NULL::float8)"
        )
        .unwrap()
        .is_none());
        assert_eq!(
            Spi::get_one::<String>("SELECT morlet_extractor_version()")
                .unwrap()
                .unwrap(),
            "morlet-energy-v1"
        );
    }
    #[pg_test(error = "Morlet extraction requires 512..32768 samples")]
    fn rejects_short_morlet_input() {
        Spi::run("SELECT morlet_features(ARRAY[1.]::float8[],4096.)").unwrap();
    }
    #[pg_test(error = "sample_rate_hz must be finite and in [128,200000]")]
    fn rejects_invalid_morlet_rate() {
        Spi::run("SELECT morlet_signature(array_fill(0.::float8,ARRAY[512]),'NaN'::float8)")
            .unwrap();
    }
    #[pg_test]
    fn sql_functions() {
        assert_eq!(
            Spi::get_one::<String>("SELECT morlet_version()")
                .unwrap()
                .unwrap(),
            "0.1.0"
        );
        assert_eq!(
            Spi::get_one::<f64>("SELECT morlet_rms(ARRAY[3.0,4.0]::float8[])")
                .unwrap()
                .unwrap(),
            (12.5_f64).sqrt()
        );
        assert!(Spi::get_one::<f64>("SELECT morlet_distance(ARRAY[1.,2.,3.]::float8[], ARRAY[12.,14.,16.]::float8[], 'zscore')").unwrap().unwrap() < 1e-12);
        assert_eq!(
            Spi::get_one::<Vec<f64>>("SELECT morlet_normalize(ARRAY[2.,2.]::float8[], 'zscore')")
                .unwrap()
                .unwrap(),
            vec![0., 0.]
        );
        assert!(Spi::get_one::<f64>("SELECT morlet_rms(NULL::float8[])")
            .unwrap()
            .is_none());
        assert!(
            Spi::get_one::<Vec<f64>>("SELECT morlet_normalize(NULL::float8[], 'zscore')")
                .unwrap()
                .is_none()
        );
        assert!(Spi::get_one::<f64>(
            "SELECT morlet_distance(NULL::float8[], ARRAY[1.]::float8[], 'none')"
        )
        .unwrap()
        .is_none());
        assert!(Spi::get_one::<f64>(
            "SELECT morlet_distance(ARRAY[1.]::float8[], ARRAY[1.]::float8[], NULL::text)"
        )
        .unwrap()
        .is_none());
    }
    #[pg_test(error = "samples must be finite and within +/-1e9")]
    fn rejects_nonfinite() {
        Spi::run("SELECT morlet_rms(ARRAY['NaN'::float8])").unwrap();
    }
    #[pg_test(error = "waveform must contain 1..32768 samples")]
    fn rejects_empty() {
        Spi::run("SELECT morlet_rms(ARRAY[]::float8[])").unwrap();
    }
    #[pg_test(error = "NULL waveform elements are unsupported")]
    fn rejects_null_elements() {
        Spi::run("SELECT morlet_rms(ARRAY[1.,NULL]::float8[])").unwrap();
    }
    #[pg_test(error = "waveforms must have equal lengths")]
    fn rejects_unequal_lengths() {
        Spi::run("SELECT morlet_distance(ARRAY[1.]::float8[],ARRAY[1.,2.]::float8[],'none')")
            .unwrap();
    }
}

#[cfg(test)]
pub mod pg_test {
    pub fn setup(_options: Vec<&str>) {}
    pub fn postgresql_conf_options() -> Vec<&'static str> {
        vec![]
    }
}
