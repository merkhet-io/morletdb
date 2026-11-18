//! C interface to MorletDB's chunk decoder for clients (docs/compressed-transfer.md). The Python SDK loads the
//! built library with `ctypes`. Every function is safe to call with any bytes: it checks its arguments,
//! never panics across the boundary and writes at most `out_len` samples to `out`.
use morlet_core::transfer::{self, FrameError};
use std::panic::{catch_unwind, AssertUnwindSafe};

pub const OK: i32 = 0;
pub const ERR_ARGUMENT: i32 = -1;
pub const ERR_FRAME: i32 = -2;
pub const ERR_CHUNK: i32 = -3;
pub const ERR_COVERAGE: i32 = -4;
pub const ERR_PANIC: i32 = -5;

/// Version of this interface; a client refuses a library whose interface it does not know.
#[no_mangle]
pub extern "C" fn morlet_ffi_abi_version() -> u32 {
    1
}

/// The highest chunk format version this library decodes; the client passes it to
/// `morlet_read_interval_encoded`, which sends samples instead of chunks of a newer version.
#[no_mangle]
pub extern "C" fn morlet_ffi_max_chunk_version() -> u32 {
    transfer::MAX_CHUNK_VERSION as u32
}

/// Checks a transfer frame and writes the samples of [interval_start, interval_end) to `out`, which must hold
/// exactly `interval_end - interval_start` doubles. Returns 0, or a negative code: -1 arguments, -2 corrupt
/// frame, -3 corrupt chunk (its start sample in `*error_sample` when that pointer is not NULL), -4 the chunks do
/// not cover the interval exactly, -5 internal error. On error `out` may be partly written.
///
/// # Safety
/// `frame` must point to `frame_len` readable bytes and `out` to `out_len` writable doubles (either may be
/// NULL only when its length is 0); `error_sample` must be NULL or point to a writable int64.
#[no_mangle]
pub unsafe extern "C" fn morlet_decode_frame(
    frame: *const u8,
    frame_len: usize,
    interval_start: i64,
    interval_end: i64,
    out: *mut f64,
    out_len: usize,
    threads: u32,
    error_sample: *mut i64,
) -> i32 {
    if (frame.is_null() && frame_len > 0) || (out.is_null() && out_len > 0) {
        return ERR_ARGUMENT;
    }
    let frame: &[u8] = if frame_len == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(frame, frame_len)
    };
    let out: &mut [f64] = if out_len == 0 {
        &mut []
    } else {
        std::slice::from_raw_parts_mut(out, out_len)
    };
    let result = catch_unwind(AssertUnwindSafe(|| {
        transfer::decode_frame(frame, interval_start, interval_end, out, threads as usize)
    }));
    match result {
        Ok(Ok(())) => OK,
        Ok(Err(FrameError::Argument(_))) => ERR_ARGUMENT,
        Ok(Err(FrameError::Frame(_))) => ERR_FRAME,
        Ok(Err(FrameError::Coverage)) => ERR_COVERAGE,
        Ok(Err(FrameError::Chunk { start_sample, .. })) => {
            if !error_sample.is_null() {
                *error_sample = start_sample;
            }
            ERR_CHUNK
        }
        Err(_) => ERR_PANIC,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use morlet_core::rawcodec;
    use std::path::PathBuf;

    fn decode(frame: &[u8], start: i64, end: i64, out: &mut [f64]) -> (i32, i64) {
        let mut at = -1;
        let r = unsafe {
            morlet_decode_frame(
                frame.as_ptr(),
                frame.len(),
                start,
                end,
                out.as_mut_ptr(),
                out.len(),
                4,
                &mut at,
            )
        };
        (r, at)
    }

    /// The frozen golden chunks decode through the C interface to their frozen samples, bit for bit.
    #[test]
    fn golden_chunks_through_the_c_interface() {
        let dir = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../testdata/rawcodec");
        let mut seen = 0;
        for entry in std::fs::read_dir(&dir).unwrap() {
            let path = entry.unwrap().path();
            if path.extension().and_then(|e| e.to_str()) != Some("chunk") {
                continue;
            }
            let chunk = std::fs::read(&path).unwrap();
            let want: Vec<u64> = std::fs::read(path.with_extension("f64"))
                .unwrap()
                .chunks_exact(8)
                .map(|b| u64::from_le_bytes(b.try_into().unwrap()))
                .collect();
            let n = want.len() as i64;
            for (start, end) in [(100, 100 + n), (101, 100 + n), (100, 101)] {
                if end <= start {
                    continue;
                }
                let frame = transfer::chunk_frame(&[(100, &chunk)], start, end).unwrap();
                let mut out = vec![0.0; (end - start) as usize];
                assert_eq!(
                    decode(&frame, start, end, &mut out).0,
                    OK,
                    "{}",
                    path.display()
                );
                let got: Vec<u64> = out.iter().map(|v| v.to_bits()).collect();
                assert_eq!(got, want[(start - 100) as usize..(end - 100) as usize]);
            }
            seen += 1;
        }
        assert!(seen >= 16, "golden chunks found: {seen}");
    }

    #[test]
    fn error_codes() {
        let c = rawcodec::encode(&[1.0; 100], 1.0).unwrap();
        let frame = transfer::chunk_frame(&[(0, &c)], 0, 100).unwrap();
        let mut out = vec![0.0; 100];
        assert_eq!(decode(&frame, 0, 100, &mut out).0, OK);
        assert_eq!(decode(&frame, 0, 100, &mut out[..99]).0, ERR_ARGUMENT);
        assert_eq!(decode(&frame[..10], 0, 100, &mut out).0, ERR_FRAME);
        let short = transfer::chunk_frame(&[(0, &c)], 0, 101).unwrap();
        let mut o = vec![0.0; 101];
        assert_eq!(decode(&short, 0, 101, &mut o).0, ERR_COVERAGE);
        let mut bad = c.clone();
        bad[4] ^= 1;
        let f = transfer::chunk_frame(&[(7, &bad)], 7, 107).unwrap();
        assert_eq!(decode(&f, 7, 107, &mut out), (ERR_CHUNK, 7));
        let r = unsafe {
            morlet_decode_frame(
                std::ptr::null(),
                5,
                0,
                1,
                out.as_mut_ptr(),
                1,
                1,
                std::ptr::null_mut(),
            )
        };
        assert_eq!(r, ERR_ARGUMENT);
        assert_eq!(morlet_ffi_max_chunk_version(), 6);
    }
}
