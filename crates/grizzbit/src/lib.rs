//! Grizzbit: lossless float64 compression for sampled signals.
//!
//! A chunk of up to [`MAX_SAMPLES`] float64 samples is stored in one of three kinds: values on a
//! detected grid as Rice-coded integers with last-place corrections ([`Kind::Grid`]), the float64 bit
//! patterns with the same coder ([`Kind::Float`]), or plain float64 ([`Kind::Raw`]). Every chunk ends
//! with a CRC-32C, and every kind decodes bit for bit: [`decode`]`(&`[`encode`]`(x, q)?)` returns `x`
//! exactly, signed zeros included.
//!
//! Samples that lie exactly on a recording's sample grid (integer ADC counts,
//! fixed decimals, a dyadic ADC step) or within a thousandth of a step of it
//! (floats scaled by a calibrated step) are stored as integers with a fixed
//! polynomial predictor and partitioned Rice coding, the scheme FLAC uses for
//! its fixed subframes, plus last-place corrections when needed. A chunk with any sample off the grid codes the float64
//! bit patterns, mapped to order-preserving integers, with the same coder, or is
//! stored as plain float64 when that is not smaller. Every kind decodes bitwise.
//! A chunk off the plain grid but on a shifted one, offset + k * step (EDF and
//! other calibrated formats whose physical range is not symmetric), uses an
//! offset grid (format version 3) when that is smaller than plain float64.
//! The stage-1 compression benchmark (evaluation/compression) motivates this:
//! a standard codec on native integers beat every decomposition codec tested.
//!
//! # Lineage
//!
//! The integer coder is FLAC's fixed polynomial predictors (orders 0 to 4) with partitioned Rice
//! coding of the residuals. The grid is the gain/offset calibration of seismic and biomedical formats
//! (SEED and miniSEED, EDF): a stored value is `offset + k * step` for an integer count `k`. Grizzbit
//! adds what float64 storage needs on top: last-place corrections, so a value a few ulps off its grid
//! point still decodes bit for bit, and the float-bit and raw fallbacks for chunks that are not on a grid.
//!
//! # Format identity
//!
//! [`FORMAT`], "Grizzbit 1", is MorletDB's encoded chunk format versions 2 and 3, byte for byte
//! (docs/chunk-format.md): version 2 for raw, float and plain-grid chunks, version 3 only for offset
//! grids. The crate was split out of morlet-core without changing a byte; `testdata/rawcodec` (frozen
//! chunks of every version, kind and grid mode) and `testdata/grizzbit` (the encoder's output on a varied
//! sample set, written before the move) prove it. A future change to the bytes would be a new format
//! version and a new Grizzbit number; version-2 and version-3 chunks keep decoding.
//!
//! # Grizzbit 2.1 (development build)
//!
//! [`encode_with`]`(.., `[`Format::Grizzbit21`]`)` opts in to chunk format version 5
//! ([`GRIZZBIT21_VERSION`]): Grizzbit 1's grid path with last-place corrections stored once per grid
//! index (or as sparse exceptions) and a per-chunk LPC predictor beside the fixed ones. It is written
//! only where it is strictly smaller than the Grizzbit 1 chunk, so every other chunk stays Grizzbit 1
//! byte for byte; [`encode`] never writes it. Layout and rules: the `g21` module (src/g21.rs). Version 4
//! was the unshipped Grizzbit 2 and is never reused.
use std::fmt;

mod f32k;
mod g21;
#[cfg(test)]
mod spike_f32;
pub use f32k::layout as float32_layout;
#[doc(hidden)]
pub use g21::layout as grizzbit21_layout;
#[doc(hidden)]
pub use g21::LpcSearch;

/// The name and number of the format this crate writes and reads: chunk format versions 2 and 3.
pub const FORMAT: &str = "Grizzbit 1";

/// The most samples one chunk holds.
pub const MAX_SAMPLES: usize = 32_768;
/// The largest sample magnitude a chunk holds; larger and non-finite samples are refused.
pub const MAX_AMPLITUDE: f64 = 1e9;

/// Why a chunk's samples cannot be encoded (or why decoded samples are refused).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SampleError {
    /// No samples, or more than [`MAX_SAMPLES`].
    InvalidLength,
    /// A sample is NaN, infinite or beyond +/-[`MAX_AMPLITUDE`].
    InvalidSample,
}
impl fmt::Display for SampleError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidLength => "waveform must contain 1..32768 samples",
            Self::InvalidSample => "samples must be finite and within +/-1e9",
        })
    }
}
impl std::error::Error for SampleError {}

/// Checks that `samples` fit in one chunk: 1..=[`MAX_SAMPLES`] finite values within +/-[`MAX_AMPLITUDE`].
pub fn validate(samples: &[f64]) -> Result<(), SampleError> {
    if samples.is_empty() || samples.len() > MAX_SAMPLES {
        return Err(SampleError::InvalidLength);
    }
    if samples
        .iter()
        .any(|v| !v.is_finite() || v.abs() > MAX_AMPLITUDE)
    {
        return Err(SampleError::InvalidSample);
    }
    Ok(())
}

/// Version 2 appends a CRC-32C of all preceding bytes. Version 1 (no checksum)
/// existed only on an unreleased development branch and is rejected, never
/// reinterpreted. Version 3 is version 2 plus an offset in the grid header; it
/// is written only for offset grids, so every other chunk keeps its version-2 bytes.
pub const FORMAT_VERSION: u8 = 3;
/// Grizzbit 2.1 (DEVELOPMENT BUILD): chunk format version 5, written only by [`encode_with`] with
/// [`Format::Grizzbit21`] and read by [`decode`]. Version 4 belongs to the unshipped Grizzbit 2 (a pco
/// chunk kind that failed its frozen study B5); it is never reused and is rejected as unknown.
/// [`FORMAT_VERSION`] stays 3: it is what [`encode`] writes and what transfer clients are offered.
pub const GRIZZBIT21_VERSION: u8 = 5;
/// Float32 codings (DRAFT, under study B8): chunk format version 6, written only by
/// [`encode_with_float32`] for chunks whose every sample is a float32 value, where strictly smaller. Layout
/// and rules: the `f32k` module (src/f32k.rs).
pub const FLOAT32_VERSION: u8 = f32k::VERSION;
/// The highest chunk format version [`decode`] reads: versions 2, 3, 5 and 6. Version 4 is reserved for the
/// unshipped Grizzbit 2 and rejected.
pub const MAX_VERSION: u8 = FLOAT32_VERSION;
const BASE_VERSION: u8 = 2;
const CHECKSUM_BYTES: usize = 4;
const KIND_RAW: u8 = 0;
const KIND_GRID: u8 = 1;
const KIND_FLOAT: u8 = 2;
const MODE_MUL: u8 = 0;
const MODE_DIV: u8 = 1;
const MAX_ORDER: usize = 4;
const MAX_PARTITION_ORDER: u32 = 8;
const ESCAPE: u64 = 63;
/// Samples within this fraction of a step of a grid point are coded on the grid.
const NEAR_GRID: f64 = 1e-3;
/// Integers above 2^53 do not convert to float64 exactly.
const MAX_GRID_INDEX: f64 = 9_007_199_254_740_992.0;

#[derive(Debug, PartialEq, Eq)]
pub enum CodecError {
    /// The samples do not fit in a chunk (encoding), or decoded samples are invalid.
    Signal(SampleError),
    InvalidQuantum,
    UnsupportedVersion(u8),
    Corrupt(&'static str),
}
impl fmt::Display for CodecError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Signal(e) => e.fmt(f),
            Self::InvalidQuantum => f.write_str("sample_quantum must be finite and positive"),
            Self::UnsupportedVersion(v) => write!(
                f,
                "unsupported encoded chunk format version {v}; re-append the samples"
            ),
            Self::Corrupt(what) => write!(f, "corrupt encoded chunk: {what}"),
        }
    }
}
impl std::error::Error for CodecError {}

/// How an encoded chunk stores its samples.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Grid,
    Float,
    Raw,
}
impl Kind {
    pub fn name(self) -> &'static str {
        match self {
            Self::Grid => "grid",
            Self::Float => "float",
            Self::Raw => "raw",
        }
    }
}

/// Grid index <-> sample mapping. A quantum of 1/d for integer d decodes as
/// k / d, so decimal grids such as 0.001 reproduce parsed values exactly.
#[derive(Clone, Copy)]
pub(crate) struct Grid {
    mode: u8,
    scale: f64,
    /// Added to every grid value; None for version-2 grids, which never add it
    /// (0.0 + -0.0 would lose the sign of a negative zero).
    offset: Option<f64>,
}
impl Grid {
    fn new(quantum: f64) -> Result<Self, CodecError> {
        if !quantum.is_finite() || quantum <= 0.0 {
            return Err(CodecError::InvalidQuantum);
        }
        let inverse = 1.0 / quantum;
        let rounded = inverse.round();
        if (1.0..=MAX_GRID_INDEX).contains(&rounded) && (inverse - rounded).abs() <= 1e-9 * rounded
        {
            Ok(Self {
                mode: MODE_DIV,
                scale: rounded,
                offset: None,
            })
        } else {
            Ok(Self {
                mode: MODE_MUL,
                scale: quantum,
                offset: None,
            })
        }
    }
    pub(crate) fn step(self) -> f64 {
        if self.mode == MODE_DIV {
            1.0 / self.scale
        } else {
            self.scale
        }
    }
    /// Nearest grid index, if `x` is within NEAR_GRID steps of it. The exact
    /// float64 is restored from a correction in units of the last place.
    fn index(self, x: f64) -> Option<i64> {
        let x0 = x;
        let x = self.offset.map_or(x, |o| x - o);
        let k = if self.mode == MODE_DIV {
            (x * self.scale).round()
        } else {
            (x / self.scale).round()
        };
        if !k.is_finite() || k.abs() > MAX_GRID_INDEX {
            return None;
        }
        let k = k as i64;
        ((self.value(k) - x0).abs() <= NEAR_GRID * self.step()).then_some(k)
    }
    fn unshifted(self, k: i64) -> f64 {
        if self.mode == MODE_DIV {
            k as f64 / self.scale
        } else {
            k as f64 * self.scale
        }
    }
    pub(crate) fn value(self, k: i64) -> f64 {
        match self.offset {
            Some(o) => o + self.unshifted(k),
            None => self.unshifted(k),
        }
    }
}

/// Encodes validated samples as Grizzbit 1 (chunk format versions 2 and 3). The result decodes bitwise
/// to the input. Unchanged by Grizzbit 2.1: use [`encode_with`] to opt in to version 5.
pub fn encode(samples: &[f64], quantum: f64) -> Result<Vec<u8>, CodecError> {
    seal(encode_body(samples, quantum, None)?)
}

/// The chunk formats this crate writes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Format {
    /// Chunk format versions 2 and 3, what [`encode`] writes.
    Grizzbit1,
    /// DEVELOPMENT BUILD, not a stable format: chunk format version 5 ([`GRIZZBIT21_VERSION`]) where it is
    /// strictly smaller than Grizzbit 1, otherwise Grizzbit 1 byte for byte. See the `g21` module docs.
    Grizzbit21,
}

/// Encodes validated samples in `format`. [`Format::Grizzbit1`] is [`encode`]. [`Format::Grizzbit21`]
/// keeps Grizzbit 1's grid detection and fit, and writes a version-5 chunk only when it is strictly
/// smaller than the Grizzbit 1 chunk (a tie keeps Grizzbit 1). [`decode`] reads both.
pub fn encode_with(samples: &[f64], quantum: f64, format: Format) -> Result<Vec<u8>, CodecError> {
    match format {
        Format::Grizzbit1 => encode(samples, quantum),
        Format::Grizzbit21 => encode_experimental(samples, quantum, Grizzbit21Features::ALL),
    }
}

/// [`encode_with`], and for a chunk whose every sample is a float32 value, the version-6 chunk instead when it
/// has strictly fewer bytes (DRAFT, study B8). Every other chunk is [`encode_with`]'s, byte for byte; on a tie
/// [`encode_with`]'s chunk is kept. [`decode`] reads both.
pub fn encode_with_float32(
    samples: &[f64],
    quantum: f64,
    format: Format,
) -> Result<Vec<u8>, CodecError> {
    let best = encode_with(samples, quantum, format)?;
    // The sealed version-6 chunk is the body plus the checksum; keep it only if strictly smaller.
    match f32k::encode_body(samples) {
        Some(body) if body.len() + CHECKSUM_BYTES < best.len() => seal(body),
        _ => Ok(best),
    }
}

/// The version-6 chunk for float32-exact samples (sealed), whatever its size against the other formats, or
/// None when a sample is not a float32 value. For tests, golden chunks and fuzzing: stores use
/// [`encode_with_float32`], which keeps it only where it is strictly smaller.
#[doc(hidden)]
pub fn encode_version6(samples: &[f64]) -> Result<Option<Vec<u8>>, CodecError> {
    validate(samples).map_err(CodecError::Signal)?;
    f32k::encode_body(samples).map(seal).transpose()
}

/// Which of Grizzbit 2.1's two codings the encoder may use (for development measurements of each alone).
#[doc(hidden)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Grizzbit21Features {
    /// Corrections once per grid index (table) or as sparse exceptions.
    pub corrections: bool,
    /// Per-chunk LPC beside the fixed predictors.
    pub lpc: bool,
    /// Which LPC orders the encoder tries.
    pub search: LpcSearch,
}
impl Grizzbit21Features {
    pub const ALL: Self = Self {
        corrections: true,
        lpc: true,
        search: LpcSearch::DEFAULT,
    };
}

/// [`encode_with`]`(.., `[`Format::Grizzbit21`]`)` with a subset of its codings. Development only.
#[doc(hidden)]
pub fn encode_experimental(
    samples: &[f64],
    quantum: f64,
    features: Grizzbit21Features,
) -> Result<Vec<u8>, CodecError> {
    let mut plans = Vec::new();
    let mut best = encode_body(samples, quantum, Some(&mut plans))?;
    for plan in &plans {
        best = keep_smaller(best, g21::encode_plan(samples.len(), plan, features));
    }
    seal(best)
}

/// The smallest version-5 chunk for `samples` (sealed), whatever its size against Grizzbit 1, or None when
/// Grizzbit 1 found no grid coding (version 5 has the grid kind only). For tests, golden chunks and fuzzing:
/// stores use [`encode_with`], which keeps it only where it is strictly smaller.
#[doc(hidden)]
pub fn encode_version5(samples: &[f64], quantum: f64) -> Result<Option<Vec<u8>>, CodecError> {
    let mut plans = Vec::new();
    encode_body(samples, quantum, Some(&mut plans))?;
    let best = plans
        .iter()
        .map(|p| g21::encode_plan(samples.len(), p, Grizzbit21Features::ALL))
        .min_by_key(|c| c.len());
    best.map(seal).transpose()
}

/// Keep the smaller: `candidate` (a version-5 body) replaces `best` only if it has strictly fewer bytes, so
/// on a tie the earlier coding, Grizzbit 1's, stays byte for byte.
fn keep_smaller(best: Vec<u8>, candidate: Vec<u8>) -> Vec<u8> {
    if candidate.len() < best.len() {
        candidate
    } else {
        best
    }
}

fn seal(mut body: Vec<u8>) -> Result<Vec<u8>, CodecError> {
    let checksum = crc32c(&body);
    body.extend_from_slice(&checksum.to_le_bytes());
    Ok(body)
}

/// A grid coding the Grizzbit 1 encoder computed: the fitted grid, the indices and the per-sample
/// last-place corrections (differences of order-preserving integers). Version 5 recodes it.
pub(crate) struct Plan {
    pub(crate) grid: Grid,
    pub(crate) ints: Vec<i64>,
    pub(crate) fix: Vec<i64>,
    /// Grizzbit 1's index stream and its estimated size in bits (partitions, without the 7 header
    /// bits), which version 5 reuses when no LPC predictor beats it.
    pub(crate) ints_stream: Vec<u8>,
    pub(crate) ints_cost: u128,
    /// Grizzbit 1's correction stream (empty without corrections), version 5's per-sample mode.
    pub(crate) fix_stream: Vec<u8>,
}

/// The Grizzbit 1 body (no checksum). With `plans`, every grid coding it computes is also handed out.
fn encode_body(
    samples: &[f64],
    quantum: f64,
    mut plans: Option<&mut Vec<Plan>>,
) -> Result<Vec<u8>, CodecError> {
    validate(samples).map_err(CodecError::Signal)?;
    let grid = Grid::new(quantum)?;
    // Chunks on the plain grid keep their version-2 encoding. Only a chunk off it
    // tries a shifted grid, before the costlier float-bit coding it would replace.
    match samples
        .iter()
        .map(|&x| grid.index(x))
        .collect::<Option<Vec<i64>>>()
    {
        Some(ints) => Ok(encode_version2(samples, grid, ints, plans)),
        None => {
            // Values off the declared grid may still sit on a grid of their own (calibrated ADC counts
            // or fixed decimals delivered as floats with no quantum). Try the step detected from the
            // chunk. A detected grid that needs no last-place corrections, or codes in at most half of
            // plain float64, is kept as it is; otherwise the previous encoding is computed too and the
            // smaller one kept. Both are bitwise exact.
            let detected = detect_step(samples).and_then(|q| {
                let g = Grid::new(q).ok()?;
                match samples
                    .iter()
                    .map(|&x| g.index(x))
                    .collect::<Option<Vec<i64>>>()
                {
                    Some(ints) => Some(encode_version2(samples, g, ints, plans.as_deref_mut())),
                    None => encode_offset_grid(samples, g, plans.as_deref_mut()),
                }
            });
            if let Some(d) = detected
                .as_ref()
                .filter(|d| grid_chunk_without_corrections(d) || d.len() <= 4 * samples.len())
            {
                return Ok(d.clone());
            }
            let fallback = encode_offset_grid(samples, grid, plans).unwrap_or_else(|| {
                let mut out = vec![BASE_VERSION];
                encode_off_grid(samples, &mut out);
                out
            });
            Ok(match detected {
                Some(d) if d.len() < fallback.len() => d,
                _ => fallback,
            })
        }
    }
}

/// Whether an unchecksummed chunk body is a grid chunk whose samples need no last-place corrections.
fn grid_chunk_without_corrections(body: &[u8]) -> bool {
    if body.len() < 3 || body[1] != KIND_GRID {
        return false;
    }
    let mut pos = 2;
    if get_varint(body, &mut pos).is_err() {
        return false;
    }
    let header = if body[0] == FORMAT_VERSION { 17 } else { 9 };
    body.get(pos + header) == Some(&0)
}

/// The step of a grid the samples lie on, estimated from the chunk itself: the smallest gap between
/// distinct values, or that gap divided by 2..=8 when the values skip levels. Accepted only if every
/// distinct value is within NEAR_GRID steps of a multiple of it (or of it plus the smallest value, for
/// shifted grids); the encoder then refines the step and stores last-place corrections, so a rough
/// estimate costs size, never exactness. None for constant or too-short chunks and noisy floats.
fn detect_step(samples: &[f64]) -> Option<f64> {
    let mut v: Vec<f64> = samples.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    v.dedup_by(|a, b| a.to_bits() == b.to_bits() || *a == *b);
    if v.len() < 3 {
        return None;
    }
    let span = v[v.len() - 1] - v[0];
    let magnitude = v[0].abs().max(v[v.len() - 1].abs());
    if span.is_nan() || span <= 0.0 || !span.is_finite() {
        return None;
    }
    // Gaps at the level of float rounding are not grid steps.
    let floor = magnitude * 1e-12;
    let gap = v
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|&d| d > floor)
        .fold(f64::INFINITY, f64::min);
    if !gap.is_finite() || span / gap > MAX_GRID_INDEX / 2.0 {
        return None;
    }
    let origin = v[0];
    (1..=8u32).map(|m| gap / m as f64).find(|&q| {
        q > 0.0
            && q.is_normal()
            && v.iter().all(|&x| {
                let k = ((x - origin) / q).round();
                ((x - origin) - k * q).abs() <= NEAR_GRID * q
            })
    })
}

/// Least-cost corrections (fewest nonzero, then smallest) over candidate grids.
fn best_grid(samples: &[f64], ints: &[i64], candidates: Vec<Grid>) -> (Grid, Vec<i64>) {
    let corrections = |g: Grid| -> Vec<i64> {
        ints.iter()
            .zip(samples)
            .map(|(&k, &x)| ordered_bits(x).wrapping_sub(ordered_bits(g.value(k))))
            .collect()
    };
    // Each candidate is scored in one pass without storing its corrections; only the winner's are
    // built. min_by_key keeps the first of equal minima, as before.
    let cost = |g: Grid| {
        let (mut nonzero, mut size) = (0usize, 0u128);
        for (&k, &x) in ints.iter().zip(samples) {
            let c = ordered_bits(x).wrapping_sub(ordered_bits(g.value(k)));
            nonzero += (c != 0) as usize;
            size += c.unsigned_abs() as u128;
        }
        (nonzero, size)
    };
    let grid = if candidates.len() == 1 {
        candidates[0]
    } else {
        candidates
            .into_iter()
            .min_by_key(|&g| cost(g))
            .expect("at least one candidate")
    };
    (grid, corrections(grid))
}

/// Floats within `ulps` units in the last place of `x`, in increasing order.
fn ulp_neighbours(x: f64, ulps: u64) -> Vec<f64> {
    let base = ordered_bits(x);
    (0..=2 * ulps)
        .map(|i| float_from_ordered(base - ulps as i64 + i as i64))
        .filter(|v| v.is_finite())
        .collect()
}

/// Writes a Grizzbit 1 grid chunk body after the version byte. With `plans`, also hands out the grid
/// coding with its two streams for version 5.
fn put_grid(
    out: &mut Vec<u8>,
    n: usize,
    grid: Grid,
    ints: Vec<i64>,
    fix: Vec<i64>,
    plans: Option<&mut Vec<Plan>>,
) {
    out.push(KIND_GRID);
    put_varint(out, n as u64);
    out.push(grid.mode);
    out.extend_from_slice(&grid.scale.to_le_bytes());
    if let Some(o) = grid.offset {
        out.extend_from_slice(&o.to_le_bytes());
    }
    let has_fix = fix.iter().any(|&v| v != 0);
    out.push(has_fix as u8);
    let mut stream = Vec::new();
    let ints_cost = encode_stream(&ints, false, &mut stream);
    put_varint(out, stream.len() as u64);
    out.extend_from_slice(&stream);
    let mut fix_stream = Vec::new();
    if has_fix {
        encode_ints(&fix, &mut fix_stream);
        out.extend_from_slice(&fix_stream);
    }
    if let Some(plans) = plans {
        plans.push(Plan {
            grid,
            ints,
            fix,
            ints_stream: stream,
            ints_cost,
            fix_stream,
        });
    }
}

/// Version 3: samples at offset + k * step. The offset comes from the first sample;
/// step and offset are then refitted to within one ulp of the values that need
/// the fewest corrections. None when the chunk is not on any shifted grid or the
/// result is not smaller than plain float64.
fn encode_offset_grid(
    samples: &[f64],
    grid: Grid,
    plans: Option<&mut Vec<Plan>>,
) -> Option<Vec<u8>> {
    let x0 = samples[0];
    let k0 = (if grid.mode == MODE_DIV {
        x0 * grid.scale
    } else {
        x0 / grid.scale
    })
    .round();
    if !k0.is_finite() || k0.abs() > MAX_GRID_INDEX {
        return None;
    }
    let first = x0 - grid.unshifted(k0 as i64);
    let start = Grid {
        offset: Some(first),
        ..grid
    };
    let ints: Vec<i64> = samples
        .iter()
        .map(|&x| start.index(x))
        .collect::<Option<_>>()?;
    let (lo, hi) = (0..ints.len()).fold((0, 0), |(lo, hi), i| {
        (
            if ints[i] < ints[lo] { i } else { lo },
            if ints[i] > ints[hi] { i } else { hi },
        )
    });
    let mut scales = vec![grid.scale];
    if grid.mode == MODE_MUL && ints[hi] > ints[lo] {
        let fitted = (samples[hi] - samples[lo]) / (ints[hi] - ints[lo]) as f64;
        if fitted.is_finite() && fitted > 0.0 {
            scales.extend(ulp_neighbours(fitted, 1));
        }
    }
    let mut candidates = vec![start];
    for scale in scales {
        let g = Grid {
            scale,
            offset: Some(0.0),
            ..grid
        };
        let fitted = samples[lo] - g.unshifted(ints[lo]);
        for offset in ulp_neighbours(fitted, 1) {
            candidates.push(Grid {
                offset: Some(offset),
                ..g
            });
        }
    }
    let (grid, fix) = best_grid(samples, &ints, candidates);
    let mut out = vec![FORMAT_VERSION];
    put_grid(&mut out, samples.len(), grid, ints, fix, plans);
    (out.len() < 3 + 8 * samples.len()).then_some(out)
}

/// The version-2 grid encoding of samples at grid indices `ints`, or float-bit
/// coding when that grid encoding is not smaller than plain float64.
fn encode_version2(
    samples: &[f64],
    grid: Grid,
    ints: Vec<i64>,
    plans: Option<&mut Vec<Plan>>,
) -> Vec<u8> {
    let mut out = vec![BASE_VERSION];
    {
        {
            // Data scaled in float64 by a slightly different step (e.g. a
            // calibrated ADC step) needs no corrections at the fitted step.
            let mut candidates = vec![grid];
            // x / k at the largest |k| is within an ulp or two of such a step.
            let (k_max, x_max) = ints
                .iter()
                .zip(samples)
                .max_by_key(|(k, _)| k.unsigned_abs())
                .expect("nonempty chunk");
            let fitted = x_max / *k_max as f64;
            if grid.mode == MODE_MUL && fitted.is_finite() && fitted > 0.0 {
                // Steps within two ulps of the fitted one. Near the smallest subnormal
                // there are fewer, and a zero step is not a grid the decoder accepts.
                let bits = fitted.to_bits();
                for scale in (bits.saturating_sub(2)..=bits + 2).map(f64::from_bits) {
                    if scale > 0.0 {
                        candidates.push(Grid {
                            mode: MODE_MUL,
                            scale,
                            offset: None,
                        });
                    }
                }
            }
            let (grid, fix) = best_grid(samples, &ints, candidates);
            put_grid(&mut out, samples.len(), grid, ints, fix, plans);
            if out.len() < 3 + 8 * samples.len() {
                return out;
            }
            out.truncate(1);
            encode_off_grid(samples, &mut out);
        }
    }
    out
}

fn encode_off_grid(samples: &[f64], out: &mut Vec<u8>) {
    out.push(KIND_FLOAT);
    put_varint(out, samples.len() as u64);
    let ordered: Vec<i64> = samples.iter().map(|&x| ordered_bits(x)).collect();
    encode_ints(&ordered, out);
    if out.len() >= 3 + 8 * samples.len() {
        // Plain little-endian float64, the byte layout of a float8[] payload.
        out.truncate(1);
        out.push(KIND_RAW);
        put_varint(out, samples.len() as u64);
        out.extend(samples.iter().flat_map(|x| x.to_le_bytes()));
    }
}

/// The checksummed body (version byte through payload) of a version-2 chunk.
pub(crate) fn verified(encoded: &[u8]) -> Result<&[u8], CodecError> {
    match encoded.first() {
        None => return Err(CodecError::Corrupt("empty")),
        Some(&BASE_VERSION)
        | Some(&FORMAT_VERSION)
        | Some(&GRIZZBIT21_VERSION)
        | Some(&f32k::VERSION) => {}
        Some(&v) if v < BASE_VERSION => return Err(CodecError::UnsupportedVersion(v)),
        Some(_) => return Err(CodecError::Corrupt("unknown format version")),
    }
    if encoded.len() < 3 + CHECKSUM_BYTES {
        return Err(CodecError::Corrupt("truncated"));
    }
    let (body, tail) = encoded.split_at(encoded.len() - CHECKSUM_BYTES);
    if crc32c(body).to_le_bytes() != tail {
        return Err(CodecError::Corrupt("checksum mismatch"));
    }
    Ok(body)
}

/// Verifies the checksum and returns the chunk's storage kind.
pub fn kind(encoded: &[u8]) -> Result<Kind, CodecError> {
    kind_of(verified(encoded)?)
}
fn kind_of(body: &[u8]) -> Result<Kind, CodecError> {
    match (body[0], body[1]) {
        (f32k::VERSION, _) => f32k::kind_of(body),
        (BASE_VERSION | FORMAT_VERSION | GRIZZBIT21_VERSION, KIND_GRID) => Ok(Kind::Grid),
        (BASE_VERSION, KIND_FLOAT) => Ok(Kind::Float),
        (BASE_VERSION, KIND_RAW) => Ok(Kind::Raw),
        _ => Err(CodecError::Corrupt("unknown kind")),
    }
}

/// Verifies the checksum, then decodes and validates the samples.
pub fn decode(encoded: &[u8]) -> Result<Vec<f64>, CodecError> {
    decode_verified(verified(encoded)?)
}

/// Decodes a body that `verified` accepted: a known version byte and at least three bytes.
fn decode_verified(encoded: &[u8]) -> Result<Vec<f64>, CodecError> {
    let kind = kind_of(encoded)?;
    if encoded[0] == f32k::VERSION {
        let samples = f32k::decode_body(encoded)?;
        validate(&samples).map_err(CodecError::Signal)?;
        return Ok(samples);
    }
    if encoded[0] == GRIZZBIT21_VERSION {
        let samples = g21::decode_body(encoded)?;
        validate(&samples).map_err(CodecError::Signal)?;
        return Ok(samples);
    }
    let mut pos = 2;
    let n = get_varint(encoded, &mut pos)?;
    if n == 0 || n > MAX_SAMPLES as u64 {
        return Err(CodecError::Corrupt("sample count"));
    }
    let n = n as usize;
    let samples: Vec<f64> = match kind {
        Kind::Raw => {
            let body = encoded.get(pos..).ok_or(CodecError::Corrupt("truncated"))?;
            if body.len() != 8 * n {
                return Err(CodecError::Corrupt("raw length"));
            }
            body.chunks_exact(8)
                .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
                .collect()
        }
        Kind::Float => decode_ints(&encoded[pos..], n)?
            .into_iter()
            .map(float_from_ordered)
            .collect(),
        Kind::Grid => {
            let shifted = encoded[0] == FORMAT_VERSION;
            let size = if shifted { 17 } else { 9 };
            let header = encoded
                .get(pos..pos + size)
                .ok_or(CodecError::Corrupt("truncated"))?;
            let mode = header[0];
            let scale = f64::from_le_bytes(header[1..9].try_into().unwrap());
            let offset = shifted.then(|| f64::from_le_bytes(header[9..17].try_into().unwrap()));
            if mode > MODE_DIV
                || !scale.is_finite()
                || scale <= 0.0
                || offset.is_some_and(|o| !o.is_finite())
            {
                return Err(CodecError::Corrupt("grid header"));
            }
            let grid = Grid {
                mode,
                scale,
                offset,
            };
            pos += size;
            let has_fix = *encoded.get(pos).ok_or(CodecError::Corrupt("truncated"))?;
            pos += 1;
            if has_fix > 1 {
                return Err(CodecError::Corrupt("grid header"));
            }
            let len = get_varint(encoded, &mut pos)?;
            let end = pos
                .checked_add(usize::try_from(len).map_err(|_| CodecError::Corrupt("length"))?)
                .filter(|&e| e <= encoded.len())
                .ok_or(CodecError::Corrupt("truncated"))?;
            let ints = decode_ints(&encoded[pos..end], n)?;
            if has_fix == 1 {
                let fix = decode_ints(&encoded[end..], n)?;
                ints.iter()
                    .zip(fix)
                    .map(|(&k, c)| float_from_ordered(ordered_bits(grid.value(k)).wrapping_add(c)))
                    .collect()
            } else if end != encoded.len() {
                return Err(CodecError::Corrupt("trailing data"));
            } else {
                ints.iter().map(|&k| grid.value(k)).collect()
            }
        }
    };
    validate(&samples).map_err(CodecError::Signal)?;
    Ok(samples)
}

/// CRC-32C (Castagnoli, reflected polynomial 0x82F63B78), as used by iSCSI and ext4.
const fn crc32c_table() -> [u32; 256] {
    let mut table = [0u32; 256];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut bit = 0;
        while bit < 8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0x82F6_3B78
            } else {
                c >> 1
            };
            bit += 1;
        }
        table[i] = c;
        i += 1;
    }
    table
}
const CRC32C_TABLE: [u32; 256] = crc32c_table();
/// Slicing-by-8 tables: CRC32C_SLICES[k][b] is the CRC contribution of byte b followed by k zero bytes.
const fn crc32c_slices() -> [[u32; 256]; 8] {
    let mut t = [[0u32; 256]; 8];
    t[0] = CRC32C_TABLE;
    let mut k = 1;
    while k < 8 {
        let mut i = 0;
        while i < 256 {
            let prev = t[k - 1][i];
            t[k][i] = (prev >> 8) ^ CRC32C_TABLE[(prev & 0xff) as usize];
            i += 1;
        }
        k += 1;
    }
    t
}
const CRC32C_SLICES: [[u32; 256]; 8] = crc32c_slices();
/// CRC-32C, eight bytes per step (slicing-by-8); equal to the byte-at-a-time definition.
pub fn crc32c(bytes: &[u8]) -> u32 {
    let t = &CRC32C_SLICES;
    let mut c = !0u32;
    let mut words = bytes.chunks_exact(8);
    for w in &mut words {
        let lo = u32::from_le_bytes([w[0], w[1], w[2], w[3]]) ^ c;
        let hi = u32::from_le_bytes([w[4], w[5], w[6], w[7]]);
        c = t[7][(lo & 0xff) as usize]
            ^ t[6][((lo >> 8) & 0xff) as usize]
            ^ t[5][((lo >> 16) & 0xff) as usize]
            ^ t[4][(lo >> 24) as usize]
            ^ t[3][(hi & 0xff) as usize]
            ^ t[2][((hi >> 8) & 0xff) as usize]
            ^ t[1][((hi >> 16) & 0xff) as usize]
            ^ t[0][(hi >> 24) as usize];
    }
    for &b in words.remainder() {
        c = CRC32C_TABLE[((c ^ b as u32) & 0xff) as usize] ^ (c >> 8);
    }
    !c
}

/// Monotonic float64 -> i64 map: +0 is 0, -0 is -1, and numeric order is kept,
/// so smooth signals within a binade give small prediction residuals.
pub(crate) fn ordered_bits(x: f64) -> i64 {
    let bits = x.to_bits();
    if bits >> 63 == 1 {
        -((bits & !(1 << 63)) as i64) - 1
    } else {
        bits as i64
    }
}
pub(crate) fn float_from_ordered(o: i64) -> f64 {
    if o < 0 {
        f64::from_bits((o.wrapping_add(1).wrapping_neg() as u64) | (1 << 63))
    } else {
        f64::from_bits(o as u64)
    }
}

pub(crate) fn predict(x: &[i64], i: usize, order: usize) -> i64 {
    match order.min(i) {
        0 => 0,
        1 => x[i - 1],
        2 => x[i - 1].wrapping_mul(2).wrapping_sub(x[i - 2]),
        3 => x[i - 1]
            .wrapping_sub(x[i - 2])
            .wrapping_mul(3)
            .wrapping_add(x[i - 3]),
        _ => x[i - 1]
            .wrapping_add(x[i - 3])
            .wrapping_mul(4)
            .wrapping_sub(x[i - 2].wrapping_mul(6))
            .wrapping_sub(x[i - 4]),
    }
}

pub(crate) fn zigzag(v: i64) -> u64 {
    ((v << 1) ^ (v >> 63)) as u64
}
pub(crate) fn unzigzag(u: u64) -> i64 {
    ((u >> 1) as i64) ^ -((u & 1) as i64)
}

/// Rice parameter (or ESCAPE) and its estimated bit cost for one partition.
fn best_param(sum: u128, max: u64, count: usize) -> (u64, u128) {
    // cost(k) = m(k+1) + (sum >> k) is discretely convex in k: cost(k+1) - cost(k) = m - ceil((sum >> k) / 2),
    // and ceil((sum >> k) / 2) never grows with k. The first k at which the cost stops falling is
    // therefore the first minimum over 0..ESCAPE, the same choice as a full scan keeping strict
    // improvements.
    //
    // The walk starts at a lower bound of that minimum: for every k <= B - 2 - bitlen(m), with B the bit
    // length of `sum`, (sum >> k) >= 2^(bitlen(m) + 1) > 2m, so the cost still falls at k. Starting at
    // k0 = B - 1 - bitlen(m) (at least 0, at most ESCAPE - 1) therefore gives the same k as starting at 0.
    if sum < 1 << 62 && count < 1 << 32 {
        // The same arithmetic in u64: with sum < 2^62, m < 2^32 and k <= 63 nothing overflows.
        let m = count as u64;
        let sum = sum as u64;
        let cost = |k: u64| m * (k + 1) + (sum >> k);
        let bits_sum = 64 - sum.leading_zeros() as i64;
        let bits_m = 64 - m.leading_zeros() as i64;
        let mut k = (bits_sum - 1 - bits_m).clamp(0, ESCAPE as i64 - 1) as u64;
        while k + 1 < ESCAPE && cost(k + 1) < cost(k) {
            k += 1;
        }
        let mut best = (k, cost(k));
        let width = 64 - max.leading_zeros() as u64;
        let escape = 7 + m * width;
        if escape < best.1 {
            best = (ESCAPE, escape);
        }
        return (best.0, best.1 as u128 + 6);
    }
    let m = count as u128;
    let cost = |k: u64| m * (k as u128 + 1) + (sum >> k);
    let bits_sum = 128 - sum.leading_zeros() as i64;
    let bits_m = 128 - m.leading_zeros() as i64;
    let mut k = (bits_sum - 1 - bits_m).clamp(0, ESCAPE as i64 - 1) as u64;
    while k + 1 < ESCAPE && cost(k + 1) < cost(k) {
        k += 1;
    }
    let mut best = (k, cost(k));
    let width = 64 - max.leading_zeros() as u128;
    let escape = 7 + m * width;
    if escape < best.1 {
        best = (ESCAPE, escape);
    }
    (best.0, best.1 + 6)
}

pub(crate) fn partition_bounds(n: usize, porder: u32, p: usize) -> (usize, usize) {
    ((p * n) >> porder, ((p + 1) * n) >> porder)
}

fn residuals(x: &[i64], order: usize, r: &mut [u64]) {
    let n = x.len();
    let warm = order.min(n);
    for i in 0..warm {
        r[i] = zigzag(x[i].wrapping_sub(predict(x, i, order)));
    }
    // The same predictors as `predict`, with the order fixed for the whole loop.
    match order {
        0 => {
            for i in warm..n {
                r[i] = zigzag(x[i]);
            }
        }
        1 => {
            for i in warm..n {
                r[i] = zigzag(x[i].wrapping_sub(x[i - 1]));
            }
        }
        2 => {
            for i in warm..n {
                r[i] = zigzag(x[i].wrapping_sub(x[i - 1].wrapping_mul(2).wrapping_sub(x[i - 2])));
            }
        }
        3 => {
            for i in warm..n {
                let p = x[i - 1]
                    .wrapping_sub(x[i - 2])
                    .wrapping_mul(3)
                    .wrapping_add(x[i - 3]);
                r[i] = zigzag(x[i].wrapping_sub(p));
            }
        }
        _ => {
            for i in warm..n {
                let p = x[i - 1]
                    .wrapping_add(x[i - 3])
                    .wrapping_mul(4)
                    .wrapping_sub(x[i - 2].wrapping_mul(6))
                    .wrapping_sub(x[i - 4]);
                r[i] = zigzag(x[i].wrapping_sub(p));
            }
        }
    }
}

/// Smallest partitioned-Rice cost of the residuals `r` over partition orders 0..=`max_porder` (the first
/// minimum) and that partition order. Partition sums and maxima are computed once at the finest partition
/// order and merged pairwise for coarser ones: partition p at order o is exactly partitions 2p and 2p+1 at
/// order o+1, since ((2p * n) >> (o+1)) == ((p * n) >> o).
pub(crate) fn partition_search(r: &[u64], max_porder: u32, scratch: &mut Scratch) -> (u128, u32) {
    let n = r.len();
    let Scratch { sums, maxs, costs } = scratch;
    sums.clear();
    maxs.clear();
    for p in 0..(1usize << max_porder) {
        let (a, b) = partition_bounds(n, max_porder, p);
        let (sum, max) = sum_and_max(&r[a..b]);
        sums.push(sum);
        maxs.push(max);
    }
    costs.clear();
    costs.resize(max_porder as usize + 1, 0);
    for porder in (0..=max_porder).rev() {
        let parts = 1usize << porder;
        let mut cost = 0u128;
        for p in 0..parts {
            let (a, b) = partition_bounds(n, porder, p);
            cost += best_param(sums[p], maxs[p], b - a).1;
        }
        costs[porder as usize] = cost;
        if porder > 0 {
            for p in 0..parts / 2 {
                sums[p] = sums[2 * p] + sums[2 * p + 1];
                maxs[p] = maxs[2 * p].max(maxs[2 * p + 1]);
            }
        }
    }
    let mut best = (costs[0], 0);
    for porder in 1..=max_porder {
        if costs[porder as usize] < best.0 {
            best = (costs[porder as usize], porder);
        }
    }
    best
}

/// The exact sum (as u128) and the maximum of `chunk`: summed in u64 when the maximum shows that cannot
/// overflow (the common case, and one that vectorises), otherwise in u128.
#[inline]
fn sum_and_max(chunk: &[u64]) -> (u128, u64) {
    let max = chunk.iter().copied().max().unwrap_or(0);
    let len = chunk.len() as u128;
    let sum = if (max as u128) * len <= u64::MAX as u128 {
        chunk.iter().sum::<u64>() as u128
    } else {
        chunk.iter().map(|&v| v as u128).sum()
    };
    (sum, max)
}

/// Buffers reused by [`partition_search`].
#[derive(Default)]
pub(crate) struct Scratch {
    sums: Vec<u128>,
    maxs: Vec<u64>,
    costs: Vec<u128>,
}

pub(crate) fn max_partition_order(n: usize) -> u32 {
    MAX_PARTITION_ORDER.min(usize::BITS - 1 - n.leading_zeros())
}

/// Chooses the predictor order and partition order with the smallest coded size (first minimum in
/// order-major, partition-order-minor sequence), then writes the partitioned Rice stream. Grizzbit 1's
/// stream coder; version 5 also uses it for its correction streams.
fn encode_ints(x: &[i64], out: &mut Vec<u8>) {
    encode_stream(x, false, out);
}

/// [`encode_ints`], and with `lpc` (version 5 only) also a per-chunk LPC predictor, written instead of
/// the best fixed predictor only when its size, side information included, is strictly smaller.
/// Without `lpc`, or when LPC does not win, the bytes are exactly [`encode_ints`]'s.
/// Returns the chosen fixed predictor's estimated size in bits (partitions, without the 7 header bits),
/// whether or not LPC replaced it.
pub(crate) fn encode_stream(x: &[i64], lpc: bool, out: &mut Vec<u8>) -> u128 {
    let n = x.len();
    let max_porder = max_partition_order(n);
    let mut r = vec![0u64; n];
    let mut scratch = Scratch::default();
    let mut best: Option<(u128, usize, u32)> = None;
    for order in 0..=MAX_ORDER {
        residuals(x, order, &mut r);
        let (cost, porder) = partition_search(&r, max_porder, &mut scratch);
        if best.is_none_or(|b| cost < b.0) {
            best = Some((cost, order, porder));
        }
    }
    let (cost, order, porder) = best.expect("at least one candidate");
    if lpc {
        if let Some(stream) = g21::lpc_stream(x, cost, g21::LpcSearch::DEFAULT) {
            out.extend(stream);
            return cost;
        }
    }
    let mut w = BitWriter::default();
    residuals(x, order, &mut r);
    w.put(order as u64, 3);
    w.put(porder as u64, 4);
    write_partitions(&mut w, &r, porder);
    out.extend(w.finish());
    cost
}

/// A version-5 side stream (table, exception gaps and values): fixed predictors 0..=`SIDE_MAX_ORDER`
/// only. These streams are nearly white, so higher orders rarely win; any order is valid to a decoder.
pub(crate) fn encode_side_stream(x: &[i64], out: &mut Vec<u8>) {
    let n = x.len();
    let max_porder = max_partition_order(n);
    let mut r = vec![0u64; n];
    let mut scratch = Scratch::default();
    let mut best: Option<(u128, usize, u32)> = None;
    for order in 0..=g21::SIDE_MAX_ORDER {
        residuals(x, order, &mut r);
        let (cost, porder) = partition_search(&r, max_porder, &mut scratch);
        if best.is_none_or(|b| cost < b.0) {
            best = Some((cost, order, porder));
        }
    }
    let (_, order, porder) = best.expect("at least one candidate");
    let mut w = BitWriter::default();
    residuals(x, order, &mut r);
    w.put(order as u64, 3);
    w.put(porder as u64, 4);
    write_partitions(&mut w, &r, porder);
    out.extend(w.finish());
}

/// Writes an LPC stream whose residuals `r` are already computed.
pub(crate) fn write_lpc_stream(model: &g21::Lpc, r: &[u64], porder: u32) -> Vec<u8> {
    let mut w = BitWriter::default();
    w.put(g21::PREDICTOR_LPC, 3);
    w.put(porder as u64, 4);
    model.write(&mut w);
    write_partitions(&mut w, r, porder);
    w.finish()
}

fn write_partitions(w: &mut BitWriter, r: &[u64], porder: u32) {
    let n = r.len();
    for p in 0..(1usize << porder) {
        let (a, b) = partition_bounds(n, porder, p);
        let chunk = &r[a..b];
        let (sum, max) = sum_and_max(chunk);
        let (k, _) = best_param(sum, max, chunk.len());
        w.put(k, 6);
        if k == ESCAPE {
            let width = 64 - max.leading_zeros();
            w.put(width as u64, 7);
            for &v in chunk {
                w.put(v, width);
            }
        } else {
            for &v in chunk {
                w.put_rice(v, k as u32);
            }
        }
    }
}

fn decode_ints(bytes: &[u8], n: usize) -> Result<Vec<i64>, CodecError> {
    decode_stream(bytes, n, false)
}

/// Decodes one stream of `n` integers. With `lpc` (version 5) the predictor field may also be
/// [`g21::PREDICTOR_LPC`], whose parameters follow the partition order.
pub(crate) fn decode_stream(bytes: &[u8], n: usize, lpc: bool) -> Result<Vec<i64>, CodecError> {
    let mut rd = BitReader::new(bytes);
    let order = rd.get(3)?;
    let porder = rd.get(4)? as u32;
    let is_lpc = lpc && order == g21::PREDICTOR_LPC;
    if (order as usize > MAX_ORDER && !is_lpc) || porder > MAX_PARTITION_ORDER || (n >> porder) == 0
    {
        return Err(CodecError::Corrupt("predictor header"));
    }
    let model = if is_lpc {
        Some(g21::Lpc::read(&mut rd)?)
    } else {
        None
    };
    let order = order as usize;
    // Residuals first, then the prediction in a second pass with the predictor fixed for the chunk.
    let mut x = Vec::with_capacity(n);
    for p in 0..(1usize << porder) {
        let (a, b) = partition_bounds(n, porder, p);
        let k = rd.get(6)?;
        if k == ESCAPE {
            let width = rd.get(7)? as u32;
            if width > 64 {
                return Err(CodecError::Corrupt("escape width"));
            }
            for _ in a..b {
                x.push(unzigzag(rd.get(width)?));
            }
        } else {
            for _ in a..b {
                x.push(unzigzag(rd.get_rice(k as u32)?));
            }
        }
    }
    if !rd.rest_is_padding() {
        return Err(CodecError::Corrupt("trailing data"));
    }
    if let Some(model) = model {
        model.restore(&mut x);
        return Ok(x);
    }
    let warm = order.min(n);
    for i in 0..warm {
        x[i] = x[i].wrapping_add(predict(&x, i, order));
    }
    match order {
        0 => {}
        1 => {
            for i in warm..n {
                x[i] = x[i].wrapping_add(x[i - 1]);
            }
        }
        2 => {
            for i in warm..n {
                x[i] = x[i].wrapping_add(x[i - 1].wrapping_mul(2).wrapping_sub(x[i - 2]));
            }
        }
        3 => {
            for i in warm..n {
                x[i] = x[i].wrapping_add(
                    x[i - 1]
                        .wrapping_sub(x[i - 2])
                        .wrapping_mul(3)
                        .wrapping_add(x[i - 3]),
                );
            }
        }
        _ => {
            for i in warm..n {
                x[i] = x[i].wrapping_add(
                    x[i - 1]
                        .wrapping_add(x[i - 3])
                        .wrapping_mul(4)
                        .wrapping_sub(x[i - 2].wrapping_mul(6))
                        .wrapping_sub(x[i - 4]),
                );
            }
        }
    }
    Ok(x)
}

pub(crate) fn put_varint(out: &mut Vec<u8>, mut v: u64) {
    while v >= 0x80 {
        out.push((v as u8) | 0x80);
        v >>= 7;
    }
    out.push(v as u8);
}
pub(crate) fn get_varint(bytes: &[u8], pos: &mut usize) -> Result<u64, CodecError> {
    let mut v = 0u64;
    for shift in (0..64).step_by(7) {
        let b = *bytes.get(*pos).ok_or(CodecError::Corrupt("truncated"))?;
        *pos += 1;
        v |= ((b & 0x7f) as u64) << shift;
        if b < 0x80 {
            return Ok(v);
        }
    }
    Err(CodecError::Corrupt("varint"))
}

#[derive(Default)]
pub(crate) struct BitWriter {
    out: Vec<u8>,
    acc: u64,
    bits: u32,
}
/// Holds fewer than 32 pending bits in `acc` between calls and emits whole 32-bit words, so a call adds at
/// most 32 bits without overflowing the 64-bit accumulator. The bytes are the MSB-first bit sequence of
/// the calls, as a byte-at-a-time writer would produce.
impl BitWriter {
    /// Appends the low `n` bits of `v`, most significant first.
    #[inline(always)]
    pub(crate) fn put(&mut self, v: u64, n: u32) {
        if n > 32 {
            self.put(v >> 32, n - 32);
            self.put(v & 0xffff_ffff, 32);
            return;
        }
        let v = if n == 0 {
            0
        } else {
            v & (u64::MAX >> (64 - n))
        };
        self.push_bits(v, n);
    }

    /// Appends `n` <= 32 bits `v` (already masked) and emits a word once 32 or more are pending.
    #[inline(always)]
    fn push_bits(&mut self, v: u64, n: u32) {
        self.acc = (self.acc << n) | v;
        self.bits += n;
        if self.bits >= 32 {
            self.bits -= 32;
            let word = (self.acc >> self.bits) as u32;
            self.out.extend_from_slice(&word.to_be_bytes());
            self.acc &= (1u64 << self.bits) - 1;
        }
    }

    /// A Rice code with parameter `k`: `v >> k` zero bits, a one bit, then the low `k` bits of `v`. The
    /// same bits as `put_unary(v >> k)` then `put(v, k)`, written in one step when they fit in 32 bits.
    #[inline(always)]
    pub(crate) fn put_rice(&mut self, v: u64, k: u32) {
        let q = v >> k;
        if q + 1 + (k as u64) <= 32 {
            let low = if k == 0 {
                0
            } else {
                v & (u64::MAX >> (64 - k))
            };
            // q zeros, then 1, then k bits: the value (1 << k) | low in q + 1 + k bits.
            self.push_bits((1u64 << k) | low, q as u32 + 1 + k);
            return;
        }
        self.put_unary(q);
        self.put(v, k);
    }

    #[inline(always)]
    fn put_unary(&mut self, mut q: u64) {
        while q >= 32 {
            self.put(0, 32);
            q -= 32;
        }
        self.put(1, q as u32 + 1);
    }
    fn finish(mut self) -> Vec<u8> {
        while self.bits >= 8 {
            self.bits -= 8;
            self.out.push((self.acc >> self.bits) as u8);
        }
        if self.bits > 0 {
            self.out.push((self.acc << (8 - self.bits)) as u8);
        }
        self.out
    }
}

/// MSB-first bit reader over a byte slice. Reads take a 64-bit big-endian window at the current
/// byte, so `get` costs a few instructions and unary codes are counted with `leading_zeros`.
/// Reading past the end is an error ("truncated bitstream"), as with a bit-by-bit reader.
pub(crate) struct BitReader<'a> {
    bytes: &'a [u8],
    pos: usize,
}
impl<'a> BitReader<'a> {
    fn new(bytes: &'a [u8]) -> Self {
        Self { bytes, pos: 0 }
    }
    /// The 64 bits starting at the current byte; bytes past the end read as zero.
    #[inline(always)]
    fn window(&self) -> u64 {
        let i = self.pos / 8;
        match self.bytes.get(i..i + 8) {
            Some(b) => u64::from_be_bytes(b.try_into().unwrap()),
            None => {
                let mut buf = [0u8; 8];
                let tail = self.bytes.get(i..).unwrap_or(&[]);
                buf[..tail.len()].copy_from_slice(tail);
                u64::from_be_bytes(buf)
            }
        }
    }
    #[inline]
    fn remaining(&self) -> usize {
        (self.bytes.len() * 8).saturating_sub(self.pos)
    }
    #[inline(always)]
    pub(crate) fn get(&mut self, n: u32) -> Result<u64, CodecError> {
        if n == 0 {
            return Ok(0);
        }
        if n as usize > self.remaining() {
            return Err(CodecError::Corrupt("truncated bitstream"));
        }
        if n > 56 {
            let hi = self.get(n - 32)?;
            return Ok((hi << 32) | self.get(32)?);
        }
        let v = (self.window() << (self.pos % 8)) >> (64 - n);
        self.pos += n as usize;
        Ok(v)
    }
    /// A Rice code with parameter k: unary quotient, then k remainder bits. Within one 64-bit window
    /// it is read with one load; otherwise, and for every error, as `get_unary` then `get(k)`.
    #[inline(always)]
    fn get_rice(&mut self, k: u32) -> Result<u64, CodecError> {
        let avail = self.remaining().min(57);
        let w = self.window() << (self.pos % 8);
        let zeros = w.leading_zeros() as usize;
        if zeros < avail && zeros + 1 + k as usize <= avail {
            let q = zeros as u64;
            if q > (u64::MAX >> k) {
                return Err(CodecError::Corrupt("rice quotient"));
            }
            let r = if k == 0 {
                0
            } else {
                (w << (zeros + 1)) >> (64 - k)
            };
            self.pos += zeros + 1 + k as usize;
            return Ok((q << k) | r);
        }
        let q = self.get_unary()?;
        if q > (u64::MAX >> k) {
            return Err(CodecError::Corrupt("rice quotient"));
        }
        Ok((q << k) | self.get(k)?)
    }
    #[inline]
    fn get_unary(&mut self) -> Result<u64, CodecError> {
        let mut q = 0u64;
        loop {
            let avail = self.remaining().min(57);
            if avail == 0 {
                return Err(CodecError::Corrupt("truncated bitstream"));
            }
            let zeros = (self.window() << (self.pos % 8)).leading_zeros() as usize;
            if zeros < avail {
                self.pos += zeros + 1;
                return Ok(q + zeros as u64);
            }
            q += avail as u64;
            self.pos += avail;
        }
    }
    fn rest_is_padding(&self) -> bool {
        let used = self.pos.div_ceil(8);
        used == self.bytes.len()
            && (self.pos.is_multiple_of(8) || self.bytes[used - 1] & (0xff >> (self.pos % 8)) == 0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // The original exhaustive encoder search, kept as an oracle: the optimised one must write
    // byte-identical streams.
    fn best_param_scan(sum: u128, max: u64, count: usize) -> (u64, u128) {
        let m = count as u128;
        let mut best = (0, u128::MAX);
        for k in 0..ESCAPE {
            // floor(v >> k) summed is within `count` of (sum >> k).
            let cost = m * (k as u128 + 1) + (sum >> k);
            if cost < best.1 {
                best = (k, cost);
            }
        }
        let width = 64 - max.leading_zeros() as u128;
        let escape = 7 + m * width;
        if escape < best.1 {
            best = (ESCAPE, escape);
        }
        (best.0, best.1 + 6)
    }

    fn _unused_partition_bounds(n: usize, porder: u32, p: usize) -> (usize, usize) {
        ((p * n) >> porder, ((p + 1) * n) >> porder)
    }

    fn encode_ints_reference(x: &[i64], out: &mut Vec<u8>) {
        let n = x.len();
        let max_porder = MAX_PARTITION_ORDER.min(usize::BITS - 1 - n.leading_zeros());
        let mut best: Option<(u128, usize, u32, Vec<u64>)> = None;
        for order in 0..=MAX_ORDER {
            let r: Vec<u64> = (0..n)
                .map(|i| zigzag(x[i].wrapping_sub(predict(x, i, order))))
                .collect();
            for porder in 0..=max_porder {
                let parts = 1usize << porder;
                let mut cost = 0u128;
                for p in 0..parts {
                    let (a, b) = partition_bounds(n, porder, p);
                    let chunk = &r[a..b];
                    let sum = chunk.iter().map(|&v| v as u128).sum();
                    let max = chunk.iter().copied().max().unwrap_or(0);
                    cost += best_param_scan(sum, max, chunk.len()).1;
                }
                if best.as_ref().is_none_or(|b| cost < b.0) {
                    best = Some((cost, order, porder, r.clone()));
                }
            }
        }
        let (_, order, porder, r) = best.expect("at least one candidate");
        let mut w = BitWriter::default();
        w.put(order as u64, 3);
        w.put(porder as u64, 4);
        for p in 0..(1usize << porder) {
            let (a, b) = partition_bounds(n, porder, p);
            let chunk = &r[a..b];
            let sum = chunk.iter().map(|&v| v as u128).sum();
            let max = chunk.iter().copied().max().unwrap_or(0);
            let (k, _) = best_param_scan(sum, max, chunk.len());
            w.put(k, 6);
            if k == ESCAPE {
                let width = 64 - max.leading_zeros();
                w.put(width as u64, 7);
                for &v in chunk {
                    w.put(v, width);
                }
            } else {
                for &v in chunk {
                    w.put_unary(v >> k);
                    w.put(v, k as u32);
                }
            }
        }
        out.extend(w.finish());
    }

    #[test]
    fn encoder_search_writes_the_reference_bytes() {
        let mut seed = 5u64;
        for case in 0..400 {
            let n = 1 + (lcg(&mut seed) % 5000) as usize;
            let amp = 1i64 << (lcg(&mut seed) % 62);
            let mut walk = 0i64;
            let x: Vec<i64> = (0..n)
                .map(|i| match case % 4 {
                    0 => (lcg(&mut seed) as i64) % amp,
                    1 => {
                        walk = walk.wrapping_add((lcg(&mut seed) % 64) as i64 - 32);
                        walk
                    }
                    2 => ((i as f64 * 0.01).sin() * 30000.0) as i64,
                    _ => lcg(&mut seed) as i64,
                })
                .collect();
            let (mut a, mut b) = (Vec::new(), Vec::new());
            encode_ints(&x, &mut a);
            encode_ints_reference(&x, &mut b);
            assert_eq!(a, b, "case {case}, n {n}");
            for (sum, max, count) in [
                (0u128, 0u64, 1usize),
                (u64::MAX as u128 * 4096, u64::MAX, 4096),
                (12345, 900, 7),
            ] {
                assert_eq!(
                    best_param(sum, max, count),
                    best_param_scan(sum, max, count)
                );
            }
        }
    }

    /// The word-emitting writer produces the MSB-first bit sequence of its calls, as a bit-by-bit one.
    #[test]
    fn bit_writer_matches_a_bit_by_bit_writer() {
        let mut seed = 13u64;
        for _ in 0..300 {
            let mut w = BitWriter::default();
            let mut bits: Vec<bool> = Vec::new();
            for _ in 0..(lcg(&mut seed) % 400) {
                let v = lcg(&mut seed) << 11 | lcg(&mut seed);
                match lcg(&mut seed) % 3 {
                    0 => {
                        let n = (lcg(&mut seed) % 65) as u32;
                        w.put(v, n);
                        bits.extend((0..n).rev().map(|b| v >> b & 1 == 1));
                    }
                    1 => {
                        let k = (lcg(&mut seed) % 30) as u32;
                        let q = lcg(&mut seed) % 70;
                        let v = (q << k) | (v & ((1u64 << k) - 1));
                        w.put_rice(v, k);
                        bits.extend(std::iter::repeat_n(false, (v >> k) as usize));
                        bits.push(true);
                        bits.extend((0..k).rev().map(|b| v >> b & 1 == 1));
                    }
                    _ => {
                        let q = lcg(&mut seed) % 100;
                        w.put_unary(q);
                        bits.extend(std::iter::repeat_n(false, q as usize));
                        bits.push(true);
                    }
                }
            }
            let want: Vec<u8> = bits
                .chunks(8)
                .map(|c| {
                    c.iter()
                        .enumerate()
                        .fold(0u8, |a, (i, &b)| a | (b as u8) << (7 - i))
                })
                .collect();
            assert_eq!(w.finish(), want);
        }
    }

    /// The fast Rice-parameter search (u64 path, walk from a lower bound) equals the full scan.
    #[test]
    fn rice_parameter_search_matches_the_full_scan() {
        let mut seed = 9u64;
        for _ in 0..20000 {
            let count = 1 + (lcg(&mut seed) % 32768) as usize;
            let bits = lcg(&mut seed) % 128;
            let sum = match lcg(&mut seed) % 4 {
                0 => (lcg(&mut seed) as u128) % (1u128 << (bits % 70)),
                1 => (1u128 << 62) - (lcg(&mut seed) % 3) as u128,
                2 => (count as u128) * (lcg(&mut seed) as u128 % 64),
                _ => ((lcg(&mut seed) as u128) << 64 | lcg(&mut seed) as u128) >> (bits % 64),
            };
            let max = lcg(&mut seed) >> (lcg(&mut seed) % 64);
            assert_eq!(
                best_param(sum, max, count),
                best_param_scan(sum, max, count),
                "sum {sum} count {count} max {max}"
            );
        }
    }

    #[test]
    fn detected_grids_compress_calibrated_floats_and_roundtrip() {
        let mut seed = 41u64;
        let steps = [
            100.0 / 32768.0,
            0.000_688_781_737_8,
            0.001,
            0.25,
            1.0 / 3.0,
            9.81 / 65536.0,
        ];
        for (i, &q) in steps.iter().enumerate() {
            let offset = if i % 2 == 0 { 0.0 } else { -3.7 };
            let mut k = 0i64;
            let x: Vec<f64> = (0..4096)
                .map(|_| {
                    k += (lcg(&mut seed) % 41) as i64 - 20;
                    offset + k as f64 * q
                })
                .collect();
            // declared quantum 1, as a client without calibration knowledge would send
            let enc = encode(&x, 1.0).unwrap();
            let back = decode(&enc).unwrap();
            assert!(
                back.iter().zip(&x).all(|(a, b)| a.to_bits() == b.to_bits()),
                "step {q}"
            );
            assert_eq!(kind(&enc).unwrap(), Kind::Grid, "step {q}");
            let mut float = vec![BASE_VERSION];
            encode_off_grid(&x, &mut float);
            assert!(
                enc.len() * 2 < float.len(),
                "step {q}: {} vs {} bytes",
                enc.len(),
                float.len()
            );
        }
    }

    #[test]
    fn detected_grids_with_last_place_noise_roundtrip_exactly() {
        let mut seed = 47u64;
        for case in 0..300 {
            let q = 10f64.powi(-((lcg(&mut seed) % 7) as i32))
                * (1.0 + (lcg(&mut seed) % 997) as f64 / 1000.0);
            let n = 3 + (lcg(&mut seed) % 4096) as usize;
            let mut k = (lcg(&mut seed) % 1000) as i64 - 500;
            let x: Vec<f64> = (0..n)
                .map(|_| {
                    k += (lcg(&mut seed) % 21) as i64 - 10;
                    let v = k as f64 * q;
                    match lcg(&mut seed) % 50 {
                        0 => f64::from_bits(v.to_bits().wrapping_add(1)),
                        1 => f64::from_bits(v.to_bits().wrapping_sub(1)),
                        _ => v,
                    }
                })
                .filter(|v| v.is_finite())
                .collect();
            let enc = encode(&x, 1.0).unwrap();
            let back = decode(&enc).unwrap();
            assert!(
                back.iter().zip(&x).all(|(a, b)| a.to_bits() == b.to_bits()),
                "case {case}, q {q}"
            );
        }
    }

    #[test]
    fn detection_never_loses_to_the_previous_encoding() {
        let mut seed = 43u64;
        for case in 0..200 {
            let n = 3 + (lcg(&mut seed) % 4096) as usize;
            let x: Vec<f64> = (0..n)
                .map(|i| match case % 4 {
                    0 => ((lcg(&mut seed) >> 11) as f64 / (1u64 << 53) as f64 - 0.5) * 1e3,
                    1 => (i as f64 * 0.01).sin(),
                    2 => {
                        ((lcg(&mut seed) % 1000) as f64) * 0.1 + 1e-9 * (lcg(&mut seed) % 3) as f64
                    }
                    _ => ((lcg(&mut seed) % 50) as f64 - 25.0) * 0.003 + 1e6,
                })
                .collect();
            let enc = encode(&x, 1.0).unwrap();
            let back = decode(&enc).unwrap();
            assert!(
                back.iter().zip(&x).all(|(a, b)| a.to_bits() == b.to_bits()),
                "case {case}"
            );
            let grid = Grid::new(1.0).unwrap();
            let previous = match x
                .iter()
                .map(|&v| grid.index(v))
                .collect::<Option<Vec<i64>>>()
            {
                Some(ints) => encode_version2(&x, grid, ints, None),
                None => encode_offset_grid(&x, grid, None).unwrap_or_else(|| {
                    let mut out = vec![BASE_VERSION];
                    encode_off_grid(&x, &mut out);
                    out
                }),
            };
            assert!(
                enc.len() <= previous.len() + 4,
                "case {case}: {} vs {}",
                enc.len(),
                previous.len()
            );
        }
    }

    // The previous decoder (bit-by-bit reader, per-sample prediction) as an oracle.
    fn decode_ints_reference(bytes: &[u8], n: usize) -> Result<Vec<i64>, CodecError> {
        let mut rd = SlowBitReader { bytes, pos: 0 };
        let order = rd.get(3)? as usize;
        let porder = rd.get(4)? as u32;
        if order > MAX_ORDER || porder > MAX_PARTITION_ORDER || (n >> porder) == 0 {
            return Err(CodecError::Corrupt("predictor header"));
        }
        let mut x = Vec::with_capacity(n);
        for p in 0..(1usize << porder) {
            let (a, b) = partition_bounds(n, porder, p);
            let k = rd.get(6)?;
            let width = if k == ESCAPE {
                let width = rd.get(7)? as u32;
                if width > 64 {
                    return Err(CodecError::Corrupt("escape width"));
                }
                width
            } else {
                0
            };
            for i in a..b {
                let v = if k == ESCAPE {
                    rd.get(width)?
                } else {
                    let q = rd.get_unary()?;
                    if q > (u64::MAX >> k) {
                        return Err(CodecError::Corrupt("rice quotient"));
                    }
                    (q << k) | rd.get(k as u32)?
                };
                let value = unzigzag(v).wrapping_add(predict(&x, i, order));
                x.push(value);
            }
        }
        if !rd.rest_is_padding() {
            return Err(CodecError::Corrupt("trailing data"));
        }
        Ok(x)
    }
    impl SlowBitReader<'_> {
        fn rest_is_padding(&self) -> bool {
            let used = self.pos.div_ceil(8);
            used == self.bytes.len()
                && (self.pos.is_multiple_of(8)
                    || self.bytes[used - 1] & (0xff >> (self.pos % 8)) == 0)
        }
    }

    #[test]
    fn fast_decoder_matches_reference_on_valid_and_corrupt_streams() {
        let mut seed = 17u64;
        for case in 0..600 {
            let n = 1 + (lcg(&mut seed) % 5000) as usize;
            let amp = 1i64 << (lcg(&mut seed) % 62);
            let mut walk = 0i64;
            let x: Vec<i64> = (0..n)
                .map(|i| match case % 4 {
                    0 => (lcg(&mut seed) as i64) % amp,
                    1 => {
                        walk = walk.wrapping_add((lcg(&mut seed) % 64) as i64 - 32);
                        walk
                    }
                    2 => ((i as f64 * 0.01).sin() * 30000.0) as i64,
                    _ => lcg(&mut seed) as i64,
                })
                .collect();
            let mut enc = Vec::new();
            encode_ints(&x, &mut enc);
            assert_eq!(decode_ints(&enc, n), Ok(x.clone()), "case {case}");
            assert_eq!(decode_ints(&enc, n), decode_ints_reference(&enc, n));
            // corrupt: flip a byte, truncate, or ask for another length
            let mut bad = enc.clone();
            let at = (lcg(&mut seed) as usize) % bad.len();
            bad[at] ^= 1 << (lcg(&mut seed) % 8);
            assert_eq!(
                decode_ints(&bad, n),
                decode_ints_reference(&bad, n),
                "flip {case}"
            );
            let cut = &enc[..(lcg(&mut seed) as usize) % enc.len()];
            assert_eq!(
                decode_ints(cut, n),
                decode_ints_reference(cut, n),
                "cut {case}"
            );
            let m = 1 + (lcg(&mut seed) % 5000) as usize;
            assert_eq!(
                decode_ints(&enc, m),
                decode_ints_reference(&enc, m),
                "len {case}"
            );
        }
        for _ in 0..3000 {
            let len = (lcg(&mut seed) % 64) as usize;
            let junk: Vec<u8> = (0..len).map(|_| lcg(&mut seed) as u8).collect();
            let m = 1 + (lcg(&mut seed) % 300) as usize;
            assert_eq!(decode_ints(&junk, m), decode_ints_reference(&junk, m));
        }
    }

    #[test]
    fn sliced_crc32c_matches_bytewise_definition() {
        let bytewise = |b: &[u8]| {
            !b.iter().fold(!0u32, |c, &x| {
                CRC32C_TABLE[((c ^ x as u32) & 0xff) as usize] ^ (c >> 8)
            })
        };
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
        let mut seed = 3u64;
        for len in 0..300 {
            let b: Vec<u8> = (0..len).map(|_| lcg(&mut seed) as u8).collect();
            assert_eq!(crc32c(&b), bytewise(&b), "len {len}");
        }
    }

    /// The original bit-by-bit reader, kept as an oracle for the word-based one.
    struct SlowBitReader<'a> {
        bytes: &'a [u8],
        pos: usize,
    }
    impl SlowBitReader<'_> {
        fn bit(&mut self) -> Result<u64, CodecError> {
            let byte = *self
                .bytes
                .get(self.pos / 8)
                .ok_or(CodecError::Corrupt("truncated bitstream"))?;
            let b = (byte >> (7 - self.pos % 8)) & 1;
            self.pos += 1;
            Ok(b as u64)
        }
        fn get(&mut self, n: u32) -> Result<u64, CodecError> {
            let mut v = 0;
            for _ in 0..n {
                v = (v << 1) | self.bit()?;
            }
            Ok(v)
        }
        fn get_unary(&mut self) -> Result<u64, CodecError> {
            let mut q = 0;
            while self.bit()? == 0 {
                q += 1;
            }
            Ok(q)
        }
    }

    #[test]
    fn word_bit_reader_matches_bit_by_bit_reader() {
        let mut seed = 99u64;
        for case in 0..3000 {
            let len = (lcg(&mut seed) % 40) as usize;
            let mut bytes: Vec<u8> = (0..len).map(|_| lcg(&mut seed) as u8).collect();
            if case % 3 == 0 {
                for b in bytes.iter_mut().take(len / 2) {
                    *b = 0; // long unary runs
                }
            }
            let (mut fast, mut slow) = (
                BitReader::new(&bytes),
                SlowBitReader {
                    bytes: &bytes,
                    pos: 0,
                },
            );
            for _ in 0..64 {
                let op = lcg(&mut seed) % 3;
                let n = (lcg(&mut seed) % 65) as u32;
                let (a, b) = if op == 0 {
                    (fast.get_unary(), slow.get_unary())
                } else {
                    (fast.get(n), slow.get(n))
                };
                match (a, b) {
                    (Ok(x), Ok(y)) => {
                        assert_eq!(x, y, "case {case}");
                        assert_eq!(fast.pos, slow.pos, "case {case}");
                    }
                    (Err(_), Err(_)) => break,
                    (x, y) => panic!("case {case}: {x:?} vs {y:?}"),
                }
            }
        }
    }

    fn lcg(seed: &mut u64) -> u64 {
        *seed = seed
            .wrapping_mul(6364136223846793005)
            .wrapping_add(1442695040888963407);
        *seed >> 11
    }
    fn roundtrip(samples: &[f64], quantum: f64) -> (Kind, usize) {
        let encoded = encode(samples, quantum).unwrap();
        let decoded = decode(&encoded).unwrap();
        assert_eq!(decoded.len(), samples.len());
        for (a, b) in decoded.iter().zip(samples) {
            assert_eq!(a.to_bits(), b.to_bits());
        }
        (kind(&encoded).unwrap(), encoded.len())
    }

    #[test]
    fn integer_counts_use_the_grid_and_compress() {
        let mut seed = 1;
        let samples: Vec<f64> = (0..32768)
            .map(|i| ((i as f64 / 40.).sin() * 3000. + (lcg(&mut seed) % 64) as f64 - 32.).round())
            .collect();
        let (kind, bytes) = roundtrip(&samples, 1.0);
        assert_eq!(kind, Kind::Grid);
        // Uniform 64-level noise costs about 8 bits/sample after prediction; float8 costs 64.
        assert!(bytes * 8 < samples.len() * 10, "{bytes} bytes");
    }

    #[test]
    fn decimal_and_dyadic_grids_are_exact() {
        // Parsed decimals: k * 0.001 differs from the parsed value for some k; k / 1000 does not.
        let decimals: Vec<f64> = (-3000..3000)
            .map(|k| format!("{:.3}", k as f64 / 1000.).parse().unwrap())
            .collect();
        assert!(decimals
            .iter()
            .enumerate()
            .any(|(i, &x)| ((i as i64 - 3000) as f64 * 0.001).to_bits() != x.to_bits()));
        assert_eq!(roundtrip(&decimals, 0.001).0, Kind::Grid);
        let step = 100.0 / 32768.0;
        let adc: Vec<f64> = (-2000..2000).map(|k| k as f64 * step).collect();
        assert_eq!(roundtrip(&adc, step).0, Kind::Grid);
    }

    #[test]
    fn calibrated_and_rounded_floats_stay_on_the_grid() {
        let mut seed = 11;
        let nominal = 100.0 / 32768.0;
        let calibrated = nominal * (1.0 - 7e-10);
        let counts: Vec<i64> = (0..32768)
            .map(|i| ((i as f64 / 50.).sin() * 2000.) as i64 + (lcg(&mut seed) % 33) as i64 - 16)
            .collect();
        let exact: Vec<f64> = counts.iter().map(|&k| k as f64 * calibrated).collect();
        let (kind, exact_bytes) = roundtrip(&exact, nominal);
        assert_eq!(kind, Kind::Grid);
        assert!(exact_bytes * 8 < 32768 * 8, "{exact_bytes}");
        // One-ulp noise, as from float32 or a second rounding step, costs corrections only.
        let noisy: Vec<f64> = exact
            .iter()
            .map(|&x| match lcg(&mut seed) % 4 {
                0 => f64::from_bits(x.to_bits() + 1),
                1 if x != 0.0 => f64::from_bits(x.to_bits() - 1),
                _ => x,
            })
            .collect();
        let (kind, noisy_bytes) = roundtrip(&noisy, nominal);
        assert_eq!(kind, Kind::Grid);
        assert!(noisy_bytes < exact_bytes + 32768 / 8 * 3, "{noisy_bytes}");
    }

    /// EDF physical values, computed as Sleep-EDF readers do:
    /// (d - dmin) * (pmax - pmin) / (dmax - dmin) + pmin.
    fn edf(pmin: f64, pmax: f64, n: usize, seed: &mut u64) -> (Vec<f64>, f64) {
        let (dmin, dmax) = (-2048.0, 2047.0);
        let mut d = 0i64;
        let x = (0..n)
            .map(|i| {
                d = (d + (lcg(seed) % 41) as i64 - 20).clamp(-2048, 2047);
                let d = d + ((i as f64 / 30.).sin() * 300.) as i64;
                (d.clamp(-2048, 2047) as f64 - dmin) * (pmax - pmin) / (dmax - dmin) + pmin
            })
            .collect();
        (x, (pmax - pmin) / (dmax - dmin))
    }

    #[test]
    fn asymmetric_edf_ranges_use_an_offset_grid() {
        let mut seed = 21;
        let (x, step) = edf(-208.0, 209.0, 4096, &mut seed);
        // The offset (5.41 steps) puts the samples on no grid through zero ...
        let grid = Grid::new(step).unwrap();
        assert!(x.iter().any(|&v| grid.index(v).is_none()));
        let mut plain = vec![BASE_VERSION];
        encode_off_grid(&x, &mut plain);
        assert_eq!(plain[1], KIND_FLOAT);
        // ... but on step and offset they are integers: version 3, a fraction of float coding.
        for quantum in [step, step / 2.0] {
            let encoded = encode(&x, quantum).unwrap();
            assert_eq!(encoded[0], FORMAT_VERSION);
            let (kind, bytes) = roundtrip(&x, quantum);
            assert_eq!(kind, Kind::Grid);
            assert!(
                bytes * 8 < 4096 * 12 && bytes * 3 < plain.len(),
                "{bytes} vs {}",
                plain.len()
            );
        }
    }

    #[test]
    fn chunks_on_the_plain_grid_keep_version_2() {
        let mut seed = 22;
        // Symmetric range: a half-step grid through zero, version 2.
        let (x, step) = edf(-192.0, 192.0, 4096, &mut seed);
        assert_eq!(encode(&x, step / 2.0).unwrap()[0], BASE_VERSION);
        assert_eq!(roundtrip(&x, step / 2.0).0, Kind::Grid);
        // Exact grids keep their version-2 bytes.
        let counts: Vec<f64> = (0..4096)
            .map(|i| ((i * 37) % 1001) as f64 - 500.0)
            .collect();
        assert_eq!(encode(&counts, 1.0).unwrap()[0], BASE_VERSION);
        let decimals: Vec<f64> = (0..2000)
            .map(|k| format!("{:.3}", k as f64 / 1000.).parse().unwrap())
            .collect();
        assert_eq!(encode(&decimals, 0.001).unwrap()[0], BASE_VERSION);
    }

    #[test]
    fn offset_grids_roundtrip_signs_decimals_and_extremes() {
        // Decimal grid shifted by 0.4 of a step, in division mode.
        let shifted: Vec<f64> = (-3000..3000).map(|k| 0.0004 + k as f64 / 1000.).collect();
        let encoded = encode(&shifted, 0.001).unwrap();
        assert_eq!(
            (encoded[0], roundtrip(&shifted, 0.001).0),
            (FORMAT_VERSION, Kind::Grid)
        );
        // Negative zero and both range ends inside an offset chunk.
        let mut x: Vec<f64> = (0..512).map(|k| 0.25 + k as f64).collect();
        x[7] = -0.0;
        x[8] = 1e9 - 0.75;
        x[9] = -1e9 + 0.25;
        roundtrip(&x, 1.0);
        for n in [1, 2, 3, 257] {
            roundtrip(&vec![0.3; n], 1.0);
            roundtrip(&(0..n).map(|k| 0.3 + k as f64).collect::<Vec<_>>(), 1.0);
        }
    }

    #[test]
    fn malformed_version3_chunks_are_rejected() {
        let x: Vec<f64> = (0..100).map(|k| 0.25 + (k % 9) as f64).collect();
        let good = encode(&x, 1.0).unwrap();
        assert_eq!(good[0], FORMAT_VERSION);
        let body = &good[..good.len() - CHECKSUM_BYTES];
        // Non-finite offset (bytes 12..20: version, kind, n, mode, scale, offset).
        let mut bad = body.to_vec();
        bad[12..20].copy_from_slice(&f64::NAN.to_le_bytes());
        assert_eq!(decode(&seal(bad)), Err(CodecError::Corrupt("grid header")));
        // Version 3 carries grids only.
        for k in [KIND_FLOAT, KIND_RAW] {
            let mut bad = body.to_vec();
            bad[1] = k;
            assert_eq!(decode(&seal(bad)), Err(CodecError::Corrupt("unknown kind")));
        }
        // A version-2 reader layout (no offset) misparses and is caught.
        let mut bad = body.to_vec();
        bad[0] = BASE_VERSION;
        assert!(decode(&seal(bad)).is_err());
    }

    #[test]
    fn off_grid_chunks_code_float_bits_or_fall_back_to_raw() {
        let mut samples: Vec<f64> = (0..1000).map(|i| i as f64).collect();
        samples[500] = 0.5;
        // Every value is on the 0.5 grid, which the encoder detects without a declared quantum.
        assert_eq!(roundtrip(&samples, 1.0).0, Kind::Grid);
        samples[500] = 0.123_456_789;
        assert_eq!(roundtrip(&samples, 1.0).0, Kind::Float);
        for x in [
            0.0,
            -0.0,
            1e-300,
            -1e-300,
            1e9,
            -1e9,
            f64::MIN_POSITIVE,
            -5e-324,
        ] {
            assert_eq!(float_from_ordered(ordered_bits(x)).to_bits(), x.to_bits());
        }
        assert!(ordered_bits(-1.0) < ordered_bits(-0.0) && ordered_bits(-0.0) < ordered_bits(0.0));
        // Negative zeros stay on the grid and keep their sign bit.
        assert_eq!(roundtrip(&[1.0, -0.0, 2.0, -0.0, -0.0], 1.0).0, Kind::Grid);
        roundtrip(&[-0.0], 0.001);
        assert_eq!(roundtrip(&[-0.0; 64], 0.001).0, Kind::Grid);
        let smooth: Vec<f64> = (0..4096).map(|i| (i as f64 / 300.).sin() + 2.0).collect();
        let (kind, bytes) = roundtrip(&smooth, 1.0);
        assert!(kind == Kind::Float && bytes < 4 * 4096, "{kind:?} {bytes}");
        // Full-entropy mantissas do not compress: plain float64 bounds the size.
        let mut seed = 3;
        let noise: Vec<f64> = (0..777)
            .map(|_| (lcg(&mut seed) as f64 / 9_007_199_254_740_992.0 - 0.5) * 2e9)
            .collect();
        assert_eq!(
            roundtrip(&noise, 1.0),
            (Kind::Raw, 2 + 2 + 8 * 777 + CHECKSUM_BYTES)
        );
    }

    #[test]
    fn extremes_and_every_partition_shape_roundtrip() {
        let mut seed = 7;
        for n in [1, 2, 3, 5, 255, 256, 257, 4095, 32768] {
            let noise: Vec<f64> = (0..n)
                .map(|_| (lcg(&mut seed) % 2_000_000_001) as f64 - 1e9)
                .collect();
            roundtrip(&noise, 1.0);
            let steps: Vec<f64> = (0..n)
                .map(|i| if i % 97 == 0 { 1e9 } else { -1e9 })
                .collect();
            roundtrip(&steps, 1.0);
            roundtrip(&vec![0.0; n], 1.0);
        }
        // Tiny quanta push indices to the 2^53 limit, beyond it the chunk is raw.
        roundtrip(&[1e9, -1e9, 0.0], 1e-6);
        assert_eq!(roundtrip(&[1e9, 1.0], 1e-9).0, Kind::Raw);
    }

    #[test]
    fn subnormal_steps_roundtrip_without_overflow() {
        // A fitted step of one or two ulps above zero once underflowed the candidate
        // search: a panic with overflow checks, a wrapped (unused) step without them.
        for q in [5e-324, 1e-323, 1.5e-323] {
            for x in [
                vec![5e-324],
                vec![1e-323, 5e-324],
                vec![5e-324, 0.0, -5e-324],
            ] {
                roundtrip(&x, q);
            }
        }
    }

    #[test]
    fn rejects_invalid_quanta() {
        assert_eq!(encode(&[1.0], 0.0), Err(CodecError::InvalidQuantum));
        assert_eq!(encode(&[1.0], f64::NAN), Err(CodecError::InvalidQuantum));
    }

    fn seal(mut body: Vec<u8>) -> Vec<u8> {
        let checksum = crc32c(&body);
        body.extend_from_slice(&checksum.to_le_bytes());
        body
    }

    #[test]
    fn checksum_detects_every_mutation_kind() {
        assert_eq!(crc32c(b"123456789"), 0xE306_9283);
        let mut seed = 5;
        for samples in [
            (0..1000).map(|i| (i % 17) as f64).collect::<Vec<f64>>(),
            (0..1000).map(|i| (i as f64 / 9.).sin()).collect(),
            vec![0.5, -0.0, 1e9],
            (0..1000).map(|i| (i % 17) as f64 + 0.3).collect(),
        ] {
            let good = encode(&samples, 1.0).unwrap();
            // Single-bit flips in the version, kind, header, payload and checksum.
            for byte in 0..good.len() {
                for bit in 0..8 {
                    let mut bad = good.clone();
                    bad[byte] ^= 1 << bit;
                    assert!(decode(&bad).is_err(), "flip {byte}:{bit}");
                    assert!(kind(&bad).is_err());
                }
            }
            // Multi-byte overwrites.
            for _ in 0..2000 {
                let mut bad = good.clone();
                let at = (lcg(&mut seed) as usize) % bad.len();
                let len = 1 + (lcg(&mut seed) as usize) % 8;
                for b in bad.iter_mut().skip(at).take(len) {
                    *b = lcg(&mut seed) as u8;
                }
                if bad != good {
                    assert!(decode(&bad).is_err());
                }
            }
            for cut in 0..good.len() {
                assert!(decode(&good[..cut]).is_err(), "truncated to {cut}");
            }
            for extra in 1..=8 {
                let mut long = good.clone();
                long.extend(std::iter::repeat_n(0u8, extra));
                assert!(decode(&long).is_err());
            }
        }
    }

    #[test]
    fn checksum_catches_mutations_that_still_decode_to_valid_samples() {
        let samples = [0.25f64, 0.5, 0.75];
        let raw = seal({
            let mut body = vec![BASE_VERSION, KIND_RAW, 3];
            body.extend(samples.iter().flat_map(|x| x.to_le_bytes()));
            body
        });
        let mut bad = raw.clone();
        bad[3] ^= 1; // lowest mantissa bit of the first sample
        let body = &bad[..bad.len() - CHECKSUM_BYTES];
        // Structurally the mutated body still decodes to valid, different samples ...
        assert!(decode(&seal(body.to_vec())).unwrap()[0] != samples[0]);
        // ... so only the checksum detects it.
        assert_eq!(decode(&bad), Err(CodecError::Corrupt("checksum mismatch")));
    }

    #[test]
    fn version_one_is_rejected_not_reinterpreted() {
        let mut v1 = encode(&[1.0, 2.0, 3.0], 1.0).unwrap();
        v1.truncate(v1.len() - CHECKSUM_BYTES);
        v1[0] = 1;
        assert_eq!(decode(&v1), Err(CodecError::UnsupportedVersion(1)));
        assert_eq!(kind(&v1), Err(CodecError::UnsupportedVersion(1)));
        assert_eq!(
            decode(&[4, 1, 1, 0, 0, 0, 0]),
            Err(CodecError::Corrupt("unknown format version"))
        );
    }

    #[test]
    fn structural_checks_still_apply_behind_a_valid_checksum() {
        assert!(matches!(encode(&[], 1.0), Err(CodecError::Signal(_))));
        assert!(matches!(
            encode(&[f64::INFINITY], 1.0),
            Err(CodecError::Signal(_))
        ));
        let mut raw = vec![BASE_VERSION, KIND_RAW, 2];
        raw.extend(f64::INFINITY.to_le_bytes());
        raw.extend(1.5f64.to_le_bytes());
        assert!(matches!(
            decode(&seal(raw.clone())),
            Err(CodecError::Signal(_))
        ));
        raw.pop();
        assert_eq!(decode(&seal(raw)), Err(CodecError::Corrupt("raw length")));
        assert_eq!(
            decode(&seal(vec![BASE_VERSION, 9, 1])),
            Err(CodecError::Corrupt("unknown kind"))
        );
        let grid = encode(&[1.0, 2.0, 3.0, 4.0], 1.0).unwrap();
        let mut body = grid[..grid.len() - CHECKSUM_BYTES].to_vec();
        body.push(0);
        assert!(decode(&seal(body)).is_err());
    }
}

/// Bounded model checking (Kani). Statements, bounds and run times: docs/verification.md.
#[cfg(kani)]
mod proofs {
    use super::*;

    /// Every f64 bit pattern maps to an i64 and back unchanged, and every i64 back to itself.
    #[kani::proof]
    fn ordered_bits_is_a_bijection() {
        let bits: u64 = kani::any();
        assert_eq!(
            float_from_ordered(ordered_bits(f64::from_bits(bits))).to_bits(),
            bits
        );
        let o: i64 = kani::any();
        assert_eq!(ordered_bits(float_from_ordered(o)), o);
    }

    /// Numeric order of non-NaN floats is kept; -0.0 sorts directly below +0.0.
    #[kani::proof]
    fn ordered_bits_keeps_order() {
        let (a, b): (f64, f64) = (kani::any(), kani::any());
        kani::assume(!a.is_nan() && !b.is_nan());
        if a < b {
            assert!(ordered_bits(a) < ordered_bits(b));
        }
        if a == b && a.to_bits() == b.to_bits() {
            assert_eq!(ordered_bits(a), ordered_bits(b));
        }
        assert_eq!(ordered_bits(-0.0), -1);
        assert_eq!(ordered_bits(0.0), 0);
    }

    #[kani::proof]
    fn zigzag_is_a_bijection() {
        let v: i64 = kani::any();
        assert_eq!(unzigzag(zigzag(v)), v);
        let u: u64 = kani::any();
        assert_eq!(zigzag(unzigzag(u)), u);
    }

    #[kani::proof]
    #[kani::unwind(11)]
    fn varint_roundtrips() {
        let v: u64 = kani::any();
        let mut out = Vec::new();
        put_varint(&mut out, v);
        assert!(out.len() <= 10);
        let mut pos = 0;
        assert_eq!(get_varint(&out, &mut pos), Ok(v));
        assert_eq!(pos, out.len());
    }

    /// Arbitrary bytes: an error or a value, never a panic or a read past the end.
    #[kani::proof]
    #[kani::unwind(12)]
    fn varint_reader_is_total() {
        let bytes: [u8; 11] = kani::any();
        let len: usize = kani::any();
        kani::assume(len <= 11);
        let start: usize = kani::any();
        kani::assume(start <= len);
        let mut pos = start;
        if get_varint(&bytes[..len], &mut pos).is_ok() {
            assert!(pos > start && pos <= len && pos - start <= 10);
        }
    }

    /// Decoding adds back exactly the prediction the encoder subtracted, for every
    /// predictor order, position and i64 history (wrapping on both sides).
    #[kani::proof]
    fn prediction_residuals_invert() {
        let x: [i64; 5] = kani::any();
        let order: usize = kani::any();
        kani::assume(order <= MAX_ORDER);
        let i: usize = kani::any();
        kani::assume(i < 5);
        let residual = zigzag(x[i].wrapping_sub(predict(&x, i, order)));
        assert_eq!(
            unzigzag(residual).wrapping_add(predict(&x[..i], i, order)),
            x[i]
        );
    }

    /// A last-place correction restores the sample bit for bit from any grid value,
    /// whatever float arithmetic produced it: encoder and decoder compute the grid value
    /// with the same code from the same stored scale and offset, and the correction is
    /// pure wrapping integer arithmetic on the order-preserving images.
    #[kani::proof]
    fn corrections_restore_any_sample() {
        let (x, v): (u64, u64) = (kani::any(), kani::any());
        let (x, v) = (f64::from_bits(x), f64::from_bits(v));
        let fix = ordered_bits(x).wrapping_sub(ordered_bits(v));
        let restored = float_from_ordered(ordered_bits(v).wrapping_add(fix));
        assert_eq!(restored.to_bits(), x.to_bits());
    }
}
