//! Version-6 chunks (float32 codings, study B8): arbitrary bodies behind a version-6 header whose kind is
//! chosen by the first input byte (grid or sign/magnitude), with a correct checksum appended, so every input
//! reaches the version-6 parser (grid header, length-prefixed streams, corrections, sign runs). Never a panic;
//! any accepted chunk decodes to valid samples. Seeded with the version-6 golden chunks (verification.yml).
#![no_main]
use libfuzzer_sys::fuzz_target;
mod common;

fuzz_target!(|body: &[u8]| {
    let mut chunk = Vec::with_capacity(body.len() + 6);
    // Golden-chunk seeds already start with the version and kind; keep them as they are.
    match body {
        [6, kind @ (1 | 3), tail @ ..] => {
            chunk.extend_from_slice(&[grizzbit::FLOAT32_VERSION, *kind]);
            chunk.extend_from_slice(tail);
        }
        [first, tail @ ..] => {
            chunk.extend_from_slice(&[grizzbit::FLOAT32_VERSION, if first & 1 == 0 { 1 } else { 3 }]);
            chunk.extend_from_slice(tail);
        }
        [] => return,
    }
    let sealed = common::seal(&chunk);
    common::check_decode(&sealed);
    let _ = grizzbit::kind(&sealed);
    let _ = grizzbit::float32_layout(&sealed);
});
