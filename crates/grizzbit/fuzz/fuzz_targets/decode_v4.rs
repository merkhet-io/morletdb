//! Version 4 (Grizzbit 2, pco) reached on every input: arbitrary bytes after the version and kind bytes
//! (varint sample count, then the pco body), with a correct checksum appended, so every mutation reaches the
//! frame parser and pco's decoder. Never a panic; any accepted chunk decodes to valid samples. Seeded with the
//! golden version-4 chunks without their first two and last four bytes (docs/verification.md).
#![no_main]
use libfuzzer_sys::fuzz_target;
mod common;

fuzz_target!(|rest: &[u8]| {
    let mut body = vec![grizzbit::PCO_VERSION, 3];
    body.extend_from_slice(rest);
    let sealed = common::seal(&body);
    common::check_decode(&sealed);
    if grizzbit::decode(&sealed).is_ok() {
        assert_eq!(grizzbit::kind(&sealed), Ok(grizzbit::Kind::Pco));
    }
});
