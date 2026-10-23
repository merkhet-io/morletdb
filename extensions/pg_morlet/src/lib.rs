use pgrx::prelude::*;

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

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;
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
