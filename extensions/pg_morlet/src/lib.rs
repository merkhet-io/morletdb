use pgrx::prelude::*;
use pgrx::JsonB;

pgrx::pg_module_magic!(name, version);

fn checked_samples(input: Vec<Option<f64>>) -> Vec<f64> {
    input
        .into_iter()
        .map(|v| v.unwrap_or_else(|| pgrx::error!("NULL waveform elements are unsupported")))
        .collect()
}

/// The samples of a float8[] argument without copying them when it has no NULLs (the usual case);
/// otherwise the same error as `checked_samples`.
fn sample_slice<'a>(input: &'a pgrx::Array<'_, f64>) -> std::borrow::Cow<'a, [f64]> {
    match input.as_slice() {
        Ok(s) => std::borrow::Cow::Borrowed(s),
        Err(_) => std::borrow::Cow::Owned(
            input
                .iter()
                .map(|v| {
                    v.unwrap_or_else(|| pgrx::error!("NULL waveform elements are unsupported"))
                })
                .collect(),
        ),
    }
}

/// A float8[] result built by PostgreSQL in one call (`construct_array`) instead of element by
/// element; the SQL type is `double precision[]`, as for `Vec<f64>`.
struct F64Array(Vec<f64>);

impl pgrx::datum::IntoDatum for F64Array {
    fn into_datum(self) -> Option<pg_sys::Datum> {
        unsafe {
            if self.0.is_empty() {
                return Some(pg_sys::Datum::from(pg_sys::construct_empty_array(
                    pg_sys::FLOAT8OID,
                )));
            }
            // float8 is passed by value on 64-bit builds: the Datum holds the bit pattern.
            let mut datums: Vec<pg_sys::Datum> = self
                .0
                .iter()
                .map(|v| pg_sys::Datum::from(v.to_bits() as usize))
                .collect();
            // float8: 8 bytes, passed by value, double alignment ('d').
            let array = pg_sys::construct_array(
                datums.as_mut_ptr(),
                datums.len() as i32,
                pg_sys::FLOAT8OID,
                8,
                true,
                b'd' as std::ffi::c_char,
            );
            Some(pg_sys::Datum::from(array))
        }
    }
    fn type_oid() -> pg_sys::Oid {
        pg_sys::FLOAT8ARRAYOID
    }
}

unsafe impl pgrx::callconv::BoxRet for F64Array {
    unsafe fn box_into<'fcx>(
        self,
        fcinfo: &mut pgrx::callconv::FcInfo<'fcx>,
    ) -> pgrx::datum::Datum<'fcx> {
        use pgrx::datum::IntoDatum;
        match self.into_datum() {
            Some(datum) => unsafe { fcinfo.return_raw_datum(datum) },
            None => fcinfo.return_null(),
        }
    }
}

pgrx::impl_sql_translatable!(F64Array, "double precision[]");

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
fn morlet_valid_chunk(samples: pgrx::Array<'_, f64>) -> bool {
    morlet_core::validate(&sample_slice(&samples)).unwrap_or_else(|e| pgrx::error!("{}", e));
    true
}

/// Lossless chunk encoding: integer grid + Rice coding, or raw float64 off the grid.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_encode_chunk(samples: pgrx::Array<'_, f64>, sample_quantum: f64) -> Vec<u8> {
    morlet_core::rawcodec::encode(&sample_slice(&samples), sample_quantum)
        .unwrap_or_else(|e| pgrx::error!("{}", e))
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_decode_chunk(encoded: &[u8]) -> F64Array {
    F64Array(morlet_core::rawcodec::decode(encoded).unwrap_or_else(|e| pgrx::error!("{}", e)))
}

/// Calls `take` with the samples of [interval_start, interval_end) from each encoded chunk, given in
/// ascending start order. Parts of the interval not covered by a chunk are simply absent, so callers
/// compare the number of samples with the interval to detect gaps.
fn for_interval_slices(
    chunks: &pgrx::Array<'_, &[u8]>,
    starts: &[Option<i64>],
    interval_start: i64,
    interval_end: i64,
    mut take: impl FnMut(&[f64]),
) {
    if chunks.len() != starts.len() {
        pgrx::error!("chunks and starts differ in length");
    }
    let mut previous = i64::MIN;
    let mut items: Vec<(&[u8], i64)> = Vec::with_capacity(starts.len());
    for (chunk, start) in chunks.iter().zip(starts) {
        let (Some(chunk), Some(start)) = (chunk, *start) else {
            pgrx::error!("chunks and starts must not contain NULL");
        };
        if start <= previous {
            pgrx::error!("chunk starts must be strictly ascending");
        }
        previous = start;
        items.push((chunk, start));
    }
    // Long reads decode their chunks on worker threads; errors are raised in chunk order as before.
    let threads = if items.len() >= DECODE_PARALLEL_MIN {
        decode_threads()
    } else {
        1
    };
    let decoded = on_threads(&items, threads, "morlet-decode", |(chunk, _)| {
        morlet_core::rawcodec::decode(chunk).map_err(|e| e.to_string())
    });
    for ((_, start), samples) in items.iter().zip(decoded) {
        let start = *start;
        let samples = samples.unwrap_or_else(|e| pgrx::error!("{}", e));
        let lo = interval_start.max(start) - start;
        let hi = interval_end.min(start + samples.len() as i64) - start;
        if lo < hi {
            take(&samples[lo as usize..hi as usize]);
        }
    }
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
) -> F64Array {
    let mut out = Vec::with_capacity((interval_end - interval_start).clamp(0, 32768) as usize);
    for_interval_slices(&chunks, &starts, interval_start, interval_end, |s| {
        out.extend_from_slice(s)
    });
    F64Array(out)
}

/// As `morlet_decode_interval`, but the samples as raw IEEE 754 float64 values, little-endian,
/// 8 bytes each (a client can map the bytes to an array without per-element parsing).
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_decode_interval_raw(
    chunks: pgrx::Array<'_, &[u8]>,
    starts: Vec<Option<i64>>,
    interval_start: i64,
    interval_end: i64,
) -> Vec<u8> {
    let n = (interval_end - interval_start).clamp(0, 1 << 24) as usize;
    let mut out = Vec::with_capacity(8 * n);
    for_interval_slices(&chunks, &starts, interval_start, interval_end, |s| {
        for v in s {
            out.extend_from_slice(&v.to_le_bytes());
        }
    });
    out
}

/// The transfer frame of an interval's encoded chunks (docs/compressed-transfer.md), for
/// `morlet_read_interval_encoded`. `counts` are the chunks' stored sample counts; returns NULL unless the chunks,
/// clipped to the interval, cover it exactly, so the caller can raise the same error as a raw read. The chunks
/// are sent unchanged (the client verifies their checksums when it decodes them), unless one has a format
/// version above `max_chunk_version`: then the interval is decoded here and its samples are sent in a raw frame.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_frame_chunks(
    chunks: pgrx::Array<'_, &[u8]>,
    starts: Vec<Option<i64>>,
    counts: Vec<Option<i32>>,
    interval_start: i64,
    interval_end: i64,
    max_chunk_version: i32,
) -> Option<Vec<u8>> {
    if chunks.len() != starts.len() || chunks.len() != counts.len() {
        pgrx::error!("chunks, starts and counts differ in length");
    }
    let mut cursor = interval_start;
    let mut items: Vec<(i64, &[u8])> = Vec::with_capacity(starts.len());
    let mut newer = false;
    for ((chunk, start), count) in chunks.iter().zip(&starts).zip(&counts) {
        let (Some(chunk), Some(start), Some(count)) = (chunk, *start, *count) else {
            pgrx::error!("chunks, starts and counts must not contain NULL");
        };
        let lo = interval_start.max(start);
        let hi = interval_end.min(start + count as i64);
        if lo >= hi || lo != cursor {
            return None;
        }
        cursor = hi;
        newer |= chunk.first().is_none_or(|&v| v as i32 > max_chunk_version);
        items.push((start, chunk));
    }
    if cursor != interval_end {
        return None;
    }
    let frame = if newer {
        let mut samples =
            Vec::with_capacity((interval_end - interval_start).clamp(0, 1 << 24) as usize);
        for_interval_slices(&chunks, &starts, interval_start, interval_end, |s| {
            samples.extend_from_slice(s)
        });
        morlet_core::transfer::raw_frame(&samples, interval_start, interval_end)
    } else {
        morlet_core::transfer::chunk_frame(&items, interval_start, interval_end)
    };
    Some(frame.unwrap_or_else(|e| pgrx::error!("{}", e)))
}

/// Little-endian float64 bytes (8 per sample) as a float8[], without per-element parsing. Used by
/// `morlet_append_chunk_raw`; the appended samples are validated by `morlet_append_chunk` as usual.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_float8_from_le_bytes(raw: &[u8]) -> F64Array {
    if !raw.len().is_multiple_of(8) {
        pgrx::error!("raw samples must be a multiple of 8 bytes (little-endian float64)");
    }
    F64Array(
        raw.chunks_exact(8)
            .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
            .collect(),
    )
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

thread_local! {
    /// The last query prepared by this backend for `morlet_envelope_distance`: a scan calls
    /// it once per stored envelope with the same query, so the shifted-query matrix is built
    /// once per query and process (each parallel worker is its own process).
    static LAG_QUERY: std::cell::RefCell<Option<(Vec<f64>, usize, morlet_core::envelope::LagQuery)>> =
        const { std::cell::RefCell::new(None) };
    /// Decoded stored envelope, reused across calls.
    static STORED: std::cell::RefCell<Vec<f64>> = const { std::cell::RefCell::new(Vec::new()) };
}

/// Bytes a prepared query matrix (exact scoring or screening) may take on this machine: a quarter of the CPU's
/// last-level cache, read once per backend. When the size cannot be read (virtual machines such as Docker Desktop's
/// expose cache levels without sizes) it is 4 MiB, which keeps the prepared forms as before; small boards such as
/// the Raspberry Pi report their cache. MORLET_SCORE_CACHE_BYTES in the server's environment overrides it. Each stored envelope streams the whole matrix, so a matrix larger than the
/// cache made exact search on a Raspberry Pi 3B+ (512 KB L2) 15x slower than direct scoring; desktop and server
/// caches keep the prepared forms. Only speed depends on this: both forms give bitwise the same distances, and
/// screened and full exact search the same results.
fn prepared_budget() -> usize {
    static BUDGET: std::sync::OnceLock<usize> = std::sync::OnceLock::new();
    *BUDGET.get_or_init(|| {
        std::env::var("MORLET_SCORE_CACHE_BYTES")
            .ok()
            .and_then(|v| v.trim().parse().ok())
            .unwrap_or_else(|| last_level_cache_bytes().map_or(4 << 20, |c| c / 4))
    })
}

/// The largest cache of CPU 0 (Linux sysfs) or the performance cluster's L2 (macOS sysctl), in bytes.
fn last_level_cache_bytes() -> Option<usize> {
    #[cfg(target_os = "linux")]
    {
        let dir = std::fs::read_dir("/sys/devices/system/cpu/cpu0/cache").ok()?;
        dir.filter_map(|e| {
            let size = std::fs::read_to_string(e.ok()?.path().join("size")).ok()?;
            let size = size.trim();
            let (digits, scale) = match size.chars().last()? {
                'K' => (&size[..size.len() - 1], 1usize << 10),
                'M' => (&size[..size.len() - 1], 1 << 20),
                'G' => (&size[..size.len() - 1], 1 << 30),
                _ => (size, 1),
            };
            digits.parse::<usize>().ok().map(|d| d * scale)
        })
        .max()
    }
    #[cfg(target_os = "macos")]
    {
        [
            "hw.perflevel0.l2cachesize",
            "hw.l3cachesize",
            "hw.l2cachesize",
        ]
        .iter()
        .filter_map(|name| {
            let name = std::ffi::CString::new(*name).ok()?;
            let mut value: u64 = 0;
            let mut len = std::mem::size_of::<u64>();
            // SAFETY: value and len describe a valid u64 buffer; sysctlbyname writes at most len bytes.
            let rc = unsafe {
                libc::sysctlbyname(
                    name.as_ptr(),
                    (&mut value as *mut u64).cast(),
                    &mut len,
                    std::ptr::null_mut(),
                    0,
                )
            };
            (rc == 0 && value > 0).then_some(value as usize)
        })
        .max()
    }
    #[cfg(not(any(target_os = "linux", target_os = "macos")))]
    {
        None
    }
}

fn same_bits(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

/// 1 - max over |lag| <= max_lag of the biased cross-correlation of a query envelope
/// (morlet_envelope) and a stored envelope.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_distance(
    query_envelope: pgrx::Array<'_, f64>,
    stored: &[u8],
    max_lag: i32,
) -> f64 {
    if max_lag < 0 {
        pgrx::error!("max_lag must be nonnegative");
    }
    let query = query_envelope
        .as_slice()
        .unwrap_or_else(|_| pgrx::error!("NULL waveform elements are unsupported"));
    let lag = max_lag as usize;
    STORED
        .with(|buf| {
            let mut g = buf.borrow_mut();
            morlet_core::envelope::decode_into(stored, &mut g)
                .unwrap_or_else(|e| pgrx::error!("{}", e));
            LAG_QUERY.with(|cell| {
                let mut cell = cell.borrow_mut();
                if !matches!(&*cell, Some((q, l, _)) if *l == lag && same_bits(q, query)) {
                    // Prepared only while its matrix fits this machine's cache (prepared_budget).
                    let fits = morlet_core::envelope::LagQuery::matrix_bytes(query.len(), lag)
                        .is_some_and(|b| b <= prepared_budget());
                    *cell = fits
                        .then(|| morlet_core::envelope::LagQuery::new(query, lag))
                        .flatten()
                        .map(|p| (query.to_vec(), lag, p));
                }
                // Bitwise identical either way; the direct form covers queries too large to prepare or to cache.
                match cell.as_ref() {
                    Some((_, _, p)) => p.distance(&g),
                    None => morlet_core::envelope::distance(query, &g, lag),
                }
            })
        })
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

thread_local! {
    /// Chunks this backend encoded in its latest `morlet_chunk_batch` call, with their sample counts (I1, #233).
    /// `morlet_valid_encoded_chunk` (the chunk tables' CHECK constraint) skips its decode for bytes identical to
    /// one of them: they are this backend's own encoder output, decodable to that many samples, and, with
    /// `morlet.verify_appends` on, already decoded and compared bit for bit with their input. Every other
    /// insert or update is decoded in full. Each entry is used once; the list is replaced by the next batch.
    static ENCODED_HERE: std::cell::RefCell<Vec<(Vec<u8>, i32)>> = const { std::cell::RefCell::new(Vec::new()) };
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_valid_encoded_chunk(encoded: &[u8], sample_count: i32) -> bool {
    let known = ENCODED_HERE.with(|list| {
        let mut list = list.borrow_mut();
        let found = list
            .iter()
            .position(|(bytes, n)| *n == sample_count && bytes.as_slice() == encoded);
        found.map(|i| list.swap_remove(i)).is_some()
    });
    if known {
        return true;
    }
    let samples = morlet_core::rawcodec::decode(encoded).unwrap_or_else(|e| pgrx::error!("{}", e));
    if samples.len() != sample_count as usize {
        pgrx::error!("encoded chunk length does not match sample_count");
    }
    true
}

#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_step(checkpoint: JsonB, start_sample: i64, samples: pgrx::Array<'_, f64>) -> JsonB {
    if start_sample < 0 {
        pgrx::error!("chunk start must be nonnegative");
    }
    let checkpoint = serde_json::from_value(checkpoint.0)
        .unwrap_or_else(|e| pgrx::error!("invalid checkpoint: {}", e));
    let mut state = morlet_core::chunks::ChunkExtractor::from_checkpoint(checkpoint)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    let segments = state
        .push(start_sample as u64, &sample_slice(&samples))
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    if state.next_sample_offset() > i64::MAX as u64 {
        pgrx::error!("Postgres sample offset overflow");
    }
    JsonB(serde_json::json!({"checkpoint":state.checkpoint(),"segments":segments}))
}

/// Worker threads for encoding the chunks of one batch: `morlet.encode_threads` (1..64; 1 encodes in the
/// backend's own thread), by default up to 4 and at most the CPUs available (I1 step 3, issue #233).
fn encode_threads() -> usize {
    let default = std::thread::available_parallelism().map_or(1, |n| n.get().min(4));
    // SAFETY: GetConfigOption returns NULL or a NUL-terminated string valid until the setting changes.
    let raw = unsafe { pg_sys::GetConfigOption(c"morlet.encode_threads".as_ptr(), true, false) };
    if raw.is_null() {
        return default;
    }
    let text = unsafe { std::ffi::CStr::from_ptr(raw) }.to_string_lossy();
    if text.is_empty() {
        return default;
    }
    match text.trim().parse::<usize>() {
        Ok(n) if (1..=64).contains(&n) => n,
        _ => pgrx::error!("morlet.encode_threads must be an integer 1..64"),
    }
}

/// `morlet.verify_appends` (on/off, default on): after encoding, decode each chunk of a batch again and
/// compare it bit for bit with its input samples (I1, #233). Off relies on the codec's tests instead.
fn verify_appends() -> bool {
    // SAFETY: GetConfigOption returns NULL or a NUL-terminated string valid until the setting changes.
    let raw = unsafe { pg_sys::GetConfigOption(c"morlet.verify_appends".as_ptr(), true, false) };
    if raw.is_null() {
        return true;
    }
    match unsafe { std::ffi::CStr::from_ptr(raw) }
        .to_string_lossy()
        .trim()
        .to_ascii_lowercase()
        .as_str()
    {
        "" | "on" | "true" | "yes" | "1" => true,
        "off" | "false" | "no" | "0" => false,
        _ => pgrx::error!("morlet.verify_appends must be on or off"),
    }
}

type ChunkCode = Option<Result<Vec<u8>, String>>;

/// Encodes each chunk as `morlet_encode_chunk` does, on up to `encode_threads()` threads, and with `verify`
/// decodes it again and compares it bit for bit with the input (a mismatch is an error). A chunk that fails
/// validation is not encoded (`None`); the caller validates it again in order and raises the error. The
/// workers run only `morlet_core` code (no PostgreSQL calls, no PostgreSQL memory) and are started with every
/// signal blocked, so PostgreSQL's signal handlers keep running in the backend's own thread.
fn encode_chunks(chunks: &[&[f64]], quantum: f64, verify: bool) -> Vec<ChunkCode> {
    let one = move |c: &[f64]| -> ChunkCode {
        morlet_core::validate(c).ok()?;
        Some(
            morlet_core::rawcodec::encode(c, quantum)
                .map_err(|e| e.to_string())
                .and_then(|bytes| {
                    if verify {
                        let back =
                            morlet_core::rawcodec::decode(&bytes).map_err(|e| e.to_string())?;
                        if back.len() != c.len()
                            || back.iter().zip(c).any(|(a, b)| a.to_bits() != b.to_bits())
                        {
                            return Err("encoded chunk does not decode to its input samples".into());
                        }
                    }
                    Ok(bytes)
                }),
        )
    };
    on_threads(chunks, encode_threads(), "morlet-encode", |c| one(c))
}

/// Runs `f` on every item, in order of results, on up to `threads` scoped threads (one contiguous part each).
/// `f` must not call PostgreSQL or use PostgreSQL memory other than reading data borrowed for the call; the
/// threads are started with every signal blocked, so PostgreSQL's signal handlers keep running in the
/// backend's own thread. With one thread (or one item) everything runs in the backend's thread.
fn on_threads<T: Sync, R: Send>(
    items: &[T],
    threads: usize,
    name: &str,
    f: impl Fn(&T) -> R + Sync,
) -> Vec<R> {
    let threads = threads.min(items.len());
    if threads <= 1 {
        return items.iter().map(&f).collect();
    }
    let per = items.len().div_ceil(threads);
    let f = &f;
    let parts: Vec<Vec<R>> = std::thread::scope(|scope| {
        // SAFETY: plain POSIX signal-mask calls on locally owned sets; the backend's mask is restored below
        // and the workers keep the fully blocked mask they inherit.
        let mut all: libc::sigset_t = unsafe { std::mem::zeroed() };
        let mut old: libc::sigset_t = unsafe { std::mem::zeroed() };
        unsafe {
            libc::sigfillset(&mut all);
            libc::pthread_sigmask(libc::SIG_BLOCK, &all, &mut old);
        }
        let handles: Vec<_> = items
            .chunks(per)
            .map(|part| {
                std::thread::Builder::new()
                    .name(name.into())
                    .spawn_scoped(scope, move || part.iter().map(f).collect::<Vec<_>>())
            })
            .collect();
        unsafe {
            libc::pthread_sigmask(libc::SIG_SETMASK, &old, std::ptr::null_mut());
        }
        handles
            .into_iter()
            .map(|h| match h {
                Ok(h) => h.join().ok(),
                Err(_) => None,
            })
            .collect::<Option<Vec<_>>>()
            .unwrap_or_else(|| pgrx::error!("worker threads failed"))
    });
    parts.into_iter().flatten().collect()
}

/// Worker threads for decoding the chunks of one long interval read: `morlet.decode_threads` (1..64; 1 decodes
/// in the backend's own thread), by default up to 4 and at most the CPUs available. Reads of fewer than
/// `DECODE_PARALLEL_MIN` chunks always decode in the backend's thread (starting threads would cost more).
fn decode_threads() -> usize {
    let default = std::thread::available_parallelism().map_or(1, |n| n.get().min(4));
    // SAFETY: GetConfigOption returns NULL or a NUL-terminated string valid until the setting changes.
    let raw = unsafe { pg_sys::GetConfigOption(c"morlet.decode_threads".as_ptr(), true, false) };
    if raw.is_null() {
        return default;
    }
    let text = unsafe { std::ffi::CStr::from_ptr(raw) }.to_string_lossy();
    if text.is_empty() {
        return default;
    }
    match text.trim().parse::<usize>() {
        Ok(n) if (1..=64).contains(&n) => n,
        _ => pgrx::error!("morlet.decode_threads must be an integer 1..64"),
    }
}

const DECODE_PARALLEL_MIN: usize = 8;

/// The per-chunk work of appending a run of consecutive chunks, in one call (I1, issue #233).
///
/// `samples` holds little-endian float64 values starting at `start_sample`; they are cut into chunks of
/// `chunk_size` samples (the last may be shorter). For each chunk, in order, this does what
/// `morlet_append_chunk` does per call: validates the samples, advances the checkpoint as
/// `morlet_chunk_step` does (so the result is identical to appending the chunks one by one), encodes the
/// chunk as `morlet_encode_chunk` does and, if `summarized`, computes `morlet_chunk_summary`. Returns the
/// chunk starts, counts, encoded chunks, summaries (NULL unless `summarized`), and the final checkpoint with
/// all emitted segments in `morlet_chunk_step`'s format. Writes nothing: `morlet_append_chunks_raw` stores it.
/// The chunks are encoded on up to `morlet.encode_threads` threads (`encode_chunks`); output and errors do
/// not depend on the thread count. With `morlet.verify_appends` on (the default) each chunk is decoded again
/// and compared bit for bit with its input. The encoded chunks are remembered for the chunk table's CHECK
/// constraint, which then skips its own decode for exactly these bytes (`ENCODED_HERE`).
#[allow(clippy::type_complexity)]
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_batch(
    checkpoint: JsonB,
    start_sample: i64,
    samples: &[u8],
    chunk_size: i32,
    sample_quantum: f64,
    summarized: bool,
) -> TableIterator<
    'static,
    (
        name!(starts, Vec<i64>),
        name!(counts, Vec<i32>),
        name!(encoded, Vec<Vec<u8>>),
        name!(summaries, Option<Vec<Vec<u8>>>),
        name!(result, JsonB),
    ),
> {
    if start_sample < 0 {
        pgrx::error!("chunk start must be nonnegative");
    }
    if !(1..=32768).contains(&chunk_size) {
        pgrx::error!("chunk_size must be 1..32768");
    }
    if samples.is_empty() || !samples.len().is_multiple_of(8) {
        pgrx::error!("raw samples must be a nonempty multiple of 8 bytes (little-endian float64)");
    }
    if samples.len() / 8 > 262144 {
        pgrx::error!("batch exceeds 262144 samples");
    }
    let values: Vec<f64> = samples
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect();
    let checkpoint = serde_json::from_value(checkpoint.0)
        .unwrap_or_else(|e| pgrx::error!("invalid checkpoint: {}", e));
    let mut state = morlet_core::chunks::ChunkExtractor::from_checkpoint(checkpoint)
        .unwrap_or_else(|e| pgrx::error!("{}", e));
    let n = values.len().div_ceil(chunk_size as usize);
    let (mut starts, mut counts, mut encoded) = (
        Vec::with_capacity(n),
        Vec::with_capacity(n),
        Vec::with_capacity(n),
    );
    let mut summaries = summarized.then(|| Vec::with_capacity(n));
    let mut segments = Vec::new();
    let mut offset = start_sample;
    let chunks: Vec<&[f64]> = values.chunks(chunk_size as usize).collect();
    let mut codes = encode_chunks(&chunks, sample_quantum, verify_appends()).into_iter();
    for chunk in chunks {
        // Errors are raised in chunk order, validation before encoding, exactly as one call per chunk would.
        segments.extend(
            state
                .push(offset as u64, chunk)
                .unwrap_or_else(|e| pgrx::error!("{}", e)),
        );
        if state.next_sample_offset() > i64::MAX as u64 {
            pgrx::error!("Postgres sample offset overflow");
        }
        encoded.push(match codes.next().flatten() {
            Some(code) => code.unwrap_or_else(|e| pgrx::error!("{}", e)),
            None => pgrx::error!("chunk was not encoded"),
        });
        if let Some(s) = summaries.as_mut() {
            let mut stats = morlet_core::intervalstats::IntervalStats::default();
            stats.add(chunk);
            s.push(stats.to_summary().to_vec());
        }
        starts.push(offset);
        counts.push(chunk.len() as i32);
        offset += chunk.len() as i64;
    }
    let result = JsonB(serde_json::json!({"checkpoint": state.checkpoint(), "segments": segments}));
    ENCODED_HERE.with(|list| {
        let mut list = list.borrow_mut();
        list.clear();
        list.extend(encoded.iter().cloned().zip(counts.iter().copied()));
    });
    TableIterator::once((starts, counts, encoded, summaries, result))
}

// ---- Exact envelope search: a parallel aggregate that screens stored envelopes in float32 with a
// rigorous error bound and keeps every window that could be among the m nearest (morlet_core::envelope_screen).
// SQL: morlet_envelope_screen(envelope, recording_id, start_sample, query_envelope, max_lag, m, budget) -> jsonb
// of [recording_id, start_sample] candidates, re-scored exactly by morlet_envelope_distance afterwards. A state
// that sees more than `budget` (max_candidates) rows stops screening: the search then fails its count check.

struct ScreenAgg {
    query: Vec<f64>,
    query_bytes: Vec<u8>,
    /// Address of the query datum seen on the previous row: within one scan it is the same datum, so
    /// the byte comparison is skipped.
    last_query_ptr: usize,
    lag: usize,
    m: usize,
    /// Recording ids, interned: candidates refer to them by index. Rows arrive grouped by recording, so
    /// the last id is checked first.
    rids: Vec<String>,
    screener: Option<morlet_core::envelope_screen::Screener<(u32, i64)>>,
    /// Rows this state has screened, and the search's max_candidates. Once a state has seen more rows than
    /// the budget, the search will fail its budget check whatever the screening finds, so later rows are
    /// only counted (by the caller's count(*)), not screened. Not serialized: each worker counts its own.
    seen: i64,
    budget: i64,
}

impl ScreenAgg {
    fn rid_index(&mut self, rid: &str) -> u32 {
        if let Some(last) = self.rids.last() {
            if last == rid {
                return (self.rids.len() - 1) as u32;
            }
        }
        match self.rids.iter().position(|r| r == rid) {
            Some(i) => i as u32,
            None => {
                self.rids.push(rid.to_owned());
                (self.rids.len() - 1) as u32
            }
        }
    }
}

fn query_from_le_bytes(bytes: &[u8]) -> Vec<f64> {
    if bytes.is_empty() || !bytes.len().is_multiple_of(8) {
        pgrx::error!(
            "the query envelope must be little-endian float64 values (morlet_float8_le_bytes)"
        );
    }
    bytes
        .chunks_exact(8)
        .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
        .collect()
}

/// Whether screened exact search can prepare a query envelope of `envelope_len` values with `max_lag`
/// (morlet_core::envelope::LagQuery::supported) and its screening matrix fits this machine's cache
/// (prepared_budget); otherwise exact search scores every eligible window directly, with the same results. A
/// screen that streams a matrix from memory for every window costs more than scoring the window directly (on a
/// Raspberry Pi 3B+: 5.8 ms against 0.79 ms per 16,384-sample window). Constant for a server process.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_envelope_screen_supported(envelope_len: i32, max_lag: i32) -> bool {
    envelope_len > 0
        && max_lag >= 0
        && morlet_core::envelope::LagQuery::supported(envelope_len as usize, max_lag as usize)
        && morlet_core::envelope_screen::ScreenQuery::matrix_bytes(
            envelope_len as usize,
            max_lag as usize,
        )
        .is_some_and(|b| b <= prepared_budget())
}

/// A float8[] as little-endian float64 bytes (the screening aggregate's query argument).
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_float8_le_bytes(values: pgrx::Array<'_, f64>) -> Vec<u8> {
    let v = values
        .as_slice()
        .unwrap_or_else(|_| pgrx::error!("NULL waveform elements are unsupported"));
    v.iter().flat_map(|x| x.to_le_bytes()).collect()
}

unsafe fn aggregate_context(fcinfo: pg_sys::FunctionCallInfo) -> pg_sys::MemoryContext {
    let mut ctx = std::ptr::null_mut();
    if unsafe { pg_sys::AggCheckCallContext(fcinfo, &mut ctx) } == 0 || ctx.is_null() {
        pgrx::error!("this function can only be called as part of its aggregate");
    }
    ctx
}

fn new_screen_agg(query: &[f64], lag: usize, m: usize, budget: i64) -> ScreenAgg {
    let screener =
        morlet_core::envelope_screen::Screener::new(query, lag, m).unwrap_or_else(|| {
            pgrx::error!("invalid query envelope, max_lag or candidate pool for screening")
        });
    ScreenAgg {
        query: query.to_vec(),
        query_bytes: query.iter().flat_map(|x| x.to_le_bytes()).collect(),
        last_query_ptr: 0,
        lag,
        m,
        rids: Vec::new(),
        screener: Some(screener),
        seen: 0,
        budget,
    }
}

#[pg_extern(parallel_safe)]
#[allow(clippy::too_many_arguments)]
fn morlet_envelope_screen_step(
    state: pgrx::Internal,
    envelope: &[u8],
    recording_id: &str,
    start_sample: i64,
    query_le: &[u8],
    max_lag: i32,
    m: i32,
    budget: i64,
    fcinfo: pg_sys::FunctionCallInfo,
) -> pgrx::Internal {
    if max_lag < 0 || m < 1 || budget < 1 {
        pgrx::error!("max_lag must be nonnegative, and the candidate pool and budget positive");
    }
    let mut state = state;
    unsafe {
        if !state.initialized() {
            let ctx = aggregate_context(fcinfo);
            let agg = new_screen_agg(
                &query_from_le_bytes(query_le),
                max_lag as usize,
                m as usize,
                budget,
            );
            state = pgrx::Internal::from(Some(pg_sys::Datum::from(
                pgrx::PgMemoryContexts::For(ctx).leak_and_drop_on_delete(agg),
            )));
        }
        let agg = state.get_mut::<ScreenAgg>().unwrap();
        agg.seen += 1;
        if agg.seen > agg.budget {
            return state; // over budget: the search fails its count check; skip the costly screening
        }
        if agg.lag != max_lag as usize || agg.m != m as usize {
            pgrx::error!("the query envelope, max_lag and pool must be the same for every row");
        }
        if agg.last_query_ptr != query_le.as_ptr() as usize {
            if agg.query_bytes != query_le {
                pgrx::error!("the query envelope, max_lag and pool must be the same for every row");
            }
            agg.last_query_ptr = query_le.as_ptr() as usize;
        }
        let ri = agg.rid_index(recording_id);
        agg.screener
            .as_mut()
            .unwrap()
            .push((ri, start_sample), envelope)
            .unwrap_or_else(|e| pgrx::error!("{}", e));
    }
    state
}

#[pg_extern(parallel_safe)]
fn morlet_envelope_screen_combine(a: pgrx::Internal, b: pgrx::Internal) -> pgrx::Internal {
    unsafe {
        if !b.initialized() {
            return a;
        }
        if !a.initialized() {
            return b;
        }
        let mut a = a;
        let mut b = b;
        let other_agg = b.get_mut::<ScreenAgg>().unwrap();
        let other = other_agg.screener.take();
        let other_rids = std::mem::take(&mut other_agg.rids);
        let mine = a.get_mut::<ScreenAgg>().unwrap();
        if let (Some(o), Some(_)) = (other, mine.screener.as_ref()) {
            // re-key the other screener's candidates to this state's recording ids
            let map: Vec<u32> = other_rids.iter().map(|r| mine.rid_index(r)).collect();
            let (kept, uppers) = o.into_parts();
            let kept = kept
                .into_iter()
                .map(|((ri, st), lo, hi)| ((map[ri as usize], st), lo, hi))
                .collect();
            let o = morlet_core::envelope_screen::Screener::from_parts(
                &mine.query,
                mine.lag,
                mine.m,
                kept,
                uppers,
            )
            .unwrap_or_else(|| pgrx::error!("invalid screen state"));
            mine.screener.as_mut().unwrap().merge(o);
        }
        a
    }
}

fn put_f64(out: &mut Vec<u8>, v: f64) {
    out.extend_from_slice(&v.to_le_bytes());
}

#[pg_extern(strict, parallel_safe)]
fn morlet_envelope_screen_serial(mut state: pgrx::Internal) -> Vec<u8> {
    let agg = unsafe { state.get_mut::<ScreenAgg>() }
        .unwrap_or_else(|| pgrx::error!("empty screen state"));
    let (kept, uppers) = agg.screener.take().unwrap().into_parts();
    let mut out = b"MS1".to_vec();
    for v in [
        agg.lag as u32,
        agg.m as u32,
        agg.query.len() as u32,
        kept.len() as u32,
        uppers.len() as u32,
    ] {
        out.extend_from_slice(&v.to_le_bytes());
    }
    agg.query.iter().for_each(|&v| put_f64(&mut out, v));
    for ((ri, start), lo, hi) in &kept {
        let rid = &agg.rids[*ri as usize];
        out.extend_from_slice(&(rid.len() as u32).to_le_bytes());
        out.extend_from_slice(rid.as_bytes());
        out.extend_from_slice(&start.to_le_bytes());
        put_f64(&mut out, *lo);
        put_f64(&mut out, *hi);
    }
    uppers.iter().for_each(|&v| put_f64(&mut out, v));
    out
}

#[pg_extern(strict, parallel_safe)]
fn morlet_envelope_screen_deserial(
    bytes: &[u8],
    _unused: pgrx::Internal,
    fcinfo: pg_sys::FunctionCallInfo,
) -> pgrx::Internal {
    let bad = || -> ! { pgrx::error!("corrupt screen state") };
    let mut pos = 3usize;
    if bytes.get(..3) != Some(b"MS1") {
        bad();
    }
    let u32_at = |p: &mut usize| -> usize {
        let v = bytes.get(*p..*p + 4).unwrap_or_else(|| bad());
        *p += 4;
        u32::from_le_bytes(v.try_into().unwrap()) as usize
    };
    let (lag, m, qlen, nkept, nuppers) = (
        u32_at(&mut pos),
        u32_at(&mut pos),
        u32_at(&mut pos),
        u32_at(&mut pos),
        u32_at(&mut pos),
    );
    let f64_at = |p: &mut usize| -> f64 {
        let v = bytes.get(*p..*p + 8).unwrap_or_else(|| bad());
        *p += 8;
        f64::from_le_bytes(v.try_into().unwrap())
    };
    let query: Vec<f64> = (0..qlen).map(|_| f64_at(&mut pos)).collect();
    let mut kept = Vec::with_capacity(nkept);
    let mut rids: Vec<String> = Vec::new();
    for _ in 0..nkept {
        let len = u32::from_le_bytes(
            bytes
                .get(pos..pos + 4)
                .unwrap_or_else(|| bad())
                .try_into()
                .unwrap(),
        ) as usize;
        pos += 4;
        let rid = std::str::from_utf8(bytes.get(pos..pos + len).unwrap_or_else(|| bad()))
            .unwrap_or_else(|_| bad())
            .to_owned();
        pos += len;
        let start = i64::from_le_bytes(
            bytes
                .get(pos..pos + 8)
                .unwrap_or_else(|| bad())
                .try_into()
                .unwrap(),
        );
        pos += 8;
        let lo = f64_at(&mut pos);
        let hi = f64_at(&mut pos);
        let ri = match rids.iter().position(|r| *r == rid) {
            Some(i) => i as u32,
            None => {
                rids.push(rid);
                (rids.len() - 1) as u32
            }
        };
        kept.push(((ri, start), lo, hi));
    }
    let uppers: Vec<f64> = (0..nuppers).map(|_| f64_at(&mut pos)).collect();
    if pos != bytes.len() {
        bad();
    }
    let screener = morlet_core::envelope_screen::Screener::from_parts(&query, lag, m, kept, uppers)
        .unwrap_or_else(|| bad());
    let query_bytes = query.iter().flat_map(|x| x.to_le_bytes()).collect();
    let agg = ScreenAgg {
        query,
        query_bytes,
        last_query_ptr: 0,
        lag,
        m,
        rids,
        screener: Some(screener),
        // A deserialized state is only combined and finished, never stepped.
        seen: 0,
        budget: i64::MAX,
    };
    unsafe {
        let ctx = aggregate_context(fcinfo);
        pgrx::Internal::from(Some(pg_sys::Datum::from(
            pgrx::PgMemoryContexts::For(ctx).leak_and_drop_on_delete(agg),
        )))
    }
}

/// The candidates as a JSON array of [recording_id, start_sample], ordered by lower bound.
#[pg_extern(parallel_safe)]
fn morlet_envelope_screen_final(mut state: pgrx::Internal) -> JsonB {
    let Some(agg) = (unsafe { state.get_mut::<ScreenAgg>() }) else {
        return JsonB(serde_json::Value::Array(Vec::new()));
    };
    let mut kept = agg.screener.take().map(|s| s.finish()).unwrap_or_default();
    kept.sort_by(|a, b| a.1.total_cmp(&b.1));
    JsonB(serde_json::Value::Array(
        kept.into_iter()
            .map(|((ri, start), _, _)| serde_json::json!([agg.rids[ri as usize], start]))
            .collect(),
    ))
}

/// Per-chunk summary of samples (`morlet_core::intervalstats`, 66 bytes), stored when the chunk is
/// appended to a collection with summaries enabled.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_summary(samples: pgrx::Array<'_, f64>) -> Vec<u8> {
    let mut s = morlet_core::intervalstats::IntervalStats::default();
    s.add(&sample_slice(&samples));
    s.to_summary().to_vec()
}

/// Integrity check of one stored chunk for `morlet_verify` (#193): NULL when the chunk is sound, otherwise a
/// description of the problem. Never raises for a bad chunk, so a scrub reports every problem in a collection
/// instead of stopping at the first. Checks that the encoded chunk decodes (structure and CRC-32C) to
/// `sample_count` samples and, when `summarized`, that its stored summary is byte-identical to the summary of
/// the decoded samples.
#[pg_extern(immutable, parallel_safe)]
fn morlet_check_chunk(
    encoded: Option<&[u8]>,
    sample_count: Option<i32>,
    summary: Option<&[u8]>,
    summarized: bool,
) -> Option<String> {
    let encoded = match encoded {
        Some(e) => e,
        None => return Some("chunk has no encoded bytes".into()),
    };
    let samples = match morlet_core::rawcodec::decode(encoded) {
        Ok(s) => s,
        Err(e) => return Some(format!("chunk does not decode: {e}")),
    };
    if Some(samples.len() as i32) != sample_count {
        return Some(format!(
            "chunk holds {} samples, sample_count says {}",
            samples.len(),
            sample_count.map_or("NULL".into(), |n| n.to_string())
        ));
    }
    if summarized {
        let mut stats = morlet_core::intervalstats::IntervalStats::default();
        stats.add(&samples);
        match summary {
            None => return Some("summary missing".into()),
            Some(s) if s != stats.to_summary().as_slice() => {
                return Some("summary differs from the chunk's samples".into())
            }
            _ => {}
        }
    }
    None
}

/// The same summary computed from a stored encoded chunk (for building summaries of existing chunks).
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_encoded_chunk_summary(encoded: &[u8]) -> Vec<u8> {
    let samples = morlet_core::rawcodec::decode(encoded).unwrap_or_else(|e| pgrx::error!("{}", e));
    let mut s = morlet_core::intervalstats::IntervalStats::default();
    s.add(&samples);
    s.to_summary().to_vec()
}

/// `[count, min, max, mean, rms]` of a stored summary.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_chunk_summary_values(summary: &[u8]) -> Vec<f64> {
    morlet_core::intervalstats::IntervalStats::from_summary(summary)
        .unwrap_or_else(|| pgrx::error!("invalid chunk summary"))
        .finish()
        .to_vec()
}

/// State of `morlet_interval_stats_agg`: one exact accumulator per bucket of [lo, hi) (`morlet_core::exactstats`).
/// Each chunk's samples in the interval are accumulated on their own (or taken from its stored summary when the
/// chunk lies entirely inside) and merged into the bucket that holds the middle of its part. A roll-up row (a
/// version-2 summary of whole chunks, #219) is merged as a whole: it must lie inside the interval and inside one
/// bucket, so every chunk it holds would have gone to that bucket. Exact merging makes the result bitwise the
/// same with or without stored summaries and roll-ups, in any order.
struct StatsAgg {
    lo: i64,
    hi: i64,
    buckets: Vec<(i64, i64, morlet_core::exactstats::ExactStats)>,
    previous: i64,
}

/// The bucket of [lo, hi) split into `nb` parts that holds sample position `x` (the rule of
/// `morlet_interval_stats_agg`).
fn bucket_of(x: i64, lo: i64, hi: i64, nb: i32) -> usize {
    (((x as i128 - lo as i128) * nb as i128) / (hi as i128 - lo as i128)).clamp(0, nb as i128 - 1)
        as usize
}

/// Bucket index (0-based) of sample position `x` in [lo, hi) split into `buckets` parts, as
/// `morlet_interval_stats_agg` assigns chunk parts by their middle. Used to pick roll-ups that lie in one bucket.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_interval_bucket(x: i64, interval_start: i64, interval_end: i64, buckets: i32) -> i32 {
    if interval_end <= interval_start || buckets < 1 {
        pgrx::error!("the interval must be nonempty and buckets positive");
    }
    bucket_of(x, interval_start, interval_end, buckets) as i32
}

#[pg_extern(parallel_safe)]
#[allow(clippy::too_many_arguments)]
fn morlet_interval_stats_step(
    state: pgrx::Internal,
    encoded: Option<&[u8]>,
    summary: Option<&[u8]>,
    start_sample: Option<i64>,
    sample_count: Option<i32>,
    interval_start: Option<i64>,
    interval_end: Option<i64>,
    buckets: Option<i32>,
    fcinfo: pg_sys::FunctionCallInfo,
) -> pgrx::Internal {
    use morlet_core::exactstats::{ExactStats, ROLLUP_VERSION};
    use morlet_core::intervalstats::IntervalStats;
    let (Some(start), Some(count), Some(lo), Some(hi), Some(nb)) = (
        start_sample,
        sample_count,
        interval_start,
        interval_end,
        buckets,
    ) else {
        pgrx::error!("chunk start, sample count, interval and bucket count must not be NULL");
    };
    if hi <= lo || !(1..=100_000).contains(&nb) || count < 1 {
        pgrx::error!("the interval must be nonempty, buckets 1..100000 and sample counts positive");
    }
    let mut state = state;
    unsafe {
        if !state.initialized() {
            let ctx = aggregate_context(fcinfo);
            let agg = StatsAgg {
                lo,
                hi,
                buckets: (0..nb)
                    .map(|_| (i64::MAX, i64::MIN, ExactStats::default()))
                    .collect(),
                previous: i64::MIN,
            };
            state = pgrx::Internal::from(Some(pg_sys::Datum::from(
                pgrx::PgMemoryContexts::For(ctx).leak_and_drop_on_delete(agg),
            )));
        }
        let agg = state.get_mut::<StatsAgg>().unwrap();
        if agg.lo != lo || agg.hi != hi || agg.buckets.len() != nb as usize {
            pgrx::error!("the interval and bucket count must be the same for every row");
        }
        if start <= agg.previous {
            pgrx::error!("chunks must be aggregated in strictly ascending start order");
        }
        agg.previous = start;
        let end = start + count as i64;
        let (a, b) = (lo.max(start), hi.min(end));
        if a >= b {
            return state;
        }
        let (k, part) = match (summary, encoded) {
            (Some(sm), _) if sm.first() == Some(&ROLLUP_VERSION) => {
                let k = bucket_of(start, lo, hi, nb);
                if start < lo || end > hi || bucket_of(end - 1, lo, hi, nb) != k {
                    pgrx::error!("a roll-up must lie inside the interval and inside one bucket");
                }
                let s =
                    ExactStats::from_bytes(sm).unwrap_or_else(|| pgrx::error!("invalid roll-up"));
                if s.count() != count as u64 {
                    pgrx::error!("roll-up does not match its sample count");
                }
                (k, s)
            }
            (Some(sm), _) if start >= lo && end <= hi => {
                let s = IntervalStats::from_summary(sm)
                    .unwrap_or_else(|| pgrx::error!("invalid chunk summary"));
                if s.count() != count as u64 {
                    pgrx::error!("chunk summary does not match the chunk's sample count");
                }
                let mut e = ExactStats::default();
                e.add_piece(&s);
                (bucket_of(a + (b - a) / 2, lo, hi, nb), e)
            }
            (_, Some(enc)) => {
                let samples =
                    morlet_core::rawcodec::decode(enc).unwrap_or_else(|e| pgrx::error!("{}", e));
                if samples.len() != count as usize {
                    pgrx::error!("encoded chunk does not match its sample count");
                }
                let mut s = IntervalStats::default();
                s.add(&samples[(a - start) as usize..(b - start) as usize]);
                let mut e = ExactStats::default();
                e.add_piece(&s);
                (bucket_of(a + (b - a) / 2, lo, hi, nb), e)
            }
            _ => pgrx::error!("a chunk needs its encoded samples unless its summary covers it"),
        };
        let bucket = &mut agg.buckets[k];
        bucket.0 = bucket.0.min(a);
        bucket.1 = bucket.1.max(b);
        bucket.2.merge(&part);
    }
    state
}

/// Non-empty buckets as `[bucket_start, bucket_end, count, min, max, mean, rms]`, concatenated.
#[pg_extern(parallel_safe)]
fn morlet_interval_stats_final(mut state: pgrx::Internal) -> Vec<f64> {
    let Some(agg) = (unsafe { state.get_mut::<StatsAgg>() }) else {
        return Vec::new();
    };
    let mut out = Vec::new();
    for (a, b, s) in &agg.buckets {
        if s.count() > 0 {
            out.extend_from_slice(&[*a as f64, *b as f64]);
            out.extend_from_slice(&s.finish());
        }
    }
    out
}

/// Exact merge of chunk summaries (version 1) and roll-ups (version 2) into one roll-up (#219), in any order.
#[pg_extern(immutable, parallel_safe)]
fn morlet_rollup_step(state: Option<Vec<u8>>, summary: Option<&[u8]>) -> Option<Vec<u8>> {
    use morlet_core::exactstats::ExactStats;
    let Some(sm) = summary else {
        pgrx::error!("a roll-up needs the summary of every chunk it holds");
    };
    let add = ExactStats::from_summary(sm)
        .unwrap_or_else(|| pgrx::error!("invalid chunk summary or roll-up"));
    let mut acc = match state {
        Some(b) => ExactStats::from_bytes(&b).unwrap_or_else(|| pgrx::error!("invalid roll-up")),
        None => ExactStats::default(),
    };
    acc.merge(&add);
    Some(acc.to_bytes())
}

/// `[count, min, max, mean, rms]` of a stored roll-up or chunk summary.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_rollup_values(summary: &[u8]) -> Vec<f64> {
    morlet_core::exactstats::ExactStats::from_summary(summary)
        .unwrap_or_else(|| pgrx::error!("invalid chunk summary or roll-up"))
        .finish()
        .to_vec()
}

/// `[start, end, min, max, rms]` (offsets into `samples`) for `buckets` consecutive parts of equal size
/// (edges at floor(k * n / buckets)), each with `morlet_core::intervalstats` semantics; empty parts are
/// skipped. Used for the overview of a search match.
#[pg_extern(immutable, parallel_safe, strict)]
fn morlet_samples_overview(samples: pgrx::Array<'_, f64>, buckets: i32) -> Vec<f64> {
    let x = sample_slice(&samples);
    if buckets < 1 {
        pgrx::error!("buckets must be positive");
    }
    let (n, nb) = (x.len(), buckets as usize);
    let mut out = Vec::with_capacity(5 * nb);
    for k in 0..nb {
        let (a, b) = (k * n / nb, (k + 1) * n / nb);
        if a < b {
            let mut s = morlet_core::intervalstats::IntervalStats::default();
            s.add(&x[a..b]);
            let f = s.finish();
            out.extend_from_slice(&[a as f64, b as f64, f[1], f[2], f[4]]);
        }
    }
    out
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
    // Tests that create the shared "env" collection or the vector extension run their DDL one at a
    // time: pgrx runs tests in parallel, and concurrent transactions creating the same catalog
    // objects in different orders deadlocked now and then. Must be each such test's first statement.
    fn serialize_shared_ddl() {
        Spi::run("SELECT pg_advisory_xact_lock(4711)").unwrap();
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
        Spi::run("SELECT morlet_enable_chunk_summaries('ten'); SELECT morlet_build_chunk_summaries('ten',r) FROM unnest(ARRAY['a1','b1']) r").unwrap();
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
        // summaries live in the chunk table and follow its policy
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(summary) FROM ten_chunks").unwrap(),
            Some(1)
        );
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
    fn probe_search_applies_time_filters_in_the_index_scan() {
        serialize_shared_ddl();
        if !pgvector_available() {
            return; // pgvector is optional; CI's pgvector job runs this
        }
        Spi::run(r#"
            CREATE EXTENSION IF NOT EXISTS vector;
            SELECT morlet_create_stream_collection('public','tprobe');
            SELECT morlet_enable_envelopes('tprobe');
            SELECT morlet_start_recording('tprobe',r,100,'z','counts',jsonb_build_object('start',t),512,256,'none','none')
              FROM (VALUES ('d1','2026-03-01T00:00:00Z'),('d2','2026-03-02T00:00:00Z')) v(r,t);
            SELECT morlet_append_chunk('tprobe',r,k*4096,ARRAY(SELECT round(100*sin(i/9.0+o))::float8
              FROM generate_series(k*4096,k*4096+4095) i)) FROM (VALUES ('d1',0),('d2',1)) v(r,o), generate_series(0,3) k
              ORDER BY r,k;
            SELECT morlet_build_envelopes('tprobe',r,512,256) FROM unnest(ARRAY['d1','d2']) r;
            SELECT morlet_enable_envelope_probes('tprobe',512);
        "#).unwrap();
        // exact_threshold 0 forces the index path
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(recording_id='d1' OR end_sample<=2048) FROM
            morlet_search_envelope_probes('tprobe','d2',2048,2560,'{}',10,0.075,true,false,50,5,0,before_only=>true)").unwrap(), Some(true));
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FROM morlet_search_envelope_probes('tprobe','d2',2048,2560,'{}',10,
            0.075,true,false,50,5,0,before_only=>true)").unwrap(), Some(10));
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(recording_id='d1' AND start_sample>=5000 AND end_sample<=9000) FROM
            morlet_search_envelope_probes('tprobe','d2',2048,2560,'{}',10,0.075,true,false,50,5,0,
            '2026-03-01T00:00:50Z','2026-03-01T00:01:30Z')").unwrap(), Some(true));
    }

    #[pg_test]
    fn row_security_covers_probe_tables() {
        serialize_shared_ddl();
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
    fn row_security_matches_only_its_own_probe_tables() {
        // '_' in a collection name must not act as a pattern wildcard: enabling row security on
        // line_1 must leave line11's probe table alone.
        Spi::run(
            r#"
            SELECT morlet_create_stream_collection('public','line_1');
            SELECT morlet_create_stream_collection('public','line11');
            CREATE TABLE line11_probes_4096 (recording_id text);
            CREATE TABLE line_1_probes_notes (recording_id text);
            SELECT morlet_enable_row_security('line_1');
        "#,
        )
        .unwrap();
        assert_eq!(
            Spi::get_one::<bool>(
                "SELECT relrowsecurity FROM pg_class WHERE relname='line11_probes_4096'"
            )
            .unwrap(),
            Some(false)
        );
        assert_eq!(
            Spi::get_one::<bool>(
                "SELECT relrowsecurity FROM pg_class WHERE relname='line_1_probes_notes'"
            )
            .unwrap(),
            Some(false)
        );
        assert_eq!(
            Spi::get_one::<bool>(
                "SELECT relrowsecurity FROM pg_class WHERE relname='line_1_chunks'"
            )
            .unwrap(),
            Some(true)
        );
    }

    #[pg_test]
    fn collection_status_reports_progress_and_backlog() {
        serialize_shared_ddl();
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
        serialize_shared_ddl();
        if !pgvector_available() {
            return; // pgvector is optional; CI's pgvector job runs this
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

    // Runs with and without pgvector installed: the vector extension is never created in this
    // database outside a test's own (rolled back) transaction.
    #[pg_test(error = "probe search needs the pgvector extension (CREATE EXTENSION vector)")]
    fn probe_search_requires_the_vector_extension() {
        serialize_shared_ddl();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT morlet_enable_envelope_probes('env',2048)").unwrap();
    }

    #[pg_test(
        error = "probe search is not enabled for window_size 2048 (morlet_enable_envelope_probes)"
    )]
    fn probe_search_requires_enabling() {
        serialize_shared_ddl();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT * FROM morlet_search_envelope_probes('env','A',2048,4096)").unwrap();
    }

    #[pg_test]
    fn search_similar_is_exact_search_without_probes() {
        serialize_shared_ddl();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        let list = |f: &str| {
            format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM {f}('env','A',2048,4096,'{{}}',10)")
        };
        assert_eq!(
            Spi::get_one::<String>(&list("morlet_search_similar")).unwrap(),
            Spi::get_one::<String>(&list("morlet_search_envelope_interval")).unwrap()
        );
        // the exhaustive default allows a million eligible windows (was 4096)
        for f in [
            "morlet_search_envelope_interval",
            "morlet_search_envelopes",
            "morlet_search_similar",
        ] {
            assert!(Spi::get_one::<String>(&format!(
                "SELECT pg_get_function_arguments('{f}'::regproc)"
            ))
            .unwrap()
            .unwrap()
            .contains("max_candidates integer DEFAULT 1000000"));
        }
    }

    #[pg_test]
    fn search_similar_uses_probes_when_enabled() {
        serialize_shared_ddl();
        if !pgvector_available() {
            return; // pgvector is optional; CI's pgvector job runs this
        }
        Spi::run("CREATE EXTENSION IF NOT EXISTS vector").unwrap();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        Spi::run("SELECT morlet_enable_envelope_probes('env',2048)").unwrap();
        let list = |f: &str| {
            format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM {f}('env','A',2048,4096,'{{}}',10)")
        };
        assert_eq!(
            Spi::get_one::<String>(&list("morlet_search_similar")).unwrap(),
            Spi::get_one::<String>(&list("morlet_search_envelope_probes")).unwrap()
        );
    }

    #[pg_test]
    fn probe_search_is_maintained_and_exact_on_small_galleries() {
        serialize_shared_ddl();
        if !pgvector_available() {
            return; // pgvector is optional; CI's pgvector job runs this
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
        let probe = "SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_probes('env','A',2048,4096,'{}',10,0.075,true,true,100,5,0)";
        assert_eq!(
            Spi::get_one::<String>(exact).unwrap(),
            Spi::get_one::<String>(probe).unwrap()
        );
        assert_eq!(Spi::get_one::<String>("SELECT recording_id||':'||start_sample FROM morlet_search_envelope_probes('env','A',2048,4096,'{}',1,0.075,true,true,50,5,0)").unwrap().unwrap(), "B:9216");
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
        serialize_shared_ddl();
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
        serialize_shared_ddl();
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
        serialize_shared_ddl();
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

    #[pg_test]
    fn envelope_search_at_its_budget_is_unchanged() {
        // #160: screening stops once a scan has seen more than max_candidates windows; at exactly the
        // budget nothing is skipped, so results equal the unbounded search.
        serialize_shared_ddl();
        envelope_fixture();
        Spi::run(
            "SELECT morlet_build_envelopes('env',r,2048,1024) FROM unnest(ARRAY['A','B','C']) r",
        )
        .unwrap();
        let q = |b: &str| {
            format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelope_interval('env','A',0,2048,'{{}}',10,0.075,true,true,{b})")
        };
        assert_eq!(
            Spi::get_one::<String>(&q("45")).unwrap(),
            Spi::get_one::<String>(&q("1000000")).unwrap()
        );
    }

    #[pg_test(error = "history exceeds 2 segments; narrow match_keys or the metadata filter")]
    fn compare_summary_refuses_oversized_history_first() {
        cal_fixture();
        Spi::run("SELECT * FROM morlet_compare_summary('cal',(SELECT segment_id FROM cal_ctx_segments WHERE recording_id='A3'),max_candidates=>2)").unwrap();
    }

    #[pg_test(error = "45 windows are eligible, more than max_candidates (5)")]
    fn envelope_search_enforces_its_budget() {
        serialize_shared_ddl();
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

    #[pg_test]
    fn late_context_invalidates_the_segments_it_changes() {
        // #150: context added or revised after a build marks the segments it can change as stale; the next
        // build replaces them. Context on keys the spec does not use leaves segments alone.
        Spi::run(r#"
            SELECT morlet_create_stream_collection('public','late');
            SELECT morlet_enable_context('late');
            SELECT morlet_start_recording('late',r,1000,'x','g') FROM unnest(ARRAY['r','s']) r;
            SELECT morlet_append_chunk('late',r,0,ARRAY(SELECT sin(i/5.0) FROM generate_series(0,8191) i)) FROM unnest(ARRAY['r','s']) r;
            SELECT morlet_add_context('late','b1','[{"recording_id":"r","key":"state","value":"cut","start_sample":0,"end_sample":8192}]');
            SELECT morlet_build_context_segments('late','r',ARRAY['state'],ARRAY['part']);
            SELECT morlet_build_context_values('late','r');
            SELECT morlet_add_context('late','b2','[{"recording_id":"r","key":"part","value":"P1","start_sample":0,"end_sample":8192}]');
        "#).unwrap();
        assert_eq!(
            Spi::get_one::<bool>("SELECT stale FROM late_ctx_segments WHERE recording_id='r'")
                .unwrap(),
            Some(true)
        );
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT morlet_build_context_segments('late','r',ARRAY['state'],ARRAY['part'])"
            )
            .unwrap(),
            Some(1)
        );
        assert_eq!(Spi::get_one::<String>("SELECT occurrence::text||':'||stale||':'||cardinality(ctx_ids) FROM late_ctx_segments WHERE recording_id='r'").unwrap().unwrap(), r#"{"part": "P1"}:false:2"#);
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM late_ctx_values").unwrap(),
            Some(0)
        ); // deleted with the old segment
           // A revision that extends an occurrence reaches a segment the old row did not cover.
        Spi::run(r#"
            SELECT morlet_add_context('late','b3','[{"recording_id":"s","key":"state","value":"cut","start_sample":0,"end_sample":4096},
              {"recording_id":"s","key":"state","value":"air","start_sample":4096,"end_sample":8192},
              {"recording_id":"s","key":"part","value":"P1","start_sample":0,"end_sample":4096}]');
            SELECT morlet_build_context_segments('late','s',ARRAY['state'],ARRAY['part']);
            SELECT morlet_revise_context('late',(SELECT ctx_id FROM late_ctx WHERE batch_id='b3' AND key='part'),'{"end_sample":8192}');
        "#).unwrap();
        assert_eq!(Spi::get_one::<i64>("SELECT count(*) FILTER (WHERE stale) FROM late_ctx_segments WHERE recording_id='s'").unwrap(), Some(2));
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT morlet_build_context_segments('late','s',ARRAY['state'],ARRAY['part'])"
            )
            .unwrap(),
            Some(2)
        );
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(occurrence::text,',' ORDER BY start_sample) FROM late_ctx_segments WHERE recording_id='s'").unwrap().unwrap(), r#"{"part": "P1"},{"part": "P1"}"#);
        // A key outside the spec changes nothing.
        Spi::run(r#"SELECT morlet_add_context('late','b4','[{"recording_id":"s","key":"tool","value":7,"start_sample":0,"end_sample":8192}]')"#).unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FILTER (WHERE stale) FROM late_ctx_segments")
                .unwrap(),
            Some(0)
        );
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
        // No context qualifies for std (every median is negligible): its pooled spread stays unknown
        // (NULL) instead of becoming the floor; duration has an estimate (0) and is floored to 0.01.
        assert_eq!(Spi::get_one::<String>(&format!("SELECT coalesce(sp[3]::text,'null')||':'||sp[1] FROM morlet_value_spreads('frz',{f6}) sp")).unwrap().unwrap(), "null:0.01");
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
        // The raw variant returns the same rows with the same samples as little-endian float64 bytes,
        // including an interval clamped at the recording's end and one with no sample on the slower channel.
        assert_eq!(Spi::get_one::<bool>("SELECT bool_and(ok) FROM (SELECT (SELECT array_agg(row(role,recording_id,sample_rate_hz,start_sample,end_sample,start_time_ns,samples)::text ORDER BY role) FROM morlet_read_acquisition('cell','A1',t0,t1)) IS NOT DISTINCT FROM (SELECT array_agg(row(role,recording_id,sample_rate_hz,start_sample,end_sample,start_time_ns,morlet_float8_from_le_bytes(samples))::text ORDER BY role) FROM morlet_read_acquisition_raw('cell','A1',t0,t1)) AS ok FROM (VALUES (9990000000::bigint,10010000000::bigint),(0,30000000),(19995000000,25000000000),(10000000000,10002000000)) v(t0,t1)) x").unwrap(), Some(true));
        assert_eq!(Spi::get_one::<String>("SELECT string_agg(role||':'||start_sample||'-'||end_sample||':'||length(samples),',' ORDER BY role) FROM morlet_read_acquisition_raw('cell','A1',10000000000,10002000000)").unwrap().unwrap(), "vib:10000-10002:16");
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

    #[pg_test]
    fn history_counts_independent_observations_only() {
        // #224: segments of one interval built with several specs must not count as their own history.
        Spi::run(
            "SELECT morlet_create_stream_collection('public','s'); SELECT morlet_enable_context('s');
             SELECT morlet_start_recording('s','r',1000,'x','g');
             SELECT morlet_append_chunk('s','r',0,ARRAY(SELECT sin(i/7.0) FROM generate_series(0,2047) i));
             SELECT morlet_add_context('s','b1',jsonb_build_array(jsonb_build_object('recording_id','r','key','state',
               'value','cut','start_sample',0,'end_sample',2048)));
             SELECT morlet_build_context_segments('s','r',ARRAY['state'],min_samples=>k) FROM generate_series(1,6) k;
             SELECT morlet_build_context_values('s','r');",
        )
        .unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM s_ctx_segments").unwrap(),
            Some(6)
        );
        let q = "SELECT hist_n||':'||status FROM morlet_compare_to_history('s',(SELECT min(segment_id) FROM s_ctx_segments)) WHERE value_name='mean'";
        assert_eq!(
            Spi::get_one::<String>(q).unwrap().unwrap(),
            "0:insufficient_history"
        );
        // Genuinely independent, compatible occurrences still count: five more cuts on other samples.
        Spi::run(
            "SELECT morlet_append_chunk('s','r',2048,ARRAY(SELECT sin(i/7.0)*(1+i/100000.0) FROM generate_series(2048,12287) i));
             SELECT morlet_add_context('s','b2',(SELECT jsonb_agg(jsonb_build_object('recording_id','r','key','state',
               'value',CASE WHEN k%2=0 THEN 'cut' ELSE 'air' END,'start_sample',k*1024+1024,'end_sample',k*1024+2048))
               FROM generate_series(1,10) k));
             SELECT morlet_build_context_segments('s','r',ARRAY['state'],min_samples=>1);
             SELECT morlet_build_context_values('s','r');",
        )
        .unwrap();
        let q1 = "SELECT hist_n FROM morlet_compare_to_history('s',(SELECT segment_id FROM s_ctx_segments WHERE start_sample=0 AND spec->>'min_samples'='1'),min_history=>1) WHERE value_name='mean'";
        assert_eq!(Spi::get_one::<i32>(q1).unwrap(), Some(5));
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
    /// I1 (#233): a batch stores exactly what chunk-by-chunk appends store (chunks, summaries, segments,
    /// checkpoint), from one call or several, and replays and overlaps go through the per-chunk path.
    #[pg_test]
    fn batched_raw_append_equals_chunk_by_chunk() {
        Spi::run(
            "CREATE TEMP TABLE batch_src AS SELECT i,round(1000*sin(i/7.)+((i*7919)%61))::float8 AS v FROM generate_series(0,10499) i;
             CREATE FUNCTION pg_temp.raw(a int,b int) RETURNS bytea LANGUAGE sql AS
               $$ SELECT morlet_float8_le_bytes(ARRAY(SELECT v FROM batch_src WHERE i BETWEEN a AND b ORDER BY i)) $$;
             SELECT morlet_create_stream_collection('public',c) FROM unnest(ARRAY['batch_one','batch_many','batch_mixed']) c;
             SELECT morlet_enable_chunk_summaries(c::regclass) FROM unnest(ARRAY['batch_one','batch_many','batch_mixed']) c;
             SELECT morlet_start_recording(c::regclass,'A',4096,'x','g') FROM unnest(ARRAY['batch_one','batch_many','batch_mixed']) c;
             SELECT morlet_append_chunk_raw('batch_one','A',s,pg_temp.raw(s,least(s+999,10499))) FROM generate_series(0,10499,1000) s ORDER BY s;
             SELECT morlet_append_chunks_raw('batch_many','A',0,pg_temp.raw(0,4999),1000);
             SELECT morlet_append_chunks_raw('batch_many','A',5000,pg_temp.raw(5000,10499),1000);
             SELECT morlet_append_chunks_raw('batch_mixed','A',0,pg_temp.raw(0,5999),1000);
             SELECT morlet_append_chunks_raw('batch_mixed','A',5000,pg_temp.raw(5000,10499),1000);",
        )
        .unwrap();
        let same = |other: &str| {
            Spi::get_one::<bool>(&format!(
                "SELECT (SELECT array_agg(row(start_sample,sample_count,encoded,summary)::text ORDER BY start_sample) FROM batch_one_chunks)
                      = (SELECT array_agg(row(start_sample,sample_count,encoded,summary)::text ORDER BY start_sample) FROM {other}_chunks)
                    AND (SELECT array_agg(row(start_sample,features)::text ORDER BY start_sample) FROM batch_one_segments)
                      = (SELECT array_agg(row(start_sample,features)::text ORDER BY start_sample) FROM {other}_segments)
                    AND (SELECT checkpoint FROM batch_one) = (SELECT checkpoint FROM {other})"
            ))
            .unwrap()
            .unwrap()
        };
        assert!(same("batch_many"));
        assert!(same("batch_mixed"));
        assert!(
            Spi::get_one::<i64>("SELECT count(*) FROM batch_one_segments")
                .unwrap()
                .unwrap()
                > 0
        );
        // An identical retry of everything is acknowledged and stores nothing new.
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT morlet_append_chunks_raw('batch_many','A',0,pg_temp.raw(0,10499),1000)"
            )
            .unwrap()
            .unwrap(),
            10500
        );
        assert!(same("batch_many"));
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn batched_raw_append_rejects_conflicting_replay() {
        Spi::run("SELECT morlet_create_stream_collection('public','batch_conflict'); SELECT morlet_start_recording('batch_conflict','A',4096,'x','g'); SELECT morlet_append_chunks_raw('batch_conflict','A',0,morlet_float8_le_bytes(ARRAY[1,2,3,4]::float8[]),2); SELECT morlet_append_chunks_raw('batch_conflict','A',0,morlet_float8_le_bytes(ARRAY[1,2,3,5]::float8[]),2)").unwrap();
    }
    #[pg_test(
        error = "chunk offset must equal the next sample offset (no gaps, overlaps or replays)"
    )]
    fn batched_raw_append_rejects_gaps() {
        Spi::run("SELECT morlet_create_stream_collection('public','batch_gap'); SELECT morlet_start_recording('batch_gap','A',4096,'x','g'); SELECT morlet_append_chunks_raw('batch_gap','A',0,morlet_float8_le_bytes(ARRAY[1,2]::float8[]),2); SELECT morlet_append_chunks_raw('batch_gap','A',5,morlet_float8_le_bytes(ARRAY[1,2]::float8[]),2)").unwrap();
    }
    /// I1 step 3: chunks encoded on worker threads are identical to chunks encoded in the backend's thread,
    /// on and off the integer grid, and errors still come in chunk order.
    #[pg_test]
    fn batched_encoding_does_not_depend_on_threads() {
        Spi::run(
            "CREATE TEMP TABLE enc_src AS SELECT i,CASE WHEN i<40000 THEN round(1000*sin(i/7.))::float8 ELSE sin(i/7.)::float8 END AS v FROM generate_series(0,99999) i;
             SELECT morlet_create_stream_collection('public',c) FROM unnest(ARRAY['enc_one','enc_four','enc_auto']) c;
             SELECT morlet_start_recording(c::regclass,'A',4096,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['enc_one','enc_four','enc_auto']) c;
             SET morlet.encode_threads = 1;
             SELECT morlet_append_chunks_raw('enc_one','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM enc_src ORDER BY i)),4096);
             SET morlet.encode_threads = 4;
             SELECT morlet_append_chunks_raw('enc_four','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM enc_src ORDER BY i)),4096);
             RESET morlet.encode_threads;
             SELECT morlet_append_chunks_raw('enc_auto','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM enc_src ORDER BY i)),4096);",
        )
        .unwrap();
        for other in ["enc_four", "enc_auto"] {
            assert!(Spi::get_one::<bool>(&format!(
                "SELECT (SELECT array_agg(row(start_sample,sample_count,encoded)::text ORDER BY start_sample) FROM enc_one_chunks)
                      = (SELECT array_agg(row(start_sample,sample_count,encoded)::text ORDER BY start_sample) FROM {other}_chunks)
                    AND (SELECT count(*) FROM {other}_chunks) = 25
                    AND (SELECT checkpoint FROM enc_one) = (SELECT checkpoint FROM {other})"
            ))
            .unwrap()
            .unwrap());
        }
    }
    #[pg_test(error = "samples must be finite and within +/-1e9")]
    fn batched_encoding_reports_the_first_invalid_chunk() {
        Spi::run("SELECT morlet_create_stream_collection('public','enc_bad'); SELECT morlet_start_recording('enc_bad','A',4096,'x','g','{}',512,256,'zscore','none'); SET morlet.encode_threads = 4; SELECT morlet_append_chunks_raw('enc_bad','A',0,morlet_float8_le_bytes(ARRAY[1,2,3,'NaN',5,6,7,8]::float8[]),2)").unwrap();
    }
    #[pg_test(error = "morlet.encode_threads must be an integer 1..64")]
    fn encode_threads_is_validated() {
        Spi::run("SELECT morlet_create_stream_collection('public','enc_guc'); SELECT morlet_start_recording('enc_guc','A',4096,'x','g'); SET morlet.encode_threads = 0; SELECT morlet_append_chunks_raw('enc_guc','A',0,morlet_float8_le_bytes(ARRAY[1,2,3,4]::float8[]),2)").unwrap();
    }
    /// I1: chunks are identical with and without the round trip, and the CHECK constraint still decodes every
    /// chunk written any other way: a corrupted copy of a just-encoded chunk is rejected.
    #[pg_test]
    fn verify_appends_does_not_change_chunks() {
        Spi::run(
            "CREATE TEMP TABLE ver_src AS SELECT i,CASE WHEN i<20000 THEN round(1000*sin(i/7.))::float8 ELSE sin(i/7.)::float8 END AS v FROM generate_series(0,49999) i;
             SELECT morlet_create_stream_collection('public',c) FROM unnest(ARRAY['ver_on','ver_off']) c;
             SELECT morlet_start_recording(c::regclass,'A',4096,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['ver_on','ver_off']) c;
             SET morlet.verify_appends = on;
             SELECT morlet_append_chunks_raw('ver_on','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM ver_src ORDER BY i)),4096);
             SET morlet.verify_appends = off;
             SELECT morlet_append_chunks_raw('ver_off','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM ver_src ORDER BY i)),4096);
             RESET morlet.verify_appends;",
        )
        .unwrap();
        assert!(Spi::get_one::<bool>(
            "SELECT (SELECT array_agg(encoded ORDER BY start_sample) FROM ver_on_chunks)
                  = (SELECT array_agg(encoded ORDER BY start_sample) FROM ver_off_chunks)
                AND (SELECT count(*) FROM ver_on_chunks) = 13
                AND (SELECT bool_and(morlet_decode_chunk(encoded) IS NOT NULL) FROM ver_on_chunks)"
        )
        .unwrap()
        .unwrap());
    }
    #[pg_test(error = "corrupt encoded chunk: checksum mismatch")]
    fn check_still_decodes_direct_copies_of_appended_chunks() {
        // The last batch's chunks are remembered; a corrupted copy of one is not identical and is decoded.
        Spi::run("SELECT morlet_create_stream_collection('public','ver_copy'); SELECT morlet_start_recording('ver_copy','A',4096,'x','g'); SELECT morlet_start_recording('ver_copy','B',4096,'x','g'); SELECT morlet_append_chunks_raw('ver_copy','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,999) i)),500); INSERT INTO ver_copy_chunks SELECT 'B',start_sample,sample_count,set_byte(encoded,20,get_byte(encoded,20) # 1) FROM ver_copy_chunks WHERE recording_id='A' AND start_sample=0").unwrap();
    }
    #[pg_test(error = "encoded chunk length does not match sample_count")]
    fn check_matches_remembered_chunks_by_sample_count() {
        // An identical copy under a different sample_count is not trusted and fails the full check.
        Spi::run("SELECT morlet_create_stream_collection('public','ver_count'); SELECT morlet_start_recording('ver_count','A',4096,'x','g'); SELECT morlet_start_recording('ver_count','B',4096,'x','g'); SET morlet.verify_appends = off; SELECT morlet_append_chunks_raw('ver_count','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,999) i)),500); INSERT INTO ver_count_chunks SELECT 'B',start_sample,sample_count+1,encoded FROM ver_count_chunks WHERE recording_id='A' AND start_sample=0").unwrap();
    }
    #[pg_test(error = "morlet.verify_appends must be on or off")]
    fn verify_appends_is_validated() {
        Spi::run("SELECT morlet_create_stream_collection('public','ver_guc'); SELECT morlet_start_recording('ver_guc','A',4096,'x','g'); SET morlet.verify_appends = 'maybe'; SELECT morlet_append_chunks_raw('ver_guc','A',0,morlet_float8_le_bytes(ARRAY[1,2,3,4]::float8[]),2)").unwrap();
    }
    /// Long reads decode on worker threads; the samples are identical to decoding in the backend's thread.
    #[pg_test]
    fn long_reads_do_not_depend_on_decode_threads() {
        Spi::run(
            "CREATE TEMP TABLE dec_src AS SELECT i,CASE WHEN i<60000 THEN round(1000*sin(i/7.))::float8 ELSE sin(i/7.)::float8 END AS v FROM generate_series(0,99999) i;
             SELECT morlet_create_stream_collection('public','dec_threads'); SELECT morlet_start_recording('dec_threads','A',4096,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('dec_threads','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM dec_src ORDER BY i)),2048);",
        )
        .unwrap();
        let read = |threads: &str| {
            Spi::run(&format!("SET morlet.decode_threads = {threads}")).unwrap();
            Spi::get_one::<Vec<u8>>("SELECT morlet_read_interval_raw('dec_threads','A',1234,98765)")
                .unwrap()
                .unwrap()
        };
        let one = read("1");
        assert_eq!(one, read("4"));
        assert_eq!(one, read("64"));
        let expected = Spi::get_one::<Vec<u8>>(
            "SELECT morlet_float8_le_bytes(ARRAY(SELECT v FROM dec_src WHERE i>=1234 AND i<98765 ORDER BY i))",
        )
        .unwrap()
        .unwrap();
        assert_eq!(one, expected);
    }
    #[pg_test(error = "corrupt encoded chunk: checksum mismatch")]
    fn long_reads_on_threads_still_verify_chunks() {
        Spi::run("SELECT morlet_create_stream_collection('public','dec_rot'); SELECT morlet_start_recording('dec_rot','A',4096,'x','g'); SELECT morlet_append_chunks_raw('dec_rot','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,19999) i)),1000); ALTER TABLE dec_rot_chunks DROP CONSTRAINT dec_rot_chunks_check; UPDATE dec_rot_chunks SET encoded=set_byte(encoded,20,get_byte(encoded,20) # 1) WHERE start_sample=13000; SET morlet.decode_threads = 4; SELECT morlet_read_interval_raw('dec_rot','A',0,20000)").unwrap();
    }
    #[pg_test(error = "morlet.decode_threads must be an integer 1..64")]
    fn decode_threads_is_validated() {
        Spi::run("SELECT morlet_create_stream_collection('public','dec_guc'); SELECT morlet_start_recording('dec_guc','A',4096,'x','g'); SELECT morlet_append_chunks_raw('dec_guc','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,19999) i)),1000); SET morlet.decode_threads = 0; SELECT morlet_read_interval_raw('dec_guc','A',0,20000)").unwrap();
    }
    /// Compressed transfer: the encoded read's frame decodes to exactly the raw read's bytes, for intervals inside
    /// one chunk, across chunk edges and over the whole recording; chunks of a newer version than the client
    /// decodes come as samples in a raw frame; a corrupt chunk is sent unchanged and fails in the client's decoder.
    #[pg_test]
    fn encoded_reads_decode_to_raw_reads() {
        Spi::run(
            "CREATE TEMP TABLE enc_src AS SELECT i,CASE WHEN i<60000 THEN round(1000*sin(i/7.))::float8 ELSE sin(i/7.)::float8 END AS v FROM generate_series(0,99999) i;
             SELECT morlet_create_stream_collection('public','enc_rd'); SELECT morlet_start_recording('enc_rd','A',4096,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('enc_rd','A',0,morlet_float8_le_bytes(ARRAY(SELECT v FROM enc_src ORDER BY i)),2048);",
        )
        .unwrap();
        let decode = |frame: &[u8], a: i64, b: i64| {
            let mut out = vec![0.0; (b - a) as usize];
            morlet_core::transfer::decode_frame(frame, a, b, &mut out, 4).map(|_| {
                out.iter()
                    .flat_map(|v| v.to_le_bytes())
                    .collect::<Vec<u8>>()
            })
        };
        for (a, b) in [
            (0i64, 100000i64),
            (1, 2),
            (2047, 2049),
            (1234, 98765),
            (59000, 61000),
            (99999, 100000),
        ] {
            let raw = Spi::get_one::<Vec<u8>>(&format!(
                "SELECT morlet_read_interval_raw('enc_rd','A',{a},{b})"
            ))
            .unwrap()
            .unwrap();
            for version in [3, 2, 1] {
                let frame = Spi::get_one::<Vec<u8>>(&format!(
                    "SELECT morlet_read_interval_encoded('enc_rd','A',{a},{b},{version})"
                ))
                .unwrap()
                .unwrap();
                assert_eq!(decode(&frame, a, b).unwrap(), raw, "{a}..{b} v{version}");
                // A client that cannot decode one of the chunks' format versions gets samples instead.
                let newer = Spi::get_one::<bool>(&format!("SELECT bool_or(get_byte(encoded,0)>{version}) FROM enc_rd_chunks WHERE start_sample<{b} AND start_sample+sample_count>{a}"))
                    .unwrap()
                    .unwrap();
                assert_eq!(&frame[..4] == b"MTR1", newer, "{a}..{b} v{version}");
                assert_eq!(
                    newer,
                    version < 2 || (version == 2 && frame[..4] == *b"MTR1")
                );
            }
        }
        let compressed = Spi::get_one::<i32>(
            "SELECT length(morlet_read_interval_encoded('enc_rd','A',0,60000))",
        )
        .unwrap()
        .unwrap();
        assert!(compressed < 8 * 60000 / 4, "{compressed} bytes");
        Spi::run("ALTER TABLE enc_rd_chunks DROP CONSTRAINT enc_rd_chunks_check; UPDATE enc_rd_chunks SET encoded=set_byte(encoded,20,get_byte(encoded,20) # 1) WHERE start_sample=4096").unwrap();
        let frame =
            Spi::get_one::<Vec<u8>>("SELECT morlet_read_interval_encoded('enc_rd','A',0,10000)")
                .unwrap()
                .unwrap();
        assert!(matches!(
            decode(&frame, 0, 10000),
            Err(morlet_core::transfer::FrameError::Chunk {
                start_sample: 4096,
                ..
            })
        ));
    }
    #[pg_test(error = "source interval is incomplete or not fully visible")]
    fn encoded_reads_fail_on_missing_chunks() {
        Spi::run("SELECT morlet_create_stream_collection('public','enc_gap'); SELECT morlet_start_recording('enc_gap','A',4096,'x','g'); SELECT morlet_append_chunks_raw('enc_gap','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,19999) i)),1000); DELETE FROM enc_gap_chunks WHERE start_sample=5000; SELECT morlet_read_interval_encoded('enc_gap','A',4500,6500)").unwrap();
    }
    #[pg_test(error = "source interval exceeds recording length")]
    fn encoded_reads_stop_at_the_checkpoint() {
        Spi::run("SELECT morlet_create_stream_collection('public','enc_end'); SELECT morlet_start_recording('enc_end','A',4096,'x','g'); SELECT morlet_append_chunks_raw('enc_end','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,999) i)),1000); SELECT morlet_read_interval_encoded('enc_end','A',0,1001)").unwrap();
    }
    /// SQL 0.3.5: new collections store chunk summaries; statistics are the same as decoding.
    #[pg_test]
    fn new_collections_store_chunk_summaries() {
        Spi::run("SELECT morlet_create_stream_collection('public','sum_dflt'); SELECT morlet_start_recording('sum_dflt','A',4096,'x','g'); SELECT morlet_append_chunks_raw('sum_dflt','A',0,morlet_float8_le_bytes(ARRAY(SELECT sin(i/3.)::float8 FROM generate_series(0,19999) i)),1000)").unwrap();
        let ok = Spi::get_one::<bool>("SELECT summarized FROM morlet_stream_relations('sum_dflt')")
            .unwrap()
            .unwrap();
        assert!(ok);
        let all =
            Spi::get_one::<bool>("SELECT count(*)=20 AND count(summary)=20 FROM sum_dflt_chunks")
                .unwrap()
                .unwrap();
        assert!(all);
        // Enabling them again is a no-op.
        Spi::run("SELECT morlet_enable_chunk_summaries('sum_dflt')").unwrap();
    }
    /// #193: the integrity scrub finds every injected corruption, reports nothing on sound data, never
    /// changes data, and continues in batches.
    #[pg_test]
    fn verify_reports_every_injected_corruption() {
        Spi::run(
            "SELECT morlet_create_stream_collection('public','scrub'); SELECT morlet_enable_chunk_summaries('scrub');
             SELECT morlet_start_recording('scrub',r,4096,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['A','B','C','D','E','F']) r;
             SELECT morlet_append_chunks_raw('scrub',r,0,morlet_float8_le_bytes(ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(0,9999) i)),1000)
               FROM unnest(ARRAY['A','B','C','D','E','F']) r;",
        )
        .unwrap();
        let problems = |id: &str| {
            Spi::get_one::<String>(&format!(
                "SELECT array_to_string(problems,' | ') FROM morlet_verify('scrub') WHERE recording_id='{id}'"
            ))
            .unwrap()
            .unwrap_or_default()
        };
        let clean = Spi::get_one::<bool>(
            "SELECT bool_and(cardinality(problems)=0 AND chunks=10 AND samples=10000) AND count(*)=6 FROM morlet_verify('scrub')",
        )
        .unwrap()
        .unwrap();
        assert!(clean);
        // Inject: B a flipped byte, C a deleted summary, D another chunk's summary, E a deleted chunk,
        // F a wrong sample_count (constraints dropped, as storage corruption or a manual edit would bypass them).
        Spi::run(
            "ALTER TABLE scrub_chunks DROP CONSTRAINT scrub_chunks_check;
             ALTER TABLE scrub_chunks DROP CONSTRAINT IF EXISTS scrub_chunks_sample_count_check;
             UPDATE scrub_chunks SET encoded=set_byte(encoded,20,get_byte(encoded,20) # 1) WHERE recording_id='B' AND start_sample=3000;
             UPDATE scrub_chunks SET summary=NULL WHERE recording_id='C' AND start_sample=5000;
             UPDATE scrub_chunks SET summary=(SELECT summary FROM scrub_chunks WHERE recording_id='D' AND start_sample=0) WHERE recording_id='D' AND start_sample=7000;
             DELETE FROM scrub_chunks WHERE recording_id='E' AND start_sample IN (4000,9000);
             UPDATE scrub_chunks SET sample_count=999 WHERE recording_id='F' AND start_sample=2000;",
        )
        .unwrap();
        let before = Spi::get_one::<String>("SELECT md5(string_agg(recording_id||start_sample||encoded::text||coalesce(summary::text,''),',' ORDER BY recording_id,start_sample)) FROM scrub_chunks").unwrap();
        assert_eq!(problems("A"), "");
        assert_eq!(
            problems("B"),
            "chunk at 3000: chunk does not decode: corrupt encoded chunk: checksum mismatch"
        );
        assert_eq!(problems("C"), "chunk at 5000: summary missing");
        assert_eq!(
            problems("D"),
            "chunk at 7000: summary differs from the chunk's samples"
        );
        assert_eq!(
            problems("E"),
            "gap: samples 4000..5000 have no chunk | chunks end at 9000, the checkpoint at 10000"
        );
        assert_eq!(
            problems("F"),
            "chunk at 2000: chunk holds 1000 samples, sample_count says 999 | gap: samples 2999..3000 have no chunk"
        );
        // Read-only, and resumable in batches.
        let after = Spi::get_one::<String>("SELECT md5(string_agg(recording_id||start_sample||encoded::text||coalesce(summary::text,''),',' ORDER BY recording_id,start_sample)) FROM scrub_chunks").unwrap();
        assert_eq!(before, after);
        let batched = Spi::get_one::<String>(
            "SELECT string_agg(recording_id,',' ORDER BY recording_id) FROM (SELECT * FROM morlet_verify('scrub',NULL,2) UNION ALL SELECT * FROM morlet_verify('scrub','B',2) UNION ALL SELECT * FROM morlet_verify('scrub','D',2)) v",
        )
        .unwrap()
        .unwrap();
        assert_eq!(batched, "A,B,C,D,E,F");
    }
    #[pg_test(error = "max_recordings must be 1..100000")]
    fn verify_rejects_bad_batch_size() {
        Spi::run("SELECT morlet_create_stream_collection('public','scrub_bad'); SELECT * FROM morlet_verify('scrub_bad',NULL,0)").unwrap();
    }
    /// Episodic memory (docs/memory-design.md): every level removes what it should and nothing else, reads fail
    /// closed with the level, statistics stay exact on chunk boundaries, the ledger records every move, and the
    /// scrub accepts compacted data.
    #[pg_test]
    fn memory_ladder_removes_exactly_what_each_level_says() {
        Spi::run(
            "SELECT morlet_create_stream_collection('public','mem'); SELECT morlet_enable_chunk_summaries('mem');
             SELECT morlet_start_recording('mem','A',4096,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('mem','A',s,morlet_float8_le_bytes(ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(s,s+9999) i)),1000)
               FROM generate_series(0,30000,10000) s ORDER BY s;
             SELECT morlet_enable_envelopes('mem'); SELECT morlet_build_envelopes('mem','A',512,256,100000);
             SELECT morlet_enable_memory('mem');
             CREATE TEMP TABLE ep AS SELECT e AS id, o FROM morlet_add_episodes('mem','[
               {\"recording_id\":\"A\",\"start_sample\":5000,\"end_sample\":15000},
               {\"recording_id\":\"A\",\"start_sample\":20000,\"end_sample\":30000},
               {\"recording_id\":\"A\",\"start_sample\":0,\"end_sample\":5000,\"exempt\":true},
               {\"recording_id\":\"A\",\"start_sample\":30000,\"end_sample\":35000},
               {\"recording_id\":\"A\",\"start_sample\":35000,\"end_sample\":40000},
               {\"recording_id\":\"A\",\"start_sample\":12000,\"end_sample\":14000}]') WITH ORDINALITY x(e,o);
             CREATE TEMP TABLE before AS SELECT * FROM morlet_interval_stats('mem','A',0,40000);",
        )
        .unwrap();
        let id = |o: i64| {
            Spi::get_one::<i64>(&format!("SELECT id FROM ep WHERE o={o}"))
                .unwrap()
                .unwrap()
        };
        let one = |sql: &str| Spi::get_one::<i64>(sql).unwrap().unwrap();
        let run = one("SELECT morlet_start_memory_run('mem','test','1')");
        // Level 1: envelopes entirely inside 5000..15000 go, except those overlapping episode 6 (12000..14000), which
        // is still at level 0 and stays searchable; samples stay; a rebuild follows the same rule.
        let inside = "SELECT count(*) FROM mem_envelopes WHERE start_sample>=5000 AND start_sample+window_size<=15000 AND NOT (start_sample<14000 AND start_sample+window_size>12000)";
        let kept6 = "SELECT count(*) FROM mem_envelopes WHERE start_sample>=5000 AND start_sample+window_size<=15000 AND start_sample<14000 AND start_sample+window_size>12000";
        assert!(one(inside) > 0);
        let before6 = one(kept6);
        assert!(before6 > 0);
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('mem',{run},'[{{\"episode_id\":{},\"level\":1}}]')",
            id(1)
        ))
        .unwrap();
        assert_eq!(one(inside), 0);
        assert_eq!(one(kept6), before6);
        Spi::run(
            "DELETE FROM mem_envelopes; SELECT morlet_build_envelopes('mem','A',512,256,100000)",
        )
        .unwrap();
        assert_eq!(one(inside), 0);
        assert_eq!(one(kept6), before6);
        assert_eq!(
            one("SELECT cardinality(samples) FROM morlet_read_interval('mem','A',6000,7000)"),
            1000
        );
        // Overlap protection: episode 6 (12000..14000) lies inside episode 1, which is at level 1 and keeps its
        // samples, so moving episode 6 to level 3 removes no chunk; the windows it was keeping searchable inside
        // episode 1 go with it.
        let moved = one(&format!("SELECT chunks_removed FROM morlet_set_level('mem',{run},'[{{\"episode_id\":{},\"level\":3}}]')", id(6)));
        assert_eq!(moved, 0);
        assert_eq!(one(kept6), 0);
        assert_eq!(
            one("SELECT cardinality(samples) FROM morlet_read_interval('mem','A',12000,14000)"),
            2000
        );
        // Level 3: chunks 20000..30000 go, their summaries stay; statistics over whole chunks are unchanged.
        let moved = one(&format!("SELECT chunks_removed FROM morlet_set_level('mem',{run},'[{{\"episode_id\":{},\"level\":3,\"reason\":{{\"why\":\"test\"}}}}]')", id(2)));
        assert_eq!(moved, 10);
        assert_eq!(one("SELECT count(*) FROM mem_kept_summaries"), 10);
        assert!(Spi::get_one::<bool>("SELECT (SELECT row(s.*)::text FROM morlet_interval_stats('mem','A',0,40000) s)=(SELECT row(b.*)::text FROM before b)").unwrap().unwrap());
        // Level 2: episode 4 represented by episode 5.
        Spi::run(&format!("SELECT * FROM morlet_set_level('mem',{run},'[{{\"episode_id\":{},\"level\":2,\"representative\":{},\"distance\":0.25}}]')", id(4), id(5))).unwrap();
        assert_eq!(
            one(&format!(
                "SELECT representative FROM mem_episodes WHERE episode_id={}",
                id(4)
            )),
            id(5)
        );
        assert_eq!(one("SELECT count(*) FROM mem_kept_summaries"), 15);
        // The scrub accepts compacted chunks; the ledger has one row per move with the removed chunks' md5.
        assert_eq!(
            one("SELECT count(*) FROM morlet_verify('mem') WHERE cardinality(problems)>0"),
            0
        );
        assert_eq!(one("SELECT count(*) FROM mem_ledger"), 4);
        assert_eq!(one("SELECT count(*) FROM mem_ledger WHERE chunks_removed>0 AND removed_md5 IS NOT NULL"), 2);
        // Dry run: reports, changes nothing.
        Spi::run(&format!(
            "SELECT morlet_finish_memory_run('mem',{run},'finished')"
        ))
        .unwrap();
        let dry = one("SELECT morlet_start_memory_run('mem','test','1','{}',true)");
        assert_eq!(one(&format!("SELECT summaries_removed FROM morlet_set_level('mem',{dry},'[{{\"episode_id\":{},\"level\":4}}]',true)", id(2))), 10);
        assert_eq!(one("SELECT count(*) FROM mem_kept_summaries"), 15);
        assert_eq!(one("SELECT count(*) FROM mem_ledger"), 4);
        // Level 4: the kept summaries go too; the scrub accepts the gap the ledger explains.
        Spi::run(&format!(
            "SELECT morlet_finish_memory_run('mem',{dry},'finished')"
        ))
        .unwrap();
        let run2 = one("SELECT morlet_start_memory_run('mem','test','1')");
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('mem',{run2},'[{{\"episode_id\":{},\"level\":4}}]')",
            id(2)
        ))
        .unwrap();
        assert_eq!(one("SELECT count(*) FROM mem_kept_summaries"), 5);
        assert_eq!(
            one("SELECT count(*) FROM morlet_verify('mem') WHERE cardinality(problems)>0"),
            0
        );
        assert_eq!(
            one("SELECT sum(episodes)::bigint FROM morlet_memory_status('mem')"),
            6
        );
    }
    /// morlet_coverage (#215): every sample of a recording is in exactly one state, and only stored windows count as
    /// searchable, so a search without a match can be reported with what it did not search.
    #[pg_test]
    fn coverage_reports_what_search_can_find() {
        Spi::run(
            "SELECT morlet_create_stream_collection('public','cov'); SELECT morlet_enable_chunk_summaries('cov');
             SELECT morlet_start_recording('cov','A',4096,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('cov','A',s,morlet_float8_le_bytes(ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(s,s+9999) i)),1000)
               FROM generate_series(0,30000,10000) s ORDER BY s;
             SELECT morlet_start_recording('cov','B',4096,'x','g','{}',512,256,'zscore','none');",
        )
        .unwrap();
        let states = |sql: &str| {
            Spi::get_one::<String>(&format!(
                "SELECT string_agg(recording_id||':'||start_sample||'..'||end_sample||' '||state,', ' ORDER BY recording_id,start_sample) FROM ({sql}) c"
            ))
            .unwrap()
            .unwrap_or_default()
        };
        // Without envelopes and memory: stored but not indexed. B has no samples, so no rows.
        assert_eq!(
            states("SELECT * FROM morlet_coverage('cov')"),
            "A:0..40000 not_indexed"
        );
        Spi::run(
            "SELECT morlet_enable_envelopes('cov'); SELECT morlet_build_envelopes('cov','A',512,256,100000);
             DELETE FROM cov_envelopes WHERE start_sample>=30000;
             SELECT morlet_enable_memory('cov');
             CREATE TEMP TABLE cov_ep AS SELECT e AS id, o FROM morlet_add_episodes('cov','[
               {\"recording_id\":\"A\",\"start_sample\":5000,\"end_sample\":15000},
               {\"recording_id\":\"A\",\"start_sample\":20000,\"end_sample\":25000},
               {\"recording_id\":\"A\",\"start_sample\":12000,\"end_sample\":13000}]') WITH ORDINALITY x(e,o);",
        )
        .unwrap();
        let one = |sql: &str| Spi::get_one::<i64>(sql).unwrap().unwrap();
        let run = one("SELECT morlet_start_memory_run('cov','test','1')");
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('cov',{run},'[{{\"episode_id\":{},\"level\":1}},{{\"episode_id\":{},\"level\":3}}]')",
            one("SELECT id FROM cov_ep WHERE o=1"),
            one("SELECT id FROM cov_ep WHERE o=2")
        ))
        .unwrap();
        // A chunk that was never stored (deleted here) is missing, not compacted.
        Spi::run("DELETE FROM cov_chunks WHERE recording_id='A' AND start_sample=38000").unwrap();
        // Level 1 removes the windows inside 5000..15000 except those overlapping the level-0 episode 12000..13000;
        // level 3 removes the chunks 20000..25000 and their inner windows; windows end at 30464.
        assert_eq!(
            states("SELECT * FROM morlet_coverage('cov')"),
            "A:0..5376 searchable, A:5376..11520 not_searchable, A:11520..13312 searchable, \
             A:13312..14592 not_searchable, A:14592..20480 searchable, A:20480..24576 summarized, \
             A:24576..30464 searchable, A:30464..38000 not_indexed, A:38000..39000 missing, A:39000..40000 not_indexed"
        );
        assert_eq!(
            one("SELECT windows FROM morlet_coverage('cov','{A}',512) WHERE start_sample=0"),
            one("SELECT count(*) FROM cov_envelopes WHERE start_sample+window_size<=5376")
        );
        assert_eq!(
            one("SELECT count(*) FROM morlet_coverage('cov') WHERE (state='searchable')<>(windows IS NOT NULL)"),
            0
        );
        // No windows of another size: nothing is searchable with that window.
        assert_eq!(
            one("SELECT count(*) FROM morlet_coverage('cov',NULL,1024) WHERE state='searchable'"),
            0
        );
        assert_eq!(
            states("SELECT * FROM morlet_coverage('cov','{A}',1024) WHERE start_sample<15000"),
            "A:0..5000 not_indexed, A:5000..12000 not_searchable, A:12000..13000 not_indexed, A:13000..15000 not_searchable"
        );
        // Level 4: the kept summaries go too.
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('cov',{run},'[{{\"episode_id\":{},\"level\":4}}]')",
            one("SELECT id FROM cov_ep WHERE o=2")
        ))
        .unwrap();
        assert_eq!(
            states(
                "SELECT * FROM morlet_coverage('cov') WHERE start_sample BETWEEN 14592 AND 24576"
            ),
            "A:14592..20480 searchable, A:20480..24576 gone, A:24576..30464 searchable"
        );
    }
    /// morlet_latest (#206): the newest samples and statistics of every recording equal what morlet_read_interval
    /// and morlet_interval_stats return at the recording's end; unreadable newest samples are a note, not an error.
    #[pg_test]
    fn latest_matches_reads_at_each_recordings_end() {
        Spi::run(
            "SELECT morlet_create_stream_collection('public','lat'); SELECT morlet_enable_chunk_summaries('lat');
             SELECT morlet_start_recording('lat','A',4096,'x','g','{\"site\":\"s1\",\"start\":\"2026-01-01T00:00:00Z\"}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('lat','A',0,morlet_float8_le_bytes(ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(0,9999) i)),1000);
             SELECT morlet_start_recording('lat','B',250,'x','g','{\"site\":\"s2\"}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('lat','B',0,morlet_float8_le_bytes(ARRAY(SELECT (i%17)::float8 FROM generate_series(0,2999) i)),1000);
             SELECT morlet_start_recording('lat','C',250,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_start_recording('lat','D',250,'x','g','{}',512,256,'zscore','none');
             SELECT morlet_append_chunks_raw('lat','D',0,morlet_float8_le_bytes(ARRAY(SELECT (i%5)::float8 FROM generate_series(0,3999) i)),1000);",
        )
        .unwrap();
        let yes = |sql: &str| Spi::get_one::<bool>(sql).unwrap().unwrap();
        let text = |sql: &str| Spi::get_one::<String>(sql).unwrap().unwrap_or_default();
        // Last n samples equal the read at the end; statistics equal morlet_interval_stats; C has no samples, no row.
        assert_eq!(
            text("SELECT string_agg(recording_id||':'||start_sample||'..'||end_sample,',' ORDER BY recording_id) FROM morlet_latest('lat',5)"),
            "A:9995..10000,B:2995..3000,D:3995..4000"
        );
        assert!(yes("SELECT l.samples=(SELECT samples FROM morlet_read_interval('lat','A',9995,10000)) FROM morlet_latest('lat',5) l WHERE recording_id='A'"));
        assert!(yes("SELECT row(l.sample_count,l.min,l.max,l.mean,l.rms)::text=(SELECT row(s.*)::text FROM morlet_interval_stats('lat','A',9995,10000) s) FROM morlet_latest('lat',5) l WHERE recording_id='A'"));
        // More than the recording holds: all of it.
        assert!(yes("SELECT start_sample=0 AND cardinality(samples)=3000 AND samples=(SELECT samples FROM morlet_read_interval('lat','B',0,3000)) FROM morlet_latest('lat',32768,'{\"site\":\"s2\"}')"));
        // n = 0: statistics of the newest chunk, no samples.
        assert!(yes("SELECT l.start_sample=9000 AND l.samples IS NULL AND row(l.sample_count,l.min,l.max,l.mean,l.rms)::text=(SELECT row(s.*)::text FROM morlet_interval_stats('lat','A',9000,10000) s) FROM morlet_latest('lat',0) l WHERE recording_id='A'"));
        // End time from the start time; NULL without one.
        assert!(yes("SELECT end_time='2026-01-01T00:00:00Z'::timestamptz+interval '2.441406 s' FROM morlet_latest('lat',1) WHERE recording_id='A'"));
        assert!(yes(
            "SELECT end_time IS NULL FROM morlet_latest('lat',1) WHERE recording_id='B'"
        ));
        // Newest samples compacted to level 3: a note, NULL values, other recordings unaffected.
        Spi::run("SELECT morlet_enable_memory('lat'); SELECT morlet_add_episodes('lat','[{\"recording_id\":\"D\",\"start_sample\":2000,\"end_sample\":4000}]')").unwrap();
        let run = Spi::get_one::<i64>("SELECT morlet_start_memory_run('lat','test','1')")
            .unwrap()
            .unwrap();
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('lat',{run},'[{{\"episode_id\":1,\"level\":3}}]')"
        ))
        .unwrap();
        assert!(yes("SELECT samples IS NULL AND sample_count IS NULL AND note LIKE '%compacted to level 3%' FROM morlet_latest('lat',5) WHERE recording_id='D'"));
        assert!(yes("SELECT samples IS NULL AND note LIKE 'the newest samples are not stored%' FROM morlet_latest('lat',0) WHERE recording_id='D'"));
        assert!(yes("SELECT count(*)=2 FROM morlet_latest('lat',5) WHERE note IS NULL AND samples IS NOT NULL"));
    }
    #[pg_test(error = "3 recordings match, more than max_recordings (2)")]
    fn latest_refuses_more_recordings_than_its_budget() {
        Spi::run(
            "SELECT morlet_create_stream_collection('public','lat_b');
             SELECT morlet_start_recording('lat_b',r,250,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['A','B','C']) r;
             SELECT morlet_append_chunks_raw('lat_b',r,0,morlet_float8_le_bytes(ARRAY[1,2,3]::float8[]),1000) FROM unnest(ARRAY['A','B','C']) r;
             SELECT * FROM morlet_latest('lat_b',1,'{}',2)",
        )
        .unwrap();
    }
    /// A window that spans two adjacent episodes leaves search only when both are out of search.
    #[pg_test]
    fn memory_windows_spanning_adjacent_episodes() {
        Spi::run("SELECT morlet_create_stream_collection('public','mem_span'); SELECT morlet_start_recording('mem_span','A',4096,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunks_raw('mem_span','A',0,morlet_float8_le_bytes(ARRAY(SELECT round(100*sin(i/9.))::float8 FROM generate_series(0,3999) i)),1000);
          SELECT morlet_enable_envelopes('mem_span'); SELECT morlet_build_envelopes('mem_span','A',512,256,1000);
          SELECT morlet_enable_memory('mem_span');
          SELECT count(*) FROM morlet_add_episodes('mem_span','[{\"recording_id\":\"A\",\"start_sample\":0,\"end_sample\":1000},{\"recording_id\":\"A\",\"start_sample\":1000,\"end_sample\":2000}]')").unwrap();
        let spanning = "SELECT count(*) FROM mem_span_envelopes WHERE start_sample<1000 AND start_sample+window_size>1000";
        let one = |sql: &str| Spi::get_one::<i64>(sql).unwrap().unwrap();
        let before = one(spanning);
        assert!(before > 0);
        let run = one("SELECT morlet_start_memory_run('mem_span','test','1')");
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('mem_span',{run},'[{{\"episode_id\":1,\"level\":1}}]')"
        ))
        .unwrap();
        assert_eq!(one(spanning), before);
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('mem_span',{run},'[{{\"episode_id\":2,\"level\":1}}]')"
        ))
        .unwrap();
        assert_eq!(one(spanning), 0);
        Spi::run("DELETE FROM mem_span_envelopes; SELECT morlet_build_envelopes('mem_span','A',512,256,1000)").unwrap();
        assert_eq!(one(spanning), 0);
        assert!(one("SELECT count(*) FROM mem_span_envelopes WHERE start_sample>=2000") > 0);
    }
    #[pg_test(
        error = "samples of recording A in 0..2000 were compacted to level 3 (episode 1, ledger 1)"
    )]
    fn memory_reads_of_compacted_samples_name_the_level() {
        Spi::run("SELECT morlet_create_stream_collection('public','mem_read'); SELECT morlet_start_recording('mem_read','A',4096,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunks_raw('mem_read','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,3999) i)),1000);
          SELECT morlet_enable_memory('mem_read'); SELECT morlet_add_episodes('mem_read','[{\"recording_id\":\"A\",\"start_sample\":0,\"end_sample\":2000}]');
          SELECT * FROM morlet_set_level('mem_read',morlet_start_memory_run('mem_read','test','1'),'[{\"episode_id\":1,\"level\":3}]');
          SELECT * FROM morlet_read_interval('mem_read','A',500,1500)").unwrap();
    }
    #[pg_test(
        error = "the statistics interval cuts the compacted chunk at sample 1000 of recording A"
    )]
    fn memory_statistics_refuse_cut_compacted_chunks() {
        Spi::run("SELECT morlet_create_stream_collection('public','mem_cut'); SELECT morlet_start_recording('mem_cut','A',4096,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunks_raw('mem_cut','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,3999) i)),1000);
          SELECT morlet_enable_memory('mem_cut'); SELECT morlet_add_episodes('mem_cut','[{\"recording_id\":\"A\",\"start_sample\":1000,\"end_sample\":2000}]');
          SELECT * FROM morlet_set_level('mem_cut',morlet_start_memory_run('mem_cut','test','1'),'[{\"episode_id\":1,\"level\":3}]');
          SELECT * FROM morlet_interval_stats('mem_cut','A',1500,3000)").unwrap();
    }
    #[pg_test(error = "episode 1 is at level 3; levels only move down")]
    fn memory_levels_only_move_down() {
        Spi::run("SELECT morlet_create_stream_collection('public','mem_up'); SELECT morlet_start_recording('mem_up','A',4096,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunks_raw('mem_up','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,3999) i)),1000);
          SELECT morlet_enable_memory('mem_up'); SELECT morlet_add_episodes('mem_up','[{\"recording_id\":\"A\",\"start_sample\":0,\"end_sample\":2000}]');
          SELECT * FROM morlet_set_level('mem_up',morlet_start_memory_run('mem_up','test','1'),'[{\"episode_id\":1,\"level\":3},{\"episode_id\":1,\"level\":2,\"representative\":1}]')").unwrap();
    }
    #[pg_test(error = "episode 1 is exempt (event library) and stays at level 0 or 1")]
    fn memory_exempt_episodes_stay_searchable_or_full() {
        Spi::run("SELECT morlet_create_stream_collection('public','mem_ex'); SELECT morlet_start_recording('mem_ex','A',4096,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunks_raw('mem_ex','A',0,morlet_float8_le_bytes(ARRAY(SELECT i::float8 FROM generate_series(0,3999) i)),1000);
          SELECT morlet_enable_memory('mem_ex'); SELECT morlet_add_episodes('mem_ex','[{\"recording_id\":\"A\",\"start_sample\":0,\"end_sample\":2000,\"exempt\":true}]');
          SELECT * FROM morlet_set_level('mem_ex',morlet_start_memory_run('mem_ex','test','1'),'[{\"episode_id\":1,\"level\":3}]')").unwrap();
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn replay_compares_signed_zero_bits() {
        // Array equality treats -0 and +0 as equal; a replay must match bit for bit.
        Spi::run("SELECT morlet_create_stream_collection('public','zero_replay'); SELECT morlet_start_recording('zero_replay','A',4096,'x','g'); SELECT morlet_append_chunk('zero_replay','A',0,ARRAY[-0.0::float8,1]); SELECT morlet_append_chunk('zero_replay','A',0,ARRAY[0.0::float8,1])").unwrap();
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn raw_replay_compares_signed_zero_bits() {
        Spi::run("SELECT morlet_create_stream_collection('public','zero_raw'); SELECT morlet_start_recording('zero_raw','A',4096,'x','g'); SELECT morlet_append_chunk_raw('zero_raw','A',0,morlet_float8_le_bytes(ARRAY[0.0::float8,1])); SELECT morlet_append_chunk_raw('zero_raw','A',0,morlet_float8_le_bytes(ARRAY[-0.0::float8,1]))").unwrap();
    }
    #[pg_test]
    fn identical_signed_zero_replay_is_acknowledged() {
        Spi::run("SELECT morlet_create_stream_collection('public','zero_same'); SELECT morlet_start_recording('zero_same','A',4096,'x','g'); SELECT morlet_append_chunk('zero_same','A',0,ARRAY[-0.0::float8,1])").unwrap();
        assert_eq!(
            Spi::get_one::<i64>(
                "SELECT morlet_append_chunk('zero_same','A',0,ARRAY[-0.0::float8,1])"
            )
            .unwrap(),
            Some(2)
        );
        assert_eq!(Spi::get_one::<String>("SELECT encode(float8send(samples[1]),'hex') FROM morlet_read_interval('zero_same','A',0,1)").unwrap().unwrap(), "8000000000000000");
    }
    #[pg_test(
        error = "envelopes of window 4096 for recording A were built with stride 2048, not 512"
    )]
    fn envelope_stride_change_is_an_error() {
        Spi::run("SELECT morlet_create_stream_collection('public','stride_env'); SELECT morlet_enable_envelopes('stride_env'); SELECT morlet_start_recording('stride_env','A',4096,'x','g'); SELECT morlet_append_chunk('stride_env','A',0,ARRAY(SELECT sin(i/7.0) FROM generate_series(0,8191) i))").unwrap();
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_envelopes('stride_env','A',4096,2048)")
                .unwrap(),
            Some(3)
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_envelopes('stride_env','A',4096,2048)")
                .unwrap(),
            Some(0)
        );
        Spi::run("SELECT morlet_build_envelopes('stride_env','A',4096,512)").unwrap();
    }
    #[pg_test]
    fn exact_search_falls_back_when_screening_cannot_prepare_the_query() {
        // Window 32768 at lag fraction 0.5 is too large to prepare for screening; exact search then scores
        // every eligible window directly, with the same results as morlet.exact_screen = off.
        Spi::run("SELECT morlet_create_stream_collection('public','big_win'); SELECT morlet_enable_envelopes('big_win'); SELECT morlet_start_recording('big_win',r,1000,'x','g') FROM unnest(ARRAY['A','B']) r; SELECT morlet_append_chunk('big_win',r,k*16384,ARRAY(SELECT sin(i/(5.0+o))*(1+(i%97)/97.0) FROM generate_series(k*16384,k*16384+16383) i)) FROM (VALUES ('A',0),('B',2)) v(r,o), generate_series(0,5) k ORDER BY r,k; SELECT morlet_build_envelopes('big_win',r,32768,16384) FROM unnest(ARRAY['A','B']) r").unwrap();
        assert_eq!(
            Spi::get_one::<bool>("SELECT morlet_envelope_screen_supported(3277,1638)").unwrap(),
            Some(false)
        );
        assert_eq!(
            Spi::get_one::<bool>("SELECT morlet_envelope_screen_supported(410,30)").unwrap(),
            Some(true)
        );
        let q = "SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelopes('big_win',ARRAY(SELECT sin(i/6.0)*(1+(i%89)/89.0) FROM generate_series(0,32767) i),1000,'x','g','{}',5,0.5)";
        let screened = Spi::get_one::<String>(q).unwrap().unwrap();
        Spi::run("SET LOCAL morlet.exact_screen = off").unwrap();
        assert_eq!(Spi::get_one::<String>(q).unwrap().unwrap(), screened);
        assert!(screened.split(',').count() >= 2);
    }
    /// Every statistic and overview, as exact bits, for a fixed list of intervals and bucket counts.
    fn rollup_probe(coll: &str, rid: &str) -> String {
        let q = format!("WITH iv(a,b,k) AS (VALUES (0,(SELECT (checkpoint->>'end')::bigint FROM {coll} WHERE recording_id='{rid}'),1),
            (12345,35000000,1),(1048576,2097152,1),(1048575,2097153,1),(31,30000,1),(7,33554439,1),
            (0,35000000,7),(5000,34000000,100),(0,33554432,32),(2097152,3145728,3))
          SELECT string_agg(format('%s..%s/%s:%s',a,b,k,(SELECT string_agg(encode(float8send(v),'hex'),',')
            FROM unnest(public.morlet_interval_buckets('{coll}','{rid}',a,b,k,'overview')) v)),';' ORDER BY a,b,k) FROM iv");
        Spi::get_one::<String>(&q).unwrap().unwrap()
    }

    #[pg_test]
    fn rollups_give_bitwise_the_same_statistics() {
        // 34 appended chunks (some 30,001 samples, so blocks do not follow chunk edges), repeated 33 times by copying
        // their stored rows forward (encoded chunks do not depend on their position): 36.1 million samples, 34
        // complete level-1 blocks and one level-2 block, without encoding them all in a debug build.
        Spi::run("SELECT morlet_create_stream_collection('public','ru'); SELECT morlet_start_recording('ru','A',1000,'x','g','{}',512,256,'zscore','none');
          DO $$ DECLARE k integer; s bigint:=0; n integer; BEGIN
            FOR k IN 0..33 LOOP
              n:=CASE WHEN k%5=0 THEN 30001 ELSE 32768 END;
              IF k%2=0 THEN
                PERFORM morlet_append_chunk('ru','A',s,ARRAY(SELECT sin(i/(3.0+k))*(1e3+k)+1e-3*(i%17) FROM generate_series(1,n) i));
              ELSE
                PERFORM morlet_append_chunk('ru','A',s,array_fill(((k%17)*0.37-3)*1e5,ARRAY[n]));
              END IF;
              s:=s+n;
            END LOOP;
            INSERT INTO ru_chunks SELECT c.recording_id,c.start_sample+r*s,c.sample_count,c.encoded,c.summary
              FROM ru_chunks c, generate_series(1,32) r;
            UPDATE ru SET checkpoint=checkpoint||jsonb_build_object('end',33*s,'next_start',33*s,'buffer_start',33*s);
          END $$").unwrap();
        let before = rollup_probe("ru", "A");
        Spi::run("SELECT morlet_enable_rollups('ru')").unwrap();
        assert_eq!(
            rollup_probe("ru", "A"),
            before,
            "an empty roll-up table changes nothing"
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_rollups('ru','A',10)").unwrap(),
            Some(10)
        );
        assert_eq!(rollup_probe("ru", "A"), before, "level 1 in part");
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_rollups('ru','A')").unwrap(),
            Some(25)
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT morlet_build_rollups('ru','A')").unwrap(),
            Some(0)
        );
        assert_eq!(
            Spi::get_one::<String>("SELECT string_agg(l,',') FROM (SELECT level||':'||count(*) l FROM ru_rollups GROUP BY level ORDER BY level) x").unwrap().unwrap(),
            "1:34,2:1"
        );
        assert_eq!(rollup_probe("ru", "A"), before, "levels 1 and 2");
        // The roll-ups are what is read: with the chunks under the level-2 block deleted, its statistics still
        // come out, bit for bit. (Only for this test: chunks are never deleted that way.)
        let block = "SELECT string_agg(encode(float8send(v),'hex'),',') FROM ru_rollups r,
            unnest(morlet_interval_buckets('ru','A',r.start_sample,r.end_sample,1,'statistics')) v WHERE r.level=2";
        let whole = Spi::get_one::<String>(block).unwrap().unwrap();
        Spi::run("DELETE FROM ru_chunks c USING ru_rollups r WHERE r.level=2 AND c.start_sample>=r.start_sample
            AND c.start_sample+c.sample_count<=r.end_sample").unwrap();
        assert_eq!(Spi::get_one::<String>(block).unwrap().unwrap(), whole);
    }

    #[pg_test]
    fn toast_compression_is_pinned_to_pglz() {
        // PostgreSQL 19 defaults to lz4 (#280); every bulk column must name pglz whatever the default is.
        // Builds without lz4 cannot set the default to lz4; the pins are checked all the same.
        let lz4 = Spi::get_one::<bool>(
            "SELECT 'lz4' = ANY(enumvals) FROM pg_settings WHERE name='default_toast_compression'",
        )
        .unwrap()
        .unwrap();
        if lz4 {
            Spi::run("SET LOCAL default_toast_compression = lz4").unwrap();
        }
        Spi::run("SELECT morlet_create_stream_collection('public','tc'); SELECT morlet_enable_envelopes('tc');
          SELECT morlet_enable_chunk_summaries('tc'); SELECT morlet_enable_rollups('tc'); SELECT morlet_enable_context('tc');
          SELECT morlet_enable_memory('tc'); SELECT morlet_create_collection('public','tcl')").unwrap();
        let bulk = "SELECT string_agg(a.attrelid::regclass||'.'||a.attname||'='||coalesce(nullif(a.attcompression::text,''),'default'),
            ' ' ORDER BY a.attrelid::regclass::text, a.attname) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid
            WHERE (c.relname LIKE 'tc\\_%' OR c.relname='tcl') AND a.attnum>0 AND NOT a.attisdropped
              AND a.atttypid IN ('bytea'::regtype,'float8[]'::regtype)";
        let pins = Spi::get_one::<String>(bulk).unwrap().unwrap();
        assert_eq!(
            pins,
            "tc_chunks.encoded=p tc_chunks.summary=p tc_ctx_values.vals=p tc_envelopes.envelope=p tc_kept_summaries.summary=p \
             tc_rollups.summary=p tc_segments.features=p tcl.samples=p"
        );
        // A collection from before the pin (the update pins existing ones with the same function).
        Spi::run(
            "ALTER TABLE tc_chunks ALTER COLUMN encoded SET COMPRESSION default;
          ALTER TABLE tc_envelopes ALTER COLUMN envelope SET COMPRESSION default;
          ALTER TABLE tcl ALTER COLUMN samples SET COMPRESSION default",
        )
        .unwrap();
        let one = |sql: &str| Spi::get_one::<i32>(sql).unwrap().unwrap();
        assert_eq!(one("SELECT morlet_pin_toast_compression('tc')"), 2);
        assert_eq!(one("SELECT morlet_pin_toast_compression('tcl')"), 1);
        assert_eq!(one("SELECT morlet_pin_toast_compression('tc')"), 0);
        assert_eq!(Spi::get_one::<String>(bulk).unwrap().unwrap(), pins);
        // Values written under an lz4 default are stored with pglz, and read back bitwise.
        Spi::run("SELECT morlet_start_recording('tc','r',1000,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunk('tc','r',0,ARRAY(SELECT (i%17)::float8 FROM generate_series(0,32767) i))").unwrap();
        let method = Spi::get_one::<String>(
            "SELECT coalesce(pg_column_compression(encoded),'none') FROM tc_chunks",
        )
        .unwrap()
        .unwrap();
        assert!(method == "pglz" || method == "none", "{method}");
        assert!(Spi::get_one::<bool>(
            "SELECT samples = ARRAY(SELECT (i%17)::float8 FROM generate_series(0,32767) i)
             FROM morlet_read_interval('tc','r',0,32768)"
        )
        .unwrap()
        .unwrap());
    }

    #[pg_test]
    fn commit_notifications_collect_ranges_once_per_transaction() {
        // #205. Delivery needs a real commit and a listener (tests/notifications.sh); here: the switches, the refusals
        // and the per-transaction ranges, with the deferred commit trigger fired early by SET CONSTRAINTS.
        let text = |sql: &str| Spi::get_one::<String>(sql).unwrap().unwrap_or_default();
        Spi::run("SELECT morlet_create_stream_collection('public','nt');
          SELECT morlet_start_recording('nt',r,1000,'x','g','{}',512,256,'zscore','none') FROM unnest(ARRAY['a','b']) r").unwrap();
        assert_eq!(
            text("SELECT morlet_enable_notifications('nt')"),
            "morlet_nt"
        );
        assert_eq!(
            text("SELECT channel||' '||detail FROM morlet_notification_channel('nt')"),
            "morlet_nt true"
        );
        assert_eq!(
            text("SELECT morlet_enable_notifications('nt','plant 7 é')"),
            "plant 7 é"
        );
        assert_eq!(
            text("SELECT channel||' '||detail FROM morlet_notification_channel('nt')"),
            "plant 7 é true"
        );
        let pending = "SELECT coalesce(current_setting('morlet.notify_pending',true),'')";
        Spi::run(
            "SELECT morlet_append_chunk('nt','a',0,array_fill(1.5,ARRAY[4096]));
          SELECT morlet_append_chunk('nt','a',4096,array_fill(2.5,ARRAY[100]));
          SELECT morlet_append_chunk('nt','b',0,array_fill(0.5,ARRAY[300]));
          SELECT morlet_append_chunk('nt','a',0,array_fill(1.5,ARRAY[4096]))",
        )
        .unwrap(); // the last is a replay
        assert_eq!(
            text(&format!(
                "SELECT (({pending})::jsonb->('nt_chunks'::regclass::oid::text))::text"
            )),
            r#"{"a": [0, 4196], "b": [0, 300]}"#
        );
        Spi::run("SET CONSTRAINTS ALL IMMEDIATE").unwrap(); // sends the notification and clears the collection's entry
        assert_eq!(text(&format!("SELECT ({pending})::jsonb::text")), "{}");
        assert!(Spi::run("SELECT morlet_disable_notifications('nt')").is_ok());
        assert_eq!(
            Spi::get_one::<bool>("SELECT morlet_disable_notifications('nt')").unwrap(),
            Some(false)
        );
        assert_eq!(
            Spi::get_one::<i64>("SELECT count(*) FROM pg_trigger WHERE tgrelid='nt_chunks'::regclass AND NOT tgisinternal").unwrap(),
            Some(0)
        );
        // Refusals: an unencoded (float8[] chunk) collection, a bad channel, detailed payloads under row security.
        Spi::run(
            "CREATE FUNCTION pg_temp.nt_err(q text) RETURNS text LANGUAGE plpgsql AS $$
          BEGIN EXECUTE q; RETURN ''; EXCEPTION WHEN OTHERS THEN RETURN SQLERRM; END $$",
        )
        .unwrap();
        let err = |sql: &str| {
            text(&format!(
                "SELECT pg_temp.nt_err({})",
                pgrx::spi::quote_literal(sql)
            ))
        };
        assert_eq!(
            err("SELECT morlet_enable_notifications('nt','')"),
            "channel must be 1..63 bytes"
        );
        Spi::run("SELECT morlet_enable_row_security('nt')").unwrap();
        assert_eq!(
            err("SELECT morlet_enable_notifications('nt')"),
            "row security is enabled on this collection: use detail => false"
        );
        assert_eq!(
            text("SELECT morlet_enable_notifications('nt', detail => false)"),
            "morlet_nt"
        );
        Spi::run(
            "CREATE TABLE nt_old(recording_id text PRIMARY KEY, checkpoint jsonb);
          CREATE TABLE nt_old_chunks(recording_id text, start_sample bigint, samples float8[]);
          CREATE TABLE nt_old_segments(recording_id text, start_sample bigint, features float8[])",
        )
        .unwrap();
        assert_eq!(
            err("SELECT morlet_enable_notifications('nt_old')"),
            "notifications need a collection with encoded chunks"
        );
    }

    #[pg_test]
    fn rollups_are_read_through_the_index() {
        // A year of chunks took 18 s with roll-ups when the planner scanned every chunk and roll-up for the gaps
        // (#219). Here: 6,800 chunks (34 appended, copied forward 199 times) and their level-1 and level-2 roll-ups.
        // A long interval must read no table sequentially and only the few chunks at its edges.
        Spi::run("SELECT morlet_create_stream_collection('public','ri'); SELECT morlet_start_recording('ri','A',1000,'x','g','{}',512,256,'zscore','none');
          DO $$ DECLARE k integer; s bigint:=0; BEGIN
            FOR k IN 0..33 LOOP
              PERFORM morlet_append_chunk('ri','A',s,array_fill((k%7)*1.5,ARRAY[CASE WHEN k%5=0 THEN 30001 ELSE 32768 END]));
              s:=s+CASE WHEN k%5=0 THEN 30001 ELSE 32768 END;
            END LOOP;
            INSERT INTO ri_chunks SELECT c.recording_id,c.start_sample+r*s,c.sample_count,c.encoded,c.summary
              FROM ri_chunks c, generate_series(1,199) r;
            UPDATE ri SET checkpoint=checkpoint||jsonb_build_object('end',200*s,'next_start',200*s,'buffer_start',200*s);
          END $$;
          SELECT morlet_enable_rollups('ri'); SELECT morlet_build_rollups('ri','A'); ANALYZE ri_chunks; ANALYZE ri_rollups;").unwrap();
        let one = |sql: &str| Spi::get_one::<i64>(sql).unwrap().unwrap();
        assert_eq!(one("SELECT count(*) FROM ri_chunks"), 6800);
        let read = || {
            one("SELECT sum(pg_stat_get_xact_tuples_returned(t))::bigint
                FROM unnest(ARRAY['ri_chunks'::regclass,'ri_rollups'::regclass]) t")
        };
        for (a, b, k) in [
            (12345, 218000000, 1),
            (0, 218000000, 37),
            (777, 150000000, 5),
        ] {
            let (seq0, fetched0) = (
                read(),
                one("SELECT pg_stat_get_xact_tuples_fetched('ri_chunks'::regclass)"),
            );
            Spi::run(&format!(
                "SELECT morlet_interval_buckets('ri','A',{a},{b},{k},'overview')"
            ))
            .unwrap();
            assert_eq!(
                read() - seq0,
                0,
                "{a}..{b}/{k}: no table is read sequentially"
            );
            let chunks =
                one("SELECT pg_stat_get_xact_tuples_fetched('ri_chunks'::regclass)") - fetched0;
            assert!(
                chunks < 100 * k,
                "{a}..{b}/{k}: {chunks} chunks read, only the edges are needed"
            );
        }
    }

    #[pg_test]
    fn rollups_survive_compaction_and_go_with_forgotten_samples() {
        Spi::run("SELECT morlet_create_stream_collection('public','rf'); SELECT morlet_start_recording('rf','A',1000,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunk('rf','A',k*32768,CASE WHEN k BETWEEN 30 AND 40
              THEN ARRAY(SELECT sin(i/7.0)*(1+k%5) FROM generate_series(1,32768) i) ELSE array_fill(k*0.5,ARRAY[32768]) END)
            FROM generate_series(0,127) k ORDER BY k;
          SELECT morlet_enable_memory('rf'); SELECT morlet_enable_rollups('rf');
          CREATE TEMP TABLE ep AS SELECT e AS id FROM morlet_add_episodes('rf','[{\"recording_id\":\"A\",\"start_sample\":1100000,\"end_sample\":1200000}]') e;").unwrap();
        let one = |sql: &str| Spi::get_one::<i64>(sql).unwrap().unwrap();
        assert_eq!(one("SELECT morlet_build_rollups('rf','A')"), 4);
        let stats = |a: i64, b: i64| {
            Spi::get_one::<String>(&format!("SELECT string_agg(encode(float8send(v),'hex'),',') FROM morlet_interval_buckets('rf','A',{a},{b},1,'statistics') s, unnest(s) v"))
                .unwrap()
                .unwrap()
        };
        let (all, tail) = (stats(0, 4194304), stats(2097152, 4194304));
        let id = one("SELECT id FROM ep");
        let run = one("SELECT morlet_start_memory_run('rf','test','1')");
        Spi::run(&format!(
            "SELECT * FROM morlet_set_level('rf',{run},'[{{\"episode_id\":{id},\"level\":3}}]')"
        ))
        .unwrap();
        assert_eq!(one("SELECT count(*) FROM rf_kept_summaries"), 2);
        assert_eq!(
            one("SELECT count(*) FROM rf_rollups"),
            4,
            "level 3 keeps the summaries, so the roll-ups stay"
        );
        assert_eq!(stats(0, 4194304), all);
        let derived = one(&format!("SELECT derived_removed FROM morlet_set_level('rf',{run},'[{{\"episode_id\":{id},\"level\":4}}]')"));
        assert_eq!(
            derived, 1,
            "the level-1 roll-up holding the forgotten chunks"
        );
        assert_eq!(one("SELECT count(*) FROM rf_rollups"), 3);
        assert_eq!(stats(2097152, 4194304), tail);
        assert_eq!(one("SELECT morlet_build_rollups('rf','A')"), 0);
    }

    #[pg_test]
    fn rollups_are_skipped_for_readers_who_may_not_read_them() {
        Spi::run("SELECT morlet_create_stream_collection('public','rp'); SELECT morlet_start_recording('rp','A',1000,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunk('rp','A',k*32768,array_fill((k%7)::float8,ARRAY[32768])) FROM generate_series(0,95) k ORDER BY k;
          SELECT morlet_enable_rollups('rp'); SELECT morlet_build_rollups('rp','A');
          CREATE ROLE rp_reader; GRANT SELECT ON rp, rp_chunks TO rp_reader;").unwrap();
        let q = "SELECT string_agg(encode(float8send(v),'hex'),',') FROM morlet_interval_buckets('rp','A',100,3000000,3,'overview') s, unnest(s) v";
        let all = Spi::get_one::<String>(q).unwrap().unwrap();
        Spi::run("SET LOCAL ROLE rp_reader").unwrap();
        assert_eq!(Spi::get_one::<String>(q).unwrap().unwrap(), all);
    }

    #[pg_test(
        error = "samples of recording A in 65536..131072 were compacted to level 4 (episode 1, ledger 1)"
    )]
    fn rollups_do_not_hide_forgotten_samples() {
        Spi::run("SELECT morlet_create_stream_collection('public','rg'); SELECT morlet_start_recording('rg','A',1000,'x','g','{}',512,256,'zscore','none');
          SELECT morlet_append_chunk('rg','A',k*32768,array_fill(k::float8,ARRAY[32768])) FROM generate_series(0,63) k ORDER BY k;
          SELECT morlet_enable_memory('rg'); SELECT morlet_enable_rollups('rg'); SELECT morlet_build_rollups('rg','A');
          SELECT morlet_set_level('rg',morlet_start_memory_run('rg','test','1'),
            jsonb_build_array(jsonb_build_object('episode_id',e,'level',4)))
          FROM morlet_add_episodes('rg','[{\"recording_id\":\"A\",\"start_sample\":65536,\"end_sample\":131072}]') e;
          SELECT * FROM morlet_interval_stats('rg','A',0,2097152);").unwrap();
    }

    #[pg_test]
    fn screened_search_with_idle_stretches_matches_the_full_path() {
        // Constant windows have distance 1 to every query; the screen now knows that instead of re-scoring
        // them (#284). A: a signal then a long idle stretch; B: idle throughout (the all-zero case of #279).
        Spi::run("SELECT morlet_create_stream_collection('public','idle'); SELECT morlet_enable_envelopes('idle'); SELECT morlet_start_recording('idle',r,1000,'x','g') FROM unnest(ARRAY['A','B']) r; SELECT morlet_append_chunk('idle',r,k*16384,ARRAY(SELECT CASE WHEN r = 'A' AND i < 32768 THEN sin(i/7.0)*(1+(i%97)/97.0) ELSE 0 END FROM generate_series(k*16384,k*16384+16383) i)) FROM unnest(ARRAY['A','B']) r, generate_series(0,2) k ORDER BY r,k; SELECT morlet_build_envelopes('idle',r,4096,512) FROM unnest(ARRAY['A','B']) r").unwrap();
        for k in [5, 100] {
            let q = format!("SELECT string_agg(recording_id||':'||start_sample||':'||distance,',' ORDER BY distance,recording_id,start_sample) FROM morlet_search_envelopes('idle',ARRAY(SELECT sin(i/6.5)*(1+(i%89)/89.0) FROM generate_series(0,4095) i),1000,'x','g','{{}}',{k},0.0075)");
            Spi::run("SET LOCAL morlet.exact_screen = on").unwrap();
            let screened = Spi::get_one::<String>(&q).unwrap().unwrap();
            Spi::run("SET LOCAL morlet.exact_screen = off").unwrap();
            assert_eq!(
                Spi::get_one::<String>(&q).unwrap().unwrap(),
                screened,
                "k = {k}"
            );
            let ones = screened.split(',').filter(|r| r.ends_with(":1")).count();
            assert_eq!(
                ones > 0,
                k == 100,
                "constant windows appear only when k exceeds the others: {screened}"
            );
        }
    }
    #[pg_test(error = "conflicting replay at chunk offset")]
    fn rejects_conflicting_chunk_replay() {
        Spi::run("SELECT morlet_create_stream_collection('public','replay_streams'); SELECT morlet_start_recording('replay_streams','A',4096,'x','g'); SELECT morlet_append_chunk('replay_streams','A',0,array_fill(0.::float8,ARRAY[137])); SELECT morlet_append_chunk('replay_streams','A',0,array_fill(1.::float8,ARRAY[137]))").unwrap();
    }
    #[pg_test]
    fn rejects_gapped_and_overlapping_chunk_offsets() {
        // After 0..1000: a gap (1001), an overlap of the stored chunk's tail (999) and an offset
        // inside it (500) are each rejected and leave the recording unchanged.
        Spi::run("SELECT morlet_create_stream_collection('public','offset_streams'); SELECT morlet_start_recording('offset_streams','A',4096,'x','g'); SELECT morlet_append_chunk('offset_streams','A',0,ARRAY(SELECT i::float8 FROM generate_series(0,999) i))").unwrap();
        Spi::run(r#"DO $$ DECLARE s bigint; msg text; BEGIN
          FOREACH s IN ARRAY ARRAY[1001,999,500] LOOP
            msg:=NULL;
            BEGIN
              PERFORM morlet_append_chunk('offset_streams','A',s,ARRAY(SELECT i::float8 FROM generate_series(s,s+99) i));
            EXCEPTION WHEN OTHERS THEN msg:=SQLERRM;
            END;
            IF msg IS DISTINCT FROM 'chunk offset must equal the next sample offset (no gaps, overlaps or replays)' THEN
              RAISE EXCEPTION 'offset %: %',s,coalesce(msg,'accepted');
            END IF;
          END LOOP;
        END $$"#).unwrap();
        assert_eq!(
            Spi::get_two::<i64, i64>("SELECT (SELECT received_samples FROM morlet_stream_status('offset_streams','A')),(SELECT count(*) FROM offset_streams_chunks)").unwrap(),
            (Some(1000), Some(1))
        );
        // the next contiguous offset is still accepted
        assert_eq!(Spi::get_one::<i64>("SELECT morlet_append_chunk('offset_streams','A',1000,ARRAY(SELECT i::float8 FROM generate_series(1000,1099) i))").unwrap(), Some(1100));
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
