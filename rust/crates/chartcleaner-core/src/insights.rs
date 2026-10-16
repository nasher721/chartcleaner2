//! Clinical insights extractor (`safety`, `conflicts`, `overnight`, `pending`, `stale`, `devices`, `micro`, `bundle`, `problems`).

use std::collections::HashSet;
use std::sync::LazyLock;

use regex::Regex;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::chart_dates::{day_number, parse_date, reference_date};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SafetyItem {
    pub category: String,
    pub status: String,
    pub value: String,
    pub previous: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ConflictPair {
    pub category: String,
    pub first: String,
    pub second: String,
    pub detail: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DeviceItem {
    pub device: String,
    pub site: String,
    pub placed_date: Option<String>,
    pub day: Option<i64>,
    pub line: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct MicroItem {
    pub specimen: String,
    pub result: String,
    pub status: String,
    pub line: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BundleGap {
    pub item: String,
    pub label: String,
    pub status: String,
}

pub const INSIGHT_ORDER: &[&str] = &[
    "safety",
    "conflicts",
    "overnight",
    "pending",
    "stale",
    "devices",
    "micro",
    "bundle",
    "problems",
];

static ALLERGY_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\s*(?:allerg(?:y|ies)|allg)\s*[:\-]\s*(.+)$").unwrap()
});
static CODE_STATUS_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:code\s+status\s*[:\-]?\s*(full\s+code|dnr(?:/dni)?|dni|comfort\s+care|cmo)|(full\s+code|dnr/dni|dnr|dni)\b)").unwrap()
});
static ISOLATION_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:isolation|precautions)\s*[:\-]?\s*(contact|droplet|airborne|neutropenic|standard|none)\b").unwrap()
});
static WEIGHT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:weight|wt|dosing\s+wt)\s*[:=]?\s*(\d{2,3}(?:\.\d+)?\s*(?:kg|lb|lbs))\b").unwrap()
});
static ANTICOAG_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(heparin|hep\s+gtt|enoxaparin|lovenox|apixaban|eliquis|rivaroxaban|xarelto|warfarin|coumadin|dabigatran|argatroban|bivalirudin)\b").unwrap()
});

static OVERNIGHT_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)^\s*(?:overnight(?:\s+events?)?|o/n|interval\s+history|24\s*(?:hr|hour)\s+events?)\s*[:\-]\s*(.+)$").unwrap()
});

static PENDING_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(?:pending|awaiting|f/u|follow[\s\-]up|to\s+be\s+done|tbd|check\s+in\s+am|recheck\s+tmrw|tomorrow)\b").unwrap()
});

static DEVICE_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(central\s+line|cvl|picc|midline|arterial\s+line|a[\-\s]line|foley|indwelling\s+catheter|ett|endotracheal\s+tube|trach(?:eostomy)?|evd|chest\s+tube|ng\s+tube|ngt|og\s+tube|ogt|peg)\b").unwrap()
});

static MICRO_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"(?i)\b(blood\s+cx|blood\s+culture|urine\s+cx|ucx|sputum\s+cx|resp\s+cx|csf\s+cx|wound\s+cx|mrsa|pseudomonas|e\.\s*coli|klebsiella|candida|staph|strep|enterococcus|no\s+growth\s+to\s+date|ngtd|gram\s+stain)\b").unwrap()
});

static PROBLEM_HEADER_RE: LazyLock<Regex> = LazyLock::new(|| {
    Regex::new(r"^\s*(?:#\s*[A-Za-z].+|\d+[\.)]\s+[A-Za-z].+)$").unwrap()
});

/// Extract structured clinical insights from cleaned chart text.
pub fn extract_insights(text: &str) -> Value {
    let lines: Vec<&str> = text.lines().collect();
    let ref_date = reference_date(text, None);

    // 1. Safety header
    let mut safety = Vec::new();
    let mut allergies: Vec<String> = Vec::new();
    let mut code_statuses: Vec<String> = Vec::new();
    let mut isolations: Vec<String> = Vec::new();
    let mut weights: Vec<String> = Vec::new();
    let mut anticoagulants: Vec<String> = Vec::new();

    for line in &lines {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        if let Some(caps) = ALLERGY_RE.captures(trimmed) {
            allergies.push(caps[1].trim().to_string());
        }
        if let Some(caps) = CODE_STATUS_RE.captures(trimmed) {
            let val = caps
                .get(1)
                .or_else(|| caps.get(2))
                .map(|m| m.as_str().trim().to_string())
                .unwrap_or_default();
            if !val.is_empty() {
                code_statuses.push(val);
            }
        }
        if let Some(caps) = ISOLATION_RE.captures(trimmed) {
            isolations.push(caps[1].trim().to_string());
        }
        if let Some(caps) = WEIGHT_RE.captures(trimmed) {
            weights.push(caps[1].trim().to_string());
        }
        if ANTICOAG_RE.is_match(trimmed) {
            anticoagulants.push(trimmed.to_string());
        }
    }

    let push_safety = |out: &mut Vec<SafetyItem>, cat: &str, vals: &[String]| {
        if let Some(last) = vals.last() {
            let first = vals.first().unwrap();
            let changed = vals.len() > 1 && !first.eq_ignore_ascii_case(last);
            out.push(SafetyItem {
                category: cat.to_string(),
                status: if changed { "changed" } else { "stated" }.to_string(),
                value: last.clone(),
                previous: if changed { Some(first.clone()) } else { None },
            });
        } else {
            out.push(SafetyItem {
                category: cat.to_string(),
                status: "not stated".to_string(),
                value: "not stated".to_string(),
                previous: None,
            });
        }
    };

    push_safety(&mut safety, "Allergies", &allergies);
    push_safety(&mut safety, "Code Status", &code_statuses);
    push_safety(&mut safety, "Isolation", &isolations);
    push_safety(&mut safety, "Weight", &weights);
    push_safety(&mut safety, "Antithrombotics", &anticoagulants);

    // 2. Conflicts
    let mut conflicts = Vec::new();
    let mut full_code_line = None;
    let mut dnr_line = None;
    let mut npo_line = None;
    let mut diet_line = None;
    let mut afebrile_line = None;
    let mut fever_line = None;

    for line in &lines {
        let t = line.trim();
        let low = t.to_ascii_lowercase();
        if low.contains("full code") {
            full_code_line = Some(t.to_string());
        }
        if low.contains("dnr") || low.contains("dni") || low.contains("do not resuscitate") {
            dnr_line = Some(t.to_string());
        }
        if low.contains("npo") || low.contains("nothing by mouth") {
            npo_line = Some(t.to_string());
        }
        if low.contains("regular diet") || low.contains("tolerating po") || low.contains("cardiac diet") {
            diet_line = Some(t.to_string());
        }
        if low.contains("afebrile") {
            afebrile_line = Some(t.to_string());
        }
        if low.contains("febrile") && !low.contains("afebrile") {
            fever_line = Some(t.to_string());
        }
    }

    if let (Some(fc), Some(dnr)) = (full_code_line, dnr_line) {
        if fc != dnr {
            conflicts.push(ConflictPair {
                category: "Code Status".to_string(),
                first: fc,
                second: dnr,
                detail: "Conflicting Full Code vs DNR/DNI statements".to_string(),
            });
        }
    }
    if let (Some(npo), Some(diet)) = (npo_line, diet_line) {
        if npo != diet {
            conflicts.push(ConflictPair {
                category: "Diet".to_string(),
                first: npo,
                second: diet,
                detail: "Conflicting NPO vs PO/regular diet statements".to_string(),
            });
        }
    }
    if let (Some(afeb), Some(fev)) = (afebrile_line, fever_line) {
        if afeb != fev {
            conflicts.push(ConflictPair {
                category: "Temperature".to_string(),
                first: afeb,
                second: fev,
                detail: "Conflicting afebrile vs febrile statements".to_string(),
            });
        }
    }

    // 3. Overnight
    let mut overnight = Vec::new();
    for line in &lines {
        if let Some(caps) = OVERNIGHT_RE.captures(line.trim()) {
            overnight.push(caps[1].trim().to_string());
        }
    }

    // 4. Pending
    let mut pending = Vec::new();
    for line in &lines {
        let t = line.trim();
        if PENDING_RE.is_match(t) {
            pending.push(t.to_string());
        }
    }

    // 5. Devices
    let mut devices = Vec::new();
    for line in &lines {
        let t = line.trim();
        if let Some(m) = DEVICE_RE.find(t) {
            let dev_date = parse_date(t, Some(ref_date));
            let day = dev_date.map(|d| day_number(d, ref_date));
            devices.push(DeviceItem {
                device: m.as_str().to_string(),
                site: String::new(),
                placed_date: dev_date.map(|d| d.to_string()),
                day,
                line: t.to_string(),
            });
        }
    }

    // 6. Micro (skip allergy lines)
    let mut micro = Vec::new();
    for line in &lines {
        let t = line.trim();
        if ALLERGY_RE.is_match(t) || t.to_ascii_lowercase().starts_with("allerg") {
            continue;
        }
        if let Some(m) = MICRO_RE.find(t) {
            let low = t.to_ascii_lowercase();
            let status = if low.contains("pending") || low.contains("ngtd") || low.contains("no growth") {
                "preliminary"
            } else {
                "resulted"
            };
            micro.push(MicroItem {
                specimen: m.as_str().to_string(),
                result: t.to_string(),
                status: status.to_string(),
                line: t.to_string(),
            });
        }
    }

    // 7. ICU Daily Bundle check ("not mentioned" never means "not done")
    let lower_chart = text.to_ascii_lowercase();
    let bundle_defs = [
        ("pain_sedation", "Pain / Sedation (RASS, SAT/SBT)", &["rass", "sedation", "sat", "sbt", "propofol", "dexmedetomidine", "fentanyl"][..]),
        ("delirium", "Delirium (CAM-ICU)", &["cam-icu", "delirium", "oriented"][..]),
        ("mobility", "Early Mobility (PT/OT, OOB)", &["pt/ot", "physical therapy", "oob", "ambulat", "mobility"][..]),
        ("dvt_prophylaxis", "DVT Prophylaxis", &["dvt", "prophylaxis", "ppX", "lovenox", "enoxaparin", "subq heparin", "scd"][..]),
        ("gi_prophylaxis", "Stress Ulcer Prophylaxis", &["gi ppx", "famotidine", "pepcid", "pantoprazole", "protonix", "ppi"][..]),
        ("nutrition", "Nutrition / Diet", &["diet", "tube feed", "enteral", "tpn", "npo", "kcal"][..]),
        ("glycemic", "Glycemic Control", &["glucose", "insulin", "sliding scale", "ssi", "bg"][..]),
        ("lines_foley", "Line / Catheter Necessity", &["foley", "central line", "cvl", "picc", "arterial line"][..]),
    ];
    let mut bundle = Vec::new();
    for (id, label, keywords) in bundle_defs {
        let mentioned = keywords.iter().any(|kw| lower_chart.contains(kw));
        bundle.push(BundleGap {
            item: id.to_string(),
            label: label.to_string(),
            status: if mentioned {
                "mentioned".to_string()
            } else {
                "not mentioned".to_string()
            },
        });
    }

    // 8. Problems
    let mut problems = Vec::new();
    let mut seen_problems = HashSet::new();
    for line in &lines {
        let t = line.trim();
        if PROBLEM_HEADER_RE.is_match(t) && t.len() <= 100 {
            let key = t.to_ascii_lowercase();
            if seen_problems.insert(key) {
                problems.push(t.to_string());
            }
        }
    }

    json!({
        "safety": safety,
        "conflicts": conflicts,
        "overnight": overnight,
        "pending": pending,
        "stale": Vec::<String>::new(),
        "devices": devices,
        "micro": micro,
        "bundle": bundle,
        "problems": problems,
    })
}
