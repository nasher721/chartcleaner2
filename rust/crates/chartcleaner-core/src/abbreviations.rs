//! Shorten full medical terms into source abbreviations, or expand abbreviations
//! back to full clinical terms, in a single longest-first word-bounded pass.

use crate::abbreviation_safety::{is_blocked, MEDICAL_ABBREVIATIONS_CSV};
use crate::regex_engine::{ChangeRecord, LineCounter, MAX_TRACKED_CHANGES};
use crate::section_parser::parse_clinical_sections;
use ahash::{AHashMap, AHashSet};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::BTreeMap;
use std::sync::Arc;

#[derive(Debug, Clone)]
pub struct SourceRow {
    pub abbreviation: String,
    pub expanded: String,
    pub sections: String,
}

static PARENTHETICAL_FULL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(.+?)\s+\(([^()]*)\)$").unwrap());
static PARENTHETICAL_TAIL_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\s+\([^()]*\)$").unwrap());
static SECTION_KEY_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^a-z0-9]+").unwrap());

fn is_uncertainty(q: &str) -> bool {
    matches!(
        q.to_lowercase().as_str(),
        "likely" | "possibly" | "probable" | "uncertain"
    )
}

/// Exact `difflib.SequenceMatcher(None, a, b).ratio()` implementation for UTF-8 char slices.
pub fn sequence_matcher_ratio(a: &str, b: &str) -> f64 {
    let a_chars: Vec<char> = a.to_lowercase().chars().collect();
    let b_chars: Vec<char> = b.to_lowercase().chars().collect();
    let total = a_chars.len() + b_chars.len();
    if total == 0 {
        return 1.0;
    }
    let matches = count_matching_chars(&a_chars, &b_chars);
    (2.0 * matches as f64) / (total as f64)
}

fn count_matching_chars(a: &[char], b: &[char]) -> usize {
    if a.is_empty() || b.is_empty() {
        return 0;
    }
    let mut best_i = 0;
    let mut best_j = 0;
    let mut best_size = 0;

    // Find longest contiguous matching block
    let mut prev_j2len = vec![0usize; b.len() + 1];
    let mut curr_j2len = vec![0usize; b.len() + 1];
    for (i, &ca) in a.iter().enumerate() {
        curr_j2len.fill(0);
        for (j, &cb) in b.iter().enumerate() {
            if ca == cb {
                let k = prev_j2len[j] + 1;
                curr_j2len[j + 1] = k;
                if k > best_size {
                    best_size = k;
                    best_i = i + 1 - k;
                    best_j = j + 1 - k;
                }
            }
        }
        std::mem::swap(&mut prev_j2len, &mut curr_j2len);
    }

    if best_size == 0 {
        return 0;
    }
    count_matching_chars(&a[..best_i], &b[..best_j])
        + best_size
        + count_matching_chars(&a[best_i + best_size..], &b[best_j + best_size..])
}

pub fn same_meaning(a: &str, b: &str) -> bool {
    sequence_matcher_ratio(a, b) >= 0.85
}

pub fn display_term(term: &str) -> String {
    let first_alt = term.split(" / ").next().unwrap_or(term);
    let stripped = PARENTHETICAL_TAIL_RE.replace(first_alt, "");
    let text = stripped.trim();
    let mut chars = text.chars();
    if let (Some(c0), Some(c1)) = (chars.next(), chars.next()) {
        if c0.is_uppercase() && c1.is_lowercase() && !chars.any(|c| c.is_uppercase()) {
            let mut out = c0.to_lowercase().to_string();
            out.push(c1);
            out.push_str(chars.as_str());
            // Re-build properly from char indices
            let mut full = c0.to_lowercase().to_string();
            let rest_start = c0.len_utf8();
            full.push_str(&text[rest_start..]);
            return full;
        }
    }
    text.to_string()
}

pub static SOURCE_ROWS: Lazy<Vec<SourceRow>> = Lazy::new(|| {
    let raw = MEDICAL_ABBREVIATIONS_CSV.trim_start_matches('\u{feff}');
    let mut rdr = csv::ReaderBuilder::new()
        .has_headers(true)
        .flexible(true)
        .from_reader(raw.as_bytes());
    let headers = rdr.headers().cloned().unwrap_or_default();
    let abbr_idx = headers
        .iter()
        .position(|h| h.trim() == "Abbreviation")
        .unwrap_or(0);
    let exp_idx = headers
        .iter()
        .position(|h| h.trim() == "Expanded version")
        .unwrap_or(1);
    let sec_idx = headers.iter().position(|h| h.trim() == "Sections");

    let mut rows = Vec::new();
    for record in rdr.records().flatten() {
        let abbreviation = record.get(abbr_idx).unwrap_or("").trim().to_string();
        let expanded = record.get(exp_idx).unwrap_or("").trim().to_string();
        let sections = sec_idx
            .and_then(|i| record.get(i))
            .unwrap_or("")
            .trim()
            .to_string();
        rows.push(SourceRow {
            abbreviation,
            expanded,
            sections,
        });
    }
    rows
});

static UNCLEAR_LETTERS: Lazy<AHashSet<String>> = Lazy::new(|| {
    let mut found: AHashMap<String, Vec<String>> = AHashMap::new();
    for row in SOURCE_ROWS.iter() {
        let abbr = row.abbreviation.trim();
        let term = row.expanded.trim();
        if abbr.chars().count() != 1
            || term.is_empty()
            || term.to_lowercase().contains("not expanded in source")
        {
            continue;
        }
        let meaning = display_term(term);
        let seen = found.entry(abbr.to_string()).or_default();
        if !seen.iter().any(|other| same_meaning(&meaning, other)) {
            seen.push(meaning);
        }
    }
    let mut out: AHashSet<String> = AHashSet::new();
    for (a, seen) in found {
        if seen.len() > 1 || a == "a" || a == "I" {
            out.insert(a);
        }
    }
    out
});

fn split_top_level_slash(expanded: &str) -> Vec<String> {
    let mut parts = Vec::new();
    let mut start = 0usize;
    let mut depth = 0i32;
    let bytes = expanded.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if bytes[i] == b'(' {
            depth += 1;
        } else if bytes[i] == b')' {
            depth = (depth - 1).max(0);
        } else if depth == 0 && bytes[i..].starts_with(b" / ") {
            let p = expanded[start..i].trim();
            if !p.is_empty() {
                parts.push(p.to_string());
            }
            start = i + 3;
            i += 2;
        }
        i += 1;
    }
    if !parts.is_empty() {
        let tail = expanded[start..].trim();
        if !tail.is_empty() {
            parts.push(tail.to_string());
        }
    }
    parts
}

fn candidates_for_row(expanded: &str, abbreviation: &str) -> Vec<(String, String)> {
    let mut result = vec![(expanded.to_string(), abbreviation.to_string())];
    if let Some(caps) = PARENTHETICAL_FULL_RE.captures(expanded) {
        let base = caps.get(1).unwrap().as_str();
        let qualifier = caps.get(2).unwrap().as_str();
        if !is_uncertainty(qualifier) {
            result.push((base.to_string(), abbreviation.to_string()));
        }
    }
    if expanded.contains("(s)") {
        let singular = expanded
            .replace("(s)", "")
            .replace("  ", " ")
            .trim()
            .to_string();
        let plural = expanded.replace("(s)", "s");
        result.push((singular, abbreviation.to_string()));
        result.push((plural, abbreviation.to_string()));
    }
    if !abbreviation.contains('/') {
        for part in split_top_level_slash(expanded) {
            result.push((part, abbreviation.to_string()));
        }
    }
    result
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CustomAbbreviation {
    pub term: String,
    pub replacement: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default)]
    pub acknowledged: bool,
    #[serde(default)]
    pub sections: Vec<String>,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default)]
struct TrieNode {
    children: AHashMap<char, usize>,
    terminal: Option<usize>, // index into MatcherEntry
}

#[derive(Debug, Clone)]
struct MatcherEntry {
    term_lower: String,
    replacement: Option<String>,
    is_custom: bool,
    custom_owner: Option<String>,
}

pub struct CompiledAbbreviationMatcher {
    nodes: Vec<TrieNode>,
    entries: Vec<MatcherEntry>,
    pub row_count: usize,
}

impl CompiledAbbreviationMatcher {
    fn build(disabled: &AHashSet<String>, custom: &[CustomAbbreviation]) -> Self {
        let rows = &*SOURCE_ROWS;
        let unclear = &*UNCLEAR_LETTERS;

        let mut by_term: AHashMap<String, Option<String>> = AHashMap::new();
        let mut aliases_by_source: AHashMap<String, AHashSet<String>> = AHashMap::new();

        for row in rows {
            let expanded = row.expanded.trim();
            let abbreviation = row.abbreviation.trim();
            let exp_low = expanded.to_lowercase();
            if expanded.is_empty() || exp_low.contains("not expanded in source") {
                continue;
            }
            let replacement = if is_blocked(abbreviation, expanded) || unclear.contains(abbreviation)
            {
                None
            } else if let Some(caps) = PARENTHETICAL_FULL_RE.captures(expanded) {
                let q = caps.get(2).unwrap().as_str();
                if is_uncertainty(q) {
                    Some(format!("{abbreviation} ({q})"))
                } else {
                    Some(abbreviation.to_string())
                }
            } else {
                Some(abbreviation.to_string())
            };
            by_term.entry(exp_low.clone()).or_insert(replacement);
            aliases_by_source
                .entry(exp_low.clone())
                .or_default()
                .insert(exp_low);
        }

        for row in rows {
            let expanded = row.expanded.trim();
            let abbreviation = row.abbreviation.trim();
            let exp_low = expanded.to_lowercase();
            if expanded.is_empty() || exp_low.contains("not expanded in source") {
                continue;
            }
            let blocked = is_blocked(abbreviation, expanded) || unclear.contains(abbreviation);
            for (alias, repl) in candidates_for_row(expanded, abbreviation) {
                let key = alias.to_lowercase();
                if !key.is_empty() && !by_term.contains_key(&key) {
                    by_term.insert(key.clone(), if blocked { None } else { Some(repl) });
                }
                aliases_by_source
                    .entry(exp_low.clone())
                    .or_default()
                    .insert(key);
            }
        }

        for source in disabled {
            if let Some(aliases) = aliases_by_source.get(source) {
                for alias in aliases {
                    by_term.insert(alias.clone(), None);
                }
            } else {
                by_term.insert(source.clone(), None);
            }
        }

        let mut custom_owner: AHashMap<String, String> = AHashMap::new();
        let mut is_custom_set: AHashSet<String> = AHashSet::new();

        for item in custom {
            let key = item.term.to_lowercase();
            let active =
                item.enabled && (item.acknowledged || !is_blocked(&item.replacement, &item.term));
            let aliases: Vec<String> = aliases_by_source
                .get(&key)
                .map(|s| s.iter().cloned().collect())
                .unwrap_or_else(|| vec![key.clone()]);
            for alias in aliases {
                by_term.insert(
                    alias.clone(),
                    if active {
                        Some(item.replacement.clone())
                    } else {
                        None
                    },
                );
                if active {
                    is_custom_set.insert(alias.clone());
                }
                custom_owner.insert(alias, key.clone());
            }
        }

        // Build character-level case-insensitive Trie
        let mut nodes = vec![TrieNode::default()];
        let mut entries = Vec::with_capacity(by_term.len());

        for (term_lower, replacement) in by_term {
            if term_lower.is_empty() {
                continue;
            }
            let entry_idx = entries.len();
            let is_custom = is_custom_set.contains(&term_lower);
            let owner = custom_owner.get(&term_lower).cloned();
            entries.push(MatcherEntry {
                term_lower: term_lower.clone(),
                replacement,
                is_custom,
                custom_owner: owner,
            });

            let mut curr = 0usize;
            for ch in term_lower.chars() {
                let next = if let Some(&n) = nodes[curr].children.get(&ch) {
                    n
                } else {
                    let n = nodes.len();
                    nodes.push(TrieNode::default());
                    nodes[curr].children.insert(ch, n);
                    n
                };
                curr = next;
            }
            nodes[curr].terminal = Some(entry_idx);
        }

        Self {
            nodes,
            entries,
            row_count: rows.len(),
        }
    }
}

static MATCHER_CACHE: Lazy<Mutex<AHashMap<String, Arc<CompiledAbbreviationMatcher>>>> =
    Lazy::new(|| Mutex::new(AHashMap::new()));

fn extract_settings(cfg: Option<&Value>) -> (AHashSet<String>, Vec<CustomAbbreviation>, String) {
    let mut disabled = AHashSet::new();
    let mut custom = Vec::new();
    if let Some(group) = cfg
        .and_then(|c| c.get("abbreviations"))
        .and_then(|v| v.as_object())
    {
        if let Some(arr) = group.get("disabled").and_then(|v| v.as_array()) {
            for val in arr {
                if let Some(s) = val.as_str() {
                    let t = s.trim().to_lowercase();
                    if !t.is_empty() {
                        disabled.insert(t);
                    }
                }
            }
        }
        if let Some(arr) = group.get("custom").and_then(|v| v.as_array()) {
            for item in arr {
                if let Some(obj) = item.as_object() {
                    let enabled = match obj.get("enabled") {
                        None => true,
                        Some(Value::Bool(b)) => *b,
                        _ => continue,
                    };
                    let term = obj
                        .get("term")
                        .and_then(|v| v.as_str())
                        .map(|s| s.trim())
                        .unwrap_or("");
                    let repl = obj
                        .get("replacement")
                        .and_then(|v| v.as_str())
                        .unwrap_or("");
                    if !term.is_empty() && !repl.is_empty() {
                        let acknowledged = obj
                            .get("acknowledged")
                            .and_then(|v| v.as_bool())
                            .unwrap_or(false);
                        let sections = obj
                            .get("sections")
                            .and_then(|v| v.as_array())
                            .map(|a| {
                                a.iter()
                                    .filter_map(|x| x.as_str())
                                    .map(|s| s.trim().to_string())
                                    .filter(|s| !s.is_empty())
                                    .collect()
                            })
                            .unwrap_or_default();
                        custom.push(CustomAbbreviation {
                            term: term.to_string(),
                            replacement: repl.to_string(),
                            enabled,
                            acknowledged,
                            sections,
                        });
                    }
                }
            }
        }
    }
    let mut sorted_disabled: Vec<&String> = disabled.iter().collect();
    sorted_disabled.sort();
    let cache_key = serde_json::to_string(&(sorted_disabled, &custom)).unwrap_or_default();
    (disabled, custom, cache_key)
}

pub fn get_matcher(cfg: Option<&Value>) -> Arc<CompiledAbbreviationMatcher> {
    let (disabled, custom, key) = extract_settings(cfg);
    {
        let cache = MATCHER_CACHE.lock();
        if let Some(m) = cache.get(&key) {
            return Arc::clone(m);
        }
    }
    let built = Arc::new(CompiledAbbreviationMatcher::build(&disabled, &custom));
    MATCHER_CACHE.lock().insert(key, Arc::clone(&built));
    built
}

fn is_word_char(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// Collapse `"SAH (SAH)"` -> `"SAH"` for all applied replacement abbreviations.
static ECHO_RE_CACHE: Lazy<Mutex<AHashMap<String, Arc<Regex>>>> =
    Lazy::new(|| Mutex::new(AHashMap::new()));

fn drop_echoes(text: &str, counts: &BTreeMap<String, usize>) -> String {
    if counts.is_empty() || !text.contains('(') {
        return text.to_string();
    }
    let mut out = text.to_string();
    let mut names: Vec<&str> = counts
        .keys()
        .map(|s| s.as_str())
        .filter(|s| !s.trim().is_empty())
        .collect();
    names.sort_by(|a, b| b.len().cmp(&a.len()));

    for abbr in names {
        let paren = format!("({abbr})");
        if !out.contains(&paren) {
            continue;
        }
        let re = {
            let mut cache = ECHO_RE_CACHE.lock();
            if let Some(r) = cache.get(abbr) {
                Arc::clone(r)
            } else {
                let esc = regex::escape(abbr);
                let pat = format!(r"({esc})\s*\({esc}\)");
                match Regex::new(&pat) {
                    Ok(r) => {
                        let arc = Arc::new(r);
                        cache.insert(abbr.to_string(), Arc::clone(&arc));
                        arc
                    }
                    Err(_) => continue,
                }
            }
        };
        let mut new_out = String::with_capacity(out.len());
        let mut last = 0usize;
        for m in re.find_iter(&out) {
            let prev_is_word = out[..m.start()]
                .chars()
                .next_back()
                .is_some_and(is_word_char);
            if prev_is_word {
                continue;
            }
            new_out.push_str(&out[last..m.start()]);
            new_out.push_str(abbr);
            last = m.end();
        }
        if last > 0 {
            new_out.push_str(&out[last..]);
            out = new_out;
        }
    }
    out
}

fn section_key(name: &str) -> String {
    let replaced = name.to_lowercase().replace('&', " and ");
    SECTION_KEY_RE
        .replace_all(&replaced, " ")
        .trim()
        .to_string()
}

pub fn scoped_spans(text: &str, mode: &str, section_names: &[String]) -> Option<Vec<(usize, usize)>> {
    let parsed = parse_clinical_sections(text);
    if parsed.sections.is_empty() {
        return None;
    }
    let wanted: AHashSet<String> = section_names.iter().map(|n| section_key(n)).collect();
    let mut spans = Vec::new();
    if mode == "except" && parsed.sections[0].start_pos > 0 {
        spans.push((0, parsed.sections[0].start_pos));
    }
    for sec in &parsed.sections {
        let named =
            wanted.contains(&section_key(&sec.title)) || wanted.contains(&section_key(&sec.domain));
        if named == (mode == "only") {
            spans.push((sec.start_pos, sec.end_pos));
        }
    }
    Some(spans)
}

#[allow(clippy::too_many_arguments)]
fn abbreviate_span(
    segment: &str,
    matcher: &CompiledAbbreviationMatcher,
    mut changes: Option<&mut Vec<ChangeRecord>>,
    offset: usize,
    full: &str,
    allowed: &AHashMap<String, Vec<(usize, usize)>>,
) -> (String, BTreeMap<String, usize>) {
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut lines = LineCounter::new(full);
    let mut out = String::with_capacity(segment.len());

    // Pre-collect (byte_idx, char, lower_char) for O(1) indexed walking
    let char_indices: Vec<(usize, char)> = segment.char_indices().collect();
    let n = char_indices.len();
    let mut idx = 0usize;
    let mut last_copied_byte = 0usize;

    while idx < n {
        let (start_byte, _ch) = char_indices[idx];
        let prev_is_word = idx > 0 && is_word_char(char_indices[idx - 1].1);

        if !prev_is_word {
            // Walk Trie starting at idx
            let mut node_idx = 0usize;
            let mut best_match: Option<(usize, usize, usize)> = None; // (end_char_idx, end_byte, entry_idx)
            let mut j = idx;
            while j < n {
                let (_, cj) = char_indices[j];
                // Case-insensitive character transition (ASCII fast path + Unicode fallback)
                let cj_low = cj.to_lowercase().next().unwrap_or(cj);
                if let Some(&next_node) = matcher.nodes[node_idx].children.get(&cj_low) {
                    node_idx = next_node;
                    let end_char_idx = j + 1;
                    let next_is_word =
                        end_char_idx < n && is_word_char(char_indices[end_char_idx].1);
                    if !next_is_word {
                        if let Some(entry_idx) = matcher.nodes[node_idx].terminal {
                            let end_byte = if end_char_idx < n {
                                char_indices[end_char_idx].0
                            } else {
                                segment.len()
                            };
                            best_match = Some((end_char_idx, end_byte, entry_idx));
                        }
                    }
                    j += 1;
                } else {
                    break;
                }
            }

            if let Some((end_char_idx, end_byte, entry_idx)) = best_match {
                let entry = &matcher.entries[entry_idx];
                let matched_slice = &segment[start_byte..end_byte];

                let mut apply_repl: Option<&str> = entry.replacement.as_deref();
                if apply_repl.is_some() && entry.is_custom {
                    if let Some(owner) = &entry.custom_owner {
                        if let Some(allowed_spans) = allowed.get(owner) {
                            let pos = offset + start_byte;
                            if !allowed_spans.iter().any(|&(a, b)| a <= pos && pos < b) {
                                apply_repl = None;
                            }
                        }
                    }
                }

                if let Some(repl) = apply_repl {
                    out.push_str(&segment[last_copied_byte..start_byte]);
                    out.push_str(repl);
                    last_copied_byte = end_byte;
                    *counts.entry(repl.to_string()).or_insert(0) += 1;

                    if let Some(ch_vec) = changes.as_deref_mut() {
                        if ch_vec.len() < MAX_TRACKED_CHANGES {
                            let abs_start = offset + start_byte;
                            ch_vec.push(ChangeRecord {
                                rule: entry.term_lower.clone(),
                                rule_id: None,
                                source: Some(
                                    if entry.is_custom { "custom" } else { "bundled" }.to_string(),
                                ),
                                before: matched_slice.to_string(),
                                after: repl.to_string(),
                                line: lines.line_at(abs_start),
                                start: Some(abs_start),
                            });
                        }
                    }
                }

                // Advance past the matched span even if replacement was None!
                idx = end_char_idx;
                continue;
            }
        }
        idx += 1;
    }

    out.push_str(&segment[last_copied_byte..]);
    if !counts.is_empty() {
        out = drop_echoes(&out, &counts);
    }
    (out, counts)
}

/// Replace whole expanded medical terms in one non-cascading pass.
pub fn abbreviate(
    text: &str,
    cfg: Option<&Value>,
    mut changes: Option<&mut Vec<ChangeRecord>>,
) -> (String, usize, Value) {
    let matcher = get_matcher(cfg);
    let (_, custom, _) = extract_settings(cfg);

    let mut allowed: AHashMap<String, Vec<(usize, usize)>> = AHashMap::new();
    for item in &custom {
        if !item.sections.is_empty() {
            let spans = scoped_spans(text, "only", &item.sections).unwrap_or_default();
            allowed.insert(item.term.to_lowercase(), spans);
        }
    }

    let scope_opt = cfg
        .and_then(|c| c.get("abbreviations"))
        .and_then(|g| g.get("scope"))
        .and_then(|s| s.as_object())
        .and_then(|s| {
            let mode = s.get("mode")?.as_str()?;
            if mode != "only" && mode != "except" {
                return None;
            }
            let secs: Vec<String> = s
                .get("sections")?
                .as_array()?
                .iter()
                .filter_map(|x| x.as_str())
                .map(|x| x.trim().to_string())
                .filter(|x| !x.is_empty())
                .collect();
            if secs.is_empty() {
                None
            } else {
                Some((mode.to_string(), secs))
            }
        });

    let mut details_map = serde_json::Map::new();
    let (result, counts) = if let Some((mode, secs)) = scope_opt {
        let spans_opt = scoped_spans(text, &mode, &secs);
        let spans = match spans_opt {
            Some(s) => s,
            None => {
                details_map.insert(
                    "note".to_string(),
                    Value::String("No section headers found.".to_string()),
                );
                if mode == "except" {
                    vec![(0, text.len())]
                } else {
                    vec![]
                }
            }
        };
        let mut parts = Vec::new();
        let mut total_counts: BTreeMap<String, usize> = BTreeMap::new();
        let mut pos = 0usize;
        for (start, end) in spans {
            parts.push(text[pos..start].to_string());
            let (seg, seg_counts) = abbreviate_span(
                &text[start..end],
                &matcher,
                changes.as_deref_mut(),
                start,
                text,
                &allowed,
            );
            parts.push(seg);
            for (k, n) in seg_counts {
                *total_counts.entry(k).or_insert(0) += n;
            }
            pos = end;
        }
        parts.push(text[pos..].to_string());
        details_map.insert(
            "scope".to_string(),
            serde_json::json!({ "mode": mode, "sections": secs }),
        );
        (parts.join(""), total_counts)
    } else {
        abbreviate_span(text, &matcher, changes.as_deref_mut(), 0, text, &allowed)
    };

    let total_replacements: usize = counts.values().sum();
    details_map.insert("source_rows".to_string(), Value::from(matcher.row_count));
    details_map.insert(
        "replacements".to_string(),
        serde_json::to_value(&counts).unwrap_or_default(),
    );
    (result, total_replacements, Value::Object(details_map))
}

fn is_expandable(abbr: &str) -> bool {
    if abbr.chars().count() < 2 || abbr.chars().any(|c| c.is_whitespace()) {
        return false;
    }
    !(abbr.chars().all(|c| c.is_alphabetic()) && abbr.chars().all(|c| c.is_lowercase()))
}

static WORD_TWO_PLUS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z]{2,}").unwrap());
static SENTENCE_START_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?:^|[.!?]\s+|\n\s*)$").unwrap());

fn is_shouting(text: &str, start: usize, end: usize, known: &AHashMap<String, Option<String>>) -> bool {
    let line_start = text[..start].rfind('\n').map(|i| i + 1).unwrap_or(0);
    let line_end = text[end..]
        .find('\n')
        .map(|i| end + i)
        .unwrap_or(text.len());
    let mut line = String::with_capacity((start - line_start) + 1 + (line_end - end));
    line.push_str(&text[line_start..start]);
    line.push(' ');
    line.push_str(&text[end..line_end]);

    let words: Vec<&str> = WORD_TWO_PLUS_RE
        .find_iter(&line)
        .map(|m| m.as_str())
        .filter(|w| !known.contains_key(*w))
        .collect();
    if words.len() < 2 {
        return false;
    }
    let upper_count = words
        .iter()
        .filter(|w| w.chars().all(|c| !c.is_alphabetic() || c.is_uppercase()))
        .count();
    (upper_count as f64) / (words.len() as f64) > 0.6
}

/// Expand abbreviations back to full terms in one pass (case-sensitive).
pub fn expand(
    text: &str,
    cfg: Option<&Value>,
    mut changes: Option<&mut Vec<ChangeRecord>>,
) -> (String, usize, Value) {
    let (disabled, custom, _) = extract_settings(cfg);
    let mut prefer: AHashMap<String, String> = AHashMap::new();
    if let Some(pref_obj) = cfg
        .and_then(|c| c.get("abbreviations"))
        .and_then(|g| g.get("expand_prefer"))
        .and_then(|p| p.as_object())
    {
        for (k, v) in pref_obj {
            if let Some(s) = v.as_str() {
                if !k.trim().is_empty() && !s.trim().is_empty() {
                    prefer.insert(k.trim().to_string(), s.trim().to_string());
                }
            }
        }
    }

    let mut meanings_map: AHashMap<String, Vec<String>> = AHashMap::new();
    for row in SOURCE_ROWS.iter() {
        let term = row.expanded.trim();
        let abbr = row.abbreviation.trim();
        let term_low = term.to_lowercase();
        if term.is_empty() || abbr.is_empty() || term_low.contains("not expanded in source") {
            continue;
        }
        if disabled.contains(&term_low) {
            continue;
        }
        let meaning = display_term(term);
        let found = meanings_map.entry(abbr.to_string()).or_default();
        if !found.iter().any(|other| same_meaning(&meaning, other)) {
            found.push(meaning);
        }
    }

    let mut chosen: AHashMap<String, Option<String>> = AHashMap::new();
    for (abbr, found) in meanings_map {
        if found.len() == 1 && is_expandable(&abbr) {
            chosen.insert(abbr, found.into_iter().next());
        } else if found.len() > 1 {
            chosen.insert(abbr, None);
        }
    }
    for item in &custom {
        if item.enabled {
            chosen.insert(item.replacement.clone(), Some(display_term(&item.term)));
        }
    }
    for (abbr, meaning) in prefer {
        chosen.insert(abbr, Some(meaning));
    }

    // Build case-sensitive trie for `chosen`
    let mut nodes = vec![TrieNode::default()];
    let mut entries: Vec<(String, Option<String>)> = Vec::with_capacity(chosen.len());
    for (abbr, meaning) in &chosen {
        if abbr.is_empty() {
            continue;
        }
        let entry_idx = entries.len();
        entries.push((abbr.clone(), meaning.clone()));
        let mut curr = 0usize;
        for ch in abbr.chars() {
            let next = if let Some(&n) = nodes[curr].children.get(&ch) {
                n
            } else {
                let n = nodes.len();
                nodes.push(TrieNode::default());
                nodes[curr].children.insert(ch, n);
                n
            };
            curr = next;
        }
        nodes[curr].terminal = Some(entry_idx);
    }

    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut ambiguous: BTreeMap<String, usize> = BTreeMap::new();
    let mut lines = LineCounter::new(text);
    let mut out = String::with_capacity(text.len());

    let char_indices: Vec<(usize, char)> = text.char_indices().collect();
    let n = char_indices.len();
    let mut idx = 0usize;
    let mut last_copied_byte = 0usize;

    let is_token_or_word_prev = |c: char| is_word_char(c) || c == '[';
    let is_token_or_word_next = |c: char| is_word_char(c) || c == ']';

    while idx < n {
        let (start_byte, _) = char_indices[idx];
        let prev_bad = idx > 0 && is_token_or_word_prev(char_indices[idx - 1].1);

        if !prev_bad {
            let mut node_idx = 0usize;
            let mut best_match: Option<(usize, usize, usize)> = None;
            let mut j = idx;
            while j < n {
                let (_, cj) = char_indices[j];
                if let Some(&next_node) = nodes[node_idx].children.get(&cj) {
                    node_idx = next_node;
                    let end_char_idx = j + 1;
                    let next_bad =
                        end_char_idx < n && is_token_or_word_next(char_indices[end_char_idx].1);
                    if !next_bad {
                        if let Some(entry_idx) = nodes[node_idx].terminal {
                            let end_byte = if end_char_idx < n {
                                char_indices[end_char_idx].0
                            } else {
                                text.len()
                            };
                            best_match = Some((end_char_idx, end_byte, entry_idx));
                        }
                    }
                    j += 1;
                } else {
                    break;
                }
            }

            if let Some((end_char_idx, end_byte, entry_idx)) = best_match {
                let (ref abbr, ref meaning_opt) = entries[entry_idx];
                if !is_shouting(text, start_byte, end_byte, &chosen) {
                    if let Some(meaning_raw) = meaning_opt {
                        let mut meaning = meaning_raw.clone();
                        if SENTENCE_START_RE.is_match(&text[..start_byte]) {
                            let mut chars = meaning.chars();
                            if let Some(first) = chars.next() {
                                if first.is_lowercase() {
                                    let mut cap = first.to_uppercase().to_string();
                                    cap.push_str(chars.as_str());
                                    meaning = cap;
                                }
                            }
                        }
                        out.push_str(&text[last_copied_byte..start_byte]);
                        out.push_str(&meaning);
                        last_copied_byte = end_byte;
                        *counts.entry(abbr.clone()).or_insert(0) += 1;

                        if let Some(ch_vec) = changes.as_deref_mut() {
                            if ch_vec.len() < MAX_TRACKED_CHANGES {
                                ch_vec.push(ChangeRecord {
                                    rule: abbr.clone(),
                                    rule_id: None,
                                    source: None,
                                    before: abbr.clone(),
                                    after: meaning,
                                    line: lines.line_at(start_byte),
                                    start: Some(start_byte),
                                });
                            }
                        }
                    } else {
                        *ambiguous.entry(abbr.clone()).or_insert(0) += 1;
                    }
                }
                idx = end_char_idx;
                continue;
            }
        }
        idx += 1;
    }

    out.push_str(&text[last_copied_byte..]);
    let total: usize = counts.values().sum();
    let details = serde_json::json!({
        "expansions": counts,
        "ambiguous": ambiguous,
    });
    (out, total, details)
}
