//! `chartcleaner-core`: High-performance clinical chart cleaning, PHI de-identification,
//! medical abbreviation compaction, clinical compactors, and fact-check verification.

pub mod abbreviation_safety;
pub mod abbreviations;
pub mod chart_dates;
pub mod clinical_identifiers;
pub mod clinical_whitelist;
pub mod compactors;
pub mod config;
pub mod delta;
pub mod engine;
pub mod fact_check;
pub mod insights;
pub mod regex_engine;
pub mod section_parser;
pub mod stages;
pub mod tokens;

pub use config::{load_config, load_default_config, save_config, set_stage_enabled, validate_config, Config};
pub use engine::{CleanResult, Pipeline, StageStat, BUILTIN_STAGE_IDS};
pub use fact_check::{FactReport, FactTracker};
pub use section_parser::{parse_clinical_sections, ClinicalSection, ParsedChart};
pub use tokens::{tokenize, untokenize};

/// Format cleaned chart text into `"text"`, `"markdown"`, `"xml"`, `"json"`, or `"smartphrase"`.
pub fn format_output(cleaned_text: &str, fmt: &str) -> anyhow::Result<String> {
    let fmt_norm = fmt.trim().to_ascii_lowercase();
    match fmt_norm.as_str() {
        "" | "text" | "txt" => Ok(cleaned_text.to_string()),
        "markdown" | "md" => Ok(parse_clinical_sections(cleaned_text).to_markdown()),
        "xml" => Ok(parse_clinical_sections(cleaned_text).to_llm_xml()),
        "json" => Ok(parse_clinical_sections(cleaned_text).to_json()),
        "smartphrase" | "dotphrase" => Ok(parse_clinical_sections(cleaned_text).to_smartphrase()),
        other => anyhow::bail!("Unsupported output format: {other}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    use std::path::Path;

    #[test]
    fn test_golden_sample_chart_parity() {
        let mut cfg = load_default_config();
        set_stage_enabled(&mut cfg, "nlp_redaction", false);

        let sample_path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../sample_chart.txt");
        let golden_path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../tests/golden/sample_chart.cleaned.txt");

        let raw = std::fs::read_to_string(&sample_path)
            .expect("sample_chart.txt should exist at workspace root");
        let expected = std::fs::read_to_string(&golden_path)
            .expect("tests/golden/sample_chart.cleaned.txt should exist");

        let pipeline = Pipeline::new(cfg, None);
        let result = pipeline.run(&raw, false, "clean");

        assert_eq!(
            result.text.trim(),
            expected.trim(),
            "Rust Pipeline output must match tests/golden/sample_chart.cleaned.txt byte-for-byte"
        );
    }

    #[test]
    fn test_clinical_identifiers_npi_dea_udi() {
        // Valid CMS NPI: 1234567893
        assert!(clinical_identifiers::is_valid_npi("1234567893"));
        assert!(!clinical_identifiers::is_valid_npi("1234567890"));

        // Valid DEA: AB1234563 (1+3+5=9, 2*(2+4+6)=24, 9+24=33 -> check digit 3)
        assert!(clinical_identifiers::is_valid_dea("AB1234563"));
        assert!(!clinical_identifiers::is_valid_dea("AB1234564"));
    }

    #[test]
    fn test_abbreviations_compress_and_expand() {
        let cfg = json!({
            "abbreviations": {
                "enabled": true,
                "custom": [],
                "disabled": []
            }
        });
        let (out, count, _) = abbreviations::abbreviate(
            "Patient with subarachnoid hemorrhage and hypertension.",
            Some(&cfg),
            None,
        );
        assert!(count >= 2);
        assert!(out.contains("SAH"));
        assert!(out.contains("HTN"));

        let (expanded, exp_count, _) = abbreviations::expand(&out, Some(&cfg), None);
        assert!(exp_count >= 1);
        assert!(expanded.to_lowercase().contains("hypertension"));
    }

    #[test]
    fn test_fact_checker_detects_dropped_allergy_and_negation() {
        let before = "Allergies: Penicillin (anaphylaxis)\nPatient denies chest pain on exertion.\nK 4.2";
        let after = "Patient has chest pain on exertion.\nK 4.2";
        let mut tracker = FactTracker::new(before);
        tracker.after_stage("whitespace", "Whitespace normalization", after);
        let report = tracker.report(after);
        assert_eq!(report.status, "alert");
        assert!(report.lost_unexpected > 0);
        assert!(!report.meaning.is_empty());
    }

    #[test]
    fn test_compactors_labs_vitals_mar() {
        let mut cfg = load_default_config();
        set_stage_enabled(&mut cfg, "labs_compact", true);
        set_stage_enabled(&mut cfg, "vitals_summary", true);
        set_stage_enabled(&mut cfg, "mar_summary", true);
        let enabled_cfg = Value::Object(cfg);

        let labs_input = "Na: 140\nK: 4.1\nCl: 102\nCO2: 24\nBUN: 18\nCr: 0.9\nGlucose: 110";
        let (labs_text, labs_count, _) = compactors::run_labs_compact(labs_input, &enabled_cfg);
        assert_eq!(labs_count, 1);
        assert!(labs_text.contains("BMP: Na 140, K 4.1"));

        let vitals_input = "BP: 120/80  130/85  118/76\nHR: 78  84  74\nSpO2: 98%  99%  99%";
        let (vitals_text, vitals_count, _) =
            compactors::run_vitals_summary(vitals_input, &enabled_cfg);
        assert_eq!(vitals_count, 1);
        assert!(vitals_text.starts_with("Vitals (3 readings):"));

        let mar_input =
            "08:00 Fentanyl 50 mcg IV Given\n10:30 Fentanyl 25 mcg IV Given\n14:00 Fentanyl 50 mcg IV Given";
        let (mar_text, mar_count, _) = compactors::run_mar_summary(mar_input, &enabled_cfg);
        assert_eq!(mar_count, 1);
        assert!(mar_text.contains("125 mcg total"));
    }
}
