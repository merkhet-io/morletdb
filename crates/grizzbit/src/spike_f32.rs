//! DEVELOPMENT ONLY (#326 float32 spike), not a format: sizes and decode speed of a "narrow float" coding,
//! the float kind with the mantissa bits that are zero in every sample of the chunk dropped before the
//! order-preserving map. Run: SPIKE_DIR=<dir with force.f32/.len, ctrl.f32/.len> cargo test --release
//! -p grizzbit spike_f32 -- --ignored --nocapture
use super::*;
use std::time::Instant;

fn shift_of(x: &[f64]) -> u32 {
    x.iter()
        .map(|v| (v.to_bits() & ((1u64 << 52) - 1)).trailing_zeros().min(52))
        .min()
        .unwrap_or(0)
}
fn narrow(x: f64, s: u32) -> i64 {
    let b = x.to_bits();
    let mag = ((b & !(1u64 << 63)) >> s) as i64;
    if b >> 63 == 1 {
        -mag - 1
    } else {
        mag
    }
}
fn widen(o: i64, s: u32) -> f64 {
    if o < 0 {
        f64::from_bits(((o.wrapping_add(1).wrapping_neg() as u64) << s) | (1 << 63))
    } else {
        f64::from_bits((o as u64) << s)
    }
}
/// version, kind, varint n, shift byte, stream, CRC
fn narrow_chunk(x: &[f64], lpc: bool) -> (Vec<u8>, u32) {
    let s = shift_of(x);
    let ints: Vec<i64> = x.iter().map(|&v| narrow(v, s)).collect();
    let mut out = vec![9u8, 7u8];
    put_varint(&mut out, x.len() as u64);
    out.push(s as u8);
    encode_stream(&ints, lpc, &mut out);
    out.extend([0u8; 4]);
    (out, s)
}

#[test]
#[ignore]
fn spike_f32() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        for &cs in &[32768usize, 4096] {
            let (mut n, mut g1, mut g21, mut nf, mut nl) = (0usize, 0usize, 0usize, 0usize, 0usize);
            let mut shifts = std::collections::BTreeMap::new();
            let (mut t_dec, mut t_enc) = (0f64, 0f64);
            let mut off = 0;
            for &len in &lens {
                let series = &all[off..off + len];
                off += len;
                for c in series.chunks(cs) {
                    n += c.len();
                    g1 += encode(c, 1.0).unwrap().len();
                    g21 += encode_with(c, 1.0, Format::Grizzbit21).unwrap().len();
                    let t = Instant::now();
                    let (f, s) = narrow_chunk(c, false);
                    t_enc += t.elapsed().as_secs_f64();
                    nf += f.len();
                    *shifts.entry(s).or_insert(0usize) += 1;
                    let (l, _) = narrow_chunk(c, true);
                    nl += l.len().min(f.len());
                    // decode the fixed-predictor stream back and check bits
                    let mut pos = 2;
                    let m = get_varint(&f, &mut pos).unwrap() as usize;
                    let s2 = f[pos] as u32;
                    let t = Instant::now();
                    let back: Vec<f64> = decode_stream(&f[pos + 1..f.len() - 4], m, false)
                        .unwrap()
                        .into_iter()
                        .map(|o| widen(o, s2))
                        .collect();
                    t_dec += t.elapsed().as_secs_f64();
                    assert!(back.iter().zip(c).all(|(a, b)| a.to_bits() == b.to_bits()));
                }
            }
            let b = |x: usize| x as f64 / n as f64;
            println!("{g} chunk {cs}: n={n} G1 {:.3} B/s, G2.1 {:.3}, narrow-fixed {:.3}, narrow+LPC {:.3}; \
                      narrow encode {:.1} M/s, decode {:.1} M/s; shifts {:?}",
                     b(g1), b(g21), b(nf), b(nl), n as f64 / t_enc / 1e6, n as f64 / t_dec / 1e6, shifts);
        }
    }
}

fn ord32(x: f32) -> i64 {
    let b = x.to_bits();
    if b >> 31 == 1 {
        -((b & 0x7fff_ffff) as i64) - 1
    } else {
        b as i64
    }
}
fn from_ord32(o: i64) -> f32 {
    if o < 0 {
        f32::from_bits(((o + 1).wrapping_neg() as u32) | 0x8000_0000)
    } else {
        f32::from_bits(o as u32)
    }
}
fn ulp32(x: f64) -> f64 {
    let f = (x.abs() as f32).max(f32::MIN_POSITIVE);
    (f32::from_bits(f.to_bits() + 1) - f) as f64
}
/// Grid in float32 precision: value(k) = f32(offset + k * scale), corrections in float32 ordered units.
fn grid32(x: &[f64]) -> Option<(f64, f64, Vec<i64>, Vec<i64>)> {
    let mut v: Vec<f64> = x.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    v.dedup();
    if v.len() < 3 {
        return None;
    }
    let mag = v[0].abs().max(v[v.len() - 1].abs());
    let floor = 16.0 * ulp32(mag);
    let gap = v
        .windows(2)
        .map(|w| w[1] - w[0])
        .filter(|&d| d > floor)
        .fold(f64::INFINITY, f64::min);
    if !gap.is_finite() {
        return None;
    }
    let x0 = v[0];
    let q = (1..=8u32).map(|m| gap / m as f64).find(|&q| {
        let ok = v
            .iter()
            .filter(|&&y| {
                let k = ((y - x0) / q).round();
                ((y - x0) - k * q).abs() <= 0.1 * q + 4.0 * ulp32(y)
            })
            .count();
        ok as f64 >= 0.99 * v.len() as f64
    })?;
    // refine scale and offset by least squares over the assigned indices
    let ks: Vec<f64> = x.iter().map(|&y| ((y - x0) / q).round()).collect();
    if ks.iter().any(|k| k.abs() > 1e15) {
        return None;
    }
    let n = x.len() as f64;
    let (sk, sx) = (ks.iter().sum::<f64>(), x.iter().sum::<f64>());
    let skk = ks.iter().map(|k| k * k).sum::<f64>();
    let skx = ks.iter().zip(x).map(|(k, y)| k * y).sum::<f64>();
    let den = n * skk - sk * sk;
    let (scale, offset) = if den != 0.0 {
        let s = (n * skx - sk * sx) / den;
        (s, (sx - s * sk) / n)
    } else {
        (q, x0)
    };
    let ints: Vec<i64> = ks.iter().map(|&k| k as i64).collect();
    let fix: Vec<i64> = ints
        .iter()
        .zip(x)
        .map(|(&k, &y)| ord32(y as f32) - ord32((offset + k as f64 * scale) as f32))
        .collect();
    Some((scale, offset, ints, fix))
}
fn grid32_chunk(x: &[f64], lpc: bool) -> Option<usize> {
    let (scale, offset, ints, fix) = grid32(x)?;
    let mut out = vec![9u8, 8u8];
    put_varint(&mut out, x.len() as u64);
    out.extend(scale.to_le_bytes());
    out.extend(offset.to_le_bytes());
    let mut s1 = Vec::new();
    encode_stream(&ints, lpc, &mut s1);
    put_varint(&mut out, s1.len() as u64);
    out.extend(&s1);
    encode_stream(&fix, lpc, &mut out);
    out.extend([0u8; 4]);
    // decode check
    let mut pos = 2;
    let m = get_varint(&out, &mut pos).unwrap() as usize;
    pos += 16;
    let l1 = get_varint(&out, &mut pos).unwrap() as usize;
    let k = decode_stream(&out[pos..pos + l1], m, lpc).unwrap();
    let f = decode_stream(&out[pos + l1..out.len() - 4], m, lpc).unwrap();
    for i in 0..m {
        let y = from_ord32(ord32((offset + k[i] as f64 * scale) as f32) + f[i]) as f64;
        assert_eq!(y.to_bits(), x[i].to_bits());
    }
    Some(out.len())
}

#[test]
#[ignore]
fn spike_f32_grid() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        for &cs in &[32768usize, 4096] {
            let (mut n, mut nf, mut gr, mut best, mut best_l, mut won) =
                (0usize, 0, 0, 0, 0, 0usize);
            let mut chunks = 0;
            let t = Instant::now();
            let mut off = 0;
            for &len in &lens {
                for c in all[off..off + len].chunks(cs) {
                    n += c.len();
                    chunks += 1;
                    let f = narrow_chunk(c, false).0.len();
                    let fl = narrow_chunk(c, true).0.len().min(f);
                    nf += f;
                    let g32 = grid32_chunk(c, false);
                    let g32l = grid32_chunk(c, true);
                    if let Some(s) = g32 {
                        gr += s;
                        if s < f {
                            won += 1;
                        }
                    } else {
                        gr += f;
                    }
                    best += g32.map_or(f, |s| s.min(f));
                    best_l += g32l.map_or(fl, |s| s.min(fl));
                }
                off += len;
            }
            let b = |x: usize| x as f64 / n as f64;
            println!("{g} chunk {cs}: narrow {:.3} B/s, grid32 {:.3}, best(narrow,grid32) {:.3}, with LPC {:.3}; \
                      grid32 smaller in {won}/{chunks} chunks; {:.1} s", b(nf), b(gr), b(best), b(best_l), t.elapsed().as_secs_f64());
        }
    }
}

/// Sign/magnitude float32 coding: magnitude bits (31-bit, order-preserving for |x|) with predictor + Rice,
/// and the sign as run lengths (first sign, then runs), coded as one more integer stream.
fn signmag_chunk(x: &[f64], lpc: bool) -> Option<(Vec<u8>, f64)> {
    if x.iter().any(|&v| (v as f32) as f64 != v) {
        return None;
    }
    let mags: Vec<i64> = x
        .iter()
        .map(|&v| ((v as f32).to_bits() & 0x7fff_ffff) as i64)
        .collect();
    let mut runs: Vec<i64> = Vec::new();
    let first = (x[0] as f32).is_sign_negative();
    let (mut cur, mut len) = (first, 0i64);
    for &v in x {
        let s = (v as f32).is_sign_negative();
        if s == cur {
            len += 1
        } else {
            runs.push(len);
            cur = s;
            len = 1
        }
    }
    runs.push(len);
    let mut out = vec![9u8, 10u8];
    put_varint(&mut out, x.len() as u64);
    out.push(first as u8);
    put_varint(&mut out, runs.len() as u64);
    let mut s1 = Vec::new();
    encode_stream(&mags, lpc, &mut s1);
    put_varint(&mut out, s1.len() as u64);
    out.extend(&s1);
    if runs.len() > 1 {
        encode_stream(&runs, false, &mut out);
    }
    out.extend([0u8; 4]);
    // decode and check, timed
    let t = Instant::now();
    let mut pos = 2;
    let m = get_varint(&out, &mut pos).unwrap() as usize;
    let neg0 = out[pos] == 1;
    pos += 1;
    let nr = get_varint(&out, &mut pos).unwrap() as usize;
    let l1 = get_varint(&out, &mut pos).unwrap() as usize;
    let mg = decode_stream(&out[pos..pos + l1], m, lpc).unwrap();
    let rs = if nr > 1 {
        decode_stream(&out[pos + l1..out.len() - 4], nr, false).unwrap()
    } else {
        vec![m as i64]
    };
    let mut y = Vec::with_capacity(m);
    let mut neg = neg0;
    let mut i = 0;
    for r in rs {
        for _ in 0..r {
            y.push(f32::from_bits(mg[i] as u32 | ((neg as u32) << 31)) as f64);
            i += 1;
        }
        neg = !neg;
    }
    let dt = t.elapsed().as_secs_f64();
    assert!(y.len() == m && y.iter().zip(x).all(|(a, b)| a.to_bits() == b.to_bits()));
    Some((out, dt))
}

#[test]
#[ignore]
fn spike_f32_signmag() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        for &cs in &[32768usize, 4096] {
            let (mut n, mut sm, mut sml, mut best, mut dec, mut enc) =
                (0usize, 0, 0, 0, 0f64, 0f64);
            let mut off = 0;
            for &len in &lens {
                for c in all[off..off + len].chunks(cs) {
                    n += c.len();
                    let t = Instant::now();
                    let (a, d) = signmag_chunk(c, false).unwrap();
                    enc += t.elapsed().as_secs_f64() - d;
                    dec += d;
                    let (b, _) = signmag_chunk(c, true).unwrap();
                    sm += a.len();
                    sml += b.len().min(a.len());
                    let f = narrow_chunk(c, true).0.len();
                    let gg = grid32_chunk(c, true).unwrap_or(usize::MAX);
                    best += b.len().min(a.len()).min(f).min(gg);
                }
                off += len;
            }
            let bb = |x: usize| x as f64 / n as f64;
            println!("{g} chunk {cs}: signmag {:.3} B/s, +LPC {:.3}, best of all {:.3}; signmag encode {:.0} M/s, decode {:.0} M/s",
                     bb(sm), bb(sml), bb(best), n as f64 / enc / 1e6, n as f64 / dec / 1e6);
        }
    }
}

/// The real entry point on the development set: sizes per format, kinds, encode and decode speed.
#[test]
#[ignore]
fn dev_version6() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        let mut chunks: Vec<&[f64]> = Vec::new();
        let mut off = 0;
        for &len in &lens {
            chunks.extend(all[off..off + len].chunks(4096));
            off += len;
        }
        let n: usize = chunks.iter().map(|c| c.len()).sum();
        for format in [Format::Grizzbit1, Format::Grizzbit21] {
            let t = Instant::now();
            let enc: Vec<Vec<u8>> = chunks
                .iter()
                .map(|c| encode_with_float32(c, 1.0, format).unwrap())
                .collect();
            let te = t.elapsed().as_secs_f64();
            let t = Instant::now();
            let mut ok = true;
            for (e, c) in enc.iter().zip(&chunks) {
                let d = decode(e).unwrap();
                ok &= d
                    .iter()
                    .zip(c.iter())
                    .all(|(a, b)| a.to_bits() == b.to_bits());
            }
            let td = t.elapsed().as_secs_f64();
            let bytes: usize = enc.iter().map(|e| e.len()).sum();
            let v6g = enc.iter().filter(|e| e[0] == 6 && e[1] == 1).count();
            let v6s = enc.iter().filter(|e| e[0] == 6 && e[1] == 3).count();
            println!("{g} {format:?}+6: {:.3} B/sample; v6 grid {v6g}, v6 sign/mag {v6s} of {}; encode {:.1} M/s, decode {:.1} M/s; exact {ok}",
                     bytes as f64 / n as f64, enc.len(), n as f64 / te / 1e6, n as f64 / td / 1e6);
        }
    }
}

#[test]
#[ignore]
fn dev_parts() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        let mut chunks: Vec<&[f64]> = Vec::new();
        let mut off = 0;
        for &len in &lens {
            chunks.extend(all[off..off + len].chunks(4096));
            off += len;
        }
        let n: usize = chunks
            .iter()
            .map(|c| c.len())
            .sum::<usize>()
            .min(20_000_000);
        let chunks: Vec<&[f64]> = {
            let mut s = 0;
            chunks
                .into_iter()
                .take_while(|c| {
                    s += c.len();
                    s <= n
                })
                .collect()
        };
        let n: usize = chunks.iter().map(|c| c.len()).sum();
        let time = |name: &str, f: &dyn Fn(&[f64])| {
            let t = Instant::now();
            for c in &chunks {
                f(c);
            }
            println!(
                "{g} {name}: {:.1} M/s",
                n as f64 / t.elapsed().as_secs_f64() / 1e6
            );
        };
        time("G1 encode", &|c| {
            std::hint::black_box(encode(c, 1.0).unwrap());
        });
        time("v6 signmag", &|c| {
            std::hint::black_box(crate::f32k::encode_signmag_pub(c));
        });
        time("v6 grid", &|c| {
            std::hint::black_box(crate::f32k::encode_grid_pub(c));
        });
    }
}

#[test]
#[ignore]
fn dev_g1_steps() {
    let dir = std::path::PathBuf::from(std::env::var("SPIKE_DIR").expect("SPIKE_DIR"));
    for g in ["force", "ctrl"] {
        let raw = std::fs::read(dir.join(format!("{g}.f32"))).unwrap();
        let lens: Vec<usize> = std::fs::read_to_string(dir.join(format!("{g}.len")))
            .unwrap()
            .lines()
            .map(|l| l.parse().unwrap())
            .collect();
        let all: Vec<f64> = raw
            .chunks_exact(4)
            .map(|b| f32::from_le_bytes(b.try_into().unwrap()) as f64)
            .collect();
        let mut chunks: Vec<&[f64]> = Vec::new();
        let mut off = 0;
        for &len in &lens {
            chunks.extend(all[off..off + len].chunks(4096));
            off += len;
        }
        let chunks: Vec<&[f64]> = {
            let mut s = 0;
            chunks
                .into_iter()
                .take_while(|c| {
                    s += c.len();
                    s <= 20_000_000
                })
                .collect()
        };
        let n: usize = chunks.iter().map(|c| c.len()).sum();
        let time = |name: &str, f: &dyn Fn(&[f64])| {
            let t = Instant::now();
            for c in &chunks {
                f(c);
            }
            println!(
                "{g} {name}: {:.1} ns/sample",
                t.elapsed().as_secs_f64() * 1e9 / n as f64
            );
        };
        time("G1 encode", &|c| {
            std::hint::black_box(encode(c, 1.0).unwrap());
        });
        time("validate", &|c| {
            std::hint::black_box(validate(c).ok());
        });
        time("grid index check (quantum 1)", &|c| {
            let g = Grid::new(1.0).unwrap();
            std::hint::black_box(c.iter().map(|&x| g.index(x)).collect::<Option<Vec<i64>>>());
        });
        time("detect_step", &|c| {
            std::hint::black_box(detect_step(c));
        });
        time("offset grid (quantum 1)", &|c| {
            std::hint::black_box(encode_offset_grid(c, Grid::new(1.0).unwrap(), None));
        });
        time("float kind", &|c| {
            let mut o = Vec::new();
            encode_off_grid(c, &mut o);
            std::hint::black_box(o);
        });
        time("f64 sort+dedup", &|c| {
            let mut v = c.to_vec();
            v.sort_by(|a, b| a.total_cmp(b));
            v.dedup();
            std::hint::black_box(v);
        });
        time("v6 signmag", &|c| {
            std::hint::black_box(crate::f32k::encode_signmag_pub(c));
        });
        time("v6 grid", &|c| {
            std::hint::black_box(crate::f32k::encode_grid_pub(c));
        });
    }
}
