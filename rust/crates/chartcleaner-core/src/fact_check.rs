//! "Nothing clinical lost" check (`fact_check`) and physiological plausibility check (`plausibility`).

use crate::regex_engine::LineSpanIndex;
use ahash::{AHashMap, AHashSet};
use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeMap;

pub const RULE_STAGES: &[&str] = &[
    "metadata_lines",
    "boilerplate",
    "learned_rules",
    "literal_replacements",
    "phi_patterns",
    "clinical_identifiers",
    "nlp_redaction",
    "tokenize_phi",
    "timestamps",
];

pub const BY_DESIGN_STAGES: &[&str] = &[
    "sections",
    "imaging_impression",
    "vitals_summary",
    "lab_compaction",
    "neuro_summary",
    "med_normalize",
    "mar_summary",
    "flowsheet_tables",
];

pub const DEDUP_STAGES: &[&str] = &["duplicate_notes", "fuzzy_dedup", "fuzzy_line_dedup"];
pub const WORD_SAFE_STAGES: &[&str] = &["medical_abbreviations", "expand_abbreviations"];

pub fn stage_category(stage_id: &str) -> &'static str {
    if stage_id.starts_with("custom:") || RULE_STAGES.contains(&stage_id) {
        "rule"
    } else if BY_DESIGN_STAGES.contains(&stage_id) || DEDUP_STAGES.contains(&stage_id) {
        "by_design"
    } else {
        "unexpected"
    }
}

static MASKS: Lazy<Vec<Regex>> = Lazy::new(|| {
    vec![
        Regex::new(r"\[\[[A-Za-z_]+\d+\]\]").unwrap(),
        Regex::new(r"\b\d{4}-\d{2}-\d{2}\b").unwrap(),
        Regex::new(r"(?:0?[1-9]|1[0-2])/(?:0?[1-9]|[12]\d|3[01])(?:/\d{2,4})?").unwrap(),
        Regex::new(r"\b\d{1,2}:\d{2}(?::\d{2})?\b").unwrap(),
    ]
});

static BP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d{2,3})/(\d{2,3})").unwrap());

static WITH_UNIT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?P<num>[<>]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[<>]?\d+(?:\.\d+)?)[^\S\n]?(?P<unit>(?:mcg|mg|g|kg|ng|pg|lbs?|mL|ml|L|units?|mEq|mmol|mOsm|mm[^\S\n]?Hg|cm[^\S\n]?H2O|%|°[^\S\n]?[CF]|[CF]|bpm|cm|mm|IU|K/u[Ll]|K/µ[Ll]|x[^\S\n]?10)(?:/[A-Za-z0-9.]+)*)"
    )
    .unwrap()
});

static LABELED_NUM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?P<num>[<>]?\d{1,3}(?:,\d{3})+(?:\.\d+)?|[<>]?\d+(?:\.\d+)?)").unwrap()
});

static SEGMENT_BREAK_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[^A-Za-z0-9\- \t]").unwrap());

static WORD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"[A-Za-z][A-Za-z\-]{2,}").unwrap());

static KEYWORD_PHRASES_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)(?:(full[^\S\n]+code)|(comfort[^\S\n]+(?:care|measures)))").unwrap()
});

static KNOWN_LABELS: Lazy<AHashSet<&'static str>> = Lazy::new(|| {
    [
        "sodium", "na", "potassium", "k", "chloride", "cl", "co2", "bicarbonate", "hco3",
        "total co2", "carbon dioxide", "bun", "urea nitrogen", "blood urea nitrogen",
        "creatinine", "creat", "cr", "glucose", "glu", "calcium", "ca", "anion gap", "ag",
        "magnesium", "mg", "phosphorus", "phos", "phosphate", "wbc", "white blood cell count",
        "white blood cells", "hemoglobin", "hgb", "hb", "hematocrit", "hct", "platelets",
        "platelet count", "plt", "mcv", "ast", "sgot", "alt", "sgpt", "alkaline phosphatase",
        "alk phos", "alp", "total bilirubin", "bilirubin total", "tbili", "albumin", "alb",
        "inr", "pt", "prothrombin time", "protime", "ptt", "aptt", "lactate", "lactic acid",
        "troponin", "troponin i", "troponin t", "hs troponin", "bp", "blood pressure", "nibp",
        "art bp", "abp", "pulse", "heart rate", "hr", "temp", "temperature", "tmax", "resp",
        "respiratory rate", "rr", "spo2", "o2 sat", "sao2", "pulse ox", "map", "icp", "cpp",
        "cvp", "gcs", "nihss", "mrs", "hunt-hess", "hunt hess", "fisher", "evd", "fio2", "peep",
        "a1c", "hba1c", "bnp", "ldl", "tsh", "ck", "lipase", "ammonia", "ph", "pco2", "po2",
        "poc glucose", "weight", "wt", "uop", "osm", "serum osm", "vent rate", "tv", "rass",
        "cpot",
    ]
    .into_iter()
    .collect()
});

static KNOWN_DRUGS: Lazy<AHashSet<&'static str>> = Lazy::new(|| {
    [
        "plavix", "clopidogrel", "brilinta", "ticagrelor", "effient", "prasugrel", "eliquis",
        "apixaban", "xarelto", "rivaroxaban", "pradaxa", "dabigatran", "coumadin", "warfarin",
        "heparin", "lovenox", "enoxaparin", "arixtra", "aspirin", "lisinopril", "enalapril",
        "ramipril", "losartan", "valsartan", "amlodipine", "nifedipine", "diltiazem", "cardizem",
        "verapamil", "metoprolol", "lopressor", "toprol", "atenolol", "carvedilol", "coreg",
        "labetalol", "propranolol", "hydralazine", "clonidine", "norvasc", "atorvastatin",
        "lipitor", "rosuvastatin", "crestor", "simvastatin", "zocor", "lasix", "furosemide",
        "bumex", "bumetanide", "spironolactone", "aldactone", "hydrochlorothiazide", "hctz",
        "chlorthalidone", "digoxin", "lanoxin", "amiodarone", "cordarone", "flecainide",
        "sotalol", "entresto", "levophed", "norepinephrine", "epinephrine", "vasopressin",
        "phenylephrine", "dopamine", "dobutamine", "milrinone", "propofol", "diprivan",
        "precedex", "dexmedetomidine", "versed", "midazolam", "fentanyl", "dilaudid",
        "hydromorphone", "morphine", "ketamine", "rocuronium", "vecuronium", "cisatracurium",
        "vancomycin", "vancocin", "zosyn", "piperacillin", "tazobactam", "ceftriaxone",
        "rocephin", "cefepime", "maxipime", "cefdinir", "keflex", "cephalexin", "ancef",
        "cefazolin", "meropenem", "merrem", "ertapenem", "invanz", "aztreonam", "ciprofloxacin",
        "cipro", "levofloxacin", "levaquin", "moxifloxacin", "avelox", "azithromycin",
        "zithromax", "clarithromycin", "amoxicillin", "augmentin", "ampicillin", "unasyn",
        "doxycycline", "vibramycin", "bactrim", "septra", "sulfamethoxazole", "trimethoprim",
        "flagyl", "metronidazole", "zyvox", "linezolid", "cubicin", "daptomycin", "gentamicin",
        "tobramycin", "amikacin", "diflucan", "fluconazole", "metformin", "glucophage",
        "glipizide", "glucotrol", "glimepiride", "amaryl", "januvia", "sitagliptin", "jardiance",
        "empagliflozin", "farxiga", "dapagliflozin", "ozempic", "wegovy", "semaglutide",
        "mounjaro", "zepbound", "tirzepatide", "trulicity", "dulaglutide", "lantus", "glargine",
        "levemir", "detemir", "humalog", "lispro", "novolog", "aspart", "tresiba", "degludec",
        "synthroid", "levothyroxine", "cytomel", "liothyronine", "tapazole", "methimazole",
        "protonix", "pantoprazole", "prilosec", "omeprazole", "nexium", "esomeprazole", "pepcid",
        "famotidine", "reglan", "metoclopramide", "zofran", "ondansetron", "phenergan",
        "promethazine", "compazine", "prochlorperazine", "albuterol", "ventolin", "proair",
        "atrovent", "ipratropium", "duoneb", "spiriva", "tiotropium", "symbicort", "advair",
        "flovent", "pulmicort", "singulair", "montelukast", "prednisone", "solumedrol",
        "methylprednisolone", "decadron", "dexamethasone", "hydrocortisone", "tylenol",
        "acetaminophen", "motrin", "advil", "ibuprofen", "aleve", "naproxen", "toradol",
        "ketorolac", "celebrex", "celecoxib", "tramadol", "ultram", "oxycodone", "oxycontin",
        "percocet", "norco", "vicodin", "gabapentin", "neurontin", "lyrica", "pregabalin",
        "flexeril", "cyclobenzaprine", "haldol", "haloperidol", "ativan", "lorazepam", "xanax",
        "alprazolam", "klonopin", "clonazepam", "valium", "diazepam", "seroquel", "quetiapine",
        "zyprexa", "olanzapine", "risperdal", "risperidone", "geodon", "ziprasidone", "abilify",
        "aripiprazole", "prozac", "fluoxetine", "zoloft", "sertraline", "celexa", "citalopram",
        "lexapro", "escitalopram", "effexor", "venlafaxine", "cymbalta", "duloxetine",
        "wellbutrin", "bupropion", "remeron", "mirtazapine", "trazodone", "keppra",
        "levetiracetam", "dilantin", "phenytoin", "depakote", "valproate", "lamictal",
        "lamotrigine", "tegretol", "carbamazepine", "robaxin", "methocarbamol", "topamax",
        "topiramate", "baclofen", "marinol", "dronabinol", "butrans", "buprenorphine", "slynd",
        "drospirenone", "narcan", "naloxone", "nizoral", "ketoconazole", "dulcolax", "bisacodyl",
        "senna", "docusate", "colace", "miralax", "klor-con", "sublimaze", "febuxostat", "uloric",
        "ezetimibe", "zetia", "definity", "perflutren", "glydo", "micronefrin", "racepinephrine",
    ]
    .into_iter()
    .collect()
});

const DRUG_STEMS: &[&str] = &[
    "dipine", "olol", "pril", "sartan", "statin", "mycin", "micin", "cillin", "conazole",
    "prazole", "parin", "xaban", "floxacin", "cycline", "semide", "tidine", "azepam", "zolam",
    "setron", "tiapine", "adone", "barbital", "racetam", "triptyline", "oxetine", "afil",
    "gliptin", "gliflozin", "glutide", "navir", "clovir", "mab", "nib", "platin", "rubicin",
    "taxel",
];

fn norm_number(raw: &str) -> String {
    let s = raw.trim_start_matches(['<', '>']).replace(',', "");
    if s.contains('.') {
        let trimmed = s.trim_end_matches('0').trim_end_matches('.');
        if trimmed.is_empty() {
            "0".to_string()
        } else {
            trimmed.to_string()
        }
    } else {
        s
    }
}

fn mask_text(text: &str) -> String {
    if !text.bytes().any(|b| b.is_ascii_digit() || b == b'[') {
        return text.to_string();
    }
    let mut bytes = text.as_bytes().to_vec();
    for (idx, rx) in MASKS.iter().enumerate() {
        for m in rx.find_iter(text) {
            let s = m.start();
            let e = m.end();
            if idx == 2 {
                let prev_bad =
                    s > 0 && (bytes[s - 1].is_ascii_digit() || matches!(bytes[s - 1], b'/' | b'.'));
                let next_bad = e < bytes.len() && (bytes[e].is_ascii_digit() || bytes[e] == b'/');
                if prev_bad || next_bad {
                    continue;
                }
            }
            bytes[s..e].fill(b' ');
        }
    }
    String::from_utf8(bytes).unwrap_or_else(|_| text.to_string())
}

fn is_drug(word: &str) -> bool {
    KNOWN_DRUGS.contains(word) || (word.len() >= 7 && DRUG_STEMS.iter().any(|st| word.ends_with(st)))
}

fn label_of(before: &str) -> Option<String> {
    let b = before
        .trim_end_matches([' ', '\t'])
        .trim_end_matches([':', '=', '#'])
        .trim_end_matches([' ', '\t']);
    if b.is_empty() {
        return None;
    }
    let last_ch = b.chars().next_back()?;
    if !last_ch.is_ascii_alphanumeric() {
        return None;
    }
    let last_seg = SEGMENT_BREAK_RE.split(b).last().unwrap_or("");
    let mut parts: Vec<&str> = last_seg.split_whitespace().collect();
    if parts.len() > 5 {
        parts = parts[parts.len() - 5..].to_vec();
    }
    if let Some(&last_w) = parts.last() {
        if matches!(last_w.to_lowercase().as_str(), "of" | "is" | "was") {
            parts.pop();
        }
    }
    for n in [4usize, 3, 2, 1] {
        if parts.len() >= n {
            let cand = parts[parts.len() - n..].join(" ");
            if KNOWN_LABELS.contains(cand.to_lowercase().as_str()) {
                return Some(cand);
            }
        }
    }
    None
}

pub fn fact_spans(masked: &str) -> Vec<(String, String, usize, usize)> {
    let bytes = masked.as_bytes();
    let mut spans: BTreeMap<usize, (String, String, usize, usize)> = BTreeMap::new();

    for caps in BP_RE.captures_iter(masked) {
        let m0 = caps.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        let prev_bad =
            s > 0 && (bytes[s - 1].is_ascii_digit() || matches!(bytes[s - 1], b'/' | b'.'));
        let next_bad = e < bytes.len() && (bytes[e].is_ascii_digit() || bytes[e] == b'/');
        if prev_bad || next_bad {
            continue;
        }
        for g in [1usize, 2] {
            let mg = caps.get(g).unwrap();
            spans.entry(mg.start()).or_insert_with(|| {
                (
                    format!("n:{}", norm_number(mg.as_str())),
                    format!("BP {}", m0.as_str()),
                    s,
                    e,
                )
            });
        }
    }

    for caps in LABELED_NUM_RE.captures_iter(masked) {
        let m0 = caps.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        let prev_bad =
            s > 0 && (bytes[s - 1].is_ascii_alphanumeric() || matches!(bytes[s - 1], b'_' | b'.'));
        let next_bad = e < bytes.len() && (bytes[e].is_ascii_digit() || bytes[e] == b'/');
        if prev_bad || next_bad {
            continue;
        }
        let mut lb = s.saturating_sub(48);
        while lb > 0 && !masked.is_char_boundary(lb) {
            lb -= 1;
        }
        if let Some(label) = label_of(&masked[lb..s]) {
            let num_m = caps.name("num").unwrap();
            spans.entry(num_m.start()).or_insert_with(|| {
                (
                    format!("n:{}", norm_number(num_m.as_str())),
                    format!("{label} {}", num_m.as_str()),
                    s,
                    e,
                )
            });
        }
    }

    for caps in WITH_UNIT_RE.captures_iter(masked) {
        let m0 = caps.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        let prev_bad =
            s > 0 && (bytes[s - 1].is_ascii_alphanumeric() || matches!(bytes[s - 1], b'_' | b'.'));
        let next_bad = e < bytes.len() && bytes[e].is_ascii_alphanumeric();
        if prev_bad || next_bad {
            continue;
        }
        let num_m = caps.name("num").unwrap();
        let unit_m = caps.name("unit").unwrap();
        spans.entry(num_m.start()).or_insert_with(|| {
            (
                format!("n:{}", norm_number(num_m.as_str())),
                format!("{} {}", num_m.as_str(), unit_m.as_str()),
                s,
                e,
            )
        });
    }

    let mut out: Vec<(String, String, usize, usize)> = spans.into_values().collect();

    for m in WORD_RE.find_iter(masked) {
        let s = m.start();
        let e = m.end();
        if (s > 0 && bytes[s - 1].is_ascii_alphabetic())
            || (e < bytes.len() && bytes[e].is_ascii_alphabetic())
        {
            continue;
        }
        let w = m.as_str().to_lowercase();
        let kw = match w.as_str() {
            "allergy" | "allergies" | "allergic" => Some("allergy"),
            "nkda" => Some("NKDA"),
            "dnr" => Some("DNR"),
            "dni" => Some("DNI"),
            _ => None,
        };
        if let Some(k) = kw {
            out.push((format!("kw:{k}"), m.as_str().to_string(), s, e));
        } else if is_drug(&w) {
            out.push((format!("drug:{w}"), m.as_str().to_string(), s, e));
        }
    }

    for caps in KEYWORD_PHRASES_RE.captures_iter(masked) {
        let m0 = caps.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        if (s > 0 && bytes[s - 1].is_ascii_alphabetic())
            || (e < bytes.len() && bytes[e].is_ascii_alphabetic())
        {
            continue;
        }
        let key = if caps.get(1).is_some() {
            "kw:full code"
        } else {
            "kw:comfort care"
        };
        out.push((key.to_string(), m0.as_str().to_string(), s, e));
    }

    out
}

pub fn fact_counts(text: &str) -> AHashMap<String, usize> {
    if text.trim().is_empty() {
        return AHashMap::new();
    }
    let masked = mask_text(text);
    let mut counts = AHashMap::new();
    for (key, _, _, _) in fact_spans(&masked) {
        *counts.entry(key).or_insert(0) += 1;
    }
    counts
}

#[derive(Debug, Clone)]
pub struct Fact {
    pub key: String,
    pub display: String,
    pub line: String,
}

pub fn extract_facts(text: &str) -> Vec<Fact> {
    let mut idx = LineSpanIndex::new(text);
    let masked = mask_text(text);
    fact_spans(&masked)
        .into_iter()
        .map(|(key, display, start, _)| {
            let (ls, le) = idx.line_span_at(start);
            Fact {
                key,
                display,
                line: text[ls..le].trim().to_string(),
            }
        })
        .collect()
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FactLoss {
    pub key: String,
    pub display: String,
    pub count: usize,
    pub stage_id: String,
    pub stage_label: String,
    pub category: String,
    pub lines: Vec<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MeaningChange {
    pub kind: String,
    pub stage_id: String,
    pub stage_label: String,
    pub category: String,
    pub before: String,
    pub after: String,
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Implausible {
    pub kind: String,
    pub label: String,
    pub value: String,
    pub unit: String,
    pub low: Option<f64>,
    pub high: f64,
    pub line: String,
    pub message: String,
    pub in_source: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FactReport {
    pub status: String,
    pub headline: String,
    pub total: usize,
    pub kept: usize,
    pub lost_unexpected: usize,
    pub lost_rule: usize,
    pub lost_by_design: usize,
    pub lost_by_stage: BTreeMap<String, usize>,
    pub meaning_flags: usize,
    pub introduced: usize,
    pub implausible: usize,
    pub losses: Vec<FactLoss>,
    pub meaning: Vec<MeaningChange>,
    pub introduced_values: Vec<FactLoss>,
    pub implausible_values: Vec<Implausible>,
}

impl FactReport {
    pub fn summary(&self) -> Value {
        json!({
            "status": self.status,
            "total": self.total,
            "kept": self.kept,
            "lost_unexpected": self.lost_unexpected,
            "lost_rule": self.lost_rule,
            "lost_by_design": self.lost_by_design,
            "lost_by_stage": self.lost_by_stage,
            "meaning_flags": self.meaning_flags,
            "introduced": self.introduced,
            "implausible": self.implausible,
        })
    }
}

pub struct FactTracker {
    _text: String,
    facts: Vec<Fact>,
    start: AHashMap<String, usize>,
    line_memo: AHashMap<String, AHashMap<String, usize>>,
    current: AHashMap<String, usize>,
    previous: String,
    drops: Vec<(String, String, AHashMap<String, usize>)>,
    meaning: Vec<MeaningChange>,
    added: Vec<(String, String, AHashMap<String, usize>)>,
}

impl FactTracker {
    pub fn new(text: &str) -> Self {
        let facts = extract_facts(text);
        let mut start = AHashMap::new();
        for f in &facts {
            *start.entry(f.key.clone()).or_insert(0) += 1;
        }
        Self {
            _text: text.to_string(),
            facts,
            current: start.clone(),
            start,
            line_memo: AHashMap::new(),
            previous: text.to_string(),
            drops: Vec::new(),
            meaning: Vec::new(),
            added: Vec::new(),
        }
    }

    fn counts_for_text(&mut self, text: &str) -> AHashMap<String, usize> {
        let mut line_freq: AHashMap<&str, usize> = AHashMap::new();
        for line in text.split('\n') {
            *line_freq.entry(line).or_insert(0) += 1;
        }
        let mut total = AHashMap::new();
        for (line, times) in line_freq {
            let line_counts = if let Some(c) = self.line_memo.get(line) {
                c
            } else {
                let c = fact_counts(line);
                self.line_memo.insert(line.to_string(), c);
                self.line_memo.get(line).unwrap()
            };
            for (k, &n) in line_counts {
                *total.entry(k.clone()).or_insert(0) += n * times;
            }
        }
        total
    }

    pub fn after_stage(&mut self, stage_id: &str, label: &str, text: &str) {
        let now = self.counts_for_text(text);
        let mut dec = AHashMap::new();
        for (k, &v) in &self.current {
            let now_v = now.get(k).copied().unwrap_or(0);
            if v > now_v {
                dec.insert(k.clone(), v - now_v);
            }
        }
        if !dec.is_empty() {
            self.drops.push((stage_id.to_string(), label.to_string(), dec));
        }

        let mut added_now = AHashMap::new();
        for (k, &v) in &now {
            let curr_v = self.current.get(k).copied().unwrap_or(0);
            if k.starts_with("n:") && !self.start.contains_key(k) && v > curr_v {
                added_now.insert(k.clone(), v - curr_v);
            }
        }
        if !added_now.is_empty() {
            self.added
                .push((stage_id.to_string(), label.to_string(), added_now));
        }

        if !BY_DESIGN_STAGES.contains(&stage_id) && !DEDUP_STAGES.contains(&stage_id) {
            self.meaning.extend(meaning_changes(
                &self.previous,
                text,
                stage_id,
                label,
            ));
        }

        self.current = now;
        self.previous = text.to_string();
    }

    pub fn report(mut self, final_text: &str) -> FactReport {
        let end = if final_text == self.previous {
            self.current.clone()
        } else {
            self.counts_for_text(final_text)
        };
        let after_lines: AHashSet<&str> = final_text.split('\n').map(|l| l.trim()).collect();

        let mut by_key: AHashMap<String, Vec<&Fact>> = AHashMap::new();
        for f in &self.facts {
            by_key.entry(f.key.clone()).or_default().push(f);
        }

        let mut losses = Vec::new();
        for (key, items) in by_key {
            let start_n = self.start.get(&key).copied().unwrap_or(0);
            let end_n = end.get(&key).copied().unwrap_or(0);
            let lost = items.len().min(start_n.saturating_sub(end_n));
            if lost == 0 {
                continue;
            }
            let gone: Vec<&Fact> = {
                let g: Vec<&Fact> = items
                    .iter()
                    .copied()
                    .filter(|f| !after_lines.contains(f.line.as_str()))
                    .collect();
                if g.is_empty() {
                    items
                } else {
                    g
                }
            };
            let mut remaining = lost;
            for (stage_id, label, dec) in &self.drops {
                let take = remaining.min(dec.get(&key).copied().unwrap_or(0));
                if take == 0 {
                    continue;
                }
                remaining -= take;
                let mut category = stage_category(stage_id);
                if stage_id == "med_normalize" && key.starts_with("n:") {
                    category = "unexpected";
                }
                if DEDUP_STAGES.contains(&stage_id.as_str()) && end_n > 0 {
                    continue;
                }
                if WORD_SAFE_STAGES.contains(&stage_id.as_str()) && !key.starts_with("n:") {
                    continue;
                }
                losses.push(FactLoss {
                    key: key.clone(),
                    display: gone[0].display.clone(),
                    count: take,
                    stage_id: stage_id.clone(),
                    stage_label: label.clone(),
                    category: category.to_string(),
                    lines: gone.iter().take(3).map(|f| f.line.clone()).collect(),
                });
                if remaining == 0 {
                    break;
                }
            }
            if remaining > 0 {
                losses.push(FactLoss {
                    key: key.clone(),
                    display: gone[0].display.clone(),
                    count: remaining,
                    stage_id: "output".to_string(),
                    stage_label: "Output".to_string(),
                    category: "unexpected".to_string(),
                    lines: gone.iter().take(3).map(|f| f.line.clone()).collect(),
                });
            }
        }

        let cat_rank = |c: &str| match c {
            "unexpected" => 0,
            "rule" => 1,
            _ => 2,
        };
        losses.sort_by(|a, b| {
            cat_rank(&a.category)
                .cmp(&cat_rank(&b.category))
                .then_with(|| a.stage_label.cmp(&b.stage_label))
                .then_with(|| a.display.cmp(&b.display))
        });

        let mut introduced_values = Vec::new();
        if !self.added.is_empty() {
            let final_facts = extract_facts(final_text);
            for (stage_id, label, new_map) in &self.added {
                for (key, &n) in new_map {
                    if end.get(key).copied().unwrap_or(0) == 0 {
                        continue;
                    }
                    let shown = final_facts.iter().find(|f| &f.key == key);
                    introduced_values.push(FactLoss {
                        key: key.clone(),
                        display: shown
                            .map(|f| f.display.clone())
                            .unwrap_or_else(|| key.trim_start_matches("n:").to_string()),
                        count: n,
                        stage_id: stage_id.clone(),
                        stage_label: label.clone(),
                        category: stage_category(stage_id).to_string(),
                        lines: shown.map(|f| vec![f.line.clone()]).unwrap_or_default(),
                    });
                }
            }
        }

        let total = self.facts.len();
        let lost_unexpected: usize = losses
            .iter()
            .filter(|x| x.category == "unexpected")
            .map(|x| x.count)
            .sum();
        let lost_rule: usize = losses
            .iter()
            .filter(|x| x.category == "rule")
            .map(|x| x.count)
            .sum();
        let lost_by_design: usize = losses
            .iter()
            .filter(|x| x.category == "by_design")
            .map(|x| x.count)
            .sum();
        let total_lost = lost_unexpected + lost_rule + lost_by_design;
        let kept = total.saturating_sub(total_lost);

        let mut lost_by_stage = BTreeMap::new();
        for x in &losses {
            if x.category != "by_design" {
                *lost_by_stage.entry(x.stage_id.clone()).or_insert(0) += x.count;
            }
        }

        let intro_unexpected: usize = introduced_values
            .iter()
            .filter(|x| x.category == "unexpected")
            .map(|x| x.count)
            .sum();
        let introduced: usize = introduced_values.iter().map(|x| x.count).sum();

        let meaning = if self.meaning.is_empty()
            && self.drops.is_empty()
            && final_text != self._text
        {
            meaning_changes(&self._text, final_text, "output", "Output")
        } else {
            self.meaning
        };
        let meaning_flags = meaning.len();

        let status = if lost_unexpected > 0 || intro_unexpected > 0 || meaning_flags > 0 {
            "alert"
        } else if lost_rule > 0 {
            "review"
        } else {
            "ok"
        };

        let mut headline = if status == "alert" {
            let mut parts = Vec::new();
            if lost_unexpected > 0 {
                parts.push(format!("{lost_unexpected} clinical value(s) lost unexpectedly"));
            }
            if meaning_flags > 0 {
                parts.push(format!("{meaning_flags} negation/laterality shift(s)"));
            }
            if intro_unexpected > 0 {
                parts.push(format!(
                    "{intro_unexpected} value(s) appeared that weren't in the chart"
                ));
            }
            parts.join("; ")
        } else if status == "review" {
            format!("{lost_rule} clinical value(s) removed by rules — review")
        } else {
            format!(
                "All {} clinical value(s) kept",
                total.saturating_sub(lost_by_design)
            )
        };
        if lost_by_design > 0 {
            headline.push_str(&format!(
                " ({lost_by_design} dropped by section/summary settings)"
            ));
        }

        FactReport {
            status: status.to_string(),
            headline,
            total,
            kept,
            lost_unexpected,
            lost_rule,
            lost_by_design,
            lost_by_stage,
            meaning_flags,
            introduced,
            implausible: 0,
            losses,
            meaning,
            introduced_values,
            implausible_values: Vec::new(),
        }
    }
}

static NEGATION_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?:no|not|denies|denied|deny|without|negative|neg|absent|never|none|nor|w/o|n't|-ve)|\(-\)",
    )
    .unwrap()
});

static NEGATION_ABBR_SET: Lazy<AHashSet<String>> = Lazy::new(|| {
    let mut set = AHashSet::new();
    for row in crate::abbreviations::SOURCE_ROWS.iter() {
        let abbr = row.abbreviation.trim();
        if abbr.len() >= 2 && has_base_negation(&row.expanded) {
            set.insert(abbr.to_string());
        }
    }
    set
});

fn has_base_negation(line: &str) -> bool {
    count_base_negations(line) > 0
}

fn count_base_negations(line: &str) -> usize {
    let bytes = line.as_bytes();
    let mut count = 0usize;
    for m in NEGATION_RE.find_iter(line) {
        let s = m.start();
        let e = m.end();
        let matched = m.as_str();
        if matched == "(-)" {
            count += 1;
            continue;
        }
        let prev_alpha = s > 0 && bytes[s - 1].is_ascii_alphabetic();
        let next_alpha = e < bytes.len() && bytes[e].is_ascii_alphabetic();
        if !prev_alpha && !next_alpha {
            count += 1;
        }
    }
    count
}

fn count_negations(line: &str) -> usize {
    let mut n = count_base_negations(line);
    // Also count negation abbreviations from the CSV (e.g. NAD, DNR, NGTD, N/A, wo)
    let mut i = 0usize;
    let bytes = line.as_bytes();
    while i < bytes.len() {
        if !bytes[i].is_ascii_alphanumeric() {
            i += 1;
            continue;
        }
        let start = i;
        while i < bytes.len()
            && (bytes[i].is_ascii_alphanumeric() || matches!(bytes[i], b'/' | b'-'))
        {
            i += 1;
        }
        let tok = line[start..i].trim_end_matches(['/', '-']);
        if NEGATION_ABBR_SET.contains(tok) && count_base_negations(tok) == 0 {
            n += 1;
        }
    }
    n
}

fn count_sides(line: &str) -> (usize, usize, usize) {
    let mut l = 0usize;
    let mut r = 0usize;
    let mut b = 0usize;
    let bytes = line.as_bytes();
    let mut i = 0usize;
    while i < bytes.len() {
        if !bytes[i].is_ascii_alphabetic() {
            i += 1;
            continue;
        }
        let start = i;
        while i < bytes.len() && (bytes[i].is_ascii_alphabetic() || bytes[i] == b'/') {
            i += 1;
        }
        let tok = &line[start..i];
        match tok.to_ascii_lowercase().as_str() {
            "left" | "lt" => l += 1,
            "right" | "rt" => r += 1,
            "bilateral" | "bilat" | "both" | "b/l" => b += 1,
            _ => {
                if tok == "L" {
                    l += 1;
                } else if tok == "R" {
                    r += 1;
                }
            }
        }
    }
    (l, r, b)
}

fn surplus_lines<'a>(lines: &[&'a str], other: &[&'a str]) -> Vec<&'a str> {
    let mut remaining: AHashMap<&'a str, usize> = AHashMap::new();
    for &ln in other {
        *remaining.entry(ln).or_insert(0) += 1;
    }
    let mut out = Vec::new();
    for &ln in lines {
        if let Some(c) = remaining.get_mut(ln) {
            if *c > 0 {
                *c -= 1;
                continue;
            }
        }
        if !ln.trim().is_empty() {
            out.push(ln);
        }
    }
    out
}

pub fn meaning_changes(
    before_text: &str,
    after_text: &str,
    stage_id: &str,
    stage_label: &str,
) -> Vec<MeaningChange> {
    let a: Vec<&str> = before_text.split('\n').collect();
    let b: Vec<&str> = after_text.split('\n').collect();
    if a.len() > 4000 || b.len() > 4000 {
        return Vec::new();
    }
    let olds = surplus_lines(&a, &b);
    let news = surplus_lines(&b, &a);
    if olds.is_empty() || news.is_empty() {
        return Vec::new();
    }
    if !olds
        .iter()
        .any(|ln| count_negations(ln) > 0 || count_sides(ln) != (0, 0, 0))
    {
        return Vec::new();
    }

    let mut pairs: Vec<(&str, &str)> = Vec::new();
    if olds.len() == news.len() {
        pairs.extend(olds.iter().copied().zip(news.iter().copied()));
    } else if olds.len() * news.len() <= 4000 {
        for &old in &olds {
            if count_negations(old) == 0 && count_sides(old) == (0, 0, 0) {
                continue;
            }
            let mut best: Option<&str> = None;
            let mut score = 0.5f64;
            for &new in &news {
                let r = crate::abbreviations::sequence_matcher_ratio(old, new);
                if r > score {
                    score = r;
                    best = Some(new);
                }
            }
            if let Some(best_new) = best {
                pairs.push((old, best_new));
            }
        }
    }

    let category = stage_category(stage_id).to_string();
    let mut out = Vec::new();
    for (old, new) in pairs {
        if old.trim().is_empty() || new.trim().is_empty() {
            continue;
        }
        let before_n = count_negations(old);
        let after_n = count_negations(new);
        let lost = if WORD_SAFE_STAGES.contains(&stage_id) {
            before_n > 0 && after_n == 0
        } else {
            after_n < before_n
        };
        if lost {
            out.push(MeaningChange {
                kind: "negation".to_string(),
                stage_id: stage_id.to_string(),
                stage_label: stage_label.to_string(),
                category: category.clone(),
                before: old.trim().chars().take(300).collect(),
                after: new.trim().chars().take(300).collect(),
                message: "Negation dropped during rewrite".to_string(),
            });
        }
        let (ol, or, ob) = count_sides(old);
        let (nl, nr, nb) = count_sides(new);
        let switched = (nl > ol || nr > or || nb > ob) && (ol, or, ob) != (nl, nr, nb);
        let vanished = (ol > 0 || or > 0 || ob > 0) && (nl, nr, nb) == (0, 0, 0);
        if switched || vanished {
            out.push(MeaningChange {
                kind: "laterality".to_string(),
                stage_id: stage_id.to_string(),
                stage_label: stage_label.to_string(),
                category: category.clone(),
                before: old.trim().chars().take(300).collect(),
                after: new.trim().chars().take(300).collect(),
                message: "Laterality changed during rewrite".to_string(),
            });
        }
    }
    out
}

