//! Reversible tokenization: swap PHI for stable codes `[[T1]]`, `[[T2]]`, and restore later.

use crate::regex_engine::compile_cached;
use once_cell::sync::Lazy;
use regex::Regex;
use std::collections::BTreeMap;

static JUNK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[\W\d]+$").unwrap());
static PLACEHOLDER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^\[\s*REDACT[^\]]*\]$").unwrap());

pub fn token_pattern(prefix: &str) -> Regex {
    let p = if prefix.is_empty() { "T" } else { prefix };
    Regex::new(&format!(r"\[\[{}\d+\]\]", regex::escape(p))).unwrap()
}

/// Collect `(start_byte, end_byte, matched_text)` PHI spans from structured patterns and labels.
pub fn tokenize_phi_spans(
    text: &str,
    patterns: &[String],
    labels: Option<&[String]>,
) -> Vec<(usize, usize, String)> {
    let mut spans: Vec<(usize, usize, String)> = Vec::new();

    for pat in patterns {
        if pat.is_empty() {
            continue;
        }
        if let Ok(re) = compile_cached(pat, false, false, false) {
            for (s, e) in re.find_spans(text) {
                let value = text[s..e].trim();
                if !value.is_empty() {
                    spans.push((s, e, value.to_string()));
                }
            }
        }
    }

    if let Some(labs) = labels {
        for lab in labs {
            let pat = format!(r"(?im)^\s*{}\s*:\s*(.+?)\s*$", regex::escape(lab));
            if let Ok(re) = Regex::new(&pat) {
                for caps in re.captures_iter(text) {
                    if let Some(m1) = caps.get(1) {
                        let value = m1.as_str().trim();
                        if !value.is_empty() && !JUNK_RE.is_match(value) {
                            spans.push((m1.start(), m1.end(), value.to_string()));
                        }
                    }
                }
            }
        }
    }

    // Longest first so "John Smith Jr" wins over "John Smith"
    spans.sort_by(|a, b| {
        let len_a = a.1.saturating_sub(a.0);
        let len_b = b.1.saturating_sub(b.0);
        len_b.cmp(&len_a).then_with(|| a.0.cmp(&b.0))
    });

    let mut out = Vec::new();
    let mut taken: Vec<(usize, usize)> = Vec::new();
    for (start, end, value) in spans {
        if taken.iter().any(|&(a, b)| !(end <= a || start >= b)) {
            continue;
        }
        if PLACEHOLDER_RE.is_match(&value) {
            continue;
        }
        taken.push((start, end));
        out.push((start, end, value));
    }
    out
}

/// Replace PHI values with `[[Tn]]` tokens. Returns `(new_text, value -> token map)`.
pub fn tokenize(
    text: &str,
    patterns: &[String],
    labels: Option<&[String]>,
    prefix: &str,
) -> (String, BTreeMap<String, String>) {
    let p = if prefix.is_empty() { "T" } else { prefix };
    let mut spans = tokenize_phi_spans(text, patterns, labels);
    // First assign token numbers in left-to-right or span order?
    // Note: Python sorts spans right-to-left (`sorted(spans, key=lambda s: -s[0])`) and assigns token numbers as encountered!
    spans.sort_by(|a, b| b.0.cmp(&a.0));

    let mut mapping: BTreeMap<String, String> = BTreeMap::new();
    let mut out = text.to_string();
    for (start, end, value) in spans {
        let next_idx = mapping.len() + 1;
        let tok = mapping
            .entry(value)
            .or_insert_with(|| format!("[[{p}{next_idx}]]"))
            .clone();
        out.replace_range(start..end, &tok);
    }
    (out, mapping)
}

/// Replace `[[Tn]]` tokens with their original values. Returns `(text, count)`.
pub fn untokenize(text: &str, mapping: &BTreeMap<String, String>, prefix: &str) -> (String, usize) {
    let mut reverse: BTreeMap<&str, &str> = BTreeMap::new();
    for (val, tok) in mapping {
        reverse.insert(tok.as_str(), val.as_str());
    }
    let re = token_pattern(prefix);
    let mut count = 0usize;
    let result = re.replace_all(text, |caps: &regex::Captures| {
        let m0 = caps.get(0).unwrap().as_str();
        if let Some(&val) = reverse.get(m0) {
            count += 1;
            val.to_string()
        } else {
            m0.to_string()
        }
    });
    (result.into_owned(), count)
}
