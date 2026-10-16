//! Builtin stage runners:
//! - Regex stages (`metadata`, `boilerplate`, `structured_phi`, `literals`, `learned_rules`)
//! - Text cleanup (`whitespace`, `bullets`, `unicode_normalize`, `caps_normalize`, `line_length`, `headers`)
//! - Section & timestamp filtering (`timestamps`, `sections`)
//! - Near-duplicate folding (`duplicate_notes`, `fuzzy_dedup`, `fuzzy_line_dedup`)
//! - Privacy (`nlp_redaction`, `clinical_identifiers`, `tokenize_phi`)
//! - Abbreviations (`abbreviations`, `expand_abbreviations`)

use crate::abbreviations::{abbreviate, expand};
use crate::clinical_identifiers::scan_clinical_identifiers;
use crate::clinical_whitelist::run_nlp_redaction;
use crate::config::{get_bool_opt, get_int_opt, get_str_opt};
use crate::regex_engine::{apply_rules, compile_cached, ChangeRecord};
use crate::tokens::tokenize;
use ahash::{AHashMap, AHashSet};
use once_cell::sync::Lazy;
use parking_lot::Mutex;
use regex::Regex;
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::sync::Arc;

pub const DEFAULT_NOTE_SPLIT: &str = r"(?=^(?:Progress Notes by .+|Attestation signed by .+|[A-Z][a-zA-Z,.\s\-]+ at \d{1,2}/\d{1,2}/\d{2,4}\s+\d{1,2}:\d{2}\s*(?:[APap]\.?[Mm]\.?)?.*)$)";

pub const BUILTIN_TIMESTAMP_PATTERNS: &[&str] = &[
    r"\b\d{4}-\d{1,2}-\d{1,2}(?:[ T]\d{1,2}:\d{2}(?::\d{2})?(?:\.\d+)?)?(?:\s?(?:Z|[+-]\d{2}:?\d{2}))?\b",
    r"\b\d{1,2}[/.]\d{1,2}[/.]\d{2,4}(?:\s+\d{1,2}:\d{2}(?::\d{2})?)?\b",
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}(?:,?\s+\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?)?\b",
];

pub const CLOCK_TIME_PATTERN: &str =
    r"(?<![\d:])\b\d{1,2}:\d{2}(?::\d{2})?\s*(?:[APap]\.?[Mm]\.?)?\b(?![\d:])";

static CRLF_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\r\n?").unwrap());
static STRIP_LEADING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)^[ \t]+").unwrap());
static TRIM_TRAILING_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?m)[ \t]+$").unwrap());
static COLLAPSE_SPACES_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r" {2,}").unwrap());
static COLLAPSE_BLANK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{3,}").unwrap());

fn replace_all_counted(re: &Regex, text: &str, repl: &str) -> (String, usize) {
    let mut count = 0usize;
    let mut out = String::with_capacity(text.len());
    let mut last = 0usize;
    for m in re.find_iter(text) {
        out.push_str(&text[last..m.start()]);
        out.push_str(repl);
        last = m.end();
        count += 1;
    }
    if count == 0 {
        return (text.to_string(), 0);
    }
    out.push_str(&text[last..]);
    (out, count)
}

// ---------------------------------------------------------------------------
// Text normalization stages
// ---------------------------------------------------------------------------

pub fn run_whitespace(text: &str, cfg: &Value) -> (String, usize, Value) {
    let mut out = text.to_string();
    let mut total = 0usize;

    let crlf_to_lf = get_bool_opt(cfg, "whitespace", "crlf_to_lf", false);
    let strip_leading = get_bool_opt(cfg, "whitespace", "strip_leading", false);
    let normalize_tabs = get_bool_opt(cfg, "whitespace", "normalize_tabs", false);
    let trim_trailing = get_bool_opt(cfg, "whitespace", "trim_trailing", true);
    let collapse_spaces = get_bool_opt(cfg, "whitespace", "collapse_spaces", false);
    let collapse_blank_lines = get_bool_opt(cfg, "whitespace", "collapse_blank_lines", true);
    let final_trim = get_bool_opt(cfg, "whitespace", "final_trim", true);

    if crlf_to_lf && out.contains('\r') {
        let (next, k) = replace_all_counted(&CRLF_RE, &out, "\n");
        out = next;
        total += k;
    }
    if strip_leading {
        let (next, k) = replace_all_counted(&STRIP_LEADING_RE, &out, "");
        out = next;
        total += k;
    }
    if normalize_tabs && out.contains('\t') {
        let k = out.matches('\t').count();
        out = out.replace('\t', "    ");
        total += k;
    }
    if trim_trailing {
        let (next, k) = replace_all_counted(&TRIM_TRAILING_RE, &out, "");
        out = next;
        total += k;
    }
    if collapse_spaces {
        let (next, k) = replace_all_counted(&COLLAPSE_SPACES_RE, &out, " ");
        out = next;
        total += k;
    }
    if collapse_blank_lines {
        let (next, k) = replace_all_counted(&COLLAPSE_BLANK_RE, &out, "\n\n");
        out = next;
        total += k;
    }
    if final_trim {
        let stripped = out.trim();
        if stripped.len() != out.len() {
            total += 1;
            out = stripped.to_string();
        }
    }

    (out, total, json!({}))
}

pub fn run_bullets(text: &str, cfg: &Value) -> (String, usize, Value) {
    let style = cfg
        .get("bullets")
        .and_then(|b| b.get("style"))
        .and_then(|v| {
            if v.is_null() {
                None
            } else {
                v.as_str().map(|s| s.to_string())
            }
        })
        .unwrap_or_else(|| "- ".to_string());
    let skip_empty = get_bool_opt(cfg, "bullets", "skip_empty", false);
    let normalize_numbered = get_bool_opt(cfg, "bullets", "normalize_numbered", false);

    let extra_glyphs: String = cfg
        .get("bullets")
        .and_then(|b| b.get("extra_glyphs"))
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|x| x.as_str()).collect())
        .unwrap_or_default();

    let glyphs = format!("[•\\-*{}]", regex::escape(&extra_glyphs));
    let mut out = text.to_string();
    let mut total = 0usize;

    if skip_empty {
        if let Ok(re) = Regex::new(&format!(r"(?m)^\s*{glyphs}[ \t]*$\n?")) {
            let (next, k) = replace_all_counted(&re, &out, "");
            out = next;
            total += k;
        }
    }

    if normalize_numbered {
        if let Ok(re) = Regex::new(r"(?m)^\s*\d+[.)]\s+") {
            let (next, k) = replace_all_counted(&re, &out, &style);
            out = next;
            total += k;
        }
    }

    if let Ok(re) = Regex::new(&format!(r"(?m)^\s*{glyphs}\s+")) {
        let (next, k) = replace_all_counted(&re, &out, &style);
        out = next;
        total += k;
    }

    (out, total, json!({}))
}

pub fn run_unicode(text: &str, cfg: &Value) -> (String, usize, Value) {
    let groups: &[(&str, &[(&str, &str)])] = &[
        (
            "quotes",
            &[
                ("\u{201c}", "\""),
                ("\u{201d}", "\""),
                ("\u{2018}", "'"),
                ("\u{2019}", "'"),
                ("\u{201a}", ","),
                ("\u{201e}", "\""),
            ],
        ),
        (
            "dashes",
            &[("\u{2013}", "-"), ("\u{2014}", "-"), ("\u{2212}", "-")],
        ),
        ("nbsp", &[("\u{00a0}", " ")]),
        (
            "zero_width",
            &[
                ("\u{200b}", ""),
                ("\u{200c}", ""),
                ("\u{200d}", ""),
                ("\u{feff}", ""),
            ],
        ),
        ("ellipsis", &[("\u{2026}", "...")]),
        (
            "ligatures",
            &[
                ("\u{fb00}", "ff"),
                ("\u{fb01}", "fi"),
                ("\u{fb02}", "fl"),
                ("\u{fb03}", "ffi"),
                ("\u{fb04}", "ffl"),
            ],
        ),
    ];

    let mut out = text.to_string();
    let mut total = 0usize;
    for (opt, pairs) in groups {
        if !get_bool_opt(cfg, "unicode_normalize", opt, false) {
            continue;
        }
        for (old, new) in *pairs {
            let n = out.matches(old).count();
            if n > 0 {
                out = out.replace(old, new);
                total += n;
            }
        }
    }
    (out, total, json!({}))
}

pub fn run_caps(text: &str, cfg: &Value) -> (String, usize, Value) {
    let mode = get_str_opt(cfg, "caps_normalize", "mode", "off");
    if mode != "sentence" {
        return (text.to_string(), 0, json!({}));
    }
    let min_chars = get_int_opt(cfg, "caps_normalize", "min_chars", 40).max(1) as usize;
    let preserve_words: Vec<String> = cfg
        .get("caps_normalize")
        .and_then(|c| c.get("preserve_words"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| regex::escape(s))
                .collect()
        })
        .unwrap_or_default();

    let preserve_re = if preserve_words.is_empty() {
        None
    } else {
        Regex::new(&format!(r"(?i)\b({})\b", preserve_words.join("|"))).ok()
    };

    let mut lines: Vec<String> = text.split('\n').map(|s| s.to_string()).collect();
    let mut changed = 0usize;

    for line in &mut lines {
        let trimmed = line.trim();
        let char_len = trimmed.chars().count();
        let has_alpha = trimmed.chars().any(|c| c.is_alphabetic());
        let all_upper = trimmed
            .chars()
            .filter(|c| c.is_alphabetic())
            .all(|c| c.is_uppercase());
        if char_len < min_chars || !has_alpha || !all_upper {
            continue;
        }

        let low = trimmed.to_lowercase();
        let mut chars = low.chars();
        let mut sentence = match chars.next() {
            Some(first) => {
                let mut s = first.to_uppercase().to_string();
                s.push_str(chars.as_str());
                s
            }
            None => continue,
        };

        if let Some(ref pre) = preserve_re {
            sentence = pre
                .replace_all(&sentence, |caps: &regex::Captures| {
                    caps.get(1).unwrap().as_str().to_uppercase()
                })
                .into_owned();
        }

        let indent_len = line.len() - line.trim_start().len();
        let indent = &line[..indent_len];
        *line = format!("{indent}{sentence}");
        changed += 1;
    }

    (lines.join("\n"), changed, json!({}))
}

pub fn run_line_length(text: &str, cfg: &Value) -> (String, usize, Value) {
    let mode = get_str_opt(cfg, "line_length", "mode", "off");
    let max_chars = get_int_opt(cfg, "line_length", "max_chars", 200);
    if mode == "off" || max_chars < 10 {
        return (text.to_string(), 0, json!({}));
    }
    let max_c = max_chars as usize;
    let marker = get_str_opt(cfg, "line_length", "marker", "…");
    let marker = if marker.is_empty() { "…".to_string() } else { marker };

    let mut count = 0usize;
    let mut out: Vec<String> = Vec::new();

    for line in text.split('\n') {
        let char_count = line.chars().count();
        if char_count <= max_c {
            out.push(line.to_string());
            continue;
        }
        if mode == "truncate" {
            let truncated: String = line.chars().take(max_c).collect();
            out.push(format!("{}{}", truncated.trim_end(), marker));
        } else {
            out.extend(wrap_line(line, max_c));
        }
        count += 1;
    }

    (out.join("\n"), count, json!({}))
}

fn wrap_line(line: &str, max_chars: usize) -> Vec<String> {
    let indent_bytes = line.len() - line.trim_start().len();
    let indent = &line[..indent_bytes];
    let indent_chars = indent.chars().count();
    let body = &line[indent_bytes..];
    let width = max_chars.saturating_sub(indent_chars).max(1);

    let mut wrapped: Vec<String> = Vec::new();
    let mut current = String::new();
    let mut current_len = 0usize;

    for word in body.split_whitespace() {
        let wlen = word.chars().count();
        if current.is_empty() {
            current.push_str(word);
            current_len = wlen;
        } else if current_len + 1 + wlen <= width {
            current.push(' ');
            current.push_str(word);
            current_len += 1 + wlen;
        } else {
            wrapped.push(format!("{indent}{current}"));
            current = word.to_string();
            current_len = wlen;
        }
    }
    if !current.is_empty() || wrapped.is_empty() {
        wrapped.push(format!("{indent}{current}"));
    }
    wrapped
}

pub fn run_headers(text: &str, cfg: &Value) -> (String, usize, Value) {
    let level = get_int_opt(cfg, "header_options", "level", 2).clamp(1, 6) as usize;
    let bold = get_bool_opt(cfg, "header_options", "bold", false);
    let keep_colon = get_bool_opt(cfg, "header_options", "keep_colon", false);
    let upper_only = get_bool_opt(cfg, "header_options", "uppercase_only", false);

    let hashes = "#".repeat(level);
    let style_header = |title: &str, colon: &str| -> String {
        if bold {
            format!("**{title}{colon}**")
        } else {
            format!("{hashes} {title}{colon}")
        }
    };

    let headers: Vec<String> = cfg
        .get("clinical_headers")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();

    let mut out = text.to_string();
    let mut total = 0usize;

    static HEADER_RE_CACHE: Lazy<Mutex<AHashMap<(String, bool), Arc<Regex>>>> =
        Lazy::new(|| Mutex::new(AHashMap::new()));

    for h in headers {
        // IMPORTANT: In Python, `^\s*({h})\s*:?\s*$` with MULTILINE matches `\n` inside `^\s*`,
        // which consumes any blank line immediately preceding the header!
        let re = {
            let mut cache = HEADER_RE_CACHE.lock();
            let key = (h.clone(), keep_colon);
            if let Some(r) = cache.get(&key) {
                Arc::clone(r)
            } else {
                let pat = if keep_colon {
                    format!(r"(?im)^\s*({h})(\s*:)?\s*$")
                } else {
                    format!(r"(?im)^\s*({h})\s*:?\s*$")
                };
                match Regex::new(&pat) {
                    Ok(r) => {
                        let arc = Arc::new(r);
                        cache.insert(key, Arc::clone(&arc));
                        arc
                    }
                    Err(_) => continue,
                }
            }
        };
        let mut count_this = 0usize;
        let next = re.replace_all(&out, |caps: &regex::Captures| {
            let m0 = caps.get(0).unwrap().as_str();
            let g1 = caps.get(1).unwrap().as_str();
            count_this += 1;
            if upper_only {
                let letters: Vec<char> = g1.chars().filter(|c| c.is_alphabetic()).collect();
                if !letters.is_empty() && !letters.iter().all(|c| c.is_uppercase()) {
                    return m0.to_string();
                }
            }
            let colon = if keep_colon {
                caps.get(2).map(|m| m.as_str()).unwrap_or("")
            } else {
                ""
            };
            style_header(g1, colon)
        });
        if count_this > 0 {
            out = next.into_owned();
            total += count_this;
        }
    }

    (out, total, json!({}))
}

// ---------------------------------------------------------------------------
// Timestamps and Sections stages
// ---------------------------------------------------------------------------

pub fn run_timestamps(text: &str, cfg: &Value, logs: &mut Vec<String>) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "timestamp_removal", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let repl = get_str_opt(cfg, "timestamp_removal", "replacement", "");
    let builtin = get_bool_opt(cfg, "timestamp_removal", "builtin_patterns", true);
    let remove_clock = get_bool_opt(cfg, "timestamp_removal", "remove_clock_times", false);

    let mut patterns: Vec<String> = Vec::new();
    if builtin {
        patterns.extend(BUILTIN_TIMESTAMP_PATTERNS.iter().map(|s| s.to_string()));
    }
    if let Some(arr) = cfg
        .get("timestamp_removal")
        .and_then(|t| t.get("extra_patterns"))
        .and_then(|v| v.as_array())
    {
        for p in arr {
            if let Some(s) = p.as_str() {
                if !s.is_empty() {
                    patterns.push(s.to_string());
                }
            }
        }
    }

    let mut out = text.to_string();
    let mut total = 0usize;

    for p in patterns {
        match compile_cached(&p, false, false, false) {
            Ok(re) => {
                let (next, k) = re.replace_all(&out, &repl);
                out = next;
                total += k;
            }
            Err(_) => {
                logs.push(format!("bad timestamp pattern skipped: {p:?}"));
            }
        }
    }

    if remove_clock {
        if let Ok(re) = compile_cached(CLOCK_TIME_PATTERN, false, false, false) {
            let (next, k) = re.replace_all(&out, &repl);
            out = next;
            total += k;
        }
    }

    (out, total, json!({}))
}

pub fn run_sections(text: &str, cfg: &Value, logs: &mut Vec<String>) -> (String, usize, Value) {
    let mode = get_str_opt(cfg, "section_filter", "mode", "off");
    if mode != "drop" && mode != "keep" {
        return (text.to_string(), 0, json!({}));
    }

    let names: Vec<String> = cfg
        .get("section_filter")
        .and_then(|s| s.get("sections"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();
    if names.is_empty() {
        return (text.to_string(), 0, json!({}));
    }

    let mut boundary: Vec<String> = cfg
        .get("section_filter")
        .and_then(|s| s.get("boundary_headers"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .filter(|s| !s.is_empty())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();
    if boundary.is_empty() {
        boundary = cfg
            .get("clinical_headers")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|x| x.as_str())
                    .filter(|s| !s.is_empty())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();
    }
    if boundary.is_empty() {
        boundary = names.clone();
    }

    let bound_pat = format!(r"(?im)^[ \t]*({})[ \t]*:?[ \t]*$", boundary.join("|"));
    let member_pat = format!(r"(?im)^[ \t]*(?:{})[ \t]*:?[ \t]*$", names.join("|"));

    let (bound_re, member_re) = match (Regex::new(&bound_pat), Regex::new(&member_pat)) {
        (Ok(b), Ok(m)) => (b, m),
        (Err(e), _) | (_, Err(e)) => {
            logs.push(format!("section_filter: bad header name ({e}); stage skipped."));
            return (text.to_string(), 0, json!({}));
        }
    };

    let bounds: Vec<(usize, String)> = bound_re
        .captures_iter(text)
        .map(|caps| {
            let m0 = caps.get(0).unwrap();
            let g1 = caps.get(1).unwrap().as_str().to_string();
            (m0.start(), g1)
        })
        .collect();

    if bounds.is_empty() {
        return (text.to_string(), 0, json!({}));
    }

    let keep_preamble = get_bool_opt(cfg, "section_filter", "keep_preamble", true);
    let first_start = bounds[0].0;
    let mut out = if keep_preamble {
        text[..first_start].to_string()
    } else {
        String::new()
    };
    let mut pos = first_start;
    let mut removed: Vec<String> = Vec::new();

    for (i, (start, header)) in bounds.iter().enumerate() {
        let seg_end = if i + 1 < bounds.len() {
            bounds[i + 1].0
        } else {
            text.len()
        };
        let first_line = text[*start..seg_end].split('\n').next().unwrap_or("");
        let is_member = member_re.is_match(first_line);
        let should_remove = if mode == "drop" {
            is_member
        } else {
            !is_member
        };
        if should_remove {
            removed.push(header.trim().to_string());
        } else {
            out.push_str(&text[pos..seg_end]);
        }
        pos = seg_end;
    }
    out.push_str(&text[pos..]);
    let count = removed.len();
    (out, count, json!({ "sections_removed": removed }))
}

// ---------------------------------------------------------------------------
// Near-duplicate folding stages (`duplicate_notes`, `fuzzy_dedup`, `fuzzy_line_dedup`)
// ---------------------------------------------------------------------------

/// RapidFuzz `fuzz.ratio(a, b)` = normalized Indel similarity `100.0 * (2 * LCS(a, b)) / (len(a) + len(b))`.
/// Includes an optional `score_cutoff` early exit for $O(1)$ length rejection and banded DP.
pub fn fuzz_ratio_cutoff(a: &str, b: &str, cutoff: f64) -> f64 {
    if a == b {
        return 100.0;
    }
    let a_chars: Vec<char> = a.chars().collect();
    let b_chars: Vec<char> = b.chars().collect();
    let len_a = a_chars.len();
    let len_b = b_chars.len();
    let total = len_a + len_b;
    if total == 0 {
        return 100.0;
    }

    // Upper bound on similarity is when the shorter string is a subsequence of the longer string:
    let max_possible = (200.0 * (len_a.min(len_b) as f64)) / (total as f64);
    if max_possible < cutoff {
        return 0.0;
    }

    // Trim common prefix and suffix to shrink the DP matrix
    let mut prefix = 0usize;
    while prefix < len_a && prefix < len_b && a_chars[prefix] == b_chars[prefix] {
        prefix += 1;
    }
    let mut suffix = 0usize;
    while suffix < (len_a - prefix)
        && suffix < (len_b - prefix)
        && a_chars[len_a - 1 - suffix] == b_chars[len_b - 1 - suffix]
    {
        suffix += 1;
    }

    let a_mid = &a_chars[prefix..len_a - suffix];
    let b_mid = &b_chars[prefix..len_b - suffix];

    let mid_lcs = if a_mid.is_empty() || b_mid.is_empty() {
        0
    } else {
        lcs_len(a_mid, b_mid)
    };

    let lcs = prefix + suffix + mid_lcs;
    (200.0 * (lcs as f64)) / (total as f64)
}

pub fn fuzz_ratio(a: &str, b: &str) -> f64 {
    fuzz_ratio_cutoff(a, b, 0.0)
}

fn lcs_len(a: &[char], b: &[char]) -> usize {
    let (short, long) = if a.len() <= b.len() { (a, b) } else { (b, a) };
    let mut dp = vec![0usize; short.len() + 1];
    for &cl in long {
        let mut prev_diag = 0usize;
        for (j, &cs) in short.iter().enumerate() {
            let temp = dp[j + 1];
            if cl == cs {
                dp[j + 1] = prev_diag + 1;
            } else if dp[j] > dp[j + 1] {
                dp[j + 1] = dp[j];
            }
            prev_diag = temp;
        }
    }
    dp[short.len()]
}

fn split_by_pattern(text: &str, split_pattern: &str) -> Vec<String> {
    // Handle lookahead split pattern `(?=...)` cleanly by finding match starts of inner pattern
    let inner = if let Some(stripped) = split_pattern
        .strip_prefix("(?=")
        .and_then(|s| s.strip_suffix(')'))
    {
        Some(stripped)
    } else {
        None
    };

    if let Some(inner_pat) = inner {
        if let Ok(re) = Regex::new(&format!("(?im){inner_pat}")) {
            let starts: Vec<usize> = re.find_iter(text).map(|m| m.start()).collect();
            if starts.is_empty() {
                return vec![text.to_string()];
            }
            let mut parts = Vec::with_capacity(starts.len() + 1);
            parts.push(text[..starts[0]].to_string());
            for i in 0..starts.len() {
                let start = starts[i];
                let end = if i + 1 < starts.len() {
                    starts[i + 1]
                } else {
                    text.len()
                };
                parts.push(text[start..end].to_string());
            }
            return parts;
        }
    }

    // Fallback for arbitrary split_pattern using compile_cached
    if let Ok(re) = compile_cached(split_pattern, false, true, false) {
        let matches = re.find_spans(text);
        if matches.is_empty() {
            return vec![text.to_string()];
        }
        let mut parts = Vec::with_capacity(matches.len() + 1);
        let mut last = 0usize;
        for (s, e) in matches {
            parts.push(text[last..s].to_string());
            last = e;
        }
        parts.push(text[last..].to_string());
        return parts;
    }

    vec![text.to_string()]
}

fn extract_note_body(segment: &str) -> &str {
    let s = segment.trim();
    match s.split_once('\n') {
        Some((_, body)) => body.trim(),
        None => "",
    }
}

pub fn run_duplicate_notes(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "duplicate_note_detection", "enabled", true) {
        return (text.to_string(), 0, json!({}));
    }
    let split_pattern = get_str_opt(
        cfg,
        "duplicate_note_detection",
        "split_pattern",
        DEFAULT_NOTE_SPLIT,
    );
    let min_body = get_int_opt(cfg, "duplicate_note_detection", "min_body_chars", 400).max(0) as usize;
    let threshold = get_int_opt(
        cfg,
        "duplicate_note_detection",
        "similarity_threshold",
        90,
    ) as f64;

    let parts = split_by_pattern(text, &split_pattern);
    if parts.len() <= 1 {
        return (text.to_string(), 0, json!({}));
    }

    let mut kept = vec![parts[0].clone()];
    let mut seen_bodies: Vec<String> = Vec::new();
    let mut seen_set: AHashSet<String> = AHashSet::new();
    let mut dropped = 0usize;

    for seg in &parts[1..] {
        let body = extract_note_body(seg);
        if body.chars().count() < min_body {
            kept.push(seg.clone());
            continue;
        }
        let body_low = body.to_lowercase();
        if seen_set.contains(&body_low)
            || seen_bodies
                .iter()
                .any(|cand| fuzz_ratio_cutoff(&body_low, cand, threshold) >= threshold)
        {
            dropped += 1;
            continue;
        }
        seen_set.insert(body_low.clone());
        seen_bodies.push(body_low);
        kept.push(seg.clone());
    }

    let notes_kept = kept.len();
    (kept.join(""), dropped, json!({ "notes_kept": notes_kept }))
}

pub fn run_fuzzy_dedup(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "fuzzy_dedup", "enabled", true) {
        return (text.to_string(), 0, json!({}));
    }
    let threshold = get_int_opt(cfg, "fuzzy_dedup", "threshold", 95) as f64;
    let min_chars = get_int_opt(cfg, "fuzzy_dedup", "min_chars", 100).max(0) as usize;

    let paragraphs: Vec<&str> = text.split("\n\n").collect();
    let mut deduped: Vec<&str> = Vec::with_capacity(paragraphs.len());
    let mut eligible: Vec<String> = Vec::new();
    let mut eligible_set: AHashSet<String> = AHashSet::new();
    let mut dropped = 0usize;

    for p in paragraphs {
        let p_clean = p.trim();
        let char_len = p_clean.chars().count();
        if char_len < min_chars {
            deduped.push(p);
            continue;
        }
        let cand_low = p_clean.to_lowercase();
        if eligible_set.contains(&cand_low)
            || eligible
                .iter()
                .any(|cand| fuzz_ratio_cutoff(&cand_low, cand, threshold) >= threshold)
        {
            dropped += 1;
        } else {
            deduped.push(p);
            if char_len > min_chars {
                eligible_set.insert(cand_low.clone());
                eligible.push(cand_low);
            }
        }
    }

    let kept_len = deduped.len();
    (
        deduped.join("\n\n"),
        dropped,
        json!({ "paragraphs_kept": kept_len }),
    )
}

pub fn run_fuzzy_line_dedup(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "fuzzy_line_dedup", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let threshold = get_int_opt(cfg, "fuzzy_line_dedup", "threshold", 92) as f64;
    let min_chars = get_int_opt(cfg, "fuzzy_line_dedup", "min_chars", 8).max(0) as usize;
    let window = get_int_opt(cfg, "fuzzy_line_dedup", "window", 50).max(1) as usize;

    let lines: Vec<&str> = text.split('\n').collect();
    if lines.len() <= 1 {
        return (text.to_string(), 0, json!({}));
    }

    let mut recent: VecDeque<String> = VecDeque::with_capacity(window);
    let mut dropped = 0usize;
    let mut kept: Vec<&str> = Vec::with_capacity(lines.len());

    for line in lines {
        let stripped = line.trim();
        if stripped.chars().count() < min_chars {
            kept.push(line);
            continue;
        }
        let low = stripped.to_lowercase();
        if recent
            .iter()
            .any(|cand| cand == &low || fuzz_ratio_cutoff(&low, cand, threshold) >= threshold)
        {
            dropped += 1;
        } else {
            kept.push(line);
            if recent.len() == window {
                recent.pop_front();
            }
            recent.push_back(low);
        }
    }

    let kept_len = kept.len();
    (kept.join("\n"), dropped, json!({ "lines_kept": kept_len }))
}

// ---------------------------------------------------------------------------
// Regex & Privacy runners
// ---------------------------------------------------------------------------

pub fn run_regex_list(
    text: &str,
    cfg: &Value,
    key: &str,
    multi_line: bool,
    dot_all: bool,
    sid: &str,
    track_changes: bool,
    _logs: &mut Vec<String>,
) -> (String, usize, Value) {
    let empty_map = serde_json::Map::new();
    let cfg_map = cfg.as_object().unwrap_or(&empty_map);
    let enabled = crate::config::get_stage_options(cfg_map, sid)
        .and_then(|o| o.get("enabled"))
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !enabled {
        return (text.to_string(), 0, json!({}));
    }
    let rules: Vec<(String, String)> = cfg
        .get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .map(|p| (p.to_string(), String::new()))
                .collect()
        })
        .unwrap_or_default();
    let (out, matched, details) =
        apply_rules(text, cfg_map, track_changes, &rules, true, multi_line, dot_all, Some(sid), false);
    (out, matched, Value::Object(details))
}

pub fn run_regex_pairs(
    text: &str,
    cfg: &Value,
    key: &str,
    phi: bool,
    sid: &str,
    track_changes: bool,
    _logs: &mut Vec<String>,
) -> (String, usize, Value) {
    let empty_map = serde_json::Map::new();
    let cfg_map = cfg.as_object().unwrap_or(&empty_map);
    let enabled = crate::config::get_stage_options(cfg_map, sid)
        .and_then(|o| o.get("enabled"))
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    if !enabled {
        return (text.to_string(), 0, json!({}));
    }
    let rules: Vec<(String, String)> = cfg
        .get(key)
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    let pair = item.as_array()?;
                    if pair.len() != 2 {
                        return None;
                    }
                    Some((pair[0].as_str()?.to_string(), pair[1].as_str()?.to_string()))
                })
                .collect()
        })
        .unwrap_or_default();
    let (out, matched, details) =
        apply_rules(text, cfg_map, track_changes, &rules, true, false, false, Some(sid), phi);
    (out, matched, Value::Object(details))
}

pub fn run_learned(
    text: &str,
    cfg: &Value,
    track_changes: bool,
    logs: &mut Vec<String>,
) -> (String, usize, Value) {
    let has_rules = cfg
        .get("learned_rules")
        .and_then(|v| v.as_array())
        .is_some_and(|a| !a.is_empty());
    if !has_rules {
        return (text.to_string(), 0, json!({}));
    }
    run_regex_pairs(
        text,
        cfg,
        "learned_rules",
        false,
        "learned_rules",
        track_changes,
        logs,
    )
}

pub fn run_clinical_identifiers_stage(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "clinical_identifiers", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let entities = scan_clinical_identifiers(text);
    if entities.is_empty() {
        return (text.to_string(), 0, json!({}));
    }

    let redact_npi = get_bool_opt(cfg, "clinical_identifiers", "redact_npi", true);
    let redact_dea = get_bool_opt(cfg, "clinical_identifiers", "redact_dea", true);
    let redact_udi = get_bool_opt(cfg, "clinical_identifiers", "redact_udi", true);
    let custom_repl = cfg
        .get("clinical_identifiers")
        .and_then(|c| c.get("replacement"))
        .and_then(|v| v.as_str());

    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    let mut out = text.to_string();
    let mut matched = 0usize;

    let mut sorted = entities;
    sorted.sort_by(|a, b| b.start.cmp(&a.start));

    for ent in sorted {
        if ent.entity_type == "NPI" && !redact_npi {
            continue;
        }
        if ent.entity_type == "DEA_NUMBER" && !redact_dea {
            continue;
        }
        if (ent.entity_type == "UDI_DEVICE_ID" || ent.entity_type == "ACCESSION_NUMBER")
            && !redact_udi
        {
            continue;
        }
        let repl = match custom_repl {
            Some(r) => r.to_string(),
            None => format!("[REDACTED_{}]", ent.entity_type),
        };
        out.replace_range(ent.start..ent.end, &repl);
        *counts.entry(ent.entity_type).or_insert(0) += 1;
        matched += 1;
    }

    let details = if counts.is_empty() {
        json!({})
    } else {
        json!({ "phi": counts })
    };
    (out, matched, details)
}

pub fn run_tokenize_stage(text: &str, cfg: &Value) -> (String, usize, Value) {
    let prefix = get_str_opt(cfg, "tokenization", "prefix", "T");
    let patterns: Vec<String> = cfg
        .get("epic_phi_patterns")
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|item| {
                    if let Some(s) = item.as_str() {
                        Some(s.to_string())
                    } else if let Some(pair) = item.as_array() {
                        pair.first()?.as_str().map(|s| s.to_string())
                    } else {
                        None
                    }
                })
                .collect()
        })
        .unwrap_or_default();

    let default_labels = vec![
        "Patient".to_string(),
        "Patient Name".to_string(),
        "Pt".to_string(),
        "Attending".to_string(),
        "Provider".to_string(),
        "PCP".to_string(),
        "Referred by".to_string(),
        "Signed by".to_string(),
        "Author".to_string(),
    ];

    let (new_text, mapping) = tokenize(text, &patterns, Some(&default_labels), &prefix);
    let count = mapping.len();
    let mut details = serde_json::Map::new();
    details.insert("tokens_issued".to_string(), Value::from(count));
    if !mapping.is_empty() {
        details.insert(
            "token_map".to_string(),
            serde_json::to_value(&mapping).unwrap_or_default(),
        );
        details.insert("phi".to_string(), json!({ "tokenized": count }));
    }
    (new_text, count, Value::Object(details))
}

pub fn run_nlp_stage(text: &str, cfg: &Value) -> (String, usize, Value) {
    let (new_text, matched, counts) = run_nlp_redaction(text, cfg);
    (new_text, matched, json!({ "phi": counts }))
}

pub fn run_abbreviations_stage(
    text: &str,
    cfg: &Value,
    track_changes: bool,
) -> (String, usize, Value) {
    let mut changes: Vec<ChangeRecord> = Vec::new();
    let (out, count, mut details) = abbreviate(
        text,
        Some(cfg),
        if track_changes {
            Some(&mut changes)
        } else {
            None
        },
    );
    if let Some(obj) = details.as_object_mut() {
        let mut rule_hits: BTreeMap<String, usize> = BTreeMap::new();
        if let Some(repls) = obj.get("replacements").and_then(|v| v.as_object()) {
            for (k, v) in repls {
                if let Some(n) = v.as_u64() {
                    rule_hits.insert(format!("abbr:{k}"), n as usize);
                }
            }
        }
        if !rule_hits.is_empty() {
            obj.insert(
                "rule_hits".to_string(),
                serde_json::to_value(rule_hits).unwrap_or_default(),
            );
        }
        if track_changes && !changes.is_empty() {
            obj.insert(
                "changes".to_string(),
                serde_json::to_value(changes).unwrap_or_default(),
            );
        }
    }
    (out, count, details)
}

pub fn run_expand_abbreviations_stage(
    text: &str,
    cfg: &Value,
    track_changes: bool,
) -> (String, usize, Value) {
    let mut changes: Vec<ChangeRecord> = Vec::new();
    let (out, count, mut details) = expand(
        text,
        Some(cfg),
        if track_changes {
            Some(&mut changes)
        } else {
            None
        },
    );
    if track_changes && !changes.is_empty() {
        if let Some(obj) = details.as_object_mut() {
            obj.insert(
                "changes".to_string(),
                serde_json::to_value(changes).unwrap_or_default(),
            );
        }
    }
    (out, count, details)
}
