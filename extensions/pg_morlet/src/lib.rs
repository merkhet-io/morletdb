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

/// Lossless chunk encoding: integer grid + Rice coding, or raw float64 off the grid.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_encode_chunk(samples: Vec<Option<f64>>, sample_quantum: f64) -> Vec<u8> {
    morlet_core::rawcodec::encode(&checked_samples(samples), sample_quantum)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_decode_chunk(encoded: &[u8]) -> Vec<f64> {
    morlet_core::rawcodec::decode(encoded).unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// Samples of [interval_start, interval_end) from encoded chunks given in ascending
/// start order, concatenated. Parts of the interval not covered by a chunk are simply
/// absent, so callers compare the length with the interval to detect gaps.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_decode_interval(
    chunks: pgrx::Array<'_, &[u8]>,
    starts: Vec<Option<i64>>,
    interval_start: i64,
    interval_end: i64,
) -> Vec<f64> {
    if chunks.len() != starts.len() {
        pgrx::error!("chunks and starts differ in length");
    }
    let mut out = Vec::with_capacity((interval_end - interval_start).clamp(0, 32768) as usize);
    let mut previous = i64::MIN;
    for (chunk, start) in chunks.iter().zip(&starts) {
        let (Some(chunk), Some(start)) = (chunk, *start) else {
            pgrx::error!("chunks and starts must not contain NULL");
        };
        if start <= previous {
            pgrx::error!("chunk starts must be strictly ascending");
        }
        previous = start;
        let samples =
            morlet_core::rawcodec::decode(chunk).unwrap_or_else(|e| pgrx::error!("{}", e));
        let lo = interval_start.max(start) - start;
        let hi = interval_end.min(start + samples.len() as i64) - start;
        if lo < hi {
            out.extend_from_slice(&samples[lo as usize..hi as usize]);
        }
    }
    out
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_encoded_chunk_kind(encoded: &[u8]) -> &'static str {
    morlet_core::rawcodec::kind(encoded)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
        .name()
}

/// Materialized envelope representation (`xc-envelope-f16-v1`), see morlet_core::envelope.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_version() -> &'static str {
    morlet_core::envelope::ENVELOPE_VERSION
}

/// Full-precision envelope of one window (used for queries).
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope(samples: Vec<Option<f64>>) -> Vec<f64> {
    morlet_core::envelope::envelope(&checked_samples(samples))
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// Stored form: version byte + binary16 values.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_encode(samples: Vec<Option<f64>>) -> Vec<u8> {
    morlet_core::envelope::encode(&checked_samples(samples))
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// Decoded and z-scored stored envelope, as matching sees it.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_decode(envelope: &[u8]) -> Vec<f64> {
    morlet_core::envelope::decode(envelope).unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// Stored envelopes of windows `first_start, first_start + stride, ...` ending at or before
/// `range_end` (at most `max_windows`), from the contiguous encoded chunks that cover them,
/// each chunk decoded once. Element k is the envelope of the window at
/// `first_start + k * stride`; bytes equal `morlet_envelope_encode` of the read interval.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelopes_from_chunks(
    chunks: pgrx::Array<'_, &[u8]>,
    starts: Vec<Option<i64>>,
    first_start: i64,
    range_end: i64,
    window_size: i32,
    stride: i32,
    max_windows: i32,
) -> Vec<Vec<u8>> {
    if chunks.len() != starts.len() {
        pgrx::error!("chunks and starts differ in length");
    }
    if window_size <= 0 || stride <= 0 || max_windows < 0 {
        pgrx::error!("window_size and stride must be positive");
    }
    let mut pairs = Vec::with_capacity(starts.len());
    for (chunk, start) in chunks.iter().zip(&starts) {
        let (Some(chunk), Some(start)) = (chunk, *start) else {
            pgrx::error!("chunks and starts must not contain NULL");
        };
        pairs.push((start, chunk));
    }
    morlet_core::envelope::encode_windows(
        &pairs,
        first_start,
        range_end,
        window_size as usize,
        stride as usize,
        max_windows as usize,
    )
    .unwrap_or_else(|_| pgrx::error!("chunks are not contiguous or do not cover the windows"))
    .into_iter()
    .map(|(_, envelope)| envelope)
    .collect()
}

/// 1 - max over |lag| <= max_lag of the biased cross-correlation of a query envelope
/// (morlet_envelope) and a stored envelope.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_distance(query_envelope: Vec<Option<f64>>, stored: &[u8], max_lag: i32) -> f64 {
    let stored = morlet_core::envelope::decode(stored).unwrap_or_else(|e| pgrx::error!("{}", e));
    if max_lag < 0 {
        pgrx::error!("max_lag must be nonnegative");
    }
    morlet_core::envelope::distance(&checked_samples(query_envelope), &stored, max_lag as usize)
        .unwrap_or_else(|_| pgrx::error!("envelope lengths differ or max_lag is too large"))
}

/// Zero-padded shift of a query envelope by `lag` values. Its inner product with a stored
/// envelope is the distance's lag term, so probe search can use an inner-product index.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_probe(query_envelope: Vec<Option<f64>>, lag: i32) -> Vec<f64> {
    morlet_core::envelope::shifted(&checked_samples(query_envelope), lag as isize)
        .unwrap_or_else(|_| pgrx::error!("lag must be smaller than the envelope length"))
}

/// `simple-v1` values of one segment, in the order of morlet_simple_value_names().
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_simple_values(samples: Vec<Option<f64>>, sample_rate_hz: f64) -> Vec<f64> {
    morlet_core::values::simple(&checked_samples(samples), sample_rate_hz)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

/// calibrated-v1 summary of one query against history rows (flattened, `width` values
/// each) with per-value spreads (NULL = undefined): {score, score_quantile, top value index
/// (1-based, 0 if none), history size}. Elements are NULL when undefined. Rows of simple-v1
/// width are simple-v1 values: a value whose history median is negligible against the
/// history's median rms is skipped (morlet_simple_value_floors).
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_calibrated_score(
    query: Vec<Option<f64>>,
    history: Vec<Option<f64>>,
    width: i32,
    spreads: Vec<Option<f64>>,
) -> Vec<Option<f64>> {
    let q = checked_samples(query);
    let flat = checked_samples(history);
    let w = width as usize;
    if w == 0 || q.len() != w || spreads.len() != w || flat.len() % w != 0 {
        pgrx::error!("query, spreads and history rows must all have width values");
    }
    let rows: Vec<&[f64]> = flat.chunks_exact(w).collect();
    let floors: Vec<f64> = if w == morlet_core::values::VALUE_NAMES.len() && !rows.is_empty() {
        let mut rms: Vec<f64> = rows.iter().map(|r| r[morlet_core::values::RMS]).collect();
        rms.sort_by(f64::total_cmp);
        let n = rms.len();
        let level = if n % 2 == 1 {
            rms[n / 2]
        } else {
            (rms[n / 2 - 1] + rms[n / 2]) / 2.0
        };
        morlet_core::values::negligible_floors(level).to_vec()
    } else {
        Vec::new()
    };
    let dev = morlet_core::compare::calibrated_deviations(&q, &rows, &spreads, &floors);
    let top = dev
        .iter()
        .enumerate()
        .filter_map(|(i, d)| d.map(|d| (i, d)))
        .max_by(|a, b| a.1.total_cmp(&b.1))
        .map(|(i, _)| (i + 1) as f64)
        .unwrap_or(0.0);
    vec![
        morlet_core::compare::score(&q, &rows, &spreads, &floors),
        if rows.len() <= 2000 {
            morlet_core::compare::score_quantile(&q, &rows, &spreads, &floors)
        } else {
            None
        },
        Some(top),
        Some(rows.len() as f64),
    ]
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_simple_value_names() -> Vec<&'static str> {
    morlet_core::values::VALUE_NAMES.to_vec()
}

/// Per simple-v1 value, the largest history median treated as zero, given the history's
/// median rms: comparisons skip such a value, as they skip a median of exactly zero.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_simple_value_floors(level: f64) -> Vec<f64> {
    morlet_core::values::negligible_floors(level).to_vec()
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_simple_values_version() -> &'static str {
    morlet_core::values::VALUES_VERSION
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_valid_encoded_chunk(encoded: &[u8], sample_count: i32) -> bool {
    let samples = morlet_core::rawcodec::decode(encoded).unwrap_or_else(|e| pgrx::error!("{}", e));
    if samples.len() != sample_count as usize {
        pgrx::error!("encoded chunk length does not match sample_count");
    }
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
    fn encoded_chunks_roundtrip_bitwise_and_report_storage() {
        Spi::run("SELECT morlet_create_stream_collection('public','encoded_streams'); SELECT morlet_start_recording('encoded_streams','counts',100,'HHZ','counts','{}',512,256,'none','morlet-energy-lowrate-v1'); SELECT morlet_start_recording('encoded_streams','milli',4096,'x','g','{}',512,256,'none','morlet-energy-v1',0.001); SELECT morlet_start_recording('encoded_streams','floats',4096,'x','g'); CREATE TEMP TABLE source AS SELECT i,round(1000*sin(i/7.)+((i*7919)%61))::float8 AS counts,(round(1000*sin(i/7.))/1000)::float8 AS milli,sin(i/7.)::float8 AS floats FROM generate_series(0,9999) i; SELECT morlet_append_chunk('encoded_streams','counts',0,ARRAY(SELECT counts FROM source WHERE i<4096 ORDER BY i)); SELECT morlet_append_chunk('encoded_streams','counts',4096,ARRAY(SELECT counts FROM source WHERE i>=4096 ORDER BY i)); SELECT morlet_append_chunk('encoded_streams','milli',0,ARRAY(SELECT milli FROM source ORDER BY i)); SELECT morlet_append_chunk('encoded_streams','floats',0,ARRAY(SELECT floats FROM source ORDER BY i))").unwrap();
        for (id, column) in [
            ("counts", "counts"),
            ("milli", "milli"),
            ("floats", "floats"),
        ] {
            // float8send compares bit patterns, so -0/+0 or rounding drift would fail here.
            let same = Spi::get_one::<bool>(&format!("SELECT (SELECT array_agg(float8send(v) ORDER BY o) FROM unnest((SELECT samples FROM morlet_read_interval('encoded_streams','{id}',4000,10000))) WITH ORDINALITY u(v,o)) = (SELECT array_agg(float8send({column}) ORDER BY i) FROM source WHERE i>=4000)")).unwrap().unwrap();
            assert!(same, "{id}");
        }
        let counts = Spi::get_three::<i64, i64, i64>("SELECT grid_chunks,raw_chunks,samples FROM morlet_recording_storage('encoded_streams','counts')").unwrap();
        assert_eq!(
            (counts.0.unwrap(), counts.1.unwrap(), counts.2.unwrap()),
            (2, 0, 10000)
        );
        let bits = Spi::get_one::<f64>(
            "SELECT stored_value_bits_per_sample FROM morlet_recording_storage('encoded_streams','counts')",
        )
        .unwrap()
        .unwrap();
        assert!(bits < 16.0, "{bits}");
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT grid_chunks FROM morlet_recording_storage('encoded_streams','milli')"
            )
            .unwrap()
            .unwrap(),
            1
        );
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT float_chunks FROM morlet_recording_storage('encoded_streams','floats')"
            )
            .unwrap()
            .unwrap(),
            1
        );
        // Identical replay is a no-op against the decoded chunk.
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_append_chunk('encoded_streams','counts',0,ARRAY(SELECT counts FROM source WHERE i<4096 ORDER BY i))").unwrap().unwrap(), 10000);
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM encoded_streams_chunks")
                .unwrap()
                .unwrap(),
            4
        );
    }
    #[pg_test]
    fn storage_reports_separate_payload_and_physical_size() {
        Spi::run("SELECT morlet_create_stream_collection('public','sized_streams'); SELECT morlet_start_recording('sized_streams','A',4096,'x','g'); SELECT morlet_start_recording('sized_streams','B',4096,'x','g'); SELECT morlet_append_chunk('sized_streams','A',0,ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(0,4095) i)); SELECT morlet_append_chunk('sized_streams','B',0,ARRAY(SELECT i::float8 FROM generate_series(0,999) i))").unwrap();
        let payload = Spi::get_two::<i64, i64>("SELECT encoded_bytes,(SELECT sum(octet_length(encoded))::bigint FROM sized_streams_chunks WHERE recording_id='A') FROM morlet_recording_storage('sized_streams','A')").unwrap();
        assert_eq!(payload.0, payload.1);
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT original_float64_bytes FROM morlet_recording_storage('sized_streams','A')"
            )
            .unwrap()
            .unwrap(),
            8 * 4096
        );
        let consistent = Spi::get_one::<bool>("SELECT recordings=2 AND samples=5096 AND original_float64_bytes=8*5096 AND total_bytes=recordings_table_bytes+chunk_table_bytes+segment_table_bytes AND chunk_table_bytes=pg_total_relation_size('sized_streams_chunks') AND segment_table_bytes>0 FROM morlet_collection_storage('sized_streams')").unwrap().unwrap();
        assert!(consistent);
    }
    #[pg_test(error = "corrupt encoded chunk: checksum mismatch")]
    fn reads_verify_chunk_integrity() {
        // Bypass the table CHECK, as storage corruption would, then read.
        Spi::run("SELECT morlet_create_stream_collection('public','rot_streams'); SELECT morlet_start_recording('rot_streams','A',4096,'x','g'); SELECT morlet_append_chunk('rot_streams','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,999) i)); ALTER TABLE rot_streams_chunks DROP CONSTRAINT rot_streams_chunks_check; UPDATE rot_streams_chunks SET encoded=set_byte(encoded,20,get_byte(encoded,20) # 1); SELECT * FROM morlet_read_interval('rot_streams','A',0,10)").unwrap();
    }
    #[pg_test]
    fn decode_interval_slices_chunks_and_leaves_gaps_out() {
        let got = Spi::get_one::<Vec<f64>>("SELECT morlet_decode_interval(ARRAY[morlet_encode_chunk(ARRAY[1,2,3]::float8[],1),morlet_encode_chunk(ARRAY[6,7,8]::float8[],1)],ARRAY[0,5]::bigint[],1,7)").unwrap().unwrap();
        assert_eq!(got, vec![2.0, 3.0, 6.0, 7.0]);
        let empty = Spi::get_one::<Vec<f64>>(
            "SELECT morlet_decode_interval('{}'::bytea[],'{}'::bigint[],0,10)",
        )
        .unwrap()
        .unwrap();
        assert!(empty.is_empty());
    }
    #[pg_test(error = "chunk starts must be strictly ascending")]
    fn decode_interval_requires_ascending_starts() {
        Spi::run("SELECT morlet_decode_interval(ARRAY[morlet_encode_chunk(ARRAY[1]::float8[],1),morlet_encode_chunk(ARRAY[2]::float8[],1)],ARRAY[5,0]::bigint[],0,10)").unwrap();
    }
    #[pg_test(error = "chunks and starts differ in length")]
    fn decode_interval_requires_matching_arrays() {
        Spi::run("SELECT morlet_decode_interval(ARRAY[morlet_encode_chunk(ARRAY[1]::float8[],1)],ARRAY[0,5]::bigint[],0,10)").unwrap();
    }
    #[pg_test(error = "corrupt encoded chunk: checksum mismatch")]
    fn encoded_chunk_check_rejects_checksum_mismatch() {
        Spi::run("SELECT morlet_create_stream_collection('public','crc_streams'); SELECT morlet_start_recording('crc_streams','A',4096,'x','g'); SELECT morlet_append_chunk('crc_streams','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,999) i)); UPDATE crc_streams_chunks SET encoded=set_byte(encoded,20,get_byte(encoded,20) # 1)").unwrap();
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn encoded_chunks_reject_conflicting_replay() {
        Spi::run("SELECT morlet_create_stream_collection('public','encoded_replay'); SELECT morlet_start_recording('encoded_replay','A',4096,'x','g'); SELECT morlet_append_chunk('encoded_replay','A',0,array_fill(3.::float8,ARRAY[137])); SELECT morlet_append_chunk('encoded_replay','A',0,array_cat(array_fill(3.::float8,ARRAY[136]),ARRAY[3.5::float8]))").unwrap();
    }
    #[pg_test(error = "corrupt encoded chunk: checksum mismatch")]
    fn encoded_chunk_check_rejects_direct_corruption() {
        Spi::run("SELECT morlet_create_stream_collection('public','encoded_corrupt'); SELECT morlet_start_recording('encoded_corrupt','A',4096,'x','g'); INSERT INTO encoded_corrupt_chunks VALUES ('A',0,3,morlet_encode_chunk(ARRAY[1,2,3]::float8[],1)||'\\x00'::bytea)").unwrap();
    }
    #[pg_test(error = "encoded chunk length does not match sample_count")]
    fn encoded_chunk_check_rejects_wrong_count() {
        Spi::run("SELECT morlet_create_stream_collection('public','encoded_count'); SELECT morlet_start_recording('encoded_count','A',4096,'x','g'); INSERT INTO encoded_count_chunks VALUES ('A',0,4,morlet_encode_chunk(ARRAY[1,2,3]::float8[],1))").unwrap();
    }
    #[pg_test(error = "recording metadata or extraction specification conflicts with import")]
    fn ensure_recording_rejects_changed_quantum() {
        Spi::run("SELECT morlet_create_stream_collection('public','quantum_import'); SELECT morlet_ensure_recording('quantum_import','A',4096,'x','g','{}',512,256,'zscore','morlet-energy-v1',0.001); SELECT morlet_ensure_recording('quantum_import','A',4096,'x','g','{}',512,256,'zscore','morlet-energy-v1',0.001); SELECT morlet_ensure_recording('quantum_import','A',4096,'x','g','{}',512,256,'zscore','morlet-energy-v1',1)").unwrap();
    }
    #[pg_test]
    fn legacy_float_chunk_collections_keep_working() {
        // The chunk table layout created before encoded storage.
        Spi::run("CREATE TABLE legacy_streams (recording_id text PRIMARY KEY, sample_rate_hz float8 NOT NULL, channel text NOT NULL, unit text NOT NULL, metadata jsonb NOT NULL, checkpoint jsonb NOT NULL); CREATE TABLE legacy_streams_chunks (recording_id text NOT NULL REFERENCES legacy_streams ON DELETE CASCADE, start_sample bigint NOT NULL, samples float8[] NOT NULL, PRIMARY KEY(recording_id,start_sample)); CREATE TABLE legacy_streams_segments (recording_id text NOT NULL REFERENCES legacy_streams ON DELETE CASCADE, start_sample bigint NOT NULL, features float8[] NOT NULL, PRIMARY KEY(recording_id,start_sample)); SELECT morlet_ensure_recording('legacy_streams','A',4096,'x','g'); SELECT morlet_append_chunk('legacy_streams','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,599) i)); SELECT morlet_append_chunk('legacy_streams','A',600,ARRAY(SELECT i::float8 FROM generate_series(600,1199) i)); SELECT morlet_append_chunk('legacy_streams','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,599) i))").unwrap();
        assert_eq!(
            Spi::get_one::<Vec<f64>>(
                "SELECT samples FROM morlet_read_interval('legacy_streams','A',598,602)"
            )
            .unwrap()
            .unwrap(),
            vec![598., 599., 600., 601.]
        );
        let storage = Spi::get_three::<i64, i64, i64>(
            "SELECT chunks,samples,grid_chunks FROM morlet_recording_storage('legacy_streams','A')",
        )
        .unwrap();
        assert_eq!(
            (storage.0.unwrap(), storage.1.unwrap(), storage.2),
            (2, 1200, None)
        );
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT indexed_segments FROM morlet_stream_status('legacy_streams','A')"
            )
            .unwrap()
            .unwrap(),
            3
        );
    }
    #[pg_test(error = "sample_quantum requires a collection with encoded chunk storage")]
    fn legacy_collections_reject_sample_quantum() {
        Spi::run("CREATE TABLE legacy_quantum (recording_id text PRIMARY KEY, sample_rate_hz float8 NOT NULL, channel text NOT NULL, unit text NOT NULL, metadata jsonb NOT NULL, checkpoint jsonb NOT NULL); CREATE TABLE legacy_quantum_chunks (recording_id text, start_sample bigint, samples float8[]); CREATE TABLE legacy_quantum_segments (recording_id text, start_sample bigint, features float8[]); SELECT morlet_start_recording('legacy_quantum','A',4096,'x','g','{}',512,256,'zscore','morlet-energy-v1',0.5)").unwrap();
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
    #[pg_test]
    fn raw_only_recordings_store_samples_without_segments() {
        Spi::run("SELECT morlet_create_stream_collection('public','raw_only'); SELECT morlet_start_recording('raw_only','slow',10,'T','C','{}',512,256,'zscore','none'); SELECT morlet_start_recording('raw_only','fast',4096,'x','g','{}',512,256,'zscore','none',0.001); SELECT morlet_start_recording('raw_only','indexed',4096,'x','g','{}',512,256,'zscore'); SELECT morlet_append_chunk('raw_only',r,0,ARRAY(SELECT (round(1000*sin(i/7.))/1000)::float8 FROM generate_series(0,4999) i)) FROM unnest(ARRAY['slow','fast','indexed']) r; SELECT morlet_append_chunk('raw_only','fast',5000,ARRAY(SELECT (round(1000*sin(i/7.))/1000)::float8 FROM generate_series(5000,5999) i))").unwrap();
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT count(*) FROM raw_only_segments WHERE recording_id<>'indexed'"
            )
            .unwrap(),
            Some(0)
        );
        assert_eq!(Spi::get_one::<String>("SELECT received_samples||' '||indexed_segments||' '||extractor_version FROM morlet_stream_status('raw_only','fast')").unwrap().unwrap(), "6000 0 none");
        assert_eq!(Spi::get_one::<bool>("SELECT samples=ARRAY(SELECT (round(1000*sin(i/7.))/1000)::float8 FROM generate_series(100,5899) i) FROM morlet_read_interval('raw_only','fast',100,5900)").unwrap(), Some(true));
        // Feature search sees only recordings that have segments.
        assert_eq!(Spi::get_one::<i64>("SELECT count(DISTINCT recording_id) FROM morlet_search_streams('raw_only',ARRAY(SELECT sin(i/7.) FROM generate_series(0,511) i),4096,'x','g','{}','zscore',100)").unwrap(), Some(1));
    }
    fn contract_fixture() {
        // A and its exact copy D (ties), B with a shifted burst, C noise; 16,384 samples each
        envelope_fixture();
        Spi::run("SELECT morlet_start_recording('env','D',100,'HHZ','counts','{\"g\":\"d\"}',512,256,'zscore','none');
                  SELECT morlet_append_chunk('env','D',0,(SELECT samples FROM morlet_read_interval('env','A',0,16384)))").unwrap();
    }

    #[pg_test]
    fn search_contract_ties_order_by_recording_then_start() {
        contract_fixture();
        Spi::run("SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C','D']) r").unwrap();
        // D is a bitwise copy of A, so D's windows tie with A's; ties order by recording_id, then start_sample
        let rows = Spi::get_one::<String>("SELECT string_agg(recording_id||':'||start_sample,',' ORDER BY ord) FROM (SELECT *, row_number() OVER () ord FROM morlet_search_envelopes('env',(SELECT samples FROM morlet_read_interval('env','B',8192,10240)),100,'HHZ','counts','{}',4,0.075,false)) x").unwrap().unwrap();
        let hits: Vec<&str> = rows.split(',').collect();
        let mut pairs = 0;
        for (k, h) in hits.iter().enumerate() {
            // the last hit may be an A whose tied D copy falls just beyond top_k
            if let (Some(start), true) = (h.strip_prefix("A:"), k + 1 < hits.len()) {
                assert_eq!(
                    hits.get(k + 1).copied(),
                    Some(format!("D:{start}").as_str()),
                    "tie order: {rows}"
                );
                pairs += 1;
            }
        }
        assert!(pairs > 0, "{rows}");
    }

    #[pg_test]
    fn search_contract_fewer_than_top_k_and_searchable_at_commit() {
        contract_fixture();
        // nothing built yet: no envelopes, so no results (not an error)
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT count(*) FROM morlet_search_envelope_interval('env','A',2048,4096,'{}',10)"
            )
            .unwrap(),
            Some(0)
        );
        // append and build in one transaction (this test's): searchable immediately
        Spi::run("SELECT morlet_build_envelopes('env','D',2048,1024)").unwrap();
        // only D is eligible (filter) and overlapping windows are suppressed: at most 16384/2048 = 8 hits,
        // fewer than top_k = 20, returned without error
        let n = Spi::get_one::<i64>(r#"SELECT count(*) FROM morlet_search_envelope_interval('env','A',2048,4096,'{"g":"d"}',20)"#).unwrap().unwrap();
        assert!(n > 0 && n <= 8, "{n}");
        assert_eq!(Spi::get_one::<i64>(r#"WITH h AS (SELECT * FROM morlet_search_envelope_interval('env','A',2048,4096,'{"g":"d"}',20))
            SELECT count(*) FROM h a JOIN h b ON a.start_sample<b.start_sample AND b.start_sample<a.end_sample"#).unwrap(), Some(0));
    }

    #[pg_test(error = "sample_rate_hz must be finite and in (0,200000]")]
    fn raw_only_recordings_validate_rate() {
        Spi::run("SELECT morlet_create_stream_collection('public','raw_only_bad'); SELECT morlet_start_recording('raw_only_bad','A',0,'x','g','{}',512,256,'zscore','none')").unwrap();
    }
    fn envelope_fixture() {
        // A: noise with a burst centred at 3000; B: the same burst at 10100 (68 samples later
        // relative to its window) plus other noise; C: noise only.
        Spi::run("SELECT morlet_create_stream_collection('public','env'); SELECT morlet_enable_envelopes('env'); SELECT morlet_start_recording('env',r,100,'HHZ','counts','{}',512,256,'zscore','none') FROM unnest(ARRAY['A','B','C']) r; CREATE TEMP TABLE burst AS SELECT i,round(800*exp(-power((i-300)/40.,2))*sin(i/2.))::float8 AS v FROM generate_series(0,599) i; SELECT morlet_append_chunk('env',r,0,ARRAY(SELECT (round(20*sin(s.i*1.7+off)+((s.i*7919+off*13)%23))+coalesce(b.v,0))::float8 FROM generate_series(0,16383) s(i) LEFT JOIN burst b ON b.i=s.i-at ORDER BY s.i)) FROM (VALUES ('A',2700,1),('B',9800,2),('C',-100000,3)) x(r,at,off)").unwrap();
    }

    fn tenant_fixture() {
        Spi::run(r#"
            SELECT morlet_create_stream_collection('public','ten');
            SELECT morlet_enable_envelopes('ten');
            SELECT morlet_start_recording('ten',r,100,'HHZ','counts',jsonb_build_object('tenant',t),512,256,'zscore','none')
              FROM (VALUES ('a1','a'),('b1','b')) v(r,t);
            SELECT morlet_append_chunk('ten',r,0,ARRAY(SELECT round(100*sin(i/7.0+o))::float8 FROM generate_series(0,8191) i))
              FROM (VALUES ('a1',1),('b1',2)) v(r,o);
            SELECT morlet_build_envelopes('ten',r,2048,1024) FROM unnest(ARRAY['a1','b1']) r;
            CREATE ROLE morlet_tenant_a;
            GRANT USAGE ON SCHEMA public TO morlet_tenant_a;
            ALTER TABLE ten ENABLE ROW LEVEL SECURITY;
            CREATE POLICY own ON ten FOR SELECT TO morlet_tenant_a USING (metadata->>'tenant'='a');
        "#).unwrap();
    }

    #[pg_test]
    fn row_security_covers_companion_tables() {
        tenant_fixture();
        assert_eq!(
            Spi::get_one::<bool>("SELECT morlet_row_security_enabled('ten')").unwrap(),
            Some(false)
        );
        assert_eq!(
            Spi::get_one::<i32>("SELECT morlet_enable_row_security('ten')").unwrap(),
            Some(3)
        ); // chunks, segments, envelopes
        Spi::run("SELECT morlet_enable_context('ten')").unwrap(); // created later: covered automatically
        Spi::run("GRANT SELECT ON ten, ten_chunks, ten_segments, ten_envelopes, ten_ctx, ten_ctx_segments, ten_ctx_values, ten_labels TO morlet_tenant_a").unwrap();
        Spi::run("SET LOCAL ROLE morlet_tenant_a").unwrap();
        for t in [
            "ten",
            "ten_chunks",
            "ten_segments",
            "ten_envelopes",
            "ten_ctx",
            "ten_ctx_segments",
            "ten_labels",
        ] {
            assert_eq!(
                Spi::get_one::<i64>(&format!("SELECT count(*) FROM {t} WHERE recording_id='b1'"))
                    .unwrap(),
                Some(0),
                "{t} leaks the hidden recording"
            );
        }
        assert!(
            Spi::get_one::<i64>("SELECT count(*) FROM ten_envelopes WHERE recording_id='a1'")
                .unwrap()
                .unwrap()
                > 0
        );
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(DISTINCT recording_id,',') FROM morlet_search_envelope_interval('ten','a1',0,2048,'{}',10)").unwrap(), Some("a1".into()));
    }

    #[pg_test(error = "new row violates row-level security policy for table \"ten_envelopes\"")]
    fn row_security_rejects_writes_for_hidden_recordings() {
        tenant_fixture();
        Spi::run("SELECT morlet_enable_row_security('ten'); GRANT SELECT, INSERT ON ten, ten_envelopes TO morlet_tenant_a; SET LOCAL ROLE morlet_tenant_a").unwrap();
        Spi::run("INSERT INTO ten_envelopes SELECT 'b1',2048,99999,envelope FROM ten_envelopes WHERE recording_id='a1' LIMIT 1").unwrap();
    }

    #[pg_test]
    fn row_security_covers_probe_tables() {
        if !pgvector_available() {
            return; // pgvector is optional; CI's pgvector job runs this
        }
        Spi::run(r#"
            CREATE EXTENSION IF NOT EXISTS vector;
            SELECT morlet_create_stream_collection('public','tenp');
            SELECT morlet_enable_envelopes('tenp');
            SELECT morlet_start_recording('tenp',r,100,'HHZ','counts',jsonb_build_object('tenant',t),512,256,'zscore','none')
              FROM (VALUES ('a1','a'),('b1','b')) v(r,t);
            SELECT morlet_append_chunk('tenp',r,0,ARRAY(SELECT round(100*sin(i/7.0+o))::float8 FROM generate_series(0,8191) i))
              FROM (VALUES ('a1',1),('b1',2)) v(r,o);
            SELECT morlet_build_envelopes('tenp',r,2048,1024) FROM unnest(ARRAY['a1','b1']) r;
            CREATE ROLE morlet_tenant_p;
            GRANT USAGE ON SCHEMA public TO morlet_tenant_p;
            ALTER TABLE tenp ENABLE ROW LEVEL SECURITY;
            CREATE POLICY own ON tenp FOR SELECT TO morlet_tenant_p USING (metadata->>'tenant'='a');
            SELECT morlet_enable_row_security('tenp');
            SELECT morlet_enable_envelope_probes('tenp',2048);
            GRANT SELECT ON tenp, tenp_chunks, tenp_envelopes, tenp_probes_2048 TO morlet_tenant_p;
            SET LOCAL ROLE morlet_tenant_p;
        "#).unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM tenp_probes_2048 WHERE recording_id='b1'")
                .unwrap(),
            Some(0)
        );
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(DISTINCT recording_id,',') FROM morlet_search_envelope_probes('tenp','a1',0,2048,'{}',10,0.075,true,true,50,5,0)").unwrap(), Some("a1".into()));
    }

    #[pg_test]
    fn collection_status_reports_progress_and_backlog() {
        envelope_fixture(); // 3 recordings x 16,384 samples
        let row = |q: &str| {
            Spi::get_one::<String>(&format!(
                "SELECT {q}::text FROM morlet_collection_status('env') LIMIT 1"
            ))
            .unwrap()
        };
        // nothing built: one row, everything unbuilt
        assert_eq!(row("(window_size, recordings, raw_samples, envelopes, recordings_without_envelopes, unbuilt_samples)"), Some("(,3,49152,0,3,49152)".into()));
        // three windows of 2048 at stride 1024 per recording: last window ends at 4096
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024,3) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        assert_eq!(
            row("(window_size, envelopes, recordings_without_envelopes, unbuilt_samples)"),
            Some("(2048,9,0,36864)".into())
        );
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        assert_eq!(
            row("(envelopes, unbuilt_samples, probe_rows, row_security)"),
            Some("(45,0,,f)".into())
        );
        assert!(Spi::get_one::<bool>(
            "SELECT chunk_bytes > 0 AND envelope_bytes > 0 FROM morlet_collection_status('env')"
        )
        .unwrap()
        .unwrap());
        if pgvector_available() {
            Spi::run("CREATE EXTENSION IF NOT EXISTS vector; SELECT morlet_enable_envelope_probes('env',2048)").unwrap();
            assert_eq!(
                row("(probe_rows, probe_backlog, probe_index_bytes > 0)"),
                Some("(45,0,t)".into())
            );
        }
    }

    #[pg_test]
    fn envelope_functions_match_the_core_definition() {
        assert_eq!(
            Spi::get_one::<String>("SELECT morlet_envelope_version()")
                .unwrap()
                .unwrap(),
            "xc-envelope-f16-v1"
        );
        assert_eq!(Spi::get_one::<i32>("SELECT cardinality(morlet_envelope(ARRAY(SELECT sin(i/5.)*i FROM generate_series(0,4095) i)))").unwrap(), Some(410));
        assert_eq!(Spi::get_one::<i32>("SELECT octet_length(morlet_envelope_encode(ARRAY(SELECT sin(i/5.)*i FROM generate_series(0,4095) i)))").unwrap(), Some(821));
        assert_eq!(Spi::get_one::<bool>("WITH x AS (SELECT ARRAY(SELECT sin(i/5.)*i FROM generate_series(0,2047) i) s) SELECT morlet_envelope_distance(morlet_envelope(s),morlet_envelope_encode(s),20)<1e-3 FROM x").unwrap(), Some(true));
    }

    fn pgvector_available() -> bool {
        Spi::get_one::<bool>(
            "SELECT EXISTS(SELECT 1 FROM pg_available_extensions WHERE name='vector')",
        )
        .unwrap()
        .unwrap()
    }

    #[pg_test]
    fn envelope_probe_is_the_lag_term() {
        // inner product of the probe with a stored envelope = n * (1 - distance) at its best lag
        assert_eq!(Spi::get_one::<bool>("WITH x AS (SELECT morlet_envelope(ARRAY(SELECT sin(i/5.)*i FROM generate_series(0,4095) i)) q, morlet_envelope_encode(ARRAY(SELECT cos(i/7.)*(i%300) FROM generate_series(0,4095) i)) g), s AS (SELECT max((SELECT sum(a*b) FROM unnest(morlet_envelope_probe(q,l),morlet_envelope_decode(g)) t(a,b))) m FROM x, generate_series(-30,30) l) SELECT abs(1-(SELECT m FROM s)/410-(SELECT morlet_envelope_distance(q,g,30) FROM x))<1e-9").unwrap(), Some(true));
        assert_eq!(
            Spi::get_one::<String>(
                "SELECT morlet_envelope_probe(ARRAY[1,2,3,4]::float8[],-1)::text"
            )
            .unwrap()
            .unwrap(),
            "{0,1,2,3}"
        );
        assert_eq!(
            Spi::get_one::<String>(
                "SELECT morlet_envelope_probe(ARRAY[1,2,3,4]::float8[],2)::text"
            )
            .unwrap()
            .unwrap(),
            "{3,4,0,0}"
        );
    }

    #[pg_test]
    fn probe_search_routes_and_respects_filters() {
        if !pgvector_available() {
            return; // pgvector is optional; CI does not install it
        }
        Spi::run("CREATE EXTENSION IF NOT EXISTS vector").unwrap();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT morlet_enable_envelope_probes('env',2048)").unwrap();
        Spi::run(r#"UPDATE env SET metadata='{"site":"north"}' WHERE recording_id='B'"#).unwrap();
        let routed = |filter: &str, threshold: i32| {
            format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_probes('env','A',2048,4096,'{filter}',10,0.075,true,true,100,5,{threshold})")
        };
        let exact = |filter: &str| {
            format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_interval('env','A',2048,4096,'{filter}',10)")
        };
        for filter in ["{}", r#"{"site":"north"}"#] {
            let reference = Spi::get_one::<String>(&exact(filter)).unwrap();
            // routed to exact search (default threshold) and forced onto filter-aware probes (threshold 0)
            assert_eq!(
                Spi::get_one::<String>(&routed(filter, 5000)).unwrap(),
                reference
            );
            assert_eq!(
                Spi::get_one::<String>(&routed(filter, 0)).unwrap(),
                reference
            );
        }
        // filtered probes never return windows outside the filter
        assert_eq!(Spi::get_one::<i64>(r#"SELECT count(*) FROM morlet_search_envelope_probes('env','A',2048,4096,'{"site":"north"}',10,0.075,true,true,100,5,0) WHERE recording_id<>'B'"#).unwrap(), Some(0));
        // session settings are restored
        assert_eq!(
            Spi::get_one::<String>("SELECT current_setting('hnsw.iterative_scan')")
                .unwrap()
                .unwrap(),
            "off"
        );
        assert_eq!(
            Spi::get_one::<String>("SELECT current_setting('hnsw.ef_search')")
                .unwrap()
                .unwrap(),
            "40"
        );
    }

    #[pg_test(
        error = "probe search is not enabled for window_size 2048 (morlet_enable_envelope_probes)"
    )]
    fn probe_search_requires_enabling() {
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT * FROM morlet_search_envelope_probes('env','A',2048,4096)").unwrap();
    }

    #[pg_test]
    fn probe_search_is_maintained_and_exact_on_small_galleries() {
        if !pgvector_available() {
            return; // pgvector is optional; CI does not install it
        }
        Spi::run("CREATE EXTENSION IF NOT EXISTS vector").unwrap();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024,3) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        assert_eq!(
            Spi::get_one::<String>("SELECT morlet_enable_envelope_probes('env',2048)::text")
                .unwrap()
                .unwrap(),
            "env_probes_2048"
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM env_probes_2048").unwrap(),
            Some(9)
        ); // backfilled
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        assert_eq!(Spi::get_one::<bool>("SELECT (SELECT count(*) FROM env_probes_2048)=(SELECT count(*) FROM env_envelopes WHERE window_size=2048)").unwrap(), Some(true));
        // per_probe covers the whole gallery here, so probe search must equal the exact search
        let exact = "SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_interval('env','A',2048,4096,'{}',10)";
        let probe = "SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_probes('env','A',2048,4096,'{}',10,0.075,true,true,100)";
        assert_eq!(
            Spi::get_one::<String>(exact).unwrap(),
            Spi::get_one::<String>(probe).unwrap()
        );
        assert_eq!(Spi::get_one::<String>("SELECT recording_id||':'||start_sample FROM morlet_search_envelope_probes('env','A',2048,4096,'{}',1)").unwrap().unwrap(), "B:9216");
        // the session's ef_search is restored
        assert_eq!(
            Spi::get_one::<String>("SELECT current_setting('hnsw.ef_search')")
                .unwrap()
                .unwrap(),
            "40"
        );
        // rows follow their envelopes
        Spi::run("DELETE FROM env_envelopes WHERE recording_id='C'").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM env_probes_2048 WHERE recording_id='C'")
                .unwrap(),
            Some(0)
        );
    }

    #[pg_test]
    fn envelope_search_refetches_after_suppression() {
        envelope_fixture();
        // stride 64 at 2048-sample windows: each pick suppresses up to 63 neighbours, so the
        // first batch of 200 rows can run out before top_k survive
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,64) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        // reference: the previous cursor-loop algorithm, scanning every row in order
        Spi::run(r#"CREATE FUNCTION pg_temp.ranked_ref(top_k integer,suppress boolean)
RETURNS TABLE(recording_id text,start_sample bigint,distance float8) LANGUAGE plpgsql AS $$
DECLARE q float8[]; hit record; ids text[]:='{}'; st bigint[]:='{}'; en bigint[]:='{}'; i integer; blocked boolean;
BEGIN
  q:=morlet_envelope((SELECT samples FROM morlet_read_interval('env','A',2048,4096)));
  FOR hit IN SELECT e.recording_id r,e.start_sample s,e.start_sample+2048 e_,morlet_envelope_distance(q,e.envelope,15) d
    FROM env_envelopes e WHERE e.window_size=2048 AND (e.recording_id<>'A' OR e.start_sample+2048<=2048 OR e.start_sample>=4096)
    ORDER BY d,e.recording_id,e.start_sample LOOP
    blocked:=false;
    IF suppress THEN
      FOR i IN 1..coalesce(cardinality(ids),0) LOOP
        IF ids[i]=hit.r AND st[i]<hit.e_ AND en[i]>hit.s THEN blocked:=true; EXIT; END IF;
      END LOOP;
    END IF;
    CONTINUE WHEN blocked;
    recording_id:=hit.r; start_sample:=hit.s; distance:=hit.d; RETURN NEXT;
    ids:=ids||hit.r; st:=st||hit.s; en:=en||hit.e_;
    EXIT WHEN cardinality(ids)=top_k;
  END LOOP;
END $$"#).unwrap();
        for (k, sup) in [(10, true), (30, true), (10, false)] {
            let new = format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_interval('env','A',2048,4096,'{{}}',{k},0.075,true,{sup},100000)");
            let reference = format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM pg_temp.ranked_ref({k},{sup})");
            assert_eq!(
                Spi::get_one::<String>(&new).unwrap(),
                Spi::get_one::<String>(&reference).unwrap(),
                "top_k {k}, suppress {sup}"
            );
        }
        // 3 recordings of 16384 samples hold at most 24 non-overlapping 2048-sample windows
        assert!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_envelope_interval('env','A',2048,4096,'{}',30,0.075,true,true,100000)").unwrap().unwrap() < 30);
    }

    #[pg_test]
    fn batched_envelope_build_equals_the_per_window_encoding() {
        envelope_fixture();
        // uneven calls so batches start mid-recording; then one large call
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024,5) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT morlet_build_envelopes('env',r,2048,1024,300) FROM unnest(ARRAY['A','B','C']) r").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM env_envelopes").unwrap(),
            Some(45)
        );
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(e.envelope = (SELECT morlet_envelope_encode(i.samples) FROM morlet_read_interval('env',e.recording_id,e.start_sample,e.start_sample+2048) i)) FROM env_envelopes e").unwrap(), Some(true));
        assert_eq!(Spi::get_one::<i64>("SELECT cardinality(morlet_envelopes_from_chunks(ARRAY[]::bytea[],ARRAY[]::bigint[],0,4096,2048,1024,10))::bigint").unwrap(), Some(0));
    }

    #[pg_test]
    fn envelope_build_resumes_and_search_finds_matching_burst() {
        envelope_fixture();
        assert_eq!(Spi::get_one::<i64>("SELECT sum(morlet_build_envelopes('env',r,2048,1024,3))::bigint FROM unnest(ARRAY['A','B','C']) r").unwrap(), Some(9));
        assert_eq!(Spi::get_one::<i64>("SELECT sum(morlet_build_envelopes('env',r,2048,1024))::bigint FROM unnest(ARRAY['A','B','C']) r").unwrap(), Some(36));
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_envelopes('env','A',2048,1024)").unwrap(),
            Some(0)
        );
        Spi::run("SELECT morlet_append_chunk('env','A',16384,array_fill(1.::float8,ARRAY[2048]))")
            .unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_envelopes('env','A',2048,1024)").unwrap(),
            Some(2)
        );
        // Query: A's window around its burst; best match outside A is B's burst window.
        let top = Spi::get_one::<String>("SELECT recording_id||':'||start_sample FROM morlet_search_envelope_interval('env','A',2048,4096,'{}',3) LIMIT 1").unwrap().unwrap();
        assert_eq!(top, "B:9216");
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_envelope_interval('env','A',2048,4096,'{}',10) WHERE recording_id='A' AND start_sample<4096 AND start_sample+2048>2048").unwrap(), Some(0));
        // Results never overlap within one recording.
        assert_eq!(Spi::get_one::<i64>("WITH h AS (SELECT * FROM morlet_search_envelopes('env',(SELECT samples FROM morlet_read_interval('env','A',2048,4096)),100,'HHZ','counts','{}',20)) SELECT count(*) FROM h a JOIN h b ON a.recording_id=b.recording_id AND a.start_sample<b.start_sample AND b.start_sample<a.end_sample").unwrap(), Some(0));
        assert_eq!(Spi::get_one::<bool>("SELECT envelope_table_bytes>0 AND total_bytes=recordings_table_bytes+chunk_table_bytes+segment_table_bytes+envelope_table_bytes FROM morlet_collection_storage('env')").unwrap(), Some(true));
    }

    #[pg_test(error = "envelope search exceeds 5 candidates; narrow the metadata filter")]
    fn envelope_search_enforces_its_budget() {
        envelope_fixture();
        Spi::run("SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r; SELECT * FROM morlet_search_envelope_interval('env','A',0,2048,'{}',10,0.075,true,true,5)").unwrap();
    }

    #[pg_test(error = "envelopes are not enabled for this collection (morlet_enable_envelopes)")]
    fn envelope_search_requires_enabled_envelopes() {
        Spi::run("SELECT morlet_create_stream_collection('public','no_env'); SELECT * FROM morlet_search_envelopes('no_env',array_fill(0.::float8,ARRAY[512]),100,'x','g')").unwrap();
    }

    #[pg_test(error = "envelope windows must be a power of two in 512..32768 samples")]
    fn envelope_rejects_other_window_lengths() {
        Spi::run("SELECT morlet_envelope(array_fill(0.::float8,ARRAY[1000]))").unwrap();
    }

    fn context_fixture() {
        // One 30000-sample recording at 1 kHz: block N1 twice (amplitude 100 for part A,
        // 150 for part B) and N2 between them; part A covers [0,15000), part B [15000,30000).
        Spi::run("SELECT morlet_create_stream_collection('public','mill'); SELECT morlet_enable_context('mill'); SELECT morlet_start_recording('mill','r1',1000,'spindle','A','{}',512,256,'zscore','none'); SELECT morlet_append_chunk('mill','r1',k*10000,ARRAY(SELECT (CASE k WHEN 0 THEN 100 WHEN 1 THEN 50 ELSE 150 END)*sin(i/3.) FROM generate_series(0,9999) i)) FROM generate_series(0,2) k; SELECT morlet_add_context('mill','b1','[{\"recording_id\":\"r1\",\"key\":\"program\",\"value\":\"P1\",\"start_sample\":0,\"end_sample\":30000},{\"recording_id\":\"r1\",\"key\":\"block\",\"value\":\"N1\",\"start_sample\":0,\"end_sample\":10000},{\"recording_id\":\"r1\",\"key\":\"block\",\"value\":\"N2\",\"start_sample\":10000,\"end_sample\":20000},{\"recording_id\":\"r1\",\"key\":\"block\",\"value\":\"N1\",\"start_sample\":20000,\"end_sample\":30000},{\"recording_id\":\"r1\",\"key\":\"part\",\"value\":\"A\",\"start_sample\":0,\"end_sample\":15000},{\"recording_id\":\"r1\",\"key\":\"part\",\"value\":\"B\",\"start_sample\":15000,\"end_sample\":30000},{\"recording_id\":\"r1\",\"key\":\"tool_change\",\"value\":7,\"start_sample\":15000}]')").unwrap();
    }

    #[pg_test]
    fn context_segments_values_and_compare_to_history() {
        context_fixture();
        // Identical retry is a no-op.
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_add_context('mill','b1',(SELECT jsonb_agg(jsonb_build_object('recording_id',recording_id,'key',key,'value',value,'start_sample',start_sample,'end_sample',end_sample) ORDER BY ctx_id DESC) FROM mill_ctx))").unwrap(), Some(0));
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_build_context_segments('mill','r1',ARRAY['program','block'],ARRAY['part'])").unwrap(), Some(3));
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_build_context_segments('mill','r1',ARRAY['program','block'],ARRAY['part'])").unwrap(), Some(0));
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(start_sample||'-'||end_sample||':'||(context->>'block')||':'||coalesce(occurrence->>'part','?'),',' ORDER BY start_sample) FROM mill_ctx_segments").unwrap().unwrap(), "0-10000:N1:A,10000-20000:N2:?,20000-30000:N1:B");
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_context_values('mill','r1')").unwrap(),
            Some(3)
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_context_values('mill','r1')").unwrap(),
            Some(0)
        );
        // Part B's N1 against history = part A's N1 (same context, other occurrence): amplitude 150 vs 100.
        let rel = Spi::get_one::<f64>("SELECT relative_deviation FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000),min_history=>1) WHERE value_name='std'").unwrap().unwrap();
        assert!((rel - 0.5).abs() < 1e-3, "{rel}");
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(status,',') FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000)) WHERE value_name IN ('std','duration_s')").unwrap().unwrap(), "insufficient_history,insufficient_history");
        assert_eq!(Spi::get_one::<i32>("SELECT hist_n FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000),min_history=>1) LIMIT 1").unwrap(), Some(1));
        // Matching only on program widens history to N2 as well.
        assert_eq!(Spi::get_one::<i32>("SELECT hist_n FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000),ARRAY['program'],min_history=>1) LIMIT 1").unwrap(), Some(2));
        // A label excludes history.
        Spi::run("SELECT morlet_add_label('mill','r1',0,10000,'air_cut','test')").unwrap();
        assert_eq!(Spi::get_one::<i32>("SELECT hist_n FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000),exclude_labels=>ARRAY['air_cut'],min_history=>1) LIMIT 1").unwrap(), Some(0));
        Spi::run("SELECT morlet_retract_label('mill',(SELECT label_id FROM mill_labels))").unwrap();
        assert_eq!(Spi::get_one::<i32>("SELECT hist_n FROM morlet_compare_to_history('mill',(SELECT segment_id FROM mill_ctx_segments WHERE start_sample=20000),exclude_labels=>ARRAY['air_cut'],min_history=>1) LIMIT 1").unwrap(), Some(1));
        // A revision makes dependent segments stale; a rebuild replaces them.
        Spi::run("SELECT morlet_revise_context('mill',(SELECT ctx_id FROM mill_ctx WHERE key='block' AND value='\"N2\"'),'{\"end_sample\":19000}')").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM mill_ctx_segments WHERE stale").unwrap(),
            Some(1)
        );
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_build_context_segments('mill','r1',ARRAY['program','block'],ARRAY['part'])").unwrap(), Some(1));
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(start_sample||'-'||end_sample,',' ORDER BY start_sample) FROM mill_ctx_segments").unwrap().unwrap(), "0-10000,10000-19000,20000-30000");
    }

    fn cal_fixture() {
        // Blocks A (amplitude ~100) and B (~10), parts 1..6 each with small variation; part 6
        // of block A has amplitude 160.
        Spi::run("SELECT morlet_create_stream_collection('public','cal'); SELECT morlet_enable_context('cal'); SELECT morlet_start_recording('cal',b||p,1000,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['A','B']) b, generate_series(1,6) p; SELECT morlet_append_chunk('cal',b||p,0,ARRAY(SELECT (CASE WHEN b='A' AND p=6 THEN 160 WHEN b='A' THEN 100*(1+0.01*p) ELSE 10*(1+0.01*p) END)*sin(i/3.)+(i%7) FROM generate_series(0,1999) i)) FROM unnest(ARRAY['A','B']) b, generate_series(1,6) p; SELECT morlet_add_context('cal','c-'||b||p,jsonb_build_array(jsonb_build_object('recording_id',b||p,'key','block','value',b,'start_sample',0,'end_sample',2000),jsonb_build_object('recording_id',b||p,'key','part','value',p,'start_sample',0,'end_sample',2000))) FROM unnest(ARRAY['A','B']) b, generate_series(1,6) p; SELECT morlet_build_context_segments('cal',b||p,ARRAY['block'],ARRAY['part']) FROM unnest(ARRAY['A','B']) b, generate_series(1,6) p; SELECT morlet_build_context_values('cal',b||p) FROM unnest(ARRAY['A','B']) b, generate_series(1,6) p").unwrap();
    }

    #[pg_test]
    fn negligible_medians_are_skipped_like_zero() {
        // A frozen channel: parts 1..5 hold a constant level, so their std is rounding
        // residue (about 1e-14), not 0. Part 6 comes back to life with a small real std.
        Spi::run("SELECT morlet_create_stream_collection('public','frz'); SELECT morlet_enable_context('frz'); SELECT morlet_start_recording('frz','F'||p,1000,'x','bar','{}',512,256,'zscore','none') FROM generate_series(1,6) p; SELECT morlet_append_chunk('frz','F'||p,0,ARRAY(SELECT 0.1*(1+0.01*p)+CASE WHEN p=6 THEN 0.001*sin(i/3.) ELSE 0 END FROM generate_series(0,1999) i)) FROM generate_series(1,6) p; SELECT morlet_add_context('frz','c-'||p,jsonb_build_array(jsonb_build_object('recording_id','F'||p,'key','block','value','F','start_sample',0,'end_sample',2000),jsonb_build_object('recording_id','F'||p,'key','part','value',p,'start_sample',0,'end_sample',2000))) FROM generate_series(1,6) p; SELECT morlet_build_context_segments('frz','F'||p,ARRAY['block'],ARRAY['part']) FROM generate_series(1,6) p; SELECT morlet_build_context_values('frz','F'||p) FROM generate_series(1,6) p").unwrap();
        let f6 = "(SELECT segment_id FROM frz_ctx_segments WHERE recording_id='F6')";
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(v.vals[3]>0 AND v.vals[3]<1e-12) FROM frz_ctx_values v JOIN frz_ctx_segments s USING (segment_id) WHERE s.recording_id<>'F6' AND v.representation='simple-v1'").unwrap(), Some(true));
        assert_eq!(Spi::get_one::<String>(&format!("SELECT status||':'||coalesce(relative_deviation::text,'-') FROM morlet_compare_to_history('frz',{f6},min_history=>1) WHERE value_name='std'")).unwrap().unwrap(), "zero_median:-");
        assert_eq!(Spi::get_one::<String>(&format!("SELECT status FROM morlet_compare_to_history('frz',{f6},min_history=>1) WHERE value_name='kurtosis'")).unwrap().unwrap(), "ok");
        // Without the floor, std's relative deviation would be about 1e11 and decide all three.
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT score<10 AND top_value<>'std' AND calibrated_score<1000 FROM morlet_compare_summary('frz',{f6},min_history=>1)")).unwrap(), Some(true));
        assert_eq!(
            Spi::get_one::<bool>(&format!(
                "SELECT bool_and(distance<10) FROM morlet_history_neighbors('frz',{f6})"
            ))
            .unwrap(),
            Some(true)
        );
        assert_eq!(
            Spi::get_one::<Vec<f64>>("SELECT morlet_simple_value_floors(2)")
                .unwrap()
                .unwrap(),
            vec![0.0, 2e-9, 2e-9, 2e-9, 2e-9, 2e-9, 0.0, 0.0]
        );
    }

    #[pg_test]
    fn history_neighbors_rank_past_occurrences_with_labels() {
        cal_fixture();
        let a3 = "(SELECT segment_id FROM cal_ctx_segments WHERE recording_id='A3')";
        // Same context (block A), other parts; the anomalous A6 is the farthest.
        assert_eq!(Spi::get_one::<String>(&format!("SELECT string_agg(recording_id,',' ORDER BY distance) FROM morlet_history_neighbors('cal',{a3})")).unwrap().unwrap().split(',').last(), Some("A6"));
        assert_eq!(
            Spi::get_one::<i64>(&format!(
                "SELECT count(*) FROM morlet_history_neighbors('cal',{a3})"
            ))
            .unwrap(),
            Some(5)
        );
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT bool_and(cardinality(vals)=8 AND labels='[]') FROM morlet_history_neighbors('cal',{a3})")).unwrap(), Some(true));
        Spi::run("SELECT morlet_add_label('cal','A6',0,2000,'chipped','test')").unwrap();
        assert_eq!(Spi::get_one::<String>(&format!("SELECT labels->0->>'label' FROM morlet_history_neighbors('cal',{a3}) WHERE recording_id='A6'")).unwrap().unwrap(), "chipped");
        assert_eq!(Spi::get_one::<i64>(&format!("SELECT count(*) FROM morlet_history_neighbors('cal',{a3},exclude_labels=>ARRAY['chipped'])")).unwrap(), Some(4));
        assert_eq!(
            Spi::get_one::<i64>(&format!(
                "SELECT count(*) FROM morlet_history_neighbors('cal',{a3},top_k=>2)"
            ))
            .unwrap(),
            Some(2)
        );
    }

    #[pg_test]
    fn calibrated_summary_ranks_an_anomalous_part_against_its_context() {
        // Blocks A (amplitude ~100) and B (~10), parts 1..6 each with small variation; part 6
        // of block A has amplitude 160.
        cal_fixture();
        assert_eq!(Spi::get_one::<i32>("SELECT cardinality(morlet_value_spreads('cal',(SELECT min(segment_id) FROM cal_ctx_segments)))").unwrap(), Some(8));
        assert_eq!(Spi::get_one::<bool>("SELECT (morlet_value_spreads('cal',(SELECT min(segment_id) FROM cal_ctx_segments)))[3]>0").unwrap(), Some(true));
        let anomalous = "(SELECT segment_id FROM cal_ctx_segments WHERE recording_id='A6')";
        let normal = "(SELECT segment_id FROM cal_ctx_segments WHERE recording_id='A3')";
        assert_eq!(Spi::get_one::<String>(&format!("SELECT status||':'||hist_n||':'||score_quantile||':'||(duration_relative_deviation=0) FROM morlet_compare_summary('cal',{anomalous})")).unwrap().unwrap(), "ok:5:1:true");
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT a.score>5*n.score AND n.score_quantile<1 FROM morlet_compare_summary('cal',{anomalous}) a, morlet_compare_summary('cal',{normal}) n")).unwrap(), Some(true));
        // The evaluated score is the plain maximum relative deviation; calibrated is reported alongside.
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT abs(s.score-c.relative_deviation)<1e-12 AND s.calibrated_score>0 FROM morlet_compare_summary('cal',{anomalous}) s, morlet_compare_to_history('cal',{anomalous}) c WHERE c.value_name='std'")).unwrap(), Some(true));
        assert!(matches!(
            Spi::get_one::<String>(&format!(
                "SELECT top_value FROM morlet_compare_summary('cal',{anomalous})"
            ))
            .unwrap()
            .as_deref(),
            Some("std")
        ));
        // The near-constant mean (0.3% shift) no longer leads: spreads are floored at 1%.
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT calibrated_deviation<1 FROM morlet_compare_to_history('cal',{anomalous}) WHERE value_name='mean'")).unwrap(), Some(true));
        assert_eq!(Spi::get_one::<String>(&format!("SELECT status||':'||calibrated_deviation FROM morlet_compare_to_history('cal',{anomalous}) WHERE value_name='duration_s'")).unwrap().unwrap(), "ok:0");
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT bool_and(calibrated_deviation IS NOT NULL OR status<>'ok') FROM morlet_compare_to_history('cal',{anomalous})")).unwrap(), Some(true));
        assert_eq!(
            Spi::get_one::<String>(&format!(
                "SELECT status FROM morlet_compare_summary('cal',{anomalous},min_history=>6)"
            ))
            .unwrap()
            .unwrap(),
            "insufficient_history"
        );
    }

    #[pg_test]
    fn time_to_samples_uses_exact_boundaries() {
        // 25.6 kHz: sample 1 sits at 39062.5 ns.
        assert_eq!(Spi::get_one::<String>("SELECT start_sample||'-'||end_sample FROM morlet_time_to_samples(25600,0,39062,39063)").unwrap().unwrap(), "1-2");
        assert_eq!(
            Spi::get_one::<String>(
                "SELECT start_sample||'-'||end_sample FROM morlet_time_to_samples(25600,0,0,39062)"
            )
            .unwrap()
            .unwrap(),
            "0-1"
        );
        assert_eq!(Spi::get_one::<String>("SELECT start_sample||'-'||end_sample FROM morlet_time_to_samples(100,5000000,9990000000,10010000000)").unwrap().unwrap(), "999-1001");
        assert_eq!(
            Spi::get_one::<String>("SELECT (morlet_sample_time_ns(25600,1000,3)=118187.5)::text")
                .unwrap()
                .unwrap(),
            "true"
        );
    }

    fn acquisition_fixture() {
        // Two acquisitions a day apart; each: vib 1 kHz (offset 0) and current 100 Hz (offset 5 ms),
        // 20 s, blocks N1 [0,10 s) and N2 [10 s,20 s), one part each. A2's N1 vibration is louder.
        Spi::run("SELECT morlet_create_stream_collection('public','cell'); SELECT morlet_enable_context('cell'); SELECT morlet_enable_acquisitions('cell'); SELECT morlet_create_acquisition('cell',a,'M7',o::timestamptz) FROM (VALUES ('A1','2026-01-01 00:00Z'),('A2','2026-01-02 00:00Z')) v(a,o); SELECT morlet_start_recording('cell',a||'-'||c,r,c,'u','{}',512,256,'zscore','none') FROM (VALUES ('A1'),('A2')) x(a), (VALUES ('vib',1000.0),('cur',100.0)) y(c,r); SELECT morlet_append_chunk('cell',a||'-vib',0,ARRAY(SELECT (CASE WHEN a='A2' AND i<10000 THEN 3 ELSE 1 END)*sin(i/3.)+0.01*(i%5) FROM generate_series(0,19999) i)) FROM unnest(ARRAY['A1','A2']) a; SELECT morlet_append_chunk('cell',a||'-cur',0,ARRAY(SELECT 5+0.1*sin(i/7.)+0.001*(i%3) FROM generate_series(0,1999) i)) FROM unnest(ARRAY['A1','A2']) a; SELECT morlet_add_acquisition_channel('cell',a,a||'-vib','vib',0) FROM unnest(ARRAY['A1','A2']) a; SELECT morlet_add_acquisition_channel('cell',a,a||'-cur','cur',5000000,'wallclock',200000) FROM unnest(ARRAY['A1','A2']) a; SELECT morlet_add_acquisition_context('cell',a,'ctl',jsonb_build_array(jsonb_build_object('key','block','value','N1','t_start_ns',0,'t_end_ns',10000000000),jsonb_build_object('key','block','value','N2','t_start_ns',10000000000,'t_end_ns',20000000000),jsonb_build_object('key','part','value',a,'t_start_ns',0,'t_end_ns',20000000000),jsonb_build_object('key','tool_change','value',7,'t_start_ns',0))) FROM unnest(ARRAY['A1','A2']) a; SELECT morlet_build_context_segments('cell',r.recording_id,ARRAY['block'],ARRAY['part']) FROM cell r; SELECT morlet_build_context_values('cell',r.recording_id) FROM cell r").unwrap();
    }

    #[pg_test]
    fn acquisitions_align_channels_and_compare_side_by_side() {
        acquisition_fixture();
        // Context mapped per channel in its own samples.
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(recording_id||':'||start_sample||'-'||end_sample,',' ORDER BY recording_id) FROM cell_ctx WHERE key='block' AND value='\"N1\"' AND recording_id LIKE 'A1-%'").unwrap().unwrap(), "A1-cur:0-1000,A1-vib:0-10000");
        // Identical retries are no-ops; a conflicting channel is an error elsewhere.
        assert_eq!(
            Spi::get_one::<bool>(
                "SELECT morlet_create_acquisition('cell','A1','M7','2026-01-01 00:00Z')"
            )
            .unwrap(),
            Some(false)
        );
        assert_eq!(Spi::get_one::<bool>("SELECT morlet_add_acquisition_channel('cell','A1','A1-cur','cur',5000000,'wallclock',200000)").unwrap(), Some(false));
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_add_acquisition_context('cell','A1','ctl',jsonb_build_array(jsonb_build_object('key','block','value','N1','t_start_ns',0,'t_end_ns',10000000000),jsonb_build_object('key','block','value','N2','t_start_ns',10000000000,'t_end_ns',20000000000),jsonb_build_object('key','part','value','A1','t_start_ns',0,'t_end_ns',20000000000),jsonb_build_object('key','tool_change','value',7,'t_start_ns',0)))").unwrap(), Some(0));
        // One acquisition-time interval read from both channels.
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(role||':'||start_sample||'-'||end_sample||':'||cardinality(samples),',' ORDER BY role) FROM morlet_read_acquisition('cell','A1',9990000000,10010000000)").unwrap().unwrap(), "cur:999-1001:2,vib:9990-10010:20");
        // A2's N1 against A1's: vibration louder (std), current unchanged.
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(role||':'||CASE role WHEN 'vib' THEN top_value ELSE '' END||':'||hist_n,',' ORDER BY role) FROM morlet_compare_acquisition('cell','A2','{\"block\":\"N1\"}','{\"part\":\"A2\"}',min_history=>1)").unwrap().unwrap(), "cur::1,vib:std:1");
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(CASE role WHEN 'vib' THEN score>1 ELSE score<0.1 END) FROM morlet_compare_acquisition('cell','A2','{\"block\":\"N1\"}','{\"part\":\"A2\"}',min_history=>1)").unwrap(), Some(true));
        assert_eq!(Spi::get_one::<String>("SELECT status FROM morlet_compare_acquisition('cell','A2','{\"block\":\"N9\"}','{\"part\":\"A2\"}') LIMIT 1").unwrap().unwrap(), "no_segment");
        // History before this point only: A1 (earlier) has none, A2 sees A1.
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(coalesce(hist_n::text,'-')||':'||status,',' ORDER BY role) FROM morlet_compare_acquisition('cell','A1','{\"block\":\"N1\"}','{\"part\":\"A1\"}',min_history=>1,before_only=>true)").unwrap().unwrap(), "0:insufficient_history,0:insufficient_history");
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(hist_n::text,',' ORDER BY role) FROM morlet_compare_acquisition('cell','A2','{\"block\":\"N1\"}','{\"part\":\"A2\"}',min_history=>1,before_only=>true)").unwrap().unwrap(), "1,1");
    }

    #[pg_test]
    fn level_track_filters_one_value_over_earlier_episodes() {
        acquisition_fixture();
        let seg = "(SELECT segment_id FROM cell_ctx_segments WHERE recording_id='A2-vib' AND context->>'block'='N1')";
        // Same machine, channel and block, earlier part first; the current channel is left out.
        assert_eq!(Spi::get_one::<String>(&format!("SELECT string_agg(episode||':'||(occurrence->>'part')||':'||status||':'||coalesce(hours_since_previous::text,'-')||':'||model,',' ORDER BY episode) FROM morlet_level_track('cell',{seg},0.01,0.04)")).unwrap().unwrap(), "1:A1:initialized:-:local-level-v1,2:A2:ok:24:local-level-v1");
        // Second episode: predicted variance 0.04 + 0.01*24, gain 0.28/0.32, on ln(std).
        assert_eq!(Spi::get_one::<bool>(&format!("WITH t AS (SELECT * FROM morlet_level_track('cell',{seg},0.01,0.04)), a AS (SELECT * FROM t WHERE episode=1), b AS (SELECT * FROM t WHERE episode=2)
            SELECT abs(a.y-ln(a.observed))<1e-12 AND a.level=a.y AND a.level_var=0.04 AND b.predicted_level=a.level
              AND abs(b.predicted_level_var-0.28)<1e-12 AND abs(b.innovation_var-0.32)<1e-12 AND abs(b.innovation-(b.y-a.y))<1e-12
              AND abs(b.level-(a.y+0.875*(b.y-a.y)))<1e-12 AND abs(b.level_var-0.28*0.04/0.32)<1e-12
              AND abs(b.standardized_innovation-b.innovation/sqrt(0.32))<1e-12 AND b.observed>2*a.observed FROM a, b")).unwrap(), Some(true));
        // transform none keeps the value; since_utc drops the earlier episode.
        assert_eq!(Spi::get_one::<bool>(&format!("SELECT bool_and(y=observed) FROM morlet_level_track('cell',{seg},0.01,0.04,transform=>'none')")).unwrap(), Some(true));
        assert_eq!(Spi::get_one::<String>(&format!("SELECT string_agg(status,',') FROM morlet_level_track('cell',{seg},0.01,0.04,since_utc=>'2026-01-01 12:00Z')")).unwrap().unwrap(), "initialized");
        // Invalid configurations and a segment before since_utc are errors.
        Spi::run(r#"DO $$ DECLARE s bigint; a text; failed int:=0; BEGIN
            SELECT segment_id INTO s FROM cell_ctx_segments WHERE recording_id='A2-vib' AND context->>'block'='N1';
            FOREACH a IN ARRAY ARRAY['-1,0.04','0.01,0','''NaN'',0.04','0.01,0.04,value_name=>''nope''','0.01,0.04,transform=>''sqrt''',
                '0.01,0.04,max_candidates=>1','0.01,0.04,since_utc=>''2026-01-03 00:00Z'''] LOOP
              BEGIN EXECUTE 'SELECT * FROM morlet_level_track(''cell'','||s||','||a||')';
              EXCEPTION WHEN others THEN failed:=failed+1; END;
            END LOOP;
            IF failed<>7 THEN RAISE EXCEPTION 'only % of 7 invalid calls failed',failed; END IF; END $$"#).unwrap();
    }

    #[pg_test]
    fn acquisition_segments_trim_edges_and_select_score_values() {
        acquisition_fixture();
        // Current logger: 0.2 ms uncertainty at 100 Hz -> 1 sample trimmed at each end; vib exact.
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_build_acquisition_segments('cell','A1',ARRAY['block'],ARRAY['part'])").unwrap(), Some(2));
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(start_sample||'-'||end_sample,',' ORDER BY start_sample) FROM cell_ctx_segments WHERE recording_id='A1-cur' AND spec ? 'trim_samples'").unwrap().unwrap(), "1-999,1001-1999");
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM cell_ctx_values v JOIN cell_ctx_segments s USING (segment_id) WHERE s.recording_id='A1-cur' AND s.spec ? 'trim_samples'").unwrap(), Some(2));
        // score_values selects the values behind the score.
        assert_eq!(Spi::get_one::<String>("SELECT top_value FROM morlet_compare_summary('cell',(SELECT segment_id FROM cell_ctx_segments WHERE recording_id='A2-vib' AND context->>'block'='N1'),min_history=>1,score_values=>ARRAY['p75'])").unwrap().unwrap(), "p75");
    }

    #[pg_test(
        error = "score_values must be a non-empty subset of {duration_s,mean,std,rms,p75,peak_abs,crest,kurtosis}"
    )]
    fn score_values_are_validated() {
        acquisition_fixture();
        Spi::run("SELECT * FROM morlet_compare_summary('cell',(SELECT min(segment_id) FROM cell_ctx_segments),score_values=>ARRAY['level'])").unwrap();
    }

    #[pg_test(
        error = "recording A1-vib already belongs to an acquisition with different attributes"
    )]
    fn acquisition_channel_conflict_is_an_error() {
        acquisition_fixture();
        Spi::run("SELECT morlet_add_acquisition_channel('cell','A2','A1-vib','vib',0)").unwrap();
    }

    #[pg_test(
        error = "before_only needs the segment's recording in an acquisition with origin_utc"
    )]
    fn before_only_needs_an_acquisition() {
        acquisition_fixture();
        Spi::run("SELECT morlet_start_recording('cell','loose',1000,'vib','u','{}',512,256,'zscore','none'); SELECT morlet_append_chunk('cell','loose',0,array_fill(1.0::float8,ARRAY[100])); SELECT morlet_add_context('cell','l1','[{\"recording_id\":\"loose\",\"key\":\"block\",\"value\":\"N1\",\"start_sample\":0,\"end_sample\":100}]'); SELECT morlet_build_context_segments('cell','loose',ARRAY['block']); SELECT morlet_build_context_values('cell','loose'); SELECT * FROM morlet_compare_summary('cell',(SELECT segment_id FROM cell_ctx_segments WHERE recording_id='loose'),before_only=>true)").unwrap();
    }

    #[pg_test(error = "context batch b1 already exists with different rows")]
    fn context_batch_retry_must_be_identical() {
        context_fixture();
        Spi::run("SELECT morlet_add_context('mill','b1','[{\"recording_id\":\"r1\",\"key\":\"program\",\"value\":\"P2\",\"start_sample\":0,\"end_sample\":30000}]')").unwrap();
    }

    #[pg_test(
        error = "overlapping context intervals for key block in recording r1 (ctx_id 3 and 8)"
    )]
    fn context_rejects_overlapping_intervals_of_one_key() {
        context_fixture();
        Spi::run("SELECT morlet_add_context('mill','b2','[{\"recording_id\":\"r1\",\"key\":\"block\",\"value\":\"N3\",\"start_sample\":15000,\"end_sample\":16000}]')").unwrap();
    }

    #[pg_test(error = "segment 2 is stale; rebuild context segments")]
    fn compare_rejects_stale_segments() {
        context_fixture();
        Spi::run("SELECT morlet_build_context_segments('mill','r1',ARRAY['block']); SELECT morlet_build_context_values('mill','r1'); SELECT morlet_revise_context('mill',3,'{}',true); SELECT * FROM morlet_compare_to_history('mill',2)").unwrap();
    }

    #[pg_test(error = "history exceeds 1 segments; narrow match_keys or the metadata filter")]
    fn compare_enforces_its_budget() {
        context_fixture();
        Spi::run("SELECT morlet_build_context_segments('mill','r1',ARRAY['program','block']); SELECT morlet_build_context_values('mill','r1'); SELECT * FROM morlet_compare_to_history('mill',3,ARRAY['program'],max_candidates=>1)").unwrap();
    }

    #[pg_test(error = "context is not enabled for this collection (morlet_enable_context)")]
    fn context_requires_enabling() {
        Spi::run("SELECT morlet_create_stream_collection('public','plain'); SELECT morlet_build_context_segments('plain','x',ARRAY['block'])").unwrap();
    }

    #[pg_test(
        error = "recording r stores raw samples only (extractor none); use morlet_search_envelope_interval or the context comparison"
    )]
    fn interval_search_explains_raw_only_recordings() {
        Spi::run("SELECT morlet_create_stream_collection('public','raw_search'); SELECT morlet_start_recording('raw_search','r',4096,'x','g','{}',512,256,'zscore','none'); SELECT morlet_append_chunk('raw_search','r',0,array_fill(1.0::float8,ARRAY[2048])); SELECT * FROM morlet_search_interval('raw_search','r',0,512)").unwrap();
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
        // The crate version must follow the SQL extension version (it drifted twice).
        assert_eq!(
            Spi::get_one::<bool>("SELECT morlet_version() = (SELECT default_version FROM pg_available_extensions WHERE name = 'pg_morlet')")
                .unwrap(),
            Some(true)
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
