//! Pipeline orchestration, stage ordering, and telemetry (`Pipeline`, `CleanResult`, `StageStat`).

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::time::Instant;

use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};

use crate::compactors;
use crate::config::{get_stage_options, load_config, Config};
use crate::fact_check::{FactReport, FactTracker};
use crate::stages::{
    run_abbreviations_stage, run_bullets, run_caps, run_clinical_identifiers_stage,
    run_duplicate_notes, run_expand_abbreviations_stage, run_fuzzy_dedup, run_fuzzy_line_dedup,
    run_headers, run_learned, run_line_length, run_nlp_stage, run_regex_list, run_regex_pairs,
    run_sections, run_timestamps, run_tokenize_stage, run_unicode, run_whitespace,
};

pub const BUILTIN_STAGE_IDS: &[&str] = &[
    "metadata_lines",
    "boilerplate",
    "tokenize_phi",
    "phi_patterns",
    "clinical_identifiers",
    "nlp_redaction",
    "literal_replacements",
    "learned_rules",
    "unicode_normalize",
    "hospital_day",
    "timestamps",
    "sections",
    "imaging_impression",
    "lab_compaction",
    "med_normalize",
    "vitals_summary",
    "neuro_summary",
    "mar_summary",
    "flowsheet_tables",
    "whitespace",
    "duplicate_notes",
    "note_order",
    "fuzzy_dedup",
    "fuzzy_line_dedup",
    "headers",
    "caps_normalize",
    "bullets",
    "medical_abbreviations",
    "line_length",
];

pub fn stage_label(stage_id: &str) -> &'static str {
    match stage_id {
        "metadata_lines" => "EMR line metadata",
        "boilerplate" => "Boilerplate blocks",
        "phi_patterns" => "Structured PHI patterns",
        "clinical_identifiers" => "Clinical identifiers (NPI / DEA / UDI)",
        "nlp_redaction" => "NLP name & contact redaction",
        "tokenize_phi" => "Reversible PHI tokenization",
        "literal_replacements" => "Literal phrase replacements",
        "learned_rules" => "Learned highlight rules",
        "unicode_normalize" => "Unicode cleanup",
        "hospital_day" => "Hospital / post-op day stamps",
        "timestamps" => "Timestamp removal",
        "sections" => "Section filter",
        "imaging_impression" => "Imaging impression-only",
        "lab_compaction" => "Lab fishbone compaction",
        "med_normalize" => "Medication list normalization",
        "vitals_summary" => "Vitals range summary",
        "neuro_summary" => "Neuro exam / EVD / drip summary",
        "mar_summary" => "MAR / PRN bolus totals",
        "flowsheet_tables" => "Flowsheet table compaction",
        "whitespace" => "Whitespace normalization",
        "duplicate_notes" => "Duplicate note removal",
        "note_order" => "Chronological note order",
        "fuzzy_dedup" => "Near-duplicate paragraph removal",
        "fuzzy_line_dedup" => "Near-duplicate line removal",
        "headers" => "Markdown clinical headers",
        "caps_normalize" => "ALL-CAPS normalization",
        "bullets" => "Bullet standardization",
        "medical_abbreviations" => "Medical abbreviations",
        "line_length" => "Line wrapping",
        _ => "Custom stage",
    }
}

pub fn stage_anchor(stage_id: &str) -> Option<&'static str> {
    match stage_id {
        "hospital_day" => Some("unicode_normalize"),
        "imaging_impression" => Some("sections"),
        "lab_compaction" => Some("imaging_impression"),
        "med_normalize" => Some("lab_compaction"),
        "vitals_summary" => Some("med_normalize"),
        "neuro_summary" => Some("vitals_summary"),
        "mar_summary" => Some("neuro_summary"),
        "flowsheet_tables" => Some("mar_summary"),
        "note_order" => Some("duplicate_notes"),
        "fuzzy_line_dedup" => Some("fuzzy_dedup"),
        _ => None,
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StageStat {
    pub stage_id: String,
    pub label: String,
    pub chars_before: usize,
    pub chars_after: usize,
    pub lines_before: usize,
    pub lines_after: usize,
    pub replacements: usize,
    pub duration_ms: f64,
    pub details: Map<String, Value>,
}

impl StageStat {
    pub fn chars_removed(&self) -> isize {
        self.chars_before as isize - self.chars_after as isize
    }

    pub fn reduction_pct(&self) -> f64 {
        if self.chars_before == 0 {
            return 0.0;
        }
        let r = (self.chars_removed() as f64 / self.chars_before as f64) * 100.0;
        (r * 10.0).round() / 10.0
    }

    pub fn to_history_dict(&self) -> Value {
        let mut safe_details = Map::new();
        for (k, v) in &self.details {
            if k == "changes" || k.contains("text") || k.contains("sample") {
                continue;
            }
            safe_details.insert(k.clone(), v.clone());
        }
        json!({
            "stage_id": self.stage_id,
            "label": self.label,
            "chars_before": self.chars_before,
            "chars_after": self.chars_after,
            "chars_removed": self.chars_removed(),
            "reduction_pct": self.reduction_pct(),
            "lines_before": self.lines_before,
            "lines_after": self.lines_after,
            "replacements": self.replacements,
            "duration_ms": (self.duration_ms * 100.0).round() / 100.0,
            "details": safe_details,
        })
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CleanResult {
    pub text: String,
    pub original_chars: usize,
    pub cleaned_chars: usize,
    pub original_lines: usize,
    pub cleaned_lines: usize,
    pub duration_ms: f64,
    pub stages: Vec<StageStat>,
    #[serde(default)]
    pub token_map: HashMap<String, String>,
    pub fact_check: Option<FactReport>,
}

impl CleanResult {
    pub fn chars_removed(&self) -> isize {
        self.original_chars as isize - self.cleaned_chars as isize
    }

    pub fn reduction_pct(&self) -> f64 {
        if self.original_chars == 0 {
            return 0.0;
        }
        let r = (self.chars_removed() as f64 / self.original_chars as f64) * 100.0;
        (r * 10.0).round() / 10.0
    }

    pub fn total_replacements(&self) -> usize {
        self.stages.iter().map(|s| s.replacements).sum()
    }

    pub fn to_history_record(&self, source: &str) -> Value {
        let mut record = json!({
            "source": source,
            "original_chars": self.original_chars,
            "cleaned_chars": self.cleaned_chars,
            "chars_removed": self.chars_removed(),
            "reduction_pct": self.reduction_pct(),
            "original_lines": self.original_lines,
            "cleaned_lines": self.cleaned_lines,
            "total_replacements": self.total_replacements(),
            "duration_ms": (self.duration_ms * 100.0).round() / 100.0,
            "stages": self.stages.iter().map(|s| s.to_history_dict()).collect::<Vec<_>>(),
        });
        if let Some(ref fc) = self.fact_check {
            record["fact_check"] = fc.summary();
        }
        record
    }
}

fn count_lines(text: &str) -> usize {
    if text.is_empty() {
        0
    } else {
        text.bytes().filter(|&b| b == b'\n').count() + 1
    }
}

pub struct Pipeline {
    pub cfg: Config,
    pub custom_dir: Option<PathBuf>,
}

impl Pipeline {
    pub fn new(cfg: Config, custom_dir: Option<PathBuf>) -> Self {
        Self { cfg, custom_dir }
    }

    pub fn from_path(config_path: &Path, custom_dir: Option<PathBuf>) -> anyhow::Result<Self> {
        let cfg = load_config(config_path)?;
        Ok(Self::new(cfg, custom_dir))
    }

    fn builtin_enabled(&self, sid: &str) -> bool {
        let group_default = match sid {
            "nlp_redaction" => Some(("nlp_redaction", true)),
            "tokenize_phi" => Some(("tokenization", false)),
            "clinical_identifiers" => Some(("clinical_identifiers", false)),
            "duplicate_notes" => Some(("duplicate_note_detection", true)),
            "fuzzy_dedup" => Some(("fuzzy_dedup", true)),
            "fuzzy_line_dedup" => Some(("fuzzy_line_dedup", false)),
            _ => None,
        };
        if let Some((group, def)) = group_default {
            return self
                .cfg
                .get(group)
                .and_then(Value::as_object)
                .and_then(|o| o.get("enabled"))
                .and_then(Value::as_bool)
                .unwrap_or(def);
        }
        if let Some(so) = get_stage_options(&self.cfg, sid) {
            if let Some(en) = so.get("enabled").and_then(Value::as_bool) {
                return en;
            }
        }
        true
    }

    pub fn ordered_stage_ids(&self) -> Vec<String> {
        let configured: Vec<String> = self
            .cfg
            .get("stage_order")
            .and_then(Value::as_array)
            .map(|arr| {
                arr.iter()
                    .filter_map(Value::as_str)
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_else(|| BUILTIN_STAGE_IDS.iter().map(|s| s.to_string()).collect());

        let mut seen = HashSet::new();
        let mut resolved = Vec::new();
        for sid in configured {
            if BUILTIN_STAGE_IDS.contains(&sid.as_str()) && seen.insert(sid.clone()) {
                resolved.push(sid);
            }
        }
        for &builtin in BUILTIN_STAGE_IDS {
            if builtin == "medical_abbreviations" {
                continue;
            }
            if seen.insert(builtin.to_string()) {
                if let Some(anchor) = stage_anchor(builtin) {
                    if let Some(idx) = resolved.iter().position(|x| x == anchor) {
                        resolved.insert(idx + 1, builtin.to_string());
                        continue;
                    }
                }
                resolved.push(builtin.to_string());
            }
        }
        if seen.insert("medical_abbreviations".to_string()) {
            let idx = if resolved.last().map(|s| s.as_str()) == Some("line_length") {
                resolved.len() - 1
            } else {
                resolved.len()
            };
            resolved.insert(idx, "medical_abbreviations".to_string());
        }
        resolved
    }

    pub fn run(&self, raw_text: &str, track_changes: bool, mode: &str) -> CleanResult {
        let t0 = Instant::now();
        let orig_chars = raw_text.chars().count();
        let orig_lines = count_lines(raw_text);
        let mut text = raw_text.to_string();
        let mut stats = Vec::new();
        let mut token_map: HashMap<String, String> = HashMap::new();
        let mut logs: Vec<String> = Vec::new();

        let cfg_val = Value::Object(self.cfg.clone());
        let mut fact_tracker = FactTracker::new(raw_text);

        let stage_ids: Vec<String> = match mode {
            "expand" => vec!["expand_abbreviations".to_string()],
            "abbreviations" => vec!["medical_abbreviations".to_string()],
            _ => self.ordered_stage_ids(),
        };

        let mut current_chars = orig_chars;
        let mut current_lines = orig_lines;

        for sid in stage_ids {
            let enabled = if sid == "expand_abbreviations" || mode == "abbreviations" {
                true
            } else {
                self.builtin_enabled(&sid)
            };
            let label = if sid == "expand_abbreviations" {
                "Expand abbreviations".to_string()
            } else {
                stage_label(&sid).to_string()
            };

            let before_c = current_chars;
            let before_l = current_lines;

            if !enabled {
                stats.push(StageStat {
                    stage_id: sid,
                    label,
                    chars_before: before_c,
                    chars_after: before_c,
                    lines_before: before_l,
                    lines_after: before_l,
                    replacements: 0,
                    duration_ms: 0.0,
                    details: Map::new(),
                });
                continue;
            }

            let t_stage = Instant::now();
            let (out_text, matched, details_val) =
                self.run_builtin(&sid, &text, &cfg_val, track_changes, &mut logs);
            let dur = t_stage.elapsed().as_secs_f64() * 1000.0;

            let changed = out_text != text;
            if changed {
                fact_tracker.after_stage(&sid, &label, &out_text);
                current_chars = out_text.chars().count();
                current_lines = count_lines(&out_text);
                text = out_text;
            }

            let details = match details_val {
                Value::Object(m) => m,
                _ => Map::new(),
            };
            if let Some(Value::Object(tmap)) = details.get("token_map") {
                for (k, v) in tmap {
                    if let Some(s) = v.as_str() {
                        token_map.insert(k.clone(), s.to_string());
                    }
                }
            }

            let after_c = current_chars;
            let after_l = current_lines;

            stats.push(StageStat {
                stage_id: sid,
                label,
                chars_before: before_c,
                chars_after: after_c,
                lines_before: before_l,
                lines_after: after_l,
                replacements: matched,
                duration_ms: dur,
                details,
            });
        }

        let should_wrap = mode == "clean"
            && self
                .cfg
                .get("wrap_output")
                .and_then(Value::as_bool)
                .unwrap_or(true);

        if should_wrap {
            let tag = self
                .cfg
                .get("wrap_tag")
                .and_then(Value::as_str)
                .filter(|s| !s.is_empty())
                .unwrap_or("patient_chart");
            let before_c = text.chars().count();
            let before_l = count_lines(&text);
            text = format!("<{tag}>\n{text}\n</{tag}>");
            stats.push(StageStat {
                stage_id: "wrapper".to_string(),
                label: format!("<{tag}> wrapper"),
                chars_before: before_c,
                chars_after: text.chars().count(),
                lines_before: before_l,
                lines_after: count_lines(&text),
                replacements: 0,
                duration_ms: 0.0,
                details: Map::new(),
            });
        }

        let total_ms = t0.elapsed().as_secs_f64() * 1000.0;
        let fact_report = fact_tracker.report(&text);

        CleanResult {
            cleaned_chars: text.chars().count(),
            cleaned_lines: count_lines(&text),
            text,
            original_chars: orig_chars,
            original_lines: orig_lines,
            duration_ms: total_ms,
            stages: stats,
            token_map,
            fact_check: Some(fact_report),
        }
    }

    pub fn stage_io(&self, raw_text: &str, stage_id: &str) -> (String, String, Option<StageStat>) {
        let mut text = raw_text.to_string();
        let cfg_val = Value::Object(self.cfg.clone());
        let mut logs = Vec::new();

        for sid in self.ordered_stage_ids() {
            let before = text.clone();
            let before_c = text.chars().count();
            let before_l = count_lines(&text);
            let t_stage = Instant::now();
            let (out_text, matched, details_val) = if self.builtin_enabled(&sid) {
                self.run_builtin(&sid, &text, &cfg_val, true, &mut logs)
            } else {
                (text.clone(), 0, Value::Object(Map::new()))
            };
            let dur = t_stage.elapsed().as_secs_f64() * 1000.0;
            text = out_text;
            if sid == stage_id {
                let details = match details_val {
                    Value::Object(m) => m,
                    _ => Map::new(),
                };
                let stat = StageStat {
                    stage_id: sid.clone(),
                    label: stage_label(&sid).to_string(),
                    chars_before: before_c,
                    chars_after: text.chars().count(),
                    lines_before: before_l,
                    lines_after: count_lines(&text),
                    replacements: matched,
                    duration_ms: dur,
                    details,
                };
                return (before, text, Some(stat));
            }
        }
        (text.clone(), text, None)
    }

    fn run_builtin(
        &self,
        sid: &str,
        text: &str,
        cfg: &Value,
        track_changes: bool,
        logs: &mut Vec<String>,
    ) -> (String, usize, Value) {
        match sid {
            "metadata_lines" => run_regex_list(
                text,
                cfg,
                "emr_line_metadata",
                true,
                false,
                "metadata_lines",
                track_changes,
                logs,
            ),
            "boilerplate" => run_regex_list(
                text,
                cfg,
                "boilerplate",
                true,
                true,
                "boilerplate",
                track_changes,
                logs,
            ),
            "tokenize_phi" => run_tokenize_stage(text, cfg),
            "phi_patterns" => run_regex_pairs(
                text,
                cfg,
                "epic_phi_patterns",
                true,
                "phi_patterns",
                track_changes,
                logs,
            ),
            "clinical_identifiers" => run_clinical_identifiers_stage(text, cfg),
            "nlp_redaction" => run_nlp_stage(text, cfg),
            "literal_replacements" => run_regex_pairs(
                text,
                cfg,
                "literal_replacements",
                false,
                "literal_replacements",
                track_changes,
                logs,
            ),
            "learned_rules" => run_learned(text, cfg, track_changes, logs),
            "unicode_normalize" => run_unicode(text, cfg),
            "hospital_day" => compactors::run_hospital_day(text, cfg),
            "timestamps" => run_timestamps(text, cfg, logs),
            "sections" => run_sections(text, cfg, logs),
            "imaging_impression" => compactors::run_imaging_impression(text, cfg),
            "lab_compaction" => compactors::run_labs_compact(text, cfg),
            "med_normalize" => compactors::run_meds_reconcile(text, cfg),
            "vitals_summary" => compactors::run_vitals_summary(text, cfg),
            "neuro_summary" => compactors::run_neuro_summary(text, cfg),
            "mar_summary" => compactors::run_mar_summary(text, cfg),
            "flowsheet_tables" => compactors::run_flowsheet_tables(text, cfg),
            "whitespace" => run_whitespace(text, cfg),
            "duplicate_notes" => run_duplicate_notes(text, cfg),
            "note_order" => compactors::run_note_order(text, cfg),
            "fuzzy_dedup" => run_fuzzy_dedup(text, cfg),
            "fuzzy_line_dedup" => run_fuzzy_line_dedup(text, cfg),
            "headers" => run_headers(text, cfg),
            "caps_normalize" => run_caps(text, cfg),
            "bullets" => run_bullets(text, cfg),
            "medical_abbreviations" => run_abbreviations_stage(text, cfg, track_changes),
            "expand_abbreviations" => run_expand_abbreviations_stage(text, cfg, track_changes),
            "line_length" => run_line_length(text, cfg),
            _ => (text.to_string(), 0, Value::Object(Map::new())),
        }
    }
}
