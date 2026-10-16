//! Safety checks for abbreviation rules (The Joint Commission Do Not Use / ISMP error-prone lists).

use ahash::AHashMap;
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

pub const DO_NOT_USE_JSON: &str =
    include_str!("../../../../chartcleaner/abbreviation_do_not_use.json");
pub const MEDICAL_ABBREVIATIONS_CSV: &str =
    include_str!("../../../../chartcleaner/medical_abbreviations.csv");

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Issue {
    pub code: String,
    pub level: String,
    pub message: String,
    pub source: String,
    pub use_instead: String,
}

#[derive(Debug, Clone, Deserialize)]
struct RawEntry {
    abbr: Vec<String>,
    #[serde(default)]
    meaning: String,
    level: String,
    source: String,
    problem: String,
    #[serde(default, rename = "use")]
    use_instead: String,
}

#[derive(Debug, Clone, Deserialize)]
struct RawPattern {
    regex: String,
    level: String,
    source: String,
    problem: String,
    #[serde(default, rename = "use")]
    use_instead: String,
}

#[derive(Debug, Clone, Deserialize)]
struct RawDoNotUseFile {
    #[serde(default)]
    entries: Vec<RawEntry>,
    #[serde(default)]
    patterns: Vec<RawPattern>,
}

struct CompiledDoNotUse {
    exact: AHashMap<String, Vec<(Option<Regex>, RawEntry)>>,
    patterns: Vec<(Regex, RawPattern)>,
}

fn norm_key(text: &str) -> String {
    text.chars()
        .filter(|&c| c != '.' && !c.is_whitespace())
        .collect::<String>()
        .to_lowercase()
}

static PART_SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[/\s,;+\-]+").unwrap());

static COMPILED_LISTS: Lazy<CompiledDoNotUse> = Lazy::new(|| {
    let raw: RawDoNotUseFile =
        serde_json::from_str(DO_NOT_USE_JSON).expect("valid abbreviation_do_not_use.json");
    let mut exact: AHashMap<String, Vec<(Option<Regex>, RawEntry)>> = AHashMap::new();
    for entry in raw.entries {
        let meaning_re = if entry.meaning.is_empty() {
            None
        } else {
            Regex::new(&format!("(?i){}", entry.meaning)).ok()
        };
        for abbr in &entry.abbr {
            exact
                .entry(norm_key(abbr))
                .or_default()
                .push((meaning_re.clone(), entry.clone()));
        }
    }
    let mut patterns = Vec::new();
    for pat in raw.patterns {
        if let Ok(re) = Regex::new(&pat.regex) {
            patterns.push((re, pat));
        }
    }
    CompiledDoNotUse { exact, patterns }
});

fn list_name(source: &str) -> String {
    if source == "The Joint Commission" {
        "The Joint Commission's Do Not Use list".to_string()
    } else {
        format!("{source}'s list of error-prone abbreviations")
    }
}

pub fn do_not_use_issues(abbreviation: &str, term: &str) -> Vec<Issue> {
    let lists = &*COMPILED_LISTS;
    let mut issues = Vec::new();

    let mut parts = vec![abbreviation];
    for p in PART_SPLIT_RE.split(abbreviation) {
        if !p.is_empty() {
            parts.push(p);
        }
    }

    let mut matched_entry: Option<&RawEntry> = None;
    'outer: for part in parts {
        let k = norm_key(part);
        if let Some(candidates) = lists.exact.get(&k) {
            for (meaning_re, entry) in candidates {
                let matches_meaning = match meaning_re {
                    None => true,
                    Some(re) => re.is_match(term),
                };
                if matches_meaning {
                    matched_entry = Some(entry);
                    break 'outer;
                }
            }
        }
    }

    if let Some(entry) = matched_entry {
        issues.push(Issue {
            code: "do_not_use".to_string(),
            level: entry.level.clone(),
            message: format!(
                "“{}” for “{}” is on {}: {}.",
                abbreviation,
                term.trim(),
                list_name(&entry.source),
                entry.problem
            ),
            source: entry.source.clone(),
            use_instead: entry.use_instead.clone(),
        });
    }

    for (re, pat) in &lists.patterns {
        if re.is_match(abbreviation) {
            issues.push(Issue {
                code: "do_not_use".to_string(),
                level: pat.level.clone(),
                message: format!("{}.", pat.problem),
                source: pat.source.clone(),
                use_instead: pat.use_instead.clone(),
            });
        }
    }

    issues
}

/// True when `term -> abbreviation` is a `block`-level Do Not Use entry.
pub fn is_blocked(abbreviation: &str, term: &str) -> bool {
    do_not_use_issues(abbreviation, term)
        .iter()
        .any(|i| i.level == "block")
}
