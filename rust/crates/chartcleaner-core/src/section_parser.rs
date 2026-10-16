//! Clinical section parser and structured LLM format transformer.
//!
//! Transforms unstructured EMR text into canonical clinical domains and supports
//! serialization to Markdown, LLM XML, JSON, Dict, and Epic SmartPhrase formats.

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Section taxonomy mapping: canonical domain key -> regex aliases.
pub const SECTION_TAXONOMY: &[(&str, &[&str])] = &[
    (
        "chief_complaint",
        &[r"Chief\s+Complaint", r"Reason\s+for\s+Visit", r"CC"],
    ),
    (
        "history_of_present_illness",
        &[r"History\s+of\s+Present\s+Illness", r"HPI", r"Subjective"],
    ),
    (
        "past_medical_history",
        &[
            r"Past\s+Medical\s+History",
            r"PMH",
            r"Past\s+Surgical\s+History",
            r"PSH",
            r"Medical\s+History",
            r"Surgical\s+History",
        ],
    ),
    (
        "medications",
        &[
            r"Current\s+Medications?",
            r"Active\s+Medications?",
            r"Home\s+Medications?",
            r"Medications?",
            r"Med\s+List",
        ],
    ),
    (
        "allergies",
        &[
            r"Allergies\s+and\s+Adverse\s+Reactions?",
            r"Allergies\s*/\s*Sensitivities",
            r"Allergies",
        ],
    ),
    ("vitals", &[r"Vital\s+Signs", r"Vitals?"]),
    (
        "physical_exam",
        &[r"Physical\s+Exam(?:ination)?", r"PE", r"Objective"],
    ),
    (
        "labs",
        &[
            r"Laboratory\s+Data",
            r"Diagnostic\s+Labs?",
            r"Recent\s+Labs?",
            r"Labs?",
        ],
    ),
    (
        "imaging",
        &[r"Diagnostic\s+Imaging", r"Radiology", r"Imaging", r"Studies"],
    ),
    (
        "hospital_course",
        &[
            r"Brief\s+Hospital\s+Course",
            r"Clinical\s+Course",
            r"Hospital\s+Course",
        ],
    ),
    (
        "assessment_and_plan",
        &[
            r"Assessment\s+(?:&|and)\s+Plan",
            r"A\s*/\s*P",
            r"Assessment",
            r"Plan",
            r"Impression",
        ],
    ),
];

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClinicalSection {
    pub domain: String,
    pub title: String,
    pub content: String,
    pub start_pos: usize,
    pub end_pos: usize,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct ParsedChart {
    pub preamble: String,
    pub sections: Vec<ClinicalSection>,
    pub metadata: BTreeMap<String, String>,
}

impl ParsedChart {
    pub fn get_section(&self, domain: &str) -> Option<&ClinicalSection> {
        self.sections.iter().find(|s| s.domain == domain)
    }

    pub fn to_dict(&self) -> serde_json::Value {
        let mut domains: BTreeMap<String, String> = BTreeMap::new();
        for s in &self.sections {
            let trimmed = s.content.trim();
            domains
                .entry(s.domain.clone())
                .and_modify(|existing| {
                    existing.push_str("\n\n");
                    existing.push_str(trimmed);
                })
                .or_insert_with(|| trimmed.to_string());
        }
        serde_json::json!({
            "metadata": self.metadata,
            "preamble": self.preamble.trim(),
            "domains": domains,
            "sections": self.sections,
        })
    }

    pub fn to_json(&self) -> String {
        serde_json::to_string_pretty(&self.to_dict()).unwrap_or_else(|_| "{}".to_string())
    }

    pub fn to_markdown(&self) -> String {
        let mut lines: Vec<String> = Vec::new();
        if !self.preamble.trim().is_empty() {
            lines.push(self.preamble.trim().to_string());
            lines.push(String::new());
        }

        for s in &self.sections {
            let title = s.title.trim().trim_end_matches(':');
            lines.push(format!("## {title}"));
            lines.push(s.content.trim().to_string());
            lines.push(String::new());
        }

        lines.join("\n").trim().to_string()
    }

    pub fn to_llm_xml(&self) -> String {
        let mut lines: Vec<String> = vec!["<patient_chart>".to_string()];

        if !self.preamble.trim().is_empty() {
            lines.push(format!(
                "  <context>\n    {}\n  </context>",
                self.preamble.trim()
            ));
        }

        for s in &self.sections {
            let tag = match s.domain.as_str() {
                "chief_complaint" => "chief_complaint",
                "history_of_present_illness" => "hpi",
                "past_medical_history" => "medical_history",
                "medications" => "active_medications",
                "allergies" => "allergies",
                "vitals" => "vitals",
                "physical_exam" => "physical_exam",
                "labs" => "laboratory_results",
                "imaging" => "imaging",
                "hospital_course" => "hospital_course",
                "assessment_and_plan" => "assessment_and_plan",
                other => other,
            };
            let body = s.content.trim();
            lines.push(format!("  <{tag}>\n    {body}\n  </{tag}>"));
        }

        lines.push("</patient_chart>".to_string());
        lines.join("\n")
    }

    pub fn to_smartphrase(&self) -> String {
        if self.sections.is_empty() {
            return self.preamble.trim().to_string();
        }
        let mut out: Vec<String> = Vec::new();
        if !self.preamble.trim().is_empty() {
            out.push(self.preamble.trim().to_string());
        }
        for s in &self.sections {
            let title = s.title.trim().trim_end_matches(':').to_uppercase();
            let body: Vec<String> = s
                .content
                .lines()
                .map(|l| {
                    let t = l.trim();
                    if let Some(rest) = t.strip_prefix("- ") {
                        format!("  * {rest}")
                    } else if let Some(rest) = t.strip_prefix("* ") {
                        format!("  * {rest}")
                    } else {
                        t.to_string()
                    }
                })
                .collect();
            out.push(format!("{title}:\n{}", body.join("\n").trim()));
        }
        out.join("\n\n")
    }
}

struct CompiledSectionMatcher {
    regex: Regex,
    group_domains: Vec<(String, &'static str)>,
}

static SECTION_MATCHER: Lazy<CompiledSectionMatcher> = Lazy::new(|| {
    let mut pattern_parts = Vec::new();
    let mut group_domains = Vec::new();
    let mut idx = 1usize;
    for (domain, aliases) in SECTION_TAXONOMY {
        for alias in *aliases {
            let grp_name = format!("sec_{idx}");
            pattern_parts.push(format!(
                r"(?P<{grp_name}>^[ \t]*(?:#+[ \t]*|\*\*)?(?:\b{alias}\b)(?:\*\*)?[ \t]*(?::[ \t]*$|:\s+[^\n]+$|$))"
            ));
            group_domains.push((grp_name, *domain));
            idx += 1;
        }
    }
    let combined = format!("(?im){}", pattern_parts.join("|"));
    let regex = Regex::new(&combined).expect("valid section taxonomy regex");
    CompiledSectionMatcher {
        regex,
        group_domains,
    }
});

/// Parse text into canonical clinical sections and metadata.
pub fn parse_clinical_sections(text: &str) -> ParsedChart {
    let matcher = &*SECTION_MATCHER;
    let mut raw_matches: Vec<(usize, &'static str)> = Vec::new();

    for caps in matcher.regex.captures_iter(text) {
        let m0 = caps.get(0).unwrap();
        let mut domain = "other";
        for (grp_name, dom) in &matcher.group_domains {
            if caps.name(grp_name).is_some() {
                domain = dom;
                break;
            }
        }
        raw_matches.push((m0.start(), domain));
    }

    if raw_matches.is_empty() {
        return ParsedChart {
            preamble: text.to_string(),
            sections: Vec::new(),
            metadata: BTreeMap::new(),
        };
    }

    let preamble = text[..raw_matches[0].0].trim().to_string();
    let mut sections = Vec::with_capacity(raw_matches.len());

    for (i, &(header_span_start, domain)) in raw_matches.iter().enumerate() {
        let end_pos = if i + 1 < raw_matches.len() {
            raw_matches[i + 1].0
        } else {
            text.len()
        };
        let chunk = &text[header_span_start..end_pos];

        let (first_line, rest) = match chunk.split_once('\n') {
            Some((fl, r)) => (fl, r),
            None => (chunk, ""),
        };

        let (title, inline) = if let Some((title_raw, inline_raw)) = first_line.split_once(':') {
            let t = title_raw
                .trim()
                .trim_start_matches('#')
                .trim_matches('*')
                .trim()
                .to_string();
            (t, inline_raw.trim())
        } else {
            let t = first_line
                .trim()
                .trim_start_matches('#')
                .trim_matches('*')
                .trim()
                .to_string();
            (t, "")
        };

        let mut body_parts = Vec::new();
        if !inline.is_empty() {
            body_parts.push(inline);
        }
        if !rest.is_empty() {
            body_parts.push(rest);
        }
        let content = body_parts.join("\n").trim().to_string();

        sections.push(ClinicalSection {
            domain: domain.to_string(),
            title,
            content,
            start_pos: header_span_start,
            end_pos,
        });
    }

    ParsedChart {
        preamble,
        sections,
        metadata: BTreeMap::new(),
    }
}
