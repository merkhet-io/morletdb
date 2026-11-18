//! Shared by the fuzz targets: the CRC-32C the codec appends (reimplemented here only
#![allow(dead_code)] // each target uses a subset
//! to build inputs; the codec's own check is what is under test) and the decode oracle.
use grizzbit::{decode, MAX_AMPLITUDE, MAX_SAMPLES};

pub fn crc32c(bytes: &[u8]) -> u32 {
    let mut c = !0u32;
    for &b in bytes {
        c ^= b as u32;
        for _ in 0..8 {
            c = if c & 1 == 1 {
                (c >> 1) ^ 0x82F6_3B78
            } else {
                c >> 1
            };
        }
    }
    !c
}

pub fn seal(body: &[u8]) -> Vec<u8> {
    let mut out = body.to_vec();
    out.extend_from_slice(&crc32c(body).to_le_bytes());
    out
}

/// Decoding either fails or returns samples the codec accepts as valid.
pub fn check_decode(bytes: &[u8]) {
    if let Ok(samples) = decode(bytes) {
        assert!(!samples.is_empty() && samples.len() <= MAX_SAMPLES);
        assert!(samples
            .iter()
            .all(|v| v.is_finite() && v.abs() <= MAX_AMPLITUDE));
    }
}
