//! Configuration loading, validation, and stage option accessors.

use std::fs;
use std::path::Path;
use serde_json::{Map, Value};
use thiserror::Error;

/// Embedded factory-default configuration from `chartcleaner/default_config.json`.
pub const DEFAULT_CONFIG_JSON: &str =
    include_str!("../../../../chartcleaner/default_config.json");

pub const REQUIRED_CONFIG_KEYS: &[&str] = &[
    "emr_line_metadata",
    "boilerplate",
    "epic_phi_patterns",
    "literal_replacements",
    "clinical_headers",
];

pub const KNOWN_CONFIG_KEYS: &[&str] = &[
    "emr_line_metadata",
    "boilerplate",
    "epic_phi_patterns",
    "literal_replacements",
    "clinical_headers",
    "duplicate_note_detection",
    "fuzzy_dedup",
    "fuzzy_line_dedup",
    "nlp_redaction",
    "nlp_allow_list",
    "wrap_output",
    "wrap_tag",
    "stage_order",
    "custom_rules",
    "audit",
    "tokenization",
    "headers_engine",
    "ingest",
    "whitespace",
    "bullets",
    "header_options",
    "unicode_normalize",
    "timestamp_removal",
    "section_filter",
    "caps_normalize",
    "line_length",
    "imaging_impression",
    "hospital_day",
    "lab_compaction",
    "med_normalize",
    "vitals_summary",
    "neuro_summary",
    "mar_summary",
    "flowsheet_tables",
    "note_order",
    "note_profiles",
    "prompt_templates",
    "note_templates",
    "stage_options",
    "learned_rules",
    "abbreviations",
    "clinical_identifiers",
    "local_llm",
    "fact_check",
];

#[derive(Debug, Error)]
pub enum ConfigError {
    #[error("Config file not found: {0}")]
    NotFound(String),
    #[error("Invalid JSON in {path}: {reason}")]
    InvalidJson { path: String, reason: String },
    #[error("{0} must contain a JSON object.")]
    NotObject(String),
    #[error("Config missing required key(s): {0}")]
    MissingKeys(String),
    #[error("I/O error writing config: {0}")]
    Io(#[from] std::io::Error),
}

pub type Config = Map<String, Value>;

/// Parse a raw JSON string into a validated `Config` map.
/// `serde_json` automatically rejects `NaN` and `Infinity` per RFC 8259.
pub fn parse_config_str(raw: &str, label: &str, check_required: bool) -> Result<Config, ConfigError> {
    let val: Value = serde_json::from_str(raw).map_err(|e| ConfigError::InvalidJson {
        path: label.to_string(),
        reason: e.to_string(),
    })?;
    let obj = match val {
        Value::Object(m) => m,
        _ => return Err(ConfigError::NotObject(label.to_string())),
    };
    if check_required {
        let missing: Vec<&str> = REQUIRED_CONFIG_KEYS
            .iter()
            .copied()
            .filter(|k| !obj.contains_key(*k))
            .collect();
        if !missing.is_empty() {
            return Err(ConfigError::MissingKeys(missing.join(", ")));
        }
    }
    Ok(obj)
}

/// Load and parse JSON configuration from a file path.
pub fn load_config(path: impl AsRef<Path>) -> Result<Config, ConfigError> {
    let p = path.as_ref();
    let raw = fs::read_to_string(p).map_err(|_| ConfigError::NotFound(p.display().to_string()))?;
    parse_config_str(&raw, &p.display().to_string(), true)
}

/// Load the factory-default configuration embedded in the binary.
pub fn load_default_config() -> Config {
    parse_config_str(DEFAULT_CONFIG_JSON, "default_config.json", true)
        .expect("embedded default_config.json must be valid")
}

/// Atomically write configuration to disk, keeping a `.bak` backup beside it.
pub fn save_config(cfg: &Config, path: impl AsRef<Path>) -> Result<(), ConfigError> {
    let p = path.as_ref();
    if p.exists() {
        let mut bak = p.as_os_str().to_os_string();
        bak.push(".bak");
        let _ = fs::copy(p, &bak);
    }
    let tmp = p.with_extension("json.tmp");
    let serialized = serde_json::to_string_pretty(&Value::Object(cfg.clone())).map_err(|e| {
        ConfigError::InvalidJson {
            path: p.display().to_string(),
            reason: e.to_string(),
        }
    })?;
    fs::write(&tmp, format!("{serialized}\n"))?;
    fs::rename(&tmp, p)?;
    Ok(())
}

/// Validate a configuration map and return `(errors, warnings)` without panicking.
pub fn validate_config(cfg: &Config) -> (Vec<String>, Vec<String>) {
    let mut errors = Vec::new();
    let mut warnings = Vec::new();

    for &req in REQUIRED_CONFIG_KEYS {
        if !cfg.contains_key(req) {
            errors.push(format!("missing required key '{req}'"));
        }
    }

    for k in cfg.keys() {
        if !KNOWN_CONFIG_KEYS.contains(&k.as_str()) {
            warnings.push(format!(
                "unknown config key '{k}' (kept as-is, ignored by engine)"
            ));
        }
    }

    for &list_key in &["emr_line_metadata", "boilerplate", "clinical_headers"] {
        if let Some(val) = cfg.get(list_key) {
            if let Some(arr) = val.as_array() {
                for (i, item) in arr.iter().enumerate() {
                    match item.as_str() {
                        Some(s) if !s.is_empty() => {
                            if let Err(e) = crate::regex_engine::compile_cached(s, true, true, false) {
                                errors.push(format!("{list_key}[{i}]: invalid regex ({e})"));
                            }
                        }
                        _ => errors.push(format!("{list_key}[{i}]: pattern must be a non-empty string")),
                    }
                }
            } else {
                errors.push(format!("{list_key}: must be a list of strings"));
            }
        }
    }

    for &pair_key in &["epic_phi_patterns", "literal_replacements", "learned_rules"] {
        if let Some(val) = cfg.get(pair_key) {
            if let Some(arr) = val.as_array() {
                for (i, item) in arr.iter().enumerate() {
                    let Some(pair) = item.as_array() else {
                        errors.push(format!(
                            "{pair_key}[{i}]: must be a two-element [pattern, replacement] list"
                        ));
                        continue;
                    };
                    if pair.len() != 2 {
                        errors.push(format!(
                            "{pair_key}[{i}]: must be a two-element [pattern, replacement] list"
                        ));
                        continue;
                    }
                    match pair[0].as_str() {
                        Some(p) if !p.is_empty() => {
                            if let Err(e) = crate::regex_engine::compile_cached(p, true, false, false) {
                                errors.push(format!("{pair_key}[{i}] pattern: invalid regex ({e})"));
                            }
                        }
                        _ => errors.push(format!("{pair_key}[{i}] pattern: must be a non-empty string")),
                    }
                    if !pair[1].is_string() {
                        errors.push(format!("{pair_key}[{i}] replacement: must be a string"));
                    }
                }
            } else {
                errors.push(format!("{pair_key}: must be a list of [pattern, replacement] pairs"));
            }
        }
    }

    (errors, warnings)
}

/// Return `config["stage_options"][sid]` if present and an object.
pub fn get_stage_options<'a>(cfg: &'a Config, sid: &str) -> Option<&'a Map<String, Value>> {
    cfg.get("stage_options")?
        .as_object()?
        .get(sid)?
        .as_object()
}

/// Helper to read a bool option from a nested group with a fallback default.
pub fn opt_bool(group: Option<&Map<String, Value>>, key: &str, default: bool) -> bool {
    group
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_bool())
        .unwrap_or(default)
}

/// Helper to read an i64 option from a nested group with a fallback default.
pub fn opt_i64(group: Option<&Map<String, Value>>, key: &str, default: i64) -> i64 {
    group
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)))
        .unwrap_or(default)
}

/// Helper to read an f64 option from a nested group with a fallback default.
pub fn opt_f64(group: Option<&Map<String, Value>>, key: &str, default: f64) -> f64 {
    group
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_f64().or_else(|| v.as_i64().map(|i| i as f64)))
        .unwrap_or(default)
}

/// Helper to read a string option from a nested group with a fallback default.
pub fn opt_str<'a>(group: Option<&'a Map<String, Value>>, key: &str, default: &'a str) -> &'a str {
    group
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_str())
        .unwrap_or(default)
}

/// Read a bool option from `cfg[group][key]` where `cfg` is a `&Value`.
pub fn get_bool_opt(cfg: &Value, group: &str, key: &str, default: bool) -> bool {
    cfg.get(group)
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_bool())
        .unwrap_or(default)
}

/// Read an integer option from `cfg[group][key]` where `cfg` is a `&Value`.
pub fn get_int_opt(cfg: &Value, group: &str, key: &str, default: i64) -> i64 {
    cfg.get(group)
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)))
        .unwrap_or(default)
}

/// Read a string option from `cfg[group][key]` where `cfg` is a `&Value`.
pub fn get_str_opt(cfg: &Value, group: &str, key: &str, default: &str) -> String {
    cfg.get(group)
        .and_then(|g| g.get(key))
        .and_then(|v| v.as_str())
        .unwrap_or(default)
        .to_string()
}

/// Enable or disable a stage in a `Config` map using the canonical config group for that stage.
pub fn set_stage_enabled(cfg: &mut Config, stage_id: &str, enabled: bool) {
    let group_key = match stage_id {
        "timestamps" => "timestamp_removal",
        "sections" => "section_filter",
        "duplicate_notes" => "duplicate_note_detection",
        "tokenize" | "tokenize_phi" => "tokenization",
        "labs_compact" => "lab_compaction",
        "meds_reconcile" => "med_normalize",
        "metadata" | "boilerplate" | "structured_phi" | "literals" | "learned_rules" => {
            let stage_opts = cfg
                .entry("stage_options".to_string())
                .or_insert_with(|| Value::Object(Map::new()));
            if let Some(obj) = stage_opts.as_object_mut() {
                let entry = obj
                    .entry(stage_id.to_string())
                    .or_insert_with(|| Value::Object(Map::new()));
                if let Some(eobj) = entry.as_object_mut() {
                    eobj.insert("enabled".to_string(), Value::Bool(enabled));
                }
            }
            return;
        }
        other => other,
    };

    let entry = cfg
        .entry(group_key.to_string())
        .or_insert_with(|| Value::Object(Map::new()));
    if let Some(obj) = entry.as_object_mut() {
        obj.insert("enabled".to_string(), Value::Bool(enabled));
    }
}
