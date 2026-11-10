//! Frozen encoded chunks (testdata/rawcodec: `.chunk` encoded bytes, `.f64` the samples as
//! little-endian float64, `.json` the quantum and encoder flag): every format version, kind and grid mode
//! must decode to its frozen samples bit for bit, forever. Files are only ever added;
//! CI fails if an existing one changes. `encoder_current` marks chunks the current
//! encoder still produces byte for byte; when an encoder change alters one, set it to
//! false and add a new file instead of editing the old one.
//!
//! New cases: add them to `cases()` and run
//!   MORLET_WRITE_GOLDEN=1 cargo test -p grizzbit --test codec_golden -- --ignored
//! which writes missing files only.
//!
//! Version-4 (Grizzbit 2, pco) chunks are marked `"encoder": "version4"` in their `.json`: they are written by
//! `encode_version4` and checked against it. They are the upgrade check for any later `pco` version (study
//! B5): a new pco must decode every one of them bit for bit; its encoder may write other bytes, and then
//! `encoder_current` is set to false and new files are added.
use grizzbit::{decode, encode, encode_version4, kind};
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

/// Uniform in [0, 1), from the generator only (no libm: platform-independent inputs).
fn unit(seed: &mut u64) -> f64 {
    lcg(seed) as f64 / 9_007_199_254_740_992.0
}

/// Version-4 cases: (name, samples). Inputs use integer arithmetic, IEEE division and products only.
fn pco_cases() -> Vec<(&'static str, Vec<f64>)> {
    let mut s = 0x4235_7063_6f34;
    let mut walk = |n: usize, step: f64| -> Vec<f64> {
        let mut x = 20.0;
        (0..n)
            .map(|_| {
                x += (unit(&mut s) - 0.5) * step;
                x
            })
            .collect()
    };
    let float = walk(4096, 0.37);
    let float32: Vec<f64> = walk(4096, 2.5)
        .into_iter()
        .map(|v| v as f32 as f64)
        .collect();
    let longest = walk(32768, 0.01);
    let mut t = 0x6e65_6172;
    let near_grid: Vec<f64> = (0..4096)
        .map(|i| {
            let x = ((lcg(&mut t) % 20_000) as f64 - 10_000.0) * 0.1;
            if i % 7 == 0 {
                f64::from_bits(x.to_bits() + 1)
            } else {
                x
            }
        })
        .collect();
    vec![
        ("v4-pco-float", float),
        ("v4-pco-float32", float32),
        ("v4-pco-near-grid", near_grid),
        ("v4-pco-constant", vec![0.1; 4096]),
        ("v4-pco-single", vec![-1234.5678]),
        ("v4-pco-max-length", longest),
    ]
}

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

#[test]
#[ignore]
fn write_missing_golden_files() {
    assert_eq!(std::env::var("MORLET_WRITE_GOLDEN").as_deref(), Ok("1"));
    std::fs::create_dir_all(dir()).unwrap();
    for (name, samples) in pco_cases() {
        let chunk = dir().join(format!("{name}.chunk"));
        if chunk.exists() {
            continue;
        }
        std::fs::write(&chunk, encode_version4(&samples).unwrap()).unwrap();
        let raw: Vec<u8> = samples.iter().flat_map(|x| x.to_le_bytes()).collect();
        std::fs::write(chunk.with_extension("f64"), raw).unwrap();
        let v = json!({"quantum_bits": format!("{:016x}", 1f64.to_bits()), "encoder_current": true,
                       "encoder": "version4"});
        std::fs::write(
            chunk.with_extension("json"),
            serde_json::to_string(&v).unwrap() + "\n",
        )
        .unwrap();
    }
    for (name, samples, quantum, ..) in cases() {
        let chunk = dir().join(format!("{name}.chunk"));
        if chunk.exists() {
            continue;
        }
        std::fs::write(&chunk, encode(&samples, quantum).unwrap()).unwrap();
        let raw: Vec<u8> = samples.iter().flat_map(|x| x.to_le_bytes()).collect();
        std::fs::write(chunk.with_extension("f64"), raw).unwrap();
        let v =
            json!({"quantum_bits": format!("{:016x}", quantum.to_bits()), "encoder_current": true});
        std::fs::write(
            chunk.with_extension("json"),
            serde_json::to_string(&v).unwrap() + "\n",
        )
        .unwrap();
    }
}

#[test]
fn every_pco_case_has_a_frozen_version4_file() {
    let cases = pco_cases();
    assert!(cases.len() >= 6);
    for (name, samples) in cases {
        let chunk = std::fs::read(dir().join(format!("{name}.chunk")))
            .unwrap_or_else(|_| panic!("{name}: missing golden file"));
        assert_eq!(&chunk[..2], &[4, 3], "{name}: version 4, kind pco");
        assert_eq!(kind(&chunk).unwrap().name(), "pco", "{name}: kind");
        let frozen: Vec<u64> = std::fs::read(dir().join(format!("{name}.f64")))
            .unwrap()
            .chunks_exact(8)
            .map(|b| u64::from_le_bytes(b.try_into().unwrap()))
            .collect();
        let now: Vec<u64> = samples.iter().map(|x| x.to_bits()).collect();
        assert_eq!(now, frozen, "{name}: case inputs changed");
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
            let encoded = match meta["encoder"].as_str() {
                None => encode(&samples, q).unwrap(),
                Some("version4") => encode_version4(&samples).unwrap(),
                Some(other) => panic!("{}: unknown encoder {other}", chunk.display()),
            };
            assert_eq!(
                encoded,
                bytes,
                "{}: encoder output changed",
                chunk.display()
            );
        }
    }
}
