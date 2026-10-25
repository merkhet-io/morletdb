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

/// Version-selected features. Rate domains of the supported versions are disjoint.
#[pg_extern(name = "morlet_features", immutable, parallel_safe, strict)]
fn morlet_features_versioned(
    samples: Vec<Option<f64>>,
    sample_rate_hz: f64,
    extractor: &str,
) -> Vec<f64> {
    morlet_core::morlet::extract_versioned(&checked_samples(samples), sample_rate_hz, extractor)
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

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_checkpoint(
    sample_rate_hz: f64,
    window_size: i32,
    stride: i32,
    normalization: &str,
) -> JsonB {
    let state = morlet_core::chunks::ChunkExtractor::new(
        sample_rate_hz,
        window_size as usize,
        stride as usize,
        normalization,
    )
    .unwrap_or_else(|e| pgrx::error!("{}", e));
    JsonB(serde_json::to_value(state.checkpoint()).unwrap())
}

#[pg_extern(name = "morlet_chunk_checkpoint", immutable, parallel_safe, strict)]
fn morlet_chunk_checkpoint_versioned(
    sample_rate_hz: f64,
    window_size: i32,
    stride: i32,
    normalization: &str,
    extractor: &str,
) -> JsonB {
    let state = morlet_core::chunks::ChunkExtractor::with_extractor(
        sample_rate_hz,
        window_size as usize,
        stride as usize,
        normalization,
        extractor,
    )
    .unwrap_or_else(|e| pgrx::error!("{}", e));
    JsonB(serde_json::to_value(state.checkpoint()).unwrap())
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_valid_chunk(samples: Vec<Option<f64>>) -> bool {
    morlet_core::validate(&checked_samples(samples)).unwrap_or_else(|e| pgrx::error!("{}", e));
    true
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_step(checkpoint: JsonB, start_sample: i64, samples: Vec<Option<f64>>) -> JsonB {
    if start_sample < 0 {
        pgrx::error!("chunk start must be nonnegative");
    }
    let checkpoint = serde_json::from_value(checkpoint.0)
        .unwrap_or_else(|e| pgrx::error!("invalid checkpoint: {}", e));
    let mut state = morlet_core::chunks::ChunkExtractor::from_checkpoint(checkpoint)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    let segments = state
        .push(start_sample as u64, &checked_samples(samples))
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    if state.next_sample_offset() > i64::MAX as u64 {
        pgrx::error!("Postgres sample offset overflow");
    }
    JsonB(serde_json::json!({"checkpoint":state.checkpoint(),"segments":segments}))
}

pgrx::extension_sql_file!("../sql/collections.sql", name = "collections");
pgrx::extension_sql_file!("../sql/streams.sql", name = "streams", finalize);

#[cfg(any(test, feature = "pg_test"))]
#[pg_schema]
mod tests {
    use pgrx::prelude::*;
    use pgrx::JsonB;
    #[pg_test]
    fn continuous_interval_workflow() {
        Spi::run(include_str!("../sql/interval_tests.sql")).unwrap();
    }
    #[pg_test]
    fn stream_search_keeps_parent_visibility_and_compatibility() {
        Spi::run("SELECT morlet_create_stream_collection('public','search_rls'); SELECT morlet_start_recording('search_rls','A',4096,'x','g','{}',512,256,'none'); SELECT morlet_start_recording('search_rls','B',4096,'x','g'); SELECT morlet_append_chunk('search_rls','A',0,array_fill(0.::float8,ARRAY[512])); SELECT morlet_append_chunk('search_rls','B',0,array_fill(0.::float8,ARRAY[512])); ALTER TABLE search_rls ENABLE ROW LEVEL SECURITY; CREATE POLICY parent_visibility ON search_rls USING(recording_id='B'); ALTER TABLE search_rls_segments ENABLE ROW LEVEL SECURITY; CREATE POLICY segment_visibility ON search_rls_segments USING(true); CREATE ROLE morlet_search_reader; GRANT SELECT ON search_rls,search_rls_segments TO morlet_search_reader; SET LOCAL ROLE morlet_search_reader").unwrap();
        assert_eq!(Spi::get_one::<Vec<String>>("SELECT array_agg(recording_id) FROM morlet_search_streams('search_rls',array_fill(0.::float8,ARRAY[512]),4096,'x','g')").unwrap().unwrap(),vec!["B".to_string()]);
    }
    #[pg_test]
    fn lowrate_recordings_are_explicit_and_isolated() {
        Spi::run("SELECT morlet_create_stream_collection('public','lowrate_streams'); SELECT morlet_start_recording('lowrate_streams','S1',100,'HHZ','counts','{\"station\":\"S\"}',2048,512,'zscore','morlet-energy-lowrate-v1'); SELECT morlet_start_recording('lowrate_streams','S2',100,'HHZ','counts','{\"station\":\"S\"}',2048,512,'zscore','morlet-energy-lowrate-v1'); SELECT morlet_append_chunk('lowrate_streams','S1',0,ARRAY(SELECT sin(i::float8/5)*exp(-power((i-1000)/150.,2)) FROM generate_series(0,4095) i)); SELECT morlet_append_chunk('lowrate_streams','S2',0,ARRAY(SELECT sin(i::float8/5)*exp(-power((i-1512)/150.,2)) FROM generate_series(0,4095) i))").unwrap();
        assert_eq!(
            Spi::get_one::<String>(
                "SELECT extractor_version FROM morlet_stream_status('lowrate_streams','S1')"
            )
            .unwrap()
            .unwrap(),
            "morlet-energy-lowrate-v1"
        );
        let hit = Spi::get_two::<String, i64>("SELECT recording_id,start_sample FROM morlet_search_interval('lowrate_streams','S1',0,2048,'{}',1)").unwrap();
        assert_eq!((hit.0.unwrap(), hit.1.unwrap()), ("S2".to_string(), 512));
        assert_eq!(Spi::get_one::<String>("SELECT extractor_version FROM morlet_search_interval('lowrate_streams','S1',0,2048,'{}',1)").unwrap().unwrap(),"morlet-energy-lowrate-v1");
        assert_eq!(Spi::get_one::<String>("SELECT extractor_version FROM morlet_read_interval('lowrate_streams','S2',512,2560)").unwrap().unwrap(),"morlet-energy-lowrate-v1");
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_streams('lowrate_streams',ARRAY(SELECT sin(i::float8/5) FROM generate_series(0,2047) i),100,'HHZ','counts','{}','zscore',10,4096,'morlet-energy-lowrate-v1')").unwrap().unwrap(),10);
    }
    #[pg_test]
    fn reads_span_maximal_chunks_with_bounded_lookup() {
        Spi::run("SELECT morlet_create_stream_collection('public','wide_chunks'); SELECT morlet_start_recording('wide_chunks','A',4096,'x','g','{}',512,32768,'none'); SELECT morlet_append_chunk('wide_chunks','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,32767) i)); SELECT morlet_append_chunk('wide_chunks','A',32768,ARRAY(SELECT i::float8 FROM generate_series(32768,65535) i))").unwrap();
        // The last sample of a maximal chunk is exactly at the lookup bound.
        let tail = Spi::get_one::<Vec<f64>>(
            "SELECT samples FROM morlet_read_interval('wide_chunks','A',32767,32770)",
        )
        .unwrap()
        .unwrap();
        assert_eq!(tail, vec![32767., 32768., 32769.]);
        let whole = Spi::get_one::<Vec<f64>>(
            "SELECT samples FROM morlet_read_interval('wide_chunks','A',1,32769)",
        )
        .unwrap()
        .unwrap();
        assert_eq!(whole.len(), 32768);
        assert_eq!((whole[0], whole[32767]), (1., 32768.));
    }
    #[pg_test(
        error = "invalid chunk extractor: rate 128..200000, window 512..4096, stride 1..32768 required"
    )]
    fn default_v1_still_rejects_seismic_rates() {
        Spi::run("SELECT morlet_create_stream_collection('public','v1_streams'); SELECT morlet_start_recording('v1_streams','S1',100,'HHZ','counts')").unwrap();
    }
    #[pg_test(error = "morlet-energy-lowrate-v1 requires a finite sample_rate_hz in [1,128)")]
    fn lowrate_rejects_v1_rates() {
        Spi::run("SELECT morlet_create_stream_collection('public','lowrate_bad'); SELECT morlet_start_recording('lowrate_bad','S1',4096,'x','g','{}',512,256,'zscore','morlet-energy-lowrate-v1')").unwrap();
    }
    #[pg_test(error = "incompatible stream representation")]
    fn search_rejects_mixed_extractor_population() {
        Spi::run("SELECT morlet_create_stream_collection('public','mixed_streams'); SELECT morlet_start_recording('mixed_streams','A',100,'HHZ','counts','{}',512,256,'zscore','morlet-energy-lowrate-v1'); SELECT morlet_append_chunk('mixed_streams','A',0,array_fill(0.::float8,ARRAY[512])); UPDATE mixed_streams SET checkpoint=jsonb_set(checkpoint,'{extractor_version}','\"morlet-energy-v1\"'); SELECT * FROM morlet_search_streams('mixed_streams',array_fill(0.::float8,ARRAY[512]),100,'HHZ','counts','{}','zscore',10,4096,'morlet-energy-lowrate-v1')").unwrap();
    }
    #[pg_test(
        error = "invalid chunk extractor: rate 128..200000, window 512..4096, stride 1..32768 required"
    )]
    fn import_cannot_relabel_lowrate_recording_as_v1() {
        Spi::run("SELECT morlet_create_stream_collection('public','import_lowrate'); SELECT morlet_ensure_recording('import_lowrate','A',100,'HHZ','counts','{}',2048,512,'zscore','morlet-energy-lowrate-v1'); SELECT morlet_ensure_recording('import_lowrate','A',100,'HHZ','counts','{}',2048,512,'zscore','morlet-energy-lowrate-v1'); SELECT morlet_ensure_recording('import_lowrate','A',100,'HHZ','counts','{}',2048,512,'zscore')").unwrap();
    }
    #[pg_test]
    fn batch_append_is_atomic_and_replay_safe() {
        Spi::run("SELECT morlet_create_stream_collection('public','batch_streams'); SELECT morlet_start_recording('batch_streams','A',4096,'x','g'); DO $$ BEGIN PERFORM morlet_append_chunks('batch_streams','A',ARRAY[ROW(0,array_fill(0.::float8,ARRAY[512]))::morlet_input_chunk,ROW(513,array_fill(0.::float8,ARRAY[512]))::morlet_input_chunk]); EXCEPTION WHEN OTHERS THEN NULL; END $$").unwrap();
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT received_samples FROM morlet_stream_status('batch_streams','A')"
            )
            .unwrap()
            .unwrap(),
            0
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM batch_streams_chunks")
                .unwrap()
                .unwrap(),
            0
        );
        for _ in 0..2 {
            assert_eq!(Spi::get_one::<i64>("SELECT morlet_append_chunks('batch_streams','A',ARRAY[ROW(0,array_fill(0.::float8,ARRAY[512]))::morlet_input_chunk,ROW(512,array_fill(0.::float8,ARRAY[512]))::morlet_input_chunk])").unwrap().unwrap(),1024);
        }
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM batch_streams_chunks")
                .unwrap()
                .unwrap(),
            2
        );
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT indexed_segments FROM morlet_stream_status('batch_streams','A')"
            )
            .unwrap()
            .unwrap(),
            3
        );
    }
    #[pg_test(error = "batch exceeds 262144 samples")]
    fn rejects_oversized_batch_before_appending() {
        Spi::run("SELECT morlet_create_stream_collection('public','large_batch'); SELECT morlet_append_chunks('large_batch','A',array_fill(ROW(0,array_fill(0.::float8,ARRAY[32768]))::morlet_input_chunk,ARRAY[9]))").unwrap();
    }
    #[pg_test(error = "batch must contain 1..64 input chunks in a one-dimensional array")]
    fn rejects_empty_batch() {
        Spi::run("SELECT morlet_create_stream_collection('public','empty_batch'); SELECT morlet_append_chunks('empty_batch','A',ARRAY[]::morlet_input_chunk[])").unwrap();
    }
    #[pg_test(error = "recording metadata or extraction specification conflicts with import")]
    fn import_rejects_changed_recording_spec() {
        Spi::run("SELECT morlet_create_stream_collection('public','import_streams'); SELECT morlet_ensure_recording('import_streams','A',4096,'x','g'); SELECT morlet_ensure_recording('import_streams','A',4096,'x','g'); SELECT morlet_ensure_recording('import_streams','A',8192,'x','g')").unwrap();
    }
    #[pg_test]
    fn durable_chunks_resume_and_rollback() {
        Spi::run("SELECT morlet_create_stream_collection('public','durable_streams'); SELECT morlet_start_recording('durable_streams','A',4096,'x','g'); SELECT morlet_append_chunk('durable_streams','A',0,ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,136) i)); SELECT morlet_append_chunk('durable_streams','A',137,ARRAY(SELECT sin(i::float8/8) FROM generate_series(137,999) i))").unwrap();
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT received_samples FROM morlet_stream_status('durable_streams','A')"
            )
            .unwrap()
            .unwrap(),
            1000
        );
        Spi::run("DO $$ BEGIN PERFORM morlet_append_chunk('durable_streams','A',1000,ARRAY(SELECT sin(i::float8/8) FROM generate_series(1000,2047) i)); RAISE EXCEPTION 'rollback'; EXCEPTION WHEN raise_exception THEN NULL; END $$").unwrap();
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT received_samples FROM morlet_stream_status('durable_streams','A')"
            )
            .unwrap()
            .unwrap(),
            1000
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM durable_streams_chunks")
                .unwrap()
                .unwrap(),
            2
        );
        Spi::run("SELECT morlet_append_chunk('durable_streams','A',1000,ARRAY(SELECT sin(i::float8/8) FROM generate_series(1000,2047) i)); SELECT morlet_append_chunk('durable_streams','A',0,ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,136) i))").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM durable_streams_chunks")
                .unwrap()
                .unwrap(),
            3
        );
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT indexed_segments FROM morlet_stream_status('durable_streams','A')"
            )
            .unwrap()
            .unwrap(),
            7
        );
        let actual = Spi::get_one::<Vec<f64>>(
            "SELECT features FROM durable_streams_segments WHERE start_sample=256",
        )
        .unwrap()
        .unwrap();
        let expected=Spi::get_one::<Vec<f64>>("SELECT morlet_features(morlet_normalize(ARRAY(SELECT sin(i::float8/8) FROM generate_series(256,767) i),'zscore'),4096)").unwrap().unwrap();
        assert_eq!(actual, expected);
        assert_eq!(Spi::get_one::<f64>("SELECT distance FROM morlet_search_streams('durable_streams',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,511) i),4096,'x','g') LIMIT 1").unwrap().unwrap(),0.);
        Spi::run("DELETE FROM durable_streams WHERE recording_id='A'").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM durable_streams_segments")
                .unwrap()
                .unwrap(),
            0
        );
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn rejects_conflicting_chunk_replay() {
        Spi::run("SELECT morlet_create_stream_collection('public','replay_streams'); SELECT morlet_start_recording('replay_streams','A',4096,'x','g'); SELECT morlet_append_chunk('replay_streams','A',0,array_fill(0.::float8,ARRAY[137])); SELECT morlet_append_chunk('replay_streams','A',0,array_fill(1.::float8,ARRAY[137]))").unwrap();
    }
    #[pg_test(error = "new row violates row-level security policy for table \"rls_streams\"")]
    fn blocked_checkpoint_update_rolls_back_append() {
        Spi::run("SELECT morlet_create_stream_collection('public','rls_streams'); SELECT morlet_start_recording('rls_streams','A',4096,'x','g'); ALTER TABLE rls_streams ENABLE ROW LEVEL SECURITY; CREATE POLICY read_streams ON rls_streams FOR SELECT USING(true); CREATE POLICY block_stream_updates ON rls_streams FOR UPDATE USING(true) WITH CHECK(false); CREATE ROLE morlet_stream_writer; GRANT SELECT,UPDATE ON rls_streams TO morlet_stream_writer; GRANT SELECT,INSERT ON rls_streams_chunks,rls_streams_segments TO morlet_stream_writer; SET LOCAL ROLE morlet_stream_writer; SELECT morlet_append_chunk('rls_streams','A',0,array_fill(0.::float8,ARRAY[512]))").unwrap();
    }
    #[pg_test]
    fn typed_segments_match_json_and_follow_writes() {
        Spi::run("SELECT morlet_create_collection('public','json_reference'); SELECT morlet_create_collection('public','typed_recordings'); INSERT INTO json_reference(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',ARRAY(SELECT sin(i::float8/8) FROM generate_series(0,1535) i)); INSERT INTO typed_recordings(sample_rate_hz,channel,unit,samples) SELECT sample_rate_hz,channel,unit,samples FROM json_reference; SELECT morlet_enable_segments('json_reference'); SELECT morlet_enable_typed_segments('typed_recordings')").unwrap();
        let json = Spi::get_one::<JsonB>(
            "SELECT jsonb_agg(h) FROM morlet_search_recording('json_reference',1,0,512) h",
        )
        .unwrap()
        .unwrap();
        let typed = Spi::get_one::<JsonB>(
            "SELECT jsonb_agg(h) FROM morlet_search_recording('typed_recordings',1,0,512) h",
        )
        .unwrap()
        .unwrap();
        assert_eq!(json.0, typed.0);
        assert_eq!(
            Spi::get_one::<i32>("SELECT cardinality(morlet_segments) FROM typed_recordings")
                .unwrap()
                .unwrap(),
            5
        );
        Spi::run("DO $$ BEGIN UPDATE typed_recordings SET samples=array_fill(0.::float8,ARRAY[512]); RAISE EXCEPTION 'rollback'; EXCEPTION WHEN raise_exception THEN NULL; END $$").unwrap();
        assert_eq!(
            Spi::get_one::<i32>("SELECT cardinality(morlet_segments) FROM typed_recordings")
                .unwrap()
                .unwrap(),
            5
        );
        Spi::run("UPDATE typed_recordings SET samples=array_fill(0.::float8,ARRAY[512]),sample_rate_hz=8192").unwrap();
        assert_eq!(
            Spi::get_one::<i32>("SELECT cardinality(morlet_segments) FROM typed_recordings")
                .unwrap()
                .unwrap(),
            1
        );
        assert_eq!(Spi::get_one::<f64>("SELECT distance FROM morlet_search_features('typed_recordings',array_fill(0.::float8,ARRAY[512]),8192,'x','g')").unwrap().unwrap(),0.);
    }
    #[pg_test]
    fn typed_search_respects_row_security() {
        Spi::run("SELECT morlet_create_collection('public','rls_typed'); SELECT morlet_enable_typed_segments('rls_typed'); INSERT INTO rls_typed(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[512])),(4096,'x','g',array_fill(0.::float8,ARRAY[512])); ALTER TABLE rls_typed ENABLE ROW LEVEL SECURITY; CREATE POLICY visible_typed ON rls_typed USING (id=2); CREATE ROLE morlet_typed_reader; GRANT SELECT ON rls_typed TO morlet_typed_reader; SET LOCAL ROLE morlet_typed_reader").unwrap();
        assert_eq!(Spi::get_one::<i64>("SELECT recording_id FROM morlet_search_features('rls_typed',array_fill(0.::float8,ARRAY[512]),4096,'x','g')").unwrap().unwrap(),2);
    }
    #[pg_test]
    fn interval_search_and_overlap_policy() {
        Spi::run("SELECT morlet_create_collection('public','interval_recordings'); SELECT morlet_enable_segments('interval_recordings'); INSERT INTO interval_recordings(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[1536])),(4096,'x','g',array_fill(0.::float8,ARRAY[512]))").unwrap();
        assert_eq!(Spi::get_one::<Vec<i32>>("SELECT array_agg(start_sample ORDER BY distance,recording_id,start_sample) FROM morlet_search_features('interval_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',10) WHERE recording_id=1").unwrap().unwrap(),vec![0,512,1024]);
        assert_eq!(Spi::get_one::<Vec<i32>>("SELECT array_agg(start_sample ORDER BY start_sample) FROM morlet_search_features('interval_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',2)").unwrap().unwrap(),vec![0,512]);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_features('interval_recordings',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',10,NULL,NULL,NULL,false)").unwrap().unwrap(),6);
        assert_eq!(Spi::get_one::<Vec<i32>>("SELECT array_agg(start_sample ORDER BY start_sample) FROM morlet_search_recording('interval_recordings',1,0,512) WHERE recording_id=1").unwrap().unwrap(),vec![512,1024]);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_recording('interval_recordings',1,0,512) WHERE recording_id=2").unwrap().unwrap(),1);
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_features('interval_recordings',NULL,4096,'x','g')").unwrap().unwrap(),0);
    }
    #[pg_test(
        error = "source exclusion requires recording ID and a valid half-open sample interval"
    )]
    fn rejects_partial_source_exclusion() {
        Spi::run("SELECT morlet_create_collection('public','partial_source'); SELECT * FROM morlet_search_features('partial_source',array_fill(0.::float8,ARRAY[512]),4096,'x','g','{}','zscore',10,1)").unwrap();
    }
    #[pg_test(error = "source interval exceeds recording length")]
    fn rejects_out_of_bounds_source() {
        Spi::run("SELECT morlet_create_collection('public','short_source'); INSERT INTO short_source(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[512])); SELECT * FROM morlet_search_recording('short_source',1,256,768)").unwrap();
    }
    #[pg_test(error = "source recording does not exist or is not visible")]
    fn source_query_respects_row_security() {
        Spi::run("SELECT morlet_create_collection('public','rls_source'); INSERT INTO rls_source(sample_rate_hz,channel,unit,samples) VALUES (4096,'x','g',array_fill(0.::float8,ARRAY[512])); ALTER TABLE rls_source ENABLE ROW LEVEL SECURITY; CREATE POLICY visible_rows ON rls_source USING (id=2); CREATE ROLE morlet_interval_reader; GRANT SELECT ON rls_source TO morlet_interval_reader; SET LOCAL ROLE morlet_interval_reader; SELECT * FROM morlet_search_recording('rls_source',1,0,512)").unwrap();
    }
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
