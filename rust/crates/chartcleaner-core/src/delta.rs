//! Semantic copy-forward note differencing engine ("Note Bloat" reducer).

use crate::stages::fuzz_ratio;
use ahash::AHashSet;
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

static DATE_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^(?:Progress\s+Notes?\s+by\s+[^\n]+?(?:on|at)\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})|(?:Note\s+Date|Date\s+of\s+Service|DOS)\s*[:#]\s*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})|^(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s+(?:Progress\s+Note|Daily\s+Note|SOAP\s+Note)|^[A-Z][a-zA-Z,.\s\-]+ at (\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})\s+\d{1,2}:\d{2})"
    )
    .unwrap()
});
static ALT_HEADER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?im)^Progress\s+Notes?\s+by\s+[^\n]+").unwrap());
static DATE_EXTRACT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4}").unwrap());
static WRAPPER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?s)^<(?P<tag>[A-Za-z_][\w\-]*)>\n(?P<body>.*)\n</(?P<tag2>[A-Za-z_][\w\-]*)>$")
        .unwrap()
});

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NoteSegment {
    pub index: usize,
    pub title: String,
    pub date_str: String,
    pub raw_text: String,
    pub paragraphs: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct NoteDelta {
    pub note_index: usize,
    pub date_str: String,
    pub baseline_similarity: f64,
    pub total_paragraphs: usize,
    pub new_paragraphs: Vec<String>,
    pub modified_paragraphs: Vec<(String, String)>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeltaResult {
    pub notes_found: usize,
    pub baseline_note: Option<NoteSegment>,
    pub deltas: Vec<NoteDelta>,
    pub compression_ratio: f64,
    pub compact_text: String,
}

fn split_into_notes(text: &str) -> Vec<NoteSegment> {
    let mut matches: Vec<(usize, String)> = DATE_HEADER_RE
        .find_iter(text)
        .map(|m| (m.start(), m.as_str().to_string()))
        .collect();
    if matches.is_empty() {
        let alt: Vec<(usize, String)> = ALT_HEADER_RE
            .find_iter(text)
            .map(|m| (m.start(), m.as_str().to_string()))
            .collect();
        if alt.len() > 1 {
            matches = alt;
        }
    }

    if matches.len() <= 1 {
        let paras: Vec<String> = text
            .split("\n\n")
            .map(|p| p.trim().to_string())
            .filter(|p| !p.is_empty())
            .collect();
        return vec![NoteSegment {
            index: 1,
            title: "Note".to_string(),
            date_str: String::new(),
            raw_text: text.to_string(),
            paragraphs: paras,
        }];
    }

    let mut notes = Vec::with_capacity(matches.len());
    for (i, (match_start, match_str)) in matches.iter().enumerate() {
        let start = if i == 0 { 0 } else { *match_start };
        let end = if i + 1 < matches.len() {
            matches[i + 1].0
        } else {
            text.len()
        };
        let chunk = text[start..end].trim().to_string();
        let header_line = match_str.trim().lines().next().unwrap_or("").to_string();
        let date_str = DATE_EXTRACT_RE
            .find(&header_line)
            .map(|m| m.as_str().to_string())
            .unwrap_or_else(|| format!("Day {}", i + 1));
        let paras: Vec<String> = chunk
            .split("\n\n")
            .map(|p| p.trim().to_string())
            .filter(|p| !p.is_empty())
            .collect();
        notes.push(NoteSegment {
            index: i + 1,
            title: header_line,
            date_str,
            raw_text: chunk,
            paragraphs: paras,
        });
    }
    notes
}

/// Split paragraph into lines and sentences (keeping decimals like `38.6` intact).
fn split_units(paragraph: &str) -> Vec<String> {
    let mut out = Vec::new();
    for line in paragraph.lines() {
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let bytes = line.as_bytes();
        let mut start = 0usize;
        let mut i = 0usize;
        while i < bytes.len() {
            if matches!(bytes[i], b'.' | b'!' | b'?') {
                let mut j = i + 1;
                while j < bytes.len() && bytes[j].is_ascii_whitespace() {
                    j += 1;
                }
                if j > i + 1
                    && j < bytes.len()
                    && (bytes[j].is_ascii_uppercase()
                        || bytes[j].is_ascii_digit()
                        || matches!(bytes[j], b'(' | b'['))
                {
                    let seg = line[start..=i].trim();
                    if !seg.is_empty() {
                        out.push(seg.to_string());
                    }
                    start = j;
                    i = j;
                    continue;
                }
            }
            i += 1;
        }
        let tail = line[start..].trim();
        if !tail.is_empty() {
            out.push(tail.to_string());
        }
    }
    out
}

fn norm_unit(unit: &str) -> String {
    unit.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase()
        .trim_end_matches([' ', '.', ';', ','])
        .to_string()
}

fn find_best_match<'a>(target: &str, candidates: &'a [String]) -> (f64, Option<&'a str>) {
    if candidates.is_empty() || target.is_empty() {
        return (0.0, None);
    }
    let mut best_sim = 0.0;
    let mut best_cand = None;
    let target_low = target.to_lowercase();
    for cand in candidates {
        let cand_low = cand.to_lowercase();
        if target_low == cand_low {
            return (100.0, Some(cand.as_str()));
        }
        let sim = fuzz_ratio(&target_low, &cand_low);
        if sim > best_sim {
            best_sim = sim;
            best_cand = Some(cand.as_str());
            if sim >= 98.0 {
                break;
            }
        }
    }
    (best_sim, best_cand)
}

pub fn extract_note_deltas(text: &str) -> DeltaResult {
    let trimmed = text.trim();
    if let Some(caps) = WRAPPER_RE.captures(trimmed) {
        let tag = caps.name("tag").unwrap().as_str();
        let tag2 = caps.name("tag2").unwrap().as_str();
        if tag == tag2 {
            let mut inner = extract_note_deltas(caps.name("body").unwrap().as_str());
            if inner.notes_found > 1 {
                inner.compact_text = format!("<{tag}>\n{}\n</{tag}>", inner.compact_text);
                return inner;
            }
        }
    }

    let notes = split_into_notes(text);
    if notes.len() <= 1 {
        return DeltaResult {
            notes_found: notes.len(),
            baseline_note: notes.into_iter().next(),
            deltas: Vec::new(),
            compression_ratio: 0.0,
            compact_text: text.to_string(),
        };
    }

    let baseline = notes[0].clone();
    let mut deltas = Vec::new();
    let mut seen_units: AHashSet<String> = baseline
        .paragraphs
        .iter()
        .flat_map(|p| split_units(p))
        .map(|u| norm_unit(&u))
        .collect();
    let mut seen_paras: Vec<String> = baseline.paragraphs.clone();
    let mut kept_chars = baseline.raw_text.len();

    for current in &notes[1..] {
        let mut new_paras = Vec::new();
        let mut mod_paras = Vec::new();
        let mut sim_scores = Vec::new();
        let header = current.title.trim();

        for p in &current.paragraphs {
            let units: Vec<String> = split_units(p)
                .into_iter()
                .filter(|u| u != header)
                .collect();
            if units.is_empty() {
                continue;
            }
            let fresh: Vec<String> = units
                .iter()
                .filter(|u| !seen_units.contains(&norm_unit(u)))
                .cloned()
                .collect();
            let (best_sim, best_match) = find_best_match(p, &seen_paras);
            sim_scores.push(best_sim);
            if fresh.is_empty() {
                continue;
            }
            let kept = fresh.join(" ");
            kept_chars += kept.len();
            if fresh.len() == units.len() && best_sim < 60.0 {
                if !p.contains(header) {
                    new_paras.push(p.clone());
                } else {
                    new_paras.push(units.join("\n"));
                }
            } else {
                mod_paras.push((best_match.unwrap_or("").to_string(), kept));
            }
        }

        for p in &current.paragraphs {
            for u in split_units(p) {
                seen_units.insert(norm_unit(&u));
            }
        }
        seen_paras.extend(current.paragraphs.iter().cloned());
        let avg_sim = if sim_scores.is_empty() {
            0.0
        } else {
            let s: f64 = sim_scores.iter().sum();
            ((s / (sim_scores.len() as f64)) * 10.0).round() / 10.0
        };
        deltas.push(NoteDelta {
            note_index: current.index,
            date_str: current.date_str.clone(),
            baseline_similarity: avg_sim,
            total_paragraphs: current.paragraphs.len(),
            new_paragraphs: new_paras,
            modified_paragraphs: mod_paras,
        });
    }

    let compact = format_delta_timeline(&baseline, &deltas);
    let raw_content: usize = notes.iter().map(|n| n.raw_text.len()).sum();
    let saved_ratio = if raw_content > 0 {
        ((100.0 * (1.0 - (kept_chars as f64) / (raw_content as f64))) * 10.0).round() / 10.0
    } else {
        0.0
    };

    DeltaResult {
        notes_found: notes.len(),
        baseline_note: Some(baseline),
        deltas,
        compression_ratio: saved_ratio.max(0.0),
        compact_text: compact,
    }
}

pub fn format_delta_timeline(baseline: &NoteSegment, deltas: &[NoteDelta]) -> String {
    let mut lines = vec![
        "## Baseline Admission Note".to_string(),
        baseline.raw_text.trim().to_string(),
        String::new(),
        "---".to_string(),
        "## Longitudinal Clinical Updates (Copy-Forward Bloat Removed)".to_string(),
    ];

    for d in deltas {
        lines.push(format!(
            "\n### Clinical Update ({}) — {:.1}% Redundant Text Pruned",
            d.date_str, d.baseline_similarity
        ));
        if d.new_paragraphs.is_empty() && d.modified_paragraphs.is_empty() {
            lines.push("*(Every sentence repeats an earlier note)*".to_string());
            continue;
        }
        if !d.new_paragraphs.is_empty() {
            lines.push("**New Clinical Findings:**".to_string());
            for np in &d.new_paragraphs {
                lines.push(format!("- {np}"));
            }
        }
        if !d.modified_paragraphs.is_empty() {
            lines.push("**New or Changed Sentences:**".to_string());
            for (_old, new) in &d.modified_paragraphs {
                lines.push(format!("- {new}"));
            }
        }
    }
    lines.join("\n")
}
