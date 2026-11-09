//! `morlet-sqlite`: the embedded edition from the command line (experimental, #351). Samples in and out are raw
//! little-endian float64 files.
use morlet_sqlite::Store;
use std::process::exit;

const USAGE: &str = "usage:
  morlet-sqlite create FILE RECORDING_ID RATE_HZ [--channel C] [--unit U] [--quantum Q] [--metadata JSON]
  morlet-sqlite append FILE RECORDING_ID START F64LE_FILE [--chunk-size N]   (default 4096)
  morlet-sqlite read   FILE RECORDING_ID START END OUT_F64LE_FILE
  morlet-sqlite stats  FILE RECORDING_ID START END
  morlet-sqlite info   FILE";

fn die(m: impl std::fmt::Display) -> ! {
    eprintln!("morlet-sqlite: {m}");
    exit(1)
}

fn num<T: std::str::FromStr>(s: &str, what: &str) -> T {
    s.parse()
        .unwrap_or_else(|_| die(format!("{what} must be a number, not {s:?}")))
}

fn opt<'a>(args: &'a [String], name: &str) -> Option<&'a str> {
    args.iter().position(|a| a == name).map(|i| {
        args.get(i + 1)
            .map(String::as_str)
            .unwrap_or_else(|| die(format!("{name} needs a value")))
    })
}

fn json_f64(x: f64) -> String {
    if x.is_finite() {
        format!("{x:?}")
    } else {
        "null".into()
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let Some(cmd) = args.first() else { die(USAGE) };
    let need = |n: usize| {
        if args.len() < n {
            die(USAGE)
        }
    };
    match cmd.as_str() {
        "create" => {
            need(4);
            let s = Store::open(&args[1]).unwrap_or_else(|e| die(e));
            s.create_recording(
                &args[2],
                num(&args[3], "RATE_HZ"),
                opt(&args, "--channel").unwrap_or(&args[2]),
                opt(&args, "--unit").unwrap_or(""),
                opt(&args, "--quantum").map_or(1.0, |q| num(q, "--quantum")),
                opt(&args, "--metadata").unwrap_or("{}"),
            )
            .unwrap_or_else(|e| die(e));
        }
        "append" => {
            need(5);
            let bytes =
                std::fs::read(&args[4]).unwrap_or_else(|e| die(format!("{}: {e}", args[4])));
            if !bytes.len().is_multiple_of(8) {
                die("the samples file is not a whole number of float64 values");
            }
            let xs: Vec<f64> = bytes
                .chunks_exact(8)
                .map(|b| f64::from_le_bytes(b.try_into().unwrap()))
                .collect();
            let mut s = Store::open(&args[1]).unwrap_or_else(|e| die(e));
            let cs = opt(&args, "--chunk-size").map_or(4096, |v| num(v, "--chunk-size"));
            let t = std::time::Instant::now();
            let end = s
                .append(&args[2], num(&args[3], "START"), &xs, cs)
                .unwrap_or_else(|e| die(e));
            println!(
                "{{\"end_sample\":{end},\"samples\":{},\"seconds\":{:.6}}}",
                xs.len(),
                t.elapsed().as_secs_f64()
            );
        }
        "read" => {
            need(6);
            let s = Store::open(&args[1]).unwrap_or_else(|e| die(e));
            let xs = s
                .read(&args[2], num(&args[3], "START"), num(&args[4], "END"))
                .unwrap_or_else(|e| die(e));
            let out: Vec<u8> = xs.iter().flat_map(|v| v.to_le_bytes()).collect();
            std::fs::write(&args[5], out).unwrap_or_else(|e| die(e));
        }
        "stats" => {
            need(5);
            let s = Store::open(&args[1]).unwrap_or_else(|e| die(e));
            let st = s
                .interval_stats(&args[2], num(&args[3], "START"), num(&args[4], "END"))
                .unwrap_or_else(|e| die(e));
            println!(
                "{{\"sample_count\":{},\"min\":{},\"max\":{},\"mean\":{},\"rms\":{}}}",
                st[0] as i64,
                json_f64(st[1]),
                json_f64(st[2]),
                json_f64(st[3]),
                json_f64(st[4])
            );
        }
        "info" => {
            need(2);
            let s = Store::open(&args[1]).unwrap_or_else(|e| die(e));
            println!("{}", s.info_json().unwrap_or_else(|e| die(e)));
        }
        _ => die(USAGE),
    }
}
