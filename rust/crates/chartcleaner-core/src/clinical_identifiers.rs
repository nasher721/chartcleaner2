//! Clinical identifier detection with checksum validation:
//! - NPI (National Provider Identifier, 10 digits with CMS Luhn-24 checksum)
//! - DEA Numbers (2 letters + 7 digits with DEA checksum algorithm)
//! - UDI (Unique Device Identifier, GS1 and HIBCC barcode formats)
//! - Clinical Accession Numbers

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ClinicalEntity {
    pub entity_type: String,
    pub value: String,
    pub start: usize,
    pub end: usize,
    pub confidence: f64,
    pub description: String,
}

static NPI_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(?:\bNPI\b[:\s#]*|\bProvider\s+ID[:\s#]*|\bNational\s+Provider\s+ID[:\s#]*)?(\b[12]\d{9}\b)").unwrap()
});

static DEA_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(?:\bDEA\b[:\s#]*(?:Number|No|#)?[:\s]*)?(\b[A-Za-z]{2}\d{7}\b)").unwrap()
});

static UDI_GS1_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\(01\)\d{14}(?:\([0-9]{2}\)[A-Za-z0-9\-]+)+").unwrap()
});

static UDI_HIBCC_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\+H[A-Za-z0-9]{4,18}(?:/[A-Za-z0-9]+)*").unwrap()
});

static ACCESSION_REGEX: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:Accession|Acc|Specimen|Order)\s*(?:ID|Number|No|#)?\s*[:#]\s*([A-Za-z0-9\-]{5,20})\b").unwrap()
});

/// Validate a 10-digit NPI using the CMS Luhn checksum algorithm over `80840` + 10 digits.
pub fn is_valid_npi(npi_str: &str) -> bool {
    let digits: Vec<u32> = npi_str.chars().filter_map(|c| c.to_digit(10)).collect();
    if digits.len() != 10 || (digits[0] != 1 && digits[0] != 2) {
        return false;
    }

    // CMS prefix 80840 -> digits [8, 0, 8, 4, 0]
    let prefix = [8u32, 0, 8, 4, 0];
    let mut total = 0u32;
    for (i, &digit) in prefix.iter().chain(digits.iter()).enumerate() {
        let pos_from_right = 15 - i;
        if pos_from_right % 2 == 0 {
            let doubled = digit * 2;
            total += (doubled / 10) + (doubled % 10);
        } else {
            total += digit;
        }
    }
    total.is_multiple_of(10)
}

pub fn find_npi_candidates(text: &str) -> Vec<ClinicalEntity> {
    let mut results = Vec::new();
    for caps in NPI_REGEX.captures_iter(text) {
        let m0 = caps.get(0).unwrap();
        let m1 = caps.get(1).unwrap();
        let num_str = m1.as_str();
        if is_valid_npi(num_str) {
            let m0_low = m0.as_str().to_lowercase();
            let has_label = m0_low.starts_with("npi") || m0_low.contains("provider");
            let conf = if has_label { 0.99 } else { 0.92 };
            results.push(ClinicalEntity {
                entity_type: "NPI".to_string(),
                value: num_str.to_string(),
                start: m1.start(),
                end: m1.end(),
                confidence: conf,
                description: "National Provider Identifier (Luhn verified)".to_string(),
            });
        }
    }
    results
}

/// Validate a 9-character DEA number using the official checksum algorithm.
pub fn is_valid_dea(dea_str: &str) -> bool {
    let clean = dea_str.trim().to_ascii_uppercase();
    let bytes = clean.as_bytes();
    if bytes.len() != 9
        || !bytes[0].is_ascii_alphabetic()
        || !bytes[1].is_ascii_alphabetic()
        || !bytes[2..].iter().all(|b| b.is_ascii_digit())
    {
        return false;
    }

    let valid_first = matches!(
        bytes[0],
        b'A' | b'B' | b'C' | b'D' | b'E' | b'F' | b'G' | b'M' | b'P' | b'R' | b'U' | b'X'
    );
    if !valid_first {
        return false;
    }

    let d: Vec<u32> = bytes[2..].iter().map(|&b| (b - b'0') as u32).collect();
    let sum1 = d[0] + d[2] + d[4];
    let sum2 = (d[1] + d[3] + d[5]) * 2;
    let check_digit = (sum1 + sum2) % 10;
    check_digit == d[6]
}

pub fn find_dea_candidates(text: &str) -> Vec<ClinicalEntity> {
    let mut results = Vec::new();
    for caps in DEA_REGEX.captures_iter(text) {
        let m0 = caps.get(0).unwrap();
        let m1 = caps.get(1).unwrap();
        let dea_str = m1.as_str().to_ascii_uppercase();
        if is_valid_dea(&dea_str) {
            let has_label = m0.as_str().to_lowercase().contains("dea");
            let conf = if has_label { 0.99 } else { 0.90 };
            results.push(ClinicalEntity {
                entity_type: "DEA_NUMBER".to_string(),
                value: dea_str,
                start: m1.start(),
                end: m1.end(),
                confidence: conf,
                description:
                    "Drug Enforcement Administration registration number (checksum verified)"
                        .to_string(),
            });
        }
    }
    results
}

fn is_word_byte(b: u8) -> bool {
    b.is_ascii_alphanumeric() || b == b'_'
}

pub fn find_device_and_specimen_identifiers(text: &str) -> Vec<ClinicalEntity> {
    let mut results = Vec::new();
    let bytes = text.as_bytes();

    for m in UDI_GS1_REGEX.find_iter(text) {
        let prev_ok = m.start() == 0 || !is_word_byte(bytes[m.start() - 1]);
        let next_ok = m.end() == bytes.len() || !is_word_byte(bytes[m.end()]);
        if prev_ok && next_ok {
            results.push(ClinicalEntity {
                entity_type: "UDI_DEVICE_ID".to_string(),
                value: m.as_str().to_string(),
                start: m.start(),
                end: m.end(),
                confidence: 0.98,
                description: "Unique Device Identifier (GS1 AI format)".to_string(),
            });
        }
    }

    for m in UDI_HIBCC_REGEX.find_iter(text) {
        let prev_ok = m.start() == 0 || !is_word_byte(bytes[m.start() - 1]);
        let next_ok = m.end() == bytes.len() || !is_word_byte(bytes[m.end()]);
        if prev_ok && next_ok {
            results.push(ClinicalEntity {
                entity_type: "UDI_DEVICE_ID".to_string(),
                value: m.as_str().to_string(),
                start: m.start(),
                end: m.end(),
                confidence: 0.95,
                description: "Unique Device Identifier (HIBCC format)".to_string(),
            });
        }
    }

    for caps in ACCESSION_REGEX.captures_iter(text) {
        if let Some(m1) = caps.get(1) {
            let val = m1.as_str();
            if !val.chars().all(|c| c.is_alphabetic()) || val.len() >= 7 {
                results.push(ClinicalEntity {
                    entity_type: "ACCESSION_NUMBER".to_string(),
                    value: val.to_string(),
                    start: m1.start(),
                    end: m1.end(),
                    confidence: 0.92,
                    description: "Clinical accession or specimen identifier".to_string(),
                });
            }
        }
    }

    results
}

pub fn scan_clinical_identifiers(text: &str) -> Vec<ClinicalEntity> {
    let mut all = Vec::new();
    all.extend(find_npi_candidates(text));
    all.extend(find_dea_candidates(text));
    all.extend(find_device_and_specimen_identifiers(text));
    all.sort_by(|a, b| a.start.cmp(&b.start).then_with(|| b.end.cmp(&a.end)));
    all
}
