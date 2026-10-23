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

/// Raises on invalid recordings; usable as a table CHECK constraint.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_valid_recording(samples: Vec<Option<f64>>, sample_rate_hz: f64) -> bool {
    let samples = checked_samples(samples);
    if samples.len() < 512 {
        pgrx::error!("recording must contain 512..32768 samples");
    }
    morlet_core::validate(&samples).unwrap_or_else(|e| pgrx::error!("{}", e));
    if !sample_rate_hz.is_finite() || !(128.0..=200000.0).contains(&sample_rate_hz) {
        pgrx::error!("sample_rate_hz must be finite and in [128,200000]");
    }
    true
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_segment_grid(
    samples: Vec<Option<f64>>,
    sample_rate_hz: f64,
    window_size: i32,
    stride: i32,
    normalization: &str,
) -> JsonB {
    let grid = morlet_core::segments::grid(
        &checked_samples(samples),
        sample_rate_hz,
        window_size,
        stride,
        normalization,
    )
    .unwrap_or_else(|e| pgrx::error!("{}", e));
    JsonB(serde_json::to_value(grid).unwrap_or_else(|e| pgrx::error!("{}", e)))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_feature_vector_distance(
    left_features: Vec<Option<f64>>,
    right_features: Vec<Option<f64>>,
) -> f64 {
    if left_features.len() != 15 || right_features.len() != 15 {
        pgrx::error!("Morlet feature vectors must contain 15 values");
    }
    morlet_core::distance(
        &checked_samples(left_features),
        &checked_samples(right_features),
        "none",
    )
    .unwrap_or_else(|e| pgrx::error!("{}", e))
}

pgrx::extension_sql_file!("../sql/collections.sql", name = "collections", finalize);

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;
    use pgrx::JsonB;
    #[pg_test]
    fn persisted_segments_follow_source_transactions() {
        Spi::run("SELECT morlet_create_collection('public','feature_recordings'); INSERT INTO feature_recordings(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,1023) i)); SELECT morlet_enable_segments('feature_recordings',512,256,'none')").unwrap();
        assert_eq!(
            Spi::get_one::<i32>(
                "SELECT jsonb_array_length(morlet_grid->'segments') FROM feature_recordings"
            )
            .unwrap()
            .unwrap(),
            3
        );
        assert_eq!(Spi::get_one::<f64>("SELECT distance FROM morlet_search_features('feature_recordings',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),4096,'x','g','{}','none',1)").unwrap().unwrap(),0.);
        Spi::run("DO $$ BEGIN UPDATE feature_recordings SET samples=array_fill(0.::float8,ARRAY[512]); RAISE EXCEPTION 'rollback'; EXCEPTION WHEN raise_exception THEN NULL; END $$").unwrap();
        assert_eq!(
            Spi::get_one::<i32>(
                "SELECT jsonb_array_length(morlet_grid->'segments') FROM feature_recordings"
            )
            .unwrap()
            .unwrap(),
            3
        );
        Spi::run("UPDATE feature_recordings SET samples=array_fill(0.::float8,ARRAY[512]),sample_rate_hz=8192").unwrap();
        assert_eq!(Spi::get_one::<f64>("SELECT distance FROM morlet_search_features('feature_recordings',array_fill(0.::float8,ARRAY[512]),8192,'x','g','{}','none',1)").unwrap().unwrap(),0.);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_features('feature_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','none')").unwrap().unwrap(),0);
        let stored_distance = Spi::get_one::<f64>("SELECT distance FROM morlet_search_features('feature_recordings',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),8192,'x','g','{}','none',1)").unwrap().unwrap();
        let direct_distance = Spi::get_one::<f64>("SELECT morlet_feature_distance(ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),array_fill(0.::float8,ARRAY[512]),8192)").unwrap().unwrap();
        assert!(stored_distance > 0.);
        assert!((stored_distance - direct_distance).abs() < 1e-12);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_features('feature_recordings',array_fill(0.::float8,ARRAY[512]),8192,'x','g','{\"asset\":\"missing\"}','none')").unwrap().unwrap(),0);
        Spi::run("DELETE FROM feature_recordings").unwrap();
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_features('feature_recordings',array_fill(0.::float8,ARRAY[512]),8192,'x','g','{}','none')").unwrap().unwrap(),0);
    }
    #[pg_test(
        error = "incompatible segment representation: match window and normalization, or rebuild for the current extractor"
    )]
    fn rejects_incompatible_grid_query() {
        Spi::run("SELECT morlet_create_collection('public','incompatible_recordings'); SELECT morlet_enable_segments('incompatible_recordings'); INSERT INTO incompatible_recordings(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[512])); SELECT * FROM morlet_search_features('incompatible_recordings',array_fill(0.::float8,ARRAY[1024]),4096,'x','g')").unwrap();
    }
    #[pg_test(error = "permission denied for table private_features")]
    fn feature_search_respects_table_permissions() {
        Spi::run("SELECT morlet_create_collection('public','private_features'); SELECT morlet_enable_segments('private_features'); CREATE ROLE morlet_feature_reader; SET LOCAL ROLE morlet_feature_reader; SELECT * FROM public.morlet_search_features('public.private_features',array_fill(0.::float8,ARRAY[512]),4096,'x','g')").unwrap();
    }
    #[pg_test]
    fn collections_search_and_rollback() {
        Spi::run("SELECT morlet_create_collection('public','test_recordings')").unwrap();
        Spi::run("INSERT INTO test_recordings(sample_rate_hz,channel,unit,metadata,samples) VALUES (4096,'vibration','g','{\"asset\":\"A\"}',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,1023) i)),(4096,'vibration','g','{\"asset\":\"B\"}',array_fill(0.::float8,ARRAY[1024]))").unwrap();
        assert_eq!(Spi::get_one::<f64>("SELECT distance FROM morlet_search('test_recordings',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),4096,'vibration','g','{\"asset\":\"A\"}','none',256,1)").unwrap().unwrap(),0.);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search('test_recordings',array_fill(0.::float8,ARRAY[512]),4096,'vibration','g','{\"asset\":\"B\"}')").unwrap().unwrap(),3);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search('test_recordings',array_fill(0.::float8,ARRAY[512]),4096,'other','g')").unwrap().unwrap(),0);
        Spi::run("DO $$ BEGIN DELETE FROM test_recordings; RAISE EXCEPTION 'rollback'; EXCEPTION WHEN raise_exception THEN NULL; END $$").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM test_recordings")
                .unwrap()
                .unwrap(),
            2
        );
    }
    #[pg_test(error = "permission denied for table private_recordings")]
    fn search_respects_table_permissions() {
        Spi::run("SELECT morlet_create_collection('public','private_recordings'); CREATE ROLE morlet_test_reader; SET LOCAL ROLE morlet_test_reader; SELECT * FROM public.morlet_search('public.private_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g')").unwrap();
    }
    #[pg_test(error = "query must be a one-dimensional array starting at 1")]
    fn rejects_multidimensional_query() {
        Spi::run("SELECT morlet_create_collection('public','dim_recordings'); SELECT * FROM morlet_search('dim_recordings',array_fill(0.::float8,ARRAY[2,512]),4096,'x','g')").unwrap();
    }
    #[pg_test(error = "NULL waveform elements are unsupported")]
    fn collection_rejects_null_sample() {
        Spi::run("SELECT morlet_create_collection('public','bad_recordings'); INSERT INTO bad_recordings(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(NULL::float8,ARRAY[512]))").unwrap();
    }
    #[pg_test(
        error = "search exceeds 4096 candidate windows; narrow the metadata filter or increase stride"
    )]
    fn search_budget() {
        Spi::run("SELECT morlet_create_collection('public','budget_recordings'); INSERT INTO budget_recordings(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[8192])); SELECT * FROM morlet_search('budget_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','none',1)").unwrap();
    }
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
