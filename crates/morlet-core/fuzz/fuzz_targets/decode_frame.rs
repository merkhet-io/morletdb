//! Compressed transfer frames (morlet_core::transfer): an interval from the first bytes, then arbitrary frame bytes
//! with a correct frame checksum appended, so mutations reach the frame parser and the chunk decoder rather than
//! stopping at the checksum. Never a panic; an accepted frame decodes to the same samples with one or four threads.
#![no_main]
use libfuzzer_sys::fuzz_target;
use morlet_core::transfer::decode_frame;
mod common;

fuzz_target!(|data: &[u8]| {
    if data.len() < 4 {
        return;
    }
    let start = u16::from_le_bytes([data[0], data[1]]) as i64;
    let end = start + 1 + (u16::from_le_bytes([data[2], data[3]]) as i64 % 70_000);
    let frame = common::seal(&data[4..]);
    let n = (end - start) as usize;
    let mut one = vec![0.0; n];
    if decode_frame(&frame, start, end, &mut one, 1).is_ok() {
        let mut four = vec![0.0; n];
        decode_frame(&frame, start, end, &mut four, 4).expect("thread count changed the result");
        assert!(one.iter().zip(&four).all(|(a, b)| a.to_bits() == b.to_bits()));
    }
    let _ = decode_frame(&data[4..], start, end, &mut one, 1);
});
