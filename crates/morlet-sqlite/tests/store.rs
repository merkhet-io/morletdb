use morlet_core::exactstats::ExactStats;
use morlet_core::intervalstats::IntervalStats;
use morlet_sqlite::{Durability, Store, APPLICATION_ID};

fn tmp(name: &str) -> std::path::PathBuf {
    let d = std::env::temp_dir().join(format!("morlet-sqlite-test-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d.join("rec.morlet")
}

/// Deterministic signal with grid values, off-grid values, tiny and large magnitudes, and -0.0.
fn signal(n: usize, seed: u64) -> Vec<f64> {
    let mut x = seed;
    (0..n)
        .map(|i| {
            x = x
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            let r = (x >> 11) as f64 / (1u64 << 53) as f64;
            match i % 97 {
                0 => -0.0,
                1 => 1e-300 * r,
                2 => 9.9e8 * (r - 0.5),
                _ if i % 5 == 0 => r * 3.7 - 1.0,
                _ => ((1000.0 * (i as f64 / 7.0).sin()) / 64.0).round() * (100.0 / 32768.0),
            }
        })
        .collect()
}

/// The exact statistics of `xs`, independent of chunking: one exact piece per sample.
fn reference(xs: &[f64]) -> [f64; 5] {
    let mut t = ExactStats::default();
    for x in xs {
        let mut s = IntervalStats::default();
        s.add(std::slice::from_ref(x));
        let mut e = ExactStats::default();
        e.add_piece(&s);
        t.merge(&e);
    }
    t.finish()
}

/// Count, min and max exact; mean and rms within a few units in the last place (chunk summaries carry a
/// compensated, not exact, sum of squares; bit-identity with pg_morlet on the same chunks is checked separately).
fn close(got: [f64; 5], want: [f64; 5]) -> bool {
    let ulps = |a: f64, b: f64| (a.to_bits() as i64 - b.to_bits() as i64).unsigned_abs();
    got[..3]
        .iter()
        .zip(&want[..3])
        .all(|(a, b)| a.to_bits() == b.to_bits())
        && ulps(got[3], want[3]) <= 4
        && ulps(got[4], want[4]) <= 4
}

fn bits(a: &[f64]) -> Vec<u64> {
    a.iter().map(|v| v.to_bits()).collect()
}

#[test]
fn round_trip_and_statistics_are_exact() {
    let path = tmp("rt");
    let mut s = Store::open(&path).unwrap();
    s.create_recording(
        "pump-7/accel-x",
        25_600.0,
        "accel-x",
        "m/s^2",
        100.0 / 32768.0,
        r#"{"asset":"pump-7"}"#,
    )
    .unwrap();
    let x = signal(250_000, 1);
    // appends of different sizes and chunk sizes
    let mut end = 0;
    for (len, cs) in [
        (1usize, 4096usize),
        (40_000, 4096),
        (65_536, 32768),
        (100_000, 1000),
        (44_463, 777),
    ] {
        end = s
            .append(
                "pump-7/accel-x",
                end,
                &x[end as usize..end as usize + len],
                cs,
            )
            .unwrap();
    }
    assert_eq!(end, 250_000);
    let mut k = 7u64;
    for _ in 0..300 {
        k = k.wrapping_mul(6364136223846793005).wrapping_add(1);
        let a = (k >> 20) % 250_000;
        let b = (a + 1 + (k >> 40) % 70_000).min(250_000);
        let (a, b) = (a as i64, b as i64);
        assert_eq!(
            bits(&s.read("pump-7/accel-x", a, b).unwrap()),
            bits(&x[a as usize..b as usize])
        );
        let st = s.interval_stats("pump-7/accel-x", a, b).unwrap();
        assert!(
            close(st, reference(&x[a as usize..b as usize])),
            "[{a},{b}): {st:?}"
        );
    }
    // the whole recording, and reopened from disk
    drop(s);
    let s = Store::open_with(&path, Durability::Normal).unwrap();
    assert_eq!(
        bits(&s.read("pump-7/accel-x", 0, 250_000).unwrap()),
        bits(&x)
    );
    assert!(close(
        s.interval_stats("pump-7/accel-x", 0, 250_000).unwrap(),
        reference(&x)
    ));
    let r = s.recording("pump-7/accel-x").unwrap().unwrap();
    assert_eq!(
        (r.end_sample, r.unit.as_str(), r.metadata.as_str()),
        (250_000, "m/s^2", r#"{"asset":"pump-7"}"#)
    );
    let app: i64 = s
        .connection()
        .pragma_query_value(None, "application_id", |r| r.get(0))
        .unwrap();
    assert_eq!(app, APPLICATION_ID);
}

#[test]
fn appends_are_contiguous_and_retries_are_idempotent() {
    let mut s = Store::open(tmp("app")).unwrap();
    s.create_recording("r", 1000.0, "x", "", 1.0, "{}").unwrap();
    let x: Vec<f64> = (0..10_000).map(|i| i as f64).collect();
    assert_eq!(s.append("r", 0, &x[..6000], 1000).unwrap(), 6000);
    assert_eq!(
        s.append("r", 0, &x[..6000], 1000).unwrap(),
        6000,
        "identical retry"
    );
    let e = s.append("r", 0, &[5.0; 10], 1000).unwrap_err().to_string();
    assert!(
        e.contains("must start at the recording's end (6000)"),
        "{e}"
    );
    assert!(s.append("r", 7000, &x[7000..], 1000).is_err(), "gap");
    assert_eq!(s.append("r", 6000, &x[6000..], 1000).unwrap(), 10_000);
    assert!(s
        .read("r", 9000, 10_001)
        .unwrap_err()
        .to_string()
        .contains("exceeds recording length"));
    assert!(s.interval_stats("r", 5, 5).is_err());
    assert!(
        s.append("r", 10_000, &[f64::NAN], 1000).is_err(),
        "non-finite samples are refused"
    );
    assert_eq!(
        s.recording("r").unwrap().unwrap().end_sample,
        10_000,
        "a refused append changes nothing"
    );
    assert!(s.create_recording("r", 1000.0, "x", "", 1.0, "{}").is_err());
    assert!(s
        .create_recording("q", 1000.0, "x", "", 1.0, "not json")
        .is_err());
    assert!(s.append("missing", 0, &x, 1000).is_err());
}

#[test]
fn explicit_chunk_layouts_are_kept() {
    let mut s = Store::open(tmp("layout")).unwrap();
    s.create_recording("r", 1000.0, "x", "", 1.0, "{}").unwrap();
    let x = signal(10_000, 3);
    assert_eq!(
        s.append_chunks("r", 0, &x, &[1, 4095, 32, 5872]).unwrap(),
        10_000
    );
    let layout: Vec<(i64, i64)> = s
        .connection()
        .prepare("SELECT start_sample,sample_count FROM chunks ORDER BY start_sample")
        .unwrap()
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .collect::<rusqlite::Result<_>>()
        .unwrap();
    assert_eq!(layout, vec![(0, 1), (1, 4095), (4096, 32), (4128, 5872)]);
    assert_eq!(bits(&s.read("r", 0, 10_000).unwrap()), bits(&x));
    assert!(
        s.append_chunks("r", 10_000, &x[..10], &[5, 4]).is_err(),
        "lengths must add up"
    );
    assert!(
        s.append_chunks("r", 10_000, &x[..10], &[10, 0]).is_err(),
        "no empty chunk"
    );
}

#[test]
fn chunks_without_summaries_give_the_same_statistics() {
    let path = tmp("nosummary");
    let mut s = Store::open(&path).unwrap();
    s.create_recording("r", 1000.0, "x", "", 1.0 / 512.0, "{}")
        .unwrap();
    let x = signal(200_000, 9);
    s.append("r", 0, &x, 4096).unwrap();
    let ivs = [
        (0i64, 200_000i64),
        (4096, 8192),
        (1, 199_999),
        (12_345, 150_000),
    ];
    let with: Vec<[f64; 5]> = ivs
        .iter()
        .map(|&(a, b)| s.interval_stats("r", a, b).unwrap())
        .collect();
    // another writer may leave summaries out (file format 1); every third chunk here
    s.connection()
        .execute(
            "UPDATE chunks SET summary=NULL WHERE (start_sample/4096)%3=1",
            [],
        )
        .unwrap();
    for (&(a, b), w) in ivs.iter().zip(&with) {
        assert_eq!(
            bits(&s.interval_stats("r", a, b).unwrap()),
            bits(w),
            "[{a},{b})"
        );
    }
}

#[test]
fn foreign_files_are_refused() {
    let path = tmp("foreign");
    let c = rusqlite::Connection::open(&path).unwrap();
    c.execute_batch("CREATE TABLE t(x)").unwrap();
    drop(c);
    assert!(Store::open(&path)
        .unwrap_err()
        .to_string()
        .contains("not a MorletDB file"));
}

/// Study B5: file format 1 never contains a version-4 (Grizzbit 2, pco) chunk. The writer keeps Grizzbit 1 even
/// for samples whose pco chunk would be smaller, and a version-4 chunk put into a file is refused on read.
#[test]
fn file_format_1_never_holds_version4_chunks() {
    let path = tmp("version4");
    let mut s = Store::open(&path).unwrap();
    s.create_recording("r", 1000.0, "x", "", 1.0, "{}").unwrap();
    // A smooth float walk: Grizzbit 2 would store pco chunks for it.
    let mut v = 20.0f64;
    let x: Vec<f64> = (0..20_000u64)
        .map(|i| {
            v += ((i.wrapping_mul(2654435761) % 1000) as f64 - 499.5) * 1e-3;
            v
        })
        .collect();
    let first = &x[..4096];
    let v4 = morlet_core::rawcodec::encode_version4(first).unwrap();
    assert!(v4.len() < morlet_core::rawcodec::encode(first, 1.0).unwrap().len());
    s.append("r", 0, &x, 4096).unwrap();
    let versions: Vec<u8> = s
        .connection()
        .prepare("SELECT encoded FROM chunks ORDER BY start_sample")
        .unwrap()
        .query_map([], |r| r.get::<_, Vec<u8>>(0))
        .unwrap()
        .map(|e| e.unwrap()[0])
        .collect();
    assert_eq!(versions.len(), 5);
    assert!(versions.iter().all(|&v| v == 2 || v == 3), "{versions:?}");
    assert_eq!(bits(&s.read("r", 0, 20_000).unwrap()), bits(&x));
    // The same samples as a version-4 chunk, written by another program: refused by reads and statistics.
    s.connection()
        .execute(
            "UPDATE chunks SET encoded=?1, summary=NULL WHERE start_sample=0",
            [&v4],
        )
        .unwrap();
    let err = s.read("r", 0, 100).unwrap_err().to_string();
    assert!(
        err.contains("chunk format version 4 is not allowed in file format 1"),
        "{err}"
    );
    assert!(s.interval_stats("r", 0, 100).is_err());
    assert_eq!(bits(&s.read("r", 4096, 20_000).unwrap()), bits(&x[4096..]));
}
