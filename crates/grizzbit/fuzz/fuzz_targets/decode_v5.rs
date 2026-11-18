//! Version-5 chunks (Grizzbit 2.1, development build): arbitrary bodies behind a version-5 grid header,
//! with a correct checksum appended, so every input reaches the version-5 parser (flags, correction
//! modes, exception blocks, table, LPC streams). Never a panic; any accepted chunk decodes to valid
//! samples. Seeded with the version-5 golden chunks (verification.yml).
#![no_main]
use libfuzzer_sys::fuzz_target;
mod common;

fuzz_target!(|body: &[u8]| {
    let mut chunk = Vec::with_capacity(body.len() + 6);
    chunk.extend_from_slice(&[grizzbit::GRIZZBIT21_VERSION, 1]);
    // Golden-chunk seeds already start with the version and kind; skip them so seeds stay valid.
    let rest = match body {
        [5, 1, tail @ ..] => tail,
        _ => body,
    };
    chunk.extend_from_slice(rest);
    let sealed = common::seal(&chunk);
    common::check_decode(&sealed);
    let _ = grizzbit::kind(&sealed);
});
