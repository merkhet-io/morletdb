//! Codec spike C1 (development only, issue #326): does Pcodec's gain over MorletDB's float fallback survive
//! at MorletDB's chunk granularity, and what does it cost in speed?
//!
//!   cargo run --release -- DATA_DIR OUT.json
//!
//! Inputs (development, see RESULTS.md): B4's four development Elf series, plus two waveforms that are
//! not B4 inputs (XJTU-SY Bearing1_1 horizontal, FEMTO Bearing1_1 horizontal).
//!
//! Per input and chunk size (4,096 and 32,768 samples), bits/sample of:
//! - raw:      today's chunk codec (morlet_core::rawcodec, quantum 1: the codec detects a grid itself);
//! - pco-L8:   pco standalone per chunk, default config (level 8, mode and delta auto-detected);
//! - pco-L4:   the same at level 4;
//! - pco-L8-classic: level 8 with mode detection off (Classic), to show what the float-mult/quant modes add;
//! - min:      per chunk the smaller of raw and pco-L8 (+1 tag byte and a 4-byte CRC on a pco chunk, as a
//!             MorletDB chunk kind would carry);
//! - pco-whole: pco on the whole series in one standalone file (B4's PCO arm, reference).
//! Every encoded chunk of every arm is decoded and compared with to_bits; failures are counted.
//! Speed: single thread, best of REPS passes per chunk, M samples/s. min_enc = raw + pco-L8 encode (both are
//! run to keep the smaller); min_dec = the chosen decoder per chunk.
use morlet_core::rawcodec;
use pco::standalone::{simple_compress, simple_decompress};
use pco::{ChunkConfig, ModeSpec};
use std::fmt::Write as _;
use std::path::{Path, PathBuf};
use std::time::Instant;

const REPS: usize = 3;
const PCO_FRAME: usize = 5; // chunk-kind tag + CRC32C a MorletDB pco chunk would add

fn elf(path: &Path) -> Vec<f64> {
    // B4's rule: every line that parses as a number, in file order; other lines are skipped.
    std::fs::read_to_string(path)
        .expect("Elf file")
        .lines()
        .filter_map(|l| l.trim().parse::<f64>().ok())
        .collect()
}

fn csv_column(dir: &Path, names: &[String], col: usize) -> Vec<f64> {
    let mut out = Vec::new();
    for n in names {
        let text = std::fs::read_to_string(dir.join(n)).expect("csv");
        for line in text.lines() {
            if let Some(v) = line
                .split(',')
                .nth(col)
                .and_then(|s| s.trim().parse::<f64>().ok())
            {
                out.push(v);
            }
        }
    }
    out
}

fn xjtu(data: &Path) -> Vec<f64> {
    let dir = data.join("xjtu-sy/extracted/XJTU-SY_Bearing_Datasets/35Hz12kN/Bearing1_1");
    let names: Vec<String> = (1..=123).map(|i| format!("{i}.csv")).collect();
    csv_column(&dir, &names, 0)
}

fn femto(data: &Path) -> Vec<f64> {
    let dir = data.join("femto/extracted/Learning_set/Bearing1_1");
    let names: Vec<String> = (1..=1638).map(|i| format!("acc_{i:05}.csv")).collect();
    csv_column(&dir, &names, 4)
}

fn same(a: &[f64], b: &[f64]) -> bool {
    a.len() == b.len() && a.iter().zip(b).all(|(x, y)| x.to_bits() == y.to_bits())
}

/// Per-chunk result of one arm: sizes, best encode/decode seconds per chunk, failures.
struct Arm {
    bytes: Vec<usize>,
    enc: Vec<f64>,
    dec: Vec<f64>,
    fail: usize,
}

fn run_arm(
    chunks: &[&[f64]],
    timed: bool,
    f_enc: &dyn Fn(&[f64]) -> Vec<u8>,
    f_dec: &dyn Fn(&[u8]) -> Vec<f64>,
) -> Arm {
    let reps = if timed { REPS } else { 1 };
    let n = chunks.len();
    let (mut bytes, mut enc, mut dec, mut fail) =
        (vec![0; n], vec![f64::MAX; n], vec![f64::MAX; n], 0);
    for rep in 0..reps {
        for (i, c) in chunks.iter().enumerate() {
            let t = Instant::now();
            let e = f_enc(c);
            enc[i] = enc[i].min(t.elapsed().as_secs_f64());
            let t = Instant::now();
            let d = f_dec(&e);
            dec[i] = dec[i].min(t.elapsed().as_secs_f64());
            if rep == 0 {
                bytes[i] = e.len();
                if !same(&d, c) {
                    fail += 1;
                }
            }
        }
    }
    Arm {
        bytes,
        enc,
        dec,
        fail,
    }
}

fn pco_enc(cfg: ChunkConfig) -> impl Fn(&[f64]) -> Vec<u8> {
    move |c| simple_compress(c, &cfg).expect("pco compress")
}
fn pco_dec(e: &[u8]) -> Vec<f64> {
    simple_decompress::<f64>(e).expect("pco decompress")
}
fn raw_enc(c: &[f64]) -> Vec<u8> {
    rawcodec::encode(c, 1.0).expect("raw encode")
}
fn raw_dec(e: &[u8]) -> Vec<f64> {
    rawcodec::decode(e).expect("raw decode")
}

fn bits(bytes: usize, n: usize) -> f64 {
    8.0 * bytes as f64 / n as f64
}
fn msps(n: usize, secs: f64) -> f64 {
    n as f64 / secs / 1e6
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let data = PathBuf::from(args.get(1).expect("DATA_DIR"));
    let out = PathBuf::from(args.get(2).expect("OUT.json"));
    let mut inputs: Vec<(String, &str, Vec<f64>)> = Vec::new();
    // B4's development series (PROTOCOL.md "Development"); never the other 18 Elf series or B4's waveforms.
    for name in [
        "Blockchain-tr",
        "POI-lat",
        "Stocks-USA",
        "electric_vehicle_charging",
    ] {
        inputs.push((
            name.into(),
            "elf-dev",
            elf(&data.join(format!("b4-elf/{name}.csv"))),
        ));
    }
    inputs.push(("XJTU-SY B1_1 horiz".into(), "wave-nonB4", xjtu(&data)));
    inputs.push(("FEMTO B1_1 horiz".into(), "wave-nonB4", femto(&data)));

    let empty = simple_compress::<f64>(&[], &ChunkConfig::default())
        .unwrap()
        .len();
    eprintln!("pco standalone bytes for an empty file (header+footer): {empty}");

    let mut json = String::from("{\n \"pco\": \"1.0.4\",\n \"inputs\": [\n");
    let mut first = true;
    for (name, set, x) in &inputs {
        let n = x.len();
        let whole = simple_compress(x, &ChunkConfig::default()).unwrap();
        let whole_ok = same(&simple_decompress::<f64>(&whole).unwrap(), x);
        for &size in &[4096usize, 32768] {
            let chunks: Vec<&[f64]> = x.chunks(size).collect();
            let raw = run_arm(&chunks, true, &raw_enc, &raw_dec);
            let l8 = run_arm(&chunks, true, &pco_enc(ChunkConfig::default()), &pco_dec);
            let l4 = run_arm(
                &chunks,
                true,
                &pco_enc(ChunkConfig::default().with_compression_level(4)),
                &pco_dec,
            );
            let cl = run_arm(
                &chunks,
                false,
                &pco_enc(ChunkConfig::default().with_mode_spec(ModeSpec::Classic)),
                &pco_dec,
            );
            let mut kinds = [0usize; 3];
            for c in &chunks {
                kinds[match rawcodec::kind(&raw_enc(c)).unwrap() {
                    rawcodec::Kind::Grid => 0,
                    rawcodec::Kind::Float => 1,
                    rawcodec::Kind::Raw => 2,
                }] += 1;
            }
            // Keep the smaller per chunk.
            let (mut min_bytes, mut pco_chosen, mut min_dec) = (0usize, 0usize, 0f64);
            for i in 0..chunks.len() {
                let p = l8.bytes[i] + PCO_FRAME;
                if p < raw.bytes[i] {
                    min_bytes += p;
                    pco_chosen += 1;
                    min_dec += l8.dec[i];
                } else {
                    min_bytes += raw.bytes[i];
                    min_dec += raw.dec[i];
                }
            }
            let sum = |v: &[usize]| v.iter().sum::<usize>();
            let tsum = |v: &[f64]| v.iter().sum::<f64>();
            let row = format!(
                "  {{\"input\":\"{name}\",\"set\":\"{set}\",\"n\":{n},\"chunk\":{size},\"chunks\":{},\
\"raw_kinds\":{{\"grid\":{},\"float\":{},\"raw\":{}}},\
\"bits\":{{\"raw\":{:.4},\"pco_l8\":{:.4},\"pco_l4\":{:.4},\"pco_l8_classic\":{:.4},\"min\":{:.4},\"pco_whole\":{:.4}}},\
\"min_pco_chunks\":{pco_chosen},\
\"fail\":{{\"raw\":{},\"pco_l8\":{},\"pco_l4\":{},\"pco_l8_classic\":{},\"pco_whole\":{}}},\
\"msamples_s\":{{\"raw_enc\":{:.1},\"raw_dec\":{:.1},\"pco_l8_enc\":{:.1},\"pco_l8_dec\":{:.1},\"pco_l4_enc\":{:.1},\"pco_l4_dec\":{:.1},\"min_enc\":{:.1},\"min_dec\":{:.1}}}}}",
                chunks.len(),
                kinds[0], kinds[1], kinds[2],
                bits(sum(&raw.bytes), n), bits(sum(&l8.bytes), n), bits(sum(&l4.bytes), n),
                bits(sum(&cl.bytes), n), bits(min_bytes, n), bits(whole.len(), n),
                raw.fail, l8.fail, l4.fail, cl.fail, usize::from(!whole_ok),
                msps(n, tsum(&raw.enc)), msps(n, tsum(&raw.dec)),
                msps(n, tsum(&l8.enc)), msps(n, tsum(&l8.dec)),
                msps(n, tsum(&l4.enc)), msps(n, tsum(&l4.dec)),
                msps(n, tsum(&raw.enc) + tsum(&l8.enc)), msps(n, min_dec),
            );
            eprintln!("{row}");
            if !first {
                json.push_str(",\n");
            }
            first = false;
            let _ = write!(json, "{row}");
        }
    }
    json.push_str("\n ]\n}\n");
    std::fs::write(&out, json).expect("write OUT.json");
}
