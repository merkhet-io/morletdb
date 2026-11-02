//! Arbitrary bodies with a correct checksum appended, so every mutation reaches the
//! parser rather than stopping at the checksum. Never a panic; any accepted body
//! decodes to valid samples. Seeded with valid encodings (fuzz/README.md).
#![no_main]
use libfuzzer_sys::fuzz_target;
mod common;

fuzz_target!(|body: &[u8]| {
    let sealed = common::seal(body);
    common::check_decode(&sealed);
    let _ = morlet_core::rawcodec::kind(&sealed);
});
