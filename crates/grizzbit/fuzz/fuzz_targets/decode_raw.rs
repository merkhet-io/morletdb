//! Arbitrary bytes, checksum included, into `decode`: never a panic.
#![no_main]
use libfuzzer_sys::fuzz_target;
mod common;

fuzz_target!(|data: &[u8]| {
    common::check_decode(data);
    let _ = grizzbit::kind(data);
});
