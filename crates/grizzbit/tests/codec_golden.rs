//! Frozen encoded chunks (testdata/rawcodec: `.chunk` encoded bytes, `.f64` the samples as
//! little-endian float64, `.json` the quantum and encoder flag): every format version, kind and grid mode
//! must decode to its frozen samples bit for bit, forever. Files are only ever added;
//! CI fails if an existing one changes. `encoder_current` marks chunks the current
//! encoder still produces byte for byte; when an encoder change alters one, set it to
//! false and add a new file instead of editing the old one.
//!
//! Version 5 (Grizzbit 2.1, development build) chunks are in `cases_v5()`; their `.json` says
//! `"format": "grizzbit21"`, and `encoder_current` then refers to `encode_with(.., Format::Grizzbit21)`.
//!
//! Version 6 (float32 codings, study B8) chunks are in `cases_v6()`; their `.json` says `"format": "float32"`,
//! and `encoder_current` then refers to `encode_with_float32(.., Format::Grizzbit1)`.
//!
//! New cases: add them to `cases()` (or `cases_v5()`, `cases_v6()`) and run
//!   MORLET_WRITE_GOLDEN=1 cargo test -p grizzbit --test codec_golden -- --ignored
//! which writes missing files only.
use grizzbit::{
    decode, encode, encode_with, encode_with_float32, float32_layout, grizzbit21_layout, kind,
    Format,
};
use serde_json::{json, Value};
use std::path::PathBuf;

fn dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../testdata/rawcodec")
}

fn lcg(seed: &mut u64) -> u64 {
    *seed = seed
        .wrapping_mul(6364136223846793005)
        .wrapping_add(1442695040888963407);
    *seed >> 11
}

/// (name, samples, quantum, expected version, kind, grid mode byte, has corrections)
type Case = (
    &'static str,
    Vec<f64>,
    f64,
    u8,
    &'static str,
    Option<u8>,
    bool,
);

fn cases() -> Vec<Case> {
    let mut s = 1;
    let calibrated = 100.0 / 32768.0 * (1.0 - 7e-10);
    let counts: Vec<i64> = (0..2048)
        .map(|i| ((i as f64 / 50.).sin() * 2000.) as i64 + (lcg(&mut s) % 33) as i64 - 16)
        .collect();
    let edf = |pmin: f64, pmax: f64, seed: &mut u64| -> Vec<f64> {
        let mut d = 0i64;
        (0..1024)
            .map(|i| {
                d = (d + (lcg(seed) % 41) as i64 - 20).clamp(-2048, 2047);
                let d = (d + ((i as f64 / 30.).sin() * 300.) as i64).clamp(-2048, 2047);
                (d as f64 + 2048.0) * (pmax - pmin) / 4095.0 + pmin
            })
            .collect()
    };
    let edf_step = 417.0 / 4095.0;
    let mut noisy_edf = edf(-208.0, 209.0, &mut s);
    for x in noisy_edf.iter_mut().step_by(3) {
        *x = f64::from_bits(x.to_bits() + 1);
    }
    vec![
        (
            "v2-raw-noise",
            (0..64)
                .map(|_| (lcg(&mut s) as f64 / 9_007_199_254_740_992.0 - 0.5) * 2e9)
                .collect(),
            1.0,
            2,
            "raw",
            None,
            false,
        ),
        ("v2-raw-single", vec![0.1], 1.0, 2, "raw", None, false),
        (
            "v2-float-smooth",
            (0..512).map(|i| (i as f64 / 300.).sin() + 2.0).collect(),
            1.0,
            2,
            "float",
            None,
            false,
        ),
        (
            "v2-grid-div-counts",
            (0..2048)
                .map(|i| ((i as f64 / 40.).sin() * 3000. + (lcg(&mut s) % 64) as f64 - 32.).round())
                .collect(),
            1.0,
            2,
            "grid",
            Some(1),
            false,
        ),
        (
            "v2-grid-div-decimals",
            (-1000..1000)
                .map(|k| format!("{:.3}", k as f64 / 1000.).parse().unwrap())
                .collect(),
            0.001,
            2,
            "grid",
            Some(1),
            false,
        ),
        (
            "v2-grid-div-signed-zeros",
            vec![1.0, -0.0, 2.0, -0.0, 0.0, -3.0],
            1.0,
            2,
            "grid",
            Some(1),
            true,
        ),
        (
            "v2-grid-mul-dyadic",
            (-1000..1000)
                .map(|k| k as f64 * (100.0 / 32768.0))
                .collect(),
            100.0 / 32768.0,
            2,
            "grid",
            Some(0),
            false,
        ),
        (
            "v2-grid-mul-calibrated",
            counts.iter().map(|&k| k as f64 * calibrated).collect(),
            100.0 / 32768.0,
            2,
            "grid",
            Some(0),
            false,
        ),
        (
            "v2-grid-mul-corrections",
            counts
                .iter()
                .enumerate()
                .map(|(i, &k)| {
                    let x = k as f64 * calibrated;
                    if i % 4 == 0 {
                        f64::from_bits(x.to_bits() + 1)
                    } else {
                        x
                    }
                })
                .collect(),
            100.0 / 32768.0,
            2,
            "grid",
            Some(0),
            true,
        ),
        (
            "v2-grid-div-max-length",
            (0..32768)
                .map(|i| ((i * 37) % 1001) as f64 - 500.0)
                .collect(),
            1.0,
            2,
            "grid",
            Some(1),
            false,
        ),
        (
            "v2-grid-div-extremes",
            vec![1e9, -1e9, 0.0, 1.0],
            1.0,
            2,
            "grid",
            Some(1),
            false,
        ),
        (
            "v3-grid-mul-edf",
            edf(-208.0, 209.0, &mut s),
            edf_step,
            3,
            "grid",
            Some(0),
            true,
        ),
        (
            "v3-grid-mul-edf-corrections",
            noisy_edf,
            edf_step,
            3,
            "grid",
            Some(0),
            true,
        ),
        (
            "v3-grid-div-shifted-decimals",
            (-1000..1000).map(|k| 0.0004 + k as f64 / 1000.).collect(),
            0.001,
            3,
            "grid",
            Some(1),
            true,
        ),
        (
            "v3-grid-mul-dyadic-offset",
            (-500..500)
                .map(|k| (k as f64 + 0.25) * (100.0 / 32768.0))
                .collect(),
            100.0 / 32768.0,
            3,
            "grid",
            Some(0),
            false,
        ),
        (
            "v3-grid-div-quarter-offset",
            (0..256).map(|k| 0.25 + k as f64).collect(),
            1.0,
            3,
            "grid",
            Some(1),
            false,
        ),
    ]
}

fn walk(n: usize, seed: &mut u64, step: u64) -> Vec<i64> {
    let mut k = 0i64;
    (0..n)
        .map(|_| {
            k += (lcg(seed) % (2 * step + 1)) as i64 - step as i64;
            k
        })
        .collect()
}

/// A noisy two-pole resonance, rounded to integers (IEEE basic operations only).
fn resonance(n: usize, seed: &mut u64) -> Vec<i64> {
    let (mut y1, mut y2) = (0.0f64, 0.0f64);
    (0..n)
        .map(|_| {
            let e = (lcg(seed) % 41) as f64 - 20.0;
            let y = 1.95 * y1 - 0.97 * y2 + e;
            y2 = y1;
            y1 = y;
            y.round() as i64
        })
        .collect()
}

/// (name, samples, quantum, correction mode, index stream uses LPC, offset grid)
type CaseV5 = (&'static str, Vec<f64>, f64, u8, bool, bool);

/// Version-5 chunks: every correction mode, LPC and fixed predictors, offset grid, 32,768 samples.
fn cases_v5() -> Vec<CaseV5> {
    let mut s = 21;
    let current = 0.000_688_781_737_8;
    // Two float64 steps: the value is a function of the index, a few ulps off the fitted grid.
    let two_step = |ks: &[i64]| -> Vec<f64> {
        ks.iter()
            .map(|&k| k as f64 * current * 1.000_000_7)
            .collect()
    };
    let walk_k = walk(4096, &mut s, 6);
    let mut table_exceptions = two_step(&walk_k);
    for (i, v) in table_exceptions.iter_mut().enumerate() {
        if i % 97 == 5 {
            *v = f64::from_bits(v.to_bits() ^ 1);
        }
    }
    let negzero: Vec<f64> = walk(4096, &mut s, 40)
        .iter()
        .enumerate()
        .map(|(i, &k)| {
            let k = k % 200;
            if k == 0 && i % 2 == 0 {
                -0.0
            } else {
                k as f64 / 1000.0
            }
        })
        .collect();
    let res = resonance(4096, &mut s);
    let res_long = resonance(32768, &mut s);
    let res_cal = resonance(4096, &mut s);
    let res_noisy: Vec<f64> = resonance(4096, &mut s)
        .iter()
        .map(|&k| {
            let v = k as f64 * (100.0 / 32768.0);
            if v == 0.0 {
                return v;
            }
            f64::from_bits((v.to_bits() as i64 + (lcg(&mut s) % 5) as i64 - 2) as u64)
        })
        .collect();
    let edf_step = 417.0 / 4095.0;
    let edf: Vec<f64> = walk(4096, &mut s, 6)
        .iter()
        .map(|&k| ((k % 2000) as f64 + 2048.0) * 417.0 / 4095.0 - 208.0)
        .collect();
    vec![
        (
            "v5-grid-mul-table",
            two_step(&walk_k),
            current,
            2,
            false,
            false,
        ),
        (
            "v5-grid-mul-table-exceptions",
            table_exceptions,
            current,
            2,
            false,
            false,
        ),
        (
            "v5-grid-div-negzero-exceptions",
            negzero,
            0.001,
            3,
            false,
            false,
        ),
        (
            "v5-grid-div-lpc",
            res.iter().map(|&k| k as f64).collect(),
            1.0,
            0,
            true,
            false,
        ),
        (
            "v5-grid-div-lpc-max-length",
            res_long.iter().map(|&k| k as f64).collect(),
            1.0,
            0,
            true,
            false,
        ),
        (
            "v5-grid-mul-lpc-table",
            two_step(&res_cal),
            current,
            2,
            true,
            false,
        ),
        (
            "v5-grid-mul-lpc-corrections",
            res_noisy,
            100.0 / 32768.0,
            1,
            true,
            false,
        ),
        ("v5-grid-mul-offset-table", edf, edf_step, 2, false, true),
    ]
}

/// (name, float32 samples, kind byte (1 grid, 3 sign/magnitude), first stream LPC, second stream present)
type Case6 = (&'static str, Vec<f64>, u8, bool, bool);

fn cases_v6() -> Vec<Case6> {
    let mut seed = 99u64;
    let f = |x: f64| x as f32 as f64;
    let crossing: Vec<f64> = (0..4096)
        .map(|i| f((i as f64 / 7.3).sin() * 300.0 + (i as f64 * 1.7).cos() * 0.01))
        .collect();
    let positive: Vec<f64> = (0..4096)
        .map(|i| f(50.0 + (i as f64 / 13.0).sin() * 20.0 + ((lcg(&mut seed) % 1000) as f64) * 1e-3))
        .collect();
    let zeros: Vec<f64> = (0..600)
        .map(|i| match i % 5 {
            0 => 0.0,
            1 => -0.0,
            _ => f((i as f64 * 0.37).sin() * 1e-3),
        })
        .collect();
    // Two-digit decimals that jump (a controller value read at a low rate), rounded to float32.
    let decimals: Vec<f64> = (0..2206)
        .map(|_| f(((lcg(&mut seed) % 300) as f64 - 150.0) / 100.0 - 0.7))
        .collect();
    // A staircase of float32-rounded steps: fixed predictors, every value on the float32 grid.
    let steps: Vec<f64> = (0..4096)
        .map(|i| f(0.3 + (i / 16 % 97) as f64 * 0.001))
        .collect();
    // An index stream with resonance: LPC beats the fixed predictors.
    let (mut y1, mut y2) = (0.0f64, 0.0f64);
    let resonance: Vec<f64> = (0..4096)
        .map(|_| {
            let e = (lcg(&mut seed) % 41) as f64 - 20.0;
            let y = 1.95 * y1 - 0.97 * y2 + e;
            y2 = y1;
            y1 = y;
            f(0.25 + y.round() * 0.0023413)
        })
        .collect();
    let long: Vec<f64> = (0..32768)
        .map(|i| {
            f((i as f64 / 61.0).sin() * 120.0 + ((lcg(&mut seed) % 1000) as f64 - 500.0) * 1e-4)
        })
        .collect();
    vec![
        ("v6-signmag-crossing", crossing, 3, false, true),
        ("v6-signmag-one-sign", positive, 3, false, false),
        ("v6-signmag-signed-zeros", zeros, 3, false, true),
        ("v6-signmag-max-length", long, 3, false, true),
        ("v6-grid-decimals-corrections", decimals, 1, true, true),
        ("v6-grid-steps-no-corrections", steps, 1, false, false),
        ("v6-grid-lpc-corrections", resonance, 1, true, true),
    ]
}

fn write_case(name: &str, samples: &[f64], quantum: f64, format: Format) {
    write_case_with(name, samples, quantum, format, false);
}

fn write_case_with(name: &str, samples: &[f64], quantum: f64, format: Format, float32: bool) {
    let chunk = dir().join(format!("{name}.chunk"));
    if chunk.exists() {
        return;
    }
    let bytes = if float32 {
        encode_with_float32(samples, quantum, format).unwrap()
    } else {
        encode_with(samples, quantum, format).unwrap()
    };
    std::fs::write(&chunk, bytes).unwrap();
    let raw: Vec<u8> = samples.iter().flat_map(|x| x.to_le_bytes()).collect();
    std::fs::write(chunk.with_extension("f64"), raw).unwrap();
    let mut v =
        json!({"quantum_bits": format!("{:016x}", quantum.to_bits()), "encoder_current": true});
    if format == Format::Grizzbit21 {
        v["format"] = json!("grizzbit21");
    }
    if float32 {
        v["format"] = json!("float32");
    }
    std::fs::write(
        chunk.with_extension("json"),
        serde_json::to_string(&v).unwrap() + "\n",
    )
    .unwrap();
}

#[test]
#[ignore]
fn write_missing_golden_files() {
    assert_eq!(std::env::var("MORLET_WRITE_GOLDEN").as_deref(), Ok("1"));
    std::fs::create_dir_all(dir()).unwrap();
    for (name, samples, quantum, ..) in cases() {
        write_case(name, &samples, quantum, Format::Grizzbit1);
    }
    for (name, samples, quantum, ..) in cases_v5() {
        write_case(name, &samples, quantum, Format::Grizzbit21);
    }
    for (name, samples, ..) in cases_v6() {
        write_case_with(name, &samples, 1.0, Format::Grizzbit1, true);
    }
}

#[test]
fn every_version6_case_has_a_frozen_file_of_the_intended_shape() {
    for (name, samples, kind_byte, lpc, second) in cases_v6() {
        let chunk = std::fs::read(dir().join(format!("{name}.chunk")))
            .unwrap_or_else(|_| panic!("{name}: missing golden file"));
        assert_eq!(chunk[0], 6, "{name}: version");
        assert_eq!(
            float32_layout(&chunk),
            Some((kind_byte, lpc, second)),
            "{name}: layout"
        );
        assert_eq!(decode(&chunk).unwrap().len(), samples.len(), "{name}");
    }
}

#[test]
fn every_version5_case_has_a_frozen_file_of_the_intended_shape() {
    for (name, samples, _, fix_mode, lpc, offset) in cases_v5() {
        let chunk = std::fs::read(dir().join(format!("{name}.chunk")))
            .unwrap_or_else(|_| panic!("{name}: missing golden file"));
        assert_eq!(chunk[0], 5, "{name}: version");
        assert_eq!(kind(&chunk).unwrap().name(), "grid", "{name}: kind");
        assert_eq!(
            grizzbit21_layout(&chunk),
            Some((fix_mode, lpc)),
            "{name}: layout"
        );
        let count_len = chunk[2..].iter().position(|&b| b < 0x80).unwrap() + 1;
        assert_eq!(chunk[2 + count_len] & 1 == 1, offset, "{name}: offset flag");
        assert_eq!(decode(&chunk).unwrap().len(), samples.len(), "{name}");
    }
}

#[test]
fn every_case_has_a_frozen_file_of_the_intended_shape() {
    for (name, _, _, version, kind_name, mode, has_fix) in cases() {
        let chunk = std::fs::read(dir().join(format!("{name}.chunk")))
            .unwrap_or_else(|_| panic!("{name}: missing golden file"));
        assert_eq!(chunk[0], version, "{name}: version");
        assert_eq!(kind(&chunk).unwrap().name(), kind_name, "{name}: kind");
        if let Some(mode) = mode {
            // version, kind, varint count, mode
            let count_len = chunk[2..].iter().position(|&b| b < 0x80).unwrap() + 1;
            let at = 2 + count_len;
            assert_eq!(chunk[at], mode, "{name}: grid mode");
            let fix_at = at + 1 + 8 + if version == 3 { 8 } else { 0 };
            assert_eq!(chunk[fix_at] == 1, has_fix, "{name}: corrections flag");
        }
    }
}

#[test]
fn frozen_chunks_decode_bit_for_bit() {
    let mut paths: Vec<_> = std::fs::read_dir(dir())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "chunk"))
        .collect();
    paths.sort();
    assert!(paths.len() >= 16);
    for chunk in paths {
        let meta: Value =
            serde_json::from_slice(&std::fs::read(chunk.with_extension("json")).unwrap()).unwrap();
        let bytes = std::fs::read(&chunk).unwrap();
        let want: Vec<u64> = std::fs::read(chunk.with_extension("f64"))
            .unwrap()
            .chunks_exact(8)
            .map(|b| f64::from_le_bytes(b.try_into().unwrap()).to_bits())
            .collect();
        let got: Vec<u64> = decode(&bytes)
            .unwrap()
            .iter()
            .map(|x| x.to_bits())
            .collect();
        assert_eq!(got, want, "{}", chunk.display());
        if meta["encoder_current"].as_bool().unwrap() {
            let q = f64::from_bits(
                u64::from_str_radix(meta["quantum_bits"].as_str().unwrap(), 16).unwrap(),
            );
            let samples: Vec<f64> = want.iter().map(|&b| f64::from_bits(b)).collect();
            let samples_f: Vec<f64> = want.iter().map(|&b| f64::from_bits(b)).collect();
            if meta["format"].as_str() == Some("float32") {
                assert_eq!(
                    encode_with_float32(&samples_f, q, Format::Grizzbit1).unwrap(),
                    bytes,
                    "{}: encoder output changed",
                    chunk.display()
                );
                continue;
            }
            // Every other frozen chunk: the default server encoder writes it unchanged, or a strictly smaller
            // version-6 chunk for float32 samples.
            let with6 = encode_with_float32(&samples_f, q, Format::Grizzbit1).unwrap();
            if meta["format"].is_null() {
                assert!(
                    with6 == bytes || (with6[0] == 6 && with6.len() < bytes.len()),
                    "{}: keep-the-smaller",
                    chunk.display()
                );
            }
            let format = match meta["format"].as_str() {
                None => Format::Grizzbit1,
                Some("grizzbit21") => Format::Grizzbit21,
                Some(other) => panic!("{}: unknown format {other}", chunk.display()),
            };
            if format == Format::Grizzbit1 {
                assert_eq!(encode(&samples, q).unwrap(), bytes, "{}", chunk.display());
            }
            assert_eq!(
                encode_with(&samples, q, format).unwrap(),
                bytes,
                "{}: encoder output changed",
                chunk.display()
            );
        }
    }
}
