//! C interface (ABI 1) for language bindings (the Python SDK loads it with ctypes). Every function returns 0 on
//! success, -1 for invalid input or a refused operation, -2 for an SQLite error and -5 for an internal error
//! (a caught panic); `morlet_sqlite_last_error` then gives the message of this thread's last failure.
use crate::{Durability, Error, Store};
use std::cell::RefCell;
use std::ffi::{c_char, CStr};
use std::panic::{catch_unwind, AssertUnwindSafe};

pub const ABI_VERSION: u32 = 1;

thread_local! {
    static LAST_ERROR: RefCell<String> = const { RefCell::new(String::new()) };
}

fn fail(code: i32, msg: String) -> i32 {
    LAST_ERROR.with(|e| *e.borrow_mut() = msg);
    code
}

fn run(f: impl FnOnce() -> crate::Result<()>) -> i32 {
    match catch_unwind(AssertUnwindSafe(f)) {
        Ok(Ok(())) => 0,
        Ok(Err(e @ Error::Invalid(_))) => fail(-1, e.to_string()),
        Ok(Err(e @ Error::Sqlite(_))) => fail(-2, e.to_string()),
        Err(_) => fail(-5, "internal error".into()),
    }
}

unsafe fn text<'a>(p: *const c_char, what: &str) -> crate::Result<&'a str> {
    if p.is_null() {
        return Err(Error::Invalid(format!("{what} must not be NULL")));
    }
    CStr::from_ptr(p)
        .to_str()
        .map_err(|_| Error::Invalid(format!("{what} must be UTF-8")))
}

unsafe fn store<'a>(h: *mut Store) -> crate::Result<&'a mut Store> {
    h.as_mut()
        .ok_or_else(|| Error::Invalid("store handle must not be NULL".into()))
}

/// Copies `s` into `buf` (NUL-terminated when it fits) and returns its length in bytes without the NUL.
unsafe fn copy_out(s: &str, buf: *mut u8, cap: usize) -> usize {
    if !buf.is_null() && cap > s.len() {
        std::ptr::copy_nonoverlapping(s.as_ptr(), buf, s.len());
        *buf.add(s.len()) = 0;
    }
    s.len()
}

#[no_mangle]
pub extern "C" fn morlet_sqlite_abi_version() -> u32 {
    ABI_VERSION
}

/// The message of this thread's last failure; returns its length (call again with a larger buffer if needed).
///
/// # Safety
/// `buf` is NULL or writable for `cap` bytes.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_last_error(buf: *mut u8, cap: usize) -> usize {
    LAST_ERROR.with(|e| copy_out(&e.borrow(), buf, cap))
}

/// Opens or creates a file; durability 0 = full (default), 1 = normal.
///
/// # Safety
/// `path` is a NUL-terminated string; `out` is writable.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_open(
    path: *const c_char,
    durability: i32,
    out: *mut *mut Store,
) -> i32 {
    run(|| {
        if out.is_null() {
            return Err(Error::Invalid("out must not be NULL".into()));
        }
        let d = match durability {
            0 => Durability::Full,
            1 => Durability::Normal,
            _ => {
                return Err(Error::Invalid(
                    "durability must be 0 (full) or 1 (normal)".into(),
                ))
            }
        };
        *out = Box::into_raw(Box::new(Store::open_with(text(path, "path")?, d)?));
        Ok(())
    })
}

/// # Safety
/// `h` is NULL or a handle from `morlet_sqlite_open` that is not used afterwards.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_close(h: *mut Store) {
    if !h.is_null() {
        drop(Box::from_raw(h));
    }
}

/// # Safety
/// `h` is a live handle; the strings are NUL-terminated.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_create_recording(
    h: *mut Store,
    recording_id: *const c_char,
    sample_rate_hz: f64,
    channel: *const c_char,
    unit: *const c_char,
    sample_quantum: f64,
    metadata_json: *const c_char,
) -> i32 {
    run(|| {
        store(h)?.create_recording(
            text(recording_id, "recording_id")?,
            sample_rate_hz,
            text(channel, "channel")?,
            text(unit, "unit")?,
            sample_quantum,
            text(metadata_json, "metadata")?,
        )
    })
}

/// # Safety
/// `h` is a live handle; `samples` is readable for `n` values; `out_end` is NULL or writable.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_append(
    h: *mut Store,
    recording_id: *const c_char,
    start_sample: i64,
    samples: *const f64,
    n: usize,
    chunk_size: u32,
    out_end: *mut i64,
) -> i32 {
    run(|| {
        if samples.is_null() && n > 0 {
            return Err(Error::Invalid("samples must not be NULL".into()));
        }
        let xs = if n == 0 {
            &[][..]
        } else {
            std::slice::from_raw_parts(samples, n)
        };
        let end = store(h)?.append(
            text(recording_id, "recording_id")?,
            start_sample,
            xs,
            chunk_size as usize,
        )?;
        if !out_end.is_null() {
            *out_end = end;
        }
        Ok(())
    })
}

/// As `morlet_sqlite_append` with the chunk boundaries given: `lengths` holds `nlen` chunk lengths (1..32768) that
/// add up to `n`.
///
/// # Safety
/// `h` is a live handle; `samples` is readable for `n` values, `lengths` for `nlen`; `out_end` is NULL or writable.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_append_chunks(
    h: *mut Store,
    recording_id: *const c_char,
    start_sample: i64,
    samples: *const f64,
    n: usize,
    lengths: *const u32,
    nlen: usize,
    out_end: *mut i64,
) -> i32 {
    run(|| {
        if (samples.is_null() && n > 0) || (lengths.is_null() && nlen > 0) {
            return Err(Error::Invalid(
                "samples and lengths must not be NULL".into(),
            ));
        }
        let xs = if n == 0 {
            &[][..]
        } else {
            std::slice::from_raw_parts(samples, n)
        };
        let ls: Vec<usize> = if nlen == 0 {
            vec![]
        } else {
            std::slice::from_raw_parts(lengths, nlen)
                .iter()
                .map(|&l| l as usize)
                .collect()
        };
        let end =
            store(h)?.append_chunks(text(recording_id, "recording_id")?, start_sample, xs, &ls)?;
        if !out_end.is_null() {
            *out_end = end;
        }
        Ok(())
    })
}

/// Reads `[a, b)` into `out`, which must hold exactly `b - a` values.
///
/// # Safety
/// `h` is a live handle; `out` is writable for `cap` values.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_read(
    h: *mut Store,
    recording_id: *const c_char,
    a: i64,
    b: i64,
    out: *mut f64,
    cap: usize,
) -> i32 {
    run(|| {
        let xs = store(h)?.read(text(recording_id, "recording_id")?, a, b)?;
        if out.is_null() || cap != xs.len() {
            return Err(Error::Invalid(
                "the output buffer must hold exactly end - start values".into(),
            ));
        }
        std::ptr::copy_nonoverlapping(xs.as_ptr(), out, xs.len());
        Ok(())
    })
}

/// `[count, min, max, mean, rms]` of `[a, b)` into `out` (5 values).
///
/// # Safety
/// `h` is a live handle; `out` is writable for 5 values.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_stats(
    h: *mut Store,
    recording_id: *const c_char,
    a: i64,
    b: i64,
    out: *mut f64,
) -> i32 {
    run(|| {
        let st = store(h)?.interval_stats(text(recording_id, "recording_id")?, a, b)?;
        if out.is_null() {
            return Err(Error::Invalid("out must not be NULL".into()));
        }
        std::ptr::copy_nonoverlapping(st.as_ptr(), out, 5);
        Ok(())
    })
}

/// The file's recordings as JSON (`Store::info_json`) into `buf`; `out_len` gets the length in bytes. If it does
/// not fit (`*out_len >= cap`), call again with a larger buffer.
///
/// # Safety
/// `h` is a live handle; `buf` is NULL or writable for `cap` bytes; `out_len` is writable.
#[no_mangle]
pub unsafe extern "C" fn morlet_sqlite_info_json(
    h: *mut Store,
    buf: *mut u8,
    cap: usize,
    out_len: *mut usize,
) -> i32 {
    run(|| {
        let s = store(h)?.info_json()?;
        if out_len.is_null() {
            return Err(Error::Invalid("out_len must not be NULL".into()));
        }
        *out_len = copy_out(&s, buf, cap);
        Ok(())
    })
}
