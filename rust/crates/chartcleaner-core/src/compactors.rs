//! Clinical compactors (Phase 3 + Next-Gen Compactors):
//! - `hospital_day` (label dates with HD# / POD#)
//! - `imaging_impression` (strip radiology report sections before Impression)
//! - `labs_compact` (condense lab result tables and grids into panel lines or fishbones)
//! - `meds_reconcile` (normalize medication lists)
//! - `vitals_summary` (summarize vitals flowsheets and I/O)
//! - `neuro_summary` (condense Neuro ICU checks, EVD/ICP, serial Na, drip titrations)
//! - `mar_summary` (condense MAR / PRN bolus logs by drug, route, unit)
//! - `flowsheet_tables` (de-tabularize multi-column clinical flowsheets)
//! - `note_order` (sort multi-note exports chronologically or reverse-chronologically)

use crate::chart_dates::{parse_date, reference_date};
use crate::config::{get_bool_opt, get_int_opt, get_str_opt};
use ahash::{AHashMap, AHashSet};
use chrono::{NaiveDate, NaiveDateTime, NaiveTime};
use once_cell::sync::Lazy;
use regex::Regex;
use serde_json::{json, Value};

fn fmt_num(v: f64) -> String {
    if (v - v.round()).abs() < 1e-9 && v.abs() < 1e12 {
        format!("{}", v.round() as i64)
    } else {
        let s = format!("{:.4}", v);
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    }
}

fn fmt_comma_i64(n: i64) -> String {
    let neg = n < 0;
    let s = n.unsigned_abs().to_string();
    let mut out = String::new();
    for (i, c) in s.chars().rev().enumerate() {
        if i > 0 && i % 3 == 0 {
            out.push(',');
        }
        out.push(c);
    }
    let formatted: String = out.chars().rev().collect();
    if neg {
        format!("-{formatted}")
    } else {
        formatted
    }
}

// ===========================================================================
// 1. hospital_day
// ===========================================================================

static HD_DATE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4}|\d{2}))"
    )
    .unwrap()
});
static HD_ADMIT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?:Admission\s+Date|Admit\s+Date|Date\s+of\s+Admission|Admitted(?:\s+on)?)\s*[:\-]?\s*"
    )
    .unwrap()
});
static HD_ALREADY_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"^[ \t]*\((?:HD|POD)#").unwrap());

fn hd_caps_to_date(caps: &regex::Captures) -> Option<NaiveDate> {
    if let Some(y_m) = caps.name("y") {
        let y: i32 = y_m.as_str().parse().ok()?;
        let m: u32 = caps.name("m")?.as_str().parse().ok()?;
        let d: u32 = caps.name("d")?.as_str().parse().ok()?;
        NaiveDate::from_ymd_opt(y, m, d)
    } else {
        let mut y: i32 = caps.name("y2")?.as_str().parse().ok()?;
        if y < 100 {
            y += 2000;
        }
        let m: u32 = caps.name("m2")?.as_str().parse().ok()?;
        let d: u32 = caps.name("d2")?.as_str().parse().ok()?;
        NaiveDate::from_ymd_opt(y, m, d)
    }
}

pub fn find_admit_date(text: &str) -> Option<NaiveDate> {
    let mut found = Vec::new();
    for m in HD_ADMIT_RE.find_iter(text) {
        let rest = &text[m.end()..];
        if let Some(caps) = HD_DATE_RE.captures(rest) {
            if caps.get(0).unwrap().start() == 0 {
                if let Some(d) = hd_caps_to_date(&caps) {
                    found.push(d);
                }
            }
        }
    }
    found.into_iter().min()
}

pub fn run_hospital_day(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "hospital_day", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let admit_opt = get_str_opt(cfg, "hospital_day", "admit_date", "auto");
    let admit_opt = if admit_opt.is_empty() {
        "auto".to_string()
    } else {
        admit_opt
    };
    let admit = if admit_opt == "auto" {
        find_admit_date(text)
    } else {
        NaiveDate::parse_from_str(&admit_opt, "%Y-%m-%d").ok()
    };
    let Some(admit) = admit else {
        return (
            text.to_string(),
            0,
            json!({ "note": "No admission date found; set one in the stage options." }),
        );
    };

    let mut surgeries: Vec<NaiveDate> = cfg
        .get("hospital_day")
        .and_then(|h| h.get("surgery_dates"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|s| s.as_str())
                .filter_map(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
                .collect()
        })
        .unwrap_or_default();
    surgeries.sort();

    let replace = get_str_opt(cfg, "hospital_day", "style", "append") == "replace";
    let mut labelled = 0usize;
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len() + 64);
    let mut last = 0usize;

    for caps in HD_DATE_RE.captures_iter(text) {
        let m0 = caps.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        let prev_bad = s > 0
            && (bytes[s - 1].is_ascii_digit()
                || matches!(bytes[s - 1], b'/' | b'.' | b'-'));
        let next_bad = e < bytes.len() && (bytes[e].is_ascii_digit() || bytes[e] == b'/');
        if prev_bad || next_bad || HD_ALREADY_RE.is_match(&text[e..]) {
            continue;
        }
        let Some(when) = hd_caps_to_date(&caps) else {
            continue;
        };
        if when < admit {
            continue;
        }
        let hd = (when - admit).num_days() + 1;
        let mut tags = vec![format!("HD#{hd}")];
        if let Some(&surg) = surgeries.iter().filter(|&&sd| sd <= when).next_back() {
            tags.push(format!("POD#{}", (when - surg).num_days()));
        }
        labelled += 1;
        out.push_str(&text[last..s]);
        if replace {
            if tags.len() > 1 {
                out.push_str(&format!("{} ({})", tags[0], tags[1]));
            } else {
                out.push_str(&tags[0]);
            }
        } else {
            out.push_str(&format!("{} ({})", m0.as_str(), tags.join(", ")));
        }
        last = e;
    }
    out.push_str(&text[last..]);
    (
        out,
        labelled,
        json!({ "admit_date": admit.format("%Y-%m-%d").to_string() }),
    )
}

// ===========================================================================
// 2. imaging_impression
// ===========================================================================

static IMG_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?im)^[ \t]*(?P<name>indications?|clinical\s+(?:history|indication)|history|reason\s+for\s+exam|technique|comparisons?|findings?|impressions?|conclusions?)[ \t]*:"
    )
    .unwrap()
});
static IMG_ANY_HEADER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?m)^[ \t]*[A-Za-z][A-Za-z /&()\-]{1,40}:[ \t]*(?:$|\S)").unwrap());

fn img_kind(name: &str) -> &'static str {
    let low = name.to_lowercase();
    if low.starts_with("indication")
        || low.starts_with("clinical")
        || low.starts_with("history")
        || low.starts_with("reason")
    {
        "indication"
    } else if low.starts_with("comparison") {
        "comparison"
    } else if low.starts_with("finding") {
        "findings"
    } else if low.starts_with("impression") || low.starts_with("conclusion") {
        "impression"
    } else {
        "technique"
    }
}

pub fn run_imaging_impression(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "imaging_impression", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let keep: AHashSet<String> = cfg
        .get("imaging_impression")
        .and_then(|c| c.get("keep"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .filter(|&k| matches!(k, "indication" | "technique" | "comparison" | "findings"))
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();

    let headers: Vec<(usize, &'static str)> = IMG_HEADER_RE
        .captures_iter(text)
        .map(|c| {
            (
                c.get(0).unwrap().start(),
                img_kind(c.name("name").unwrap().as_str()),
            )
        })
        .collect();
    let radiology_starts: AHashSet<usize> = headers.iter().map(|&(p, _)| p).collect();

    let mut cuts: Vec<(usize, usize)> = Vec::new();
    let mut reports: AHashSet<usize> = AHashSet::new();

    for (i, &(pos, kind)) in headers.iter().enumerate() {
        if kind != "findings" {
            continue;
        }
        let impression = headers[i + 1..]
            .iter()
            .find(|&&(_, k)| k == "impression" || k == "findings");
        let Some(&(imp_pos, "impression")) = impression else {
            continue;
        };
        let between = &text[pos..imp_pos];
        if between.matches('\n').count() > 80 {
            continue;
        }
        if IMG_ANY_HEADER_RE
            .find_iter(between)
            .any(|m| !radiology_starts.contains(&(m.start() + pos)))
        {
            continue;
        }

        let mut start = pos;
        let mut j = i as isize - 1;
        while j >= 0 {
            let (jp, jk) = headers[j as usize];
            if !matches!(jk, "indication" | "technique" | "comparison") {
                break;
            }
            let gap = &text[jp..start];
            if IMG_ANY_HEADER_RE
                .find_iter(gap)
                .any(|m| !radiology_starts.contains(&(m.start() + jp)))
            {
                break;
            }
            start = jp;
            j -= 1;
        }

        let mut sections: Vec<(usize, &'static str)> = headers
            .iter()
            .copied()
            .filter(|&(p, _)| start <= p && p < imp_pos)
            .collect();
        sections.push((imp_pos, "impression"));
        for pair in sections.windows(2) {
            let (s_pos, s_kind) = pair[0];
            let (e_pos, _) = pair[1];
            if !keep.contains(s_kind) {
                cuts.push((s_pos, e_pos));
                reports.insert(imp_pos);
            }
        }
    }

    if cuts.is_empty() {
        return (text.to_string(), 0, json!({}));
    }
    cuts.sort();
    cuts.dedup();
    let mut out = String::with_capacity(text.len());
    let mut last = 0usize;
    for (s, e) in cuts {
        if s < last {
            continue;
        }
        out.push_str(&text[last..s]);
        last = e;
    }
    out.push_str(&text[last..]);
    let n = reports.len();
    (out, n, json!({ "reports_trimmed": n }))
}

// ===========================================================================
// 3. labs_compact (config key `lab_compaction`)
// ===========================================================================

const LAB_DEFS: &[(&str, &str, &[&str])] = &[
    ("Na", "BMP", &["sodium", "na"]),
    ("K", "BMP", &["potassium", "k"]),
    ("Cl", "BMP", &["chloride", "cl"]),
    (
        "CO2",
        "BMP",
        &[
            "co2",
            "bicarbonate",
            "hco3",
            "total co2",
            "carbon dioxide",
        ],
    ),
    (
        "BUN",
        "BMP",
        &["bun", "urea nitrogen", "blood urea nitrogen"],
    ),
    ("Cr", "BMP", &["creatinine", "creat", "cr"]),
    ("Glu", "BMP", &["glucose", "glu"]),
    ("Ca", "BMP", &["calcium", "ca"]),
    ("AG", "BMP", &["anion gap", "ag"]),
    ("Mg", "Other", &["magnesium", "mg"]),
    ("Phos", "Other", &["phosphorus", "phos", "phosphate"]),
    (
        "WBC",
        "CBC",
        &["wbc", "white blood cell count", "white blood cells"],
    ),
    ("Hgb", "CBC", &["hemoglobin", "hgb", "hb"]),
    ("Hct", "CBC", &["hematocrit", "hct"]),
    ("Plt", "CBC", &["platelets", "platelet count", "plt"]),
    ("MCV", "CBC", &["mcv"]),
    ("AST", "LFT", &["ast", "sgot"]),
    ("ALT", "LFT", &["alt", "sgpt"]),
    ("ALP", "LFT", &["alkaline phosphatase", "alk phos", "alp"]),
    (
        "TBili",
        "LFT",
        &[
            "total bilirubin",
            "bilirubin total",
            "bilirubin, total",
            "tbili",
        ],
    ),
    ("Alb", "LFT", &["albumin", "alb"]),
    ("INR", "Coags", &["inr"]),
    ("PT", "Coags", &["pt", "prothrombin time", "protime"]),
    ("PTT", "Coags", &["ptt", "aptt"]),
    ("Lactate", "Other", &["lactate", "lactic acid"]),
    (
        "Trop",
        "Other",
        &["troponin", "troponin i", "troponin t", "hs troponin"],
    ),
];
const PANEL_ORDER: &[&str] = &["BMP", "CBC", "LFT", "Coags", "Other"];

static LAB_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 ,()/\-]*?)[ \t]*(?:\t|:|[ \t]{2,})[ \t]*(?P<value>[<>]?\d+(?:\.\d+)?)[ \t]*(?P<flag1>(?:\(?(?:H|L|HH|LL|A|C|High|Low|Critical)\)?|\*))?[ \t]*(?:(?P<range>\d+(?:\.\d+)?\s*-\s*\d+(?:\.\d+)?|[<>]\s*\d+(?:\.\d+)?)[ \t]*)?(?:(?P<unit>[A-Za-z%/µμ][A-Za-z0-9%/µμ.^]*(?:/[A-Za-z0-9.^]+)?)[ \t]*)?(?P<flag2>(?:\(?(?:H|L|HH|LL|A|C|High|Low|Critical)\)?|\*))?(?:[ \t]+(?:Final|Prelim(?:inary)?|Corrected))?[ \t]*$"
    )
    .unwrap()
});
static LAB_GRID_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:Lab|Component|Recent Labs)?[ \t]*(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+\d{3,4})?[ \t]*){2,}$"
    )
    .unwrap()
});
static LAB_GRID_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 ,()/\-]*?)[ \t]+(?P<values>(?:(?:[<>]?\d+(?:\.\d+)?|-+|--)[*HL]?[ \t]*){2,})$"
    )
    .unwrap()
});
static LAB_COLUMNS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^[ \t]*(?:Component|Test|Lab)[ \t]+(?:Value|Result)\b[^\n]*$").unwrap()
});
static LAB_DATE_EXTRACT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+\d{3,4})?")
        .unwrap()
});

fn lab_alias_map(cfg: &Value) -> AHashMap<String, String> {
    let mut names = AHashMap::new();
    for &(short, _panel, spellings) in LAB_DEFS {
        for &sp in spellings {
            names.insert(sp.to_string(), short.to_string());
        }
    }
    if let Some(aliases) = cfg
        .get("lab_compaction")
        .and_then(|c| c.get("aliases"))
        .and_then(|v| v.as_object())
    {
        for (sp, sh) in aliases {
            if let Some(s) = sh.as_str() {
                if !sp.trim().is_empty() {
                    names.insert(sp.trim().to_lowercase(), s.trim().to_string());
                }
            }
        }
    }
    names
}

fn lab_short(name: &str, aliases: &AHashMap<String, String>) -> Option<String> {
    let key = name
        .replace(',', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    aliases
        .get(&key)
        .or_else(|| aliases.get(&key.replace(' ', "")))
        .cloned()
}

fn lab_panel(short: &str) -> &'static str {
    LAB_DEFS
        .iter()
        .find(|&&(s, _, _)| s == short)
        .map(|&(_, p, _)| p)
        .unwrap_or("Other")
}

fn lab_flag(f1: Option<&str>, f2: Option<&str>) -> &'static str {
    for f in [f1, f2].into_iter().flatten() {
        let clean = f.trim_matches(|c| c == '(' || c == ')').to_uppercase();
        match clean.as_str() {
            "H" | "HIGH" | "HH" => return " (H)",
            "L" | "LOW" | "LL" => return " (L)",
            "*" | "A" => return " (abnl)",
            "C" | "CRITICAL" => return " (crit)",
            _ => {}
        }
    }
    ""
}

fn lab_fishbone(values: &AHashMap<String, String>, panel: &str) -> Option<String> {
    if panel == "BMP"
        && ["Na", "Cl", "BUN", "K", "CO2", "Cr", "Glu"]
            .iter()
            .all(|k| values.contains_key(*k))
    {
        let cols = [
            (&values["Na"], &values["K"]),
            (&values["Cl"], &values["CO2"]),
            (&values["BUN"], &values["Cr"]),
        ];
        let widths: Vec<usize> = cols.iter().map(|(a, b)| a.len().max(b.len())).collect();
        let top = cols
            .iter()
            .zip(&widths)
            .map(|((a, _), &w)| format!("{:<width$}", a, width = w))
            .collect::<Vec<_>>()
            .join(" | ");
        let bottom = cols
            .iter()
            .zip(&widths)
            .map(|((_, b), &w)| format!("{:<width$}", b, width = w))
            .collect::<Vec<_>>()
            .join(" | ");
        return Some(format!(
            "{top} /\n{}< {}\n{bottom} \\",
            "-".repeat(top.len()),
            values["Glu"]
        ));
    }
    if panel == "CBC"
        && ["WBC", "Hgb", "Hct", "Plt"]
            .iter()
            .all(|k| values.contains_key(*k))
    {
        let pad = " ".repeat(values["WBC"].len() + 2);
        return Some(format!(
            "{pad}{}\n{} >--< {}\n{pad}{}",
            values["Hgb"], values["WBC"], values["Plt"], values["Hct"]
        ));
    }
    None
}

fn format_labs(entries: &[(String, String, String)], style: &str) -> String {
    let mut by_panel: AHashMap<&str, Vec<&(String, String, String)>> = AHashMap::new();
    for item in entries {
        by_panel.entry(lab_panel(&item.0)).or_default().push(item);
    }
    let mut lines = Vec::new();
    for &panel in PANEL_ORDER {
        let Some(items) = by_panel.get(panel) else {
            continue;
        };
        if style == "fishbone" {
            let map: AHashMap<String, String> =
                items.iter().map(|i| (i.0.clone(), i.2.clone())).collect();
            if let Some(drawn) = lab_fishbone(&map, panel) {
                lines.push(format!("{panel}:\n{drawn}"));
                continue;
            }
        }
        let joined = items
            .iter()
            .map(|(s, shown, _)| format!("{s} {shown}"))
            .collect::<Vec<_>>()
            .join(", ");
        lines.push(format!("{panel}: {joined}"));
    }
    lines.join("\n")
}

pub fn run_labs_compact(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "lab_compaction", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let style = get_str_opt(cfg, "lab_compaction", "style", "line");
    let latest_only = get_bool_opt(cfg, "lab_compaction", "latest_only", false);
    let keep_ref = get_bool_opt(cfg, "lab_compaction", "keep_reference_ranges", false);
    let aliases = lab_alias_map(cfg);

    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut blocks = 0usize;
    let mut skipped = 0usize;
    let mut i = 0usize;

    while i < lines.len() {
        if LAB_GRID_HEADER_RE.is_match(lines[i]) {
            let mut j = i + 1;
            while j < lines.len() && LAB_GRID_ROW_RE.is_match(lines[j]) {
                j += 1;
            }
            if j - i - 1 >= 2 {
                let mut entries = Vec::new();
                let mut ok = true;
                for &rline in &lines[i + 1..j] {
                    let Some(caps) = LAB_GRID_ROW_RE.captures(rline) else {
                        ok = false;
                        break;
                    };
                    let Some(short) = lab_short(caps.name("name").unwrap().as_str(), &aliases)
                    else {
                        ok = false;
                        break;
                    };
                    let cells: Vec<&str> = caps
                        .name("values")
                        .unwrap()
                        .as_str()
                        .split_whitespace()
                        .filter(|c| !c.chars().all(|ch| ch == '-'))
                        .collect();
                    if cells.is_empty() {
                        ok = false;
                        break;
                    }
                    let fmt_cell = |cell: &str| -> String {
                        let bare = cell.trim_end_matches(['*', 'H', 'L']);
                        let fl = &cell[bare.len()..];
                        format!(
                            "{bare}{}",
                            lab_flag(if fl.is_empty() { None } else { Some(fl) }, None)
                        )
                    };
                    let shown = if latest_only {
                        fmt_cell(cells.last().unwrap())
                    } else {
                        cells.iter().map(|&c| fmt_cell(c)).collect::<Vec<_>>().join(" → ")
                    };
                    let bare_last = cells
                        .last()
                        .unwrap()
                        .trim_end_matches(['*', 'H', 'L'])
                        .to_string();
                    entries.push((short, shown, bare_last));
                }
                if ok {
                    let dates: Vec<&str> = LAB_DATE_EXTRACT_RE
                        .find_iter(lines[i])
                        .map(|m| m.as_str())
                        .collect();
                    let span = if dates.len() > 1 && !latest_only {
                        format!(" ({} → {})", dates[0], dates.last().unwrap())
                    } else if let Some(&d) = dates.last() {
                        format!(" ({d})")
                    } else {
                        String::new()
                    };
                    let body = format_labs(&entries, "line");
                    let rendered = body
                        .lines()
                        .map(|l| {
                            if let Some((head, tail)) = l.split_once(':') {
                                format!("{head}{span}:{tail}")
                            } else {
                                l.to_string()
                            }
                        })
                        .collect::<Vec<_>>()
                        .join("\n");
                    out.push(rendered);
                    blocks += 1;
                    i = j;
                    continue;
                } else {
                    skipped += 1;
                }
            }
        }

        let is_lab_row = |line: &str| -> bool {
            LAB_ROW_RE
                .captures(line)
                .and_then(|c| lab_short(c.name("name")?.as_str(), &aliases))
                .is_some()
        };

        if is_lab_row(lines[i]) {
            let mut j = i;
            while j < lines.len() && is_lab_row(lines[j]) {
                j += 1;
            }
            if j - i >= 3 {
                let mut entries = Vec::new();
                for &rline in &lines[i..j] {
                    let caps = LAB_ROW_RE.captures(rline).unwrap();
                    let short = lab_short(caps.name("name").unwrap().as_str(), &aliases).unwrap();
                    let val = caps.name("value").unwrap().as_str();
                    let mut shown = format!(
                        "{val}{}",
                        lab_flag(
                            caps.name("flag1").map(|m| m.as_str()),
                            caps.name("flag2").map(|m| m.as_str())
                        )
                    );
                    if keep_ref {
                        if let Some(rng) = caps.name("range") {
                            let clean_rng =
                                rng.as_str().split_whitespace().collect::<Vec<_>>().join(" ");
                            shown.push_str(&format!(" [{clean_rng}]"));
                        }
                    }
                    entries.push((short, shown, val.to_string()));
                }
                if out.last().is_some_and(|last| LAB_COLUMNS_RE.is_match(last)) {
                    out.pop();
                }
                out.push(format_labs(&entries, &style));
                blocks += 1;
                i = j;
                continue;
            }
        }

        out.push(lines[i].to_string());
        i += 1;
    }

    let mut details = json!({ "blocks": blocks });
    if skipped > 0 {
        details["skipped"] = json!(skipped);
    }
    (out.join("\n"), blocks, details)
}

// ===========================================================================
// 4. meds_reconcile (config key `med_normalize`)
// ===========================================================================

static MED_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:current\s+(?:outpatient\s+|inpatient\s+)?|active\s+|home\s+|scheduled\s+|prn\s+|discharge\s+)?(?:medications?|meds|continuous\s+infusions?|infusions)[ \t]*:?[ \t]*$"
    )
    .unwrap()
});
static MED_COL_HEADER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^[ \t]*Medication[ \t]+Sig\b.*$").unwrap());
static MED_OTHER_HEADER_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[ \t]*[A-Z][A-Za-z /&()\-]{2,40}:?[ \t]*$").unwrap());
static MED_CONT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[ \t]*(?:[-•*·]|\d+[.)])?[ \t]*[a-z]").unwrap());
static MED_BULLET_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[ \t]*(?:[-•*·]|\d+[.)])?[ \t]*").unwrap());
static MED_STATUS_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\[?\s*\b(Held|Hold|Paused|Discontinued|Stopped)\b\s*\]?").unwrap());
static MED_SIG_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(?:Take|Give|Inject|Apply|Inhale|Place|Use|Instill)\s+(?P<qty>\d+(?:\.\d+)?(?:\s*(?:tablets?|capsules?|puffs?|units?|mL|drops?|patch(?:es)?|sprays?))?)\s*(?:\((?P<total>[^)]*?)\s*total\))?"
    )
    .unwrap()
});
static MED_STRENGTH_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\b\d[\d,]*(?:\.\d+)?\s*(?:mg|mcg|g|mEq|units?)\b").unwrap());
static MED_ONE_TAB_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)^1\s*(?:tablet|capsule)$").unwrap());
static MED_PUNC_SPACE_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\s+([,.;])").unwrap());

const DEFAULT_MED_DROPS: &[&str] = &[
    r"\bDispense:?\s*\d+(?:\.\d+)?\s*[A-Za-z]*(?:\s*\([^)]*\))?",
    r"\bRefills?:?\s*\d+",
    r"\b(?:Start|End|Last)\s+(?:Date|dose)?:?\s*\d{1,2}/\d{1,2}/\d{2,4}",
    r"\b(?:Ordering|Authorizing|Prescribing)\s+Provider:?\s*[^;\n]*",
    r"\bPatient\s+not\s+taking:?[^;\n]*",
    r"\b(?:Not\s+)?Taking\b",
    r"\s\d+\s+(?:tablet|capsule|each|vial|bottle|inhaler|patch)s?\s+\d+\s*$",
    r"\(\s*[A-Z][A-Z0-9 \-]{2,}\s*\)",
];

const DEFAULT_ROUTES: &[(&str, &str)] = &[
    ("by mouth", "PO"),
    ("oral", "PO"),
    ("orally", "PO"),
    ("intravenous", "IV"),
    ("intravenously", "IV"),
    ("intramuscular", "IM"),
    ("subcutaneous", "subcut"),
    ("subcutaneously", "subcut"),
    ("sublingual", "SL"),
    ("per tube", "per tube"),
    ("via g-tube", "per G-tube"),
    ("inhalation", "inhaled"),
    ("topical", "topical"),
    ("rectal", "PR"),
    ("nasal", "intranasal"),
    ("transdermal", "transdermal"),
];

const DEFAULT_FREQS: &[(&str, &str)] = &[
    ("once daily", "daily"),
    ("one time daily", "daily"),
    ("every day", "daily"),
    ("daily", "daily"),
    ("two times daily", "BID"),
    ("twice daily", "BID"),
    ("2 times daily", "BID"),
    ("three times daily", "TID"),
    ("3 times daily", "TID"),
    ("four times daily", "QID"),
    ("4 times daily", "QID"),
    ("every morning", "every morning"),
    ("nightly", "nightly"),
    ("at bedtime", "at bedtime"),
    ("every other day", "every other day"),
    ("weekly", "weekly"),
    ("once", "once"),
    ("every 2 hours", "q2h"),
    ("every 4 hours", "q4h"),
    ("every 6 hours", "q6h"),
    ("every 8 hours", "q8h"),
    ("every 12 hours", "q12h"),
    ("every 24 hours", "q24h"),
    ("continuous", "continuous"),
    ("as needed", "PRN"),
    ("as needed for", "PRN"),
];

fn build_med_phrase_map(base: &[(&str, &str)], extra: Option<&Value>) -> Vec<(Regex, String)> {
    let mut merged: AHashMap<String, String> = base
        .iter()
        .map(|&(k, v)| (k.to_string(), v.to_string()))
        .collect();
    if let Some(obj) = extra.and_then(|v| v.as_object()) {
        for (k, v) in obj {
            if let Some(s) = v.as_str() {
                merged.insert(k.to_lowercase(), s.to_string());
            }
        }
    }
    let mut pairs: Vec<(String, String)> = merged.into_iter().collect();
    pairs.sort_by(|a, b| b.0.len().cmp(&a.0.len()));
    pairs
        .into_iter()
        .filter_map(|(k, v)| {
            Regex::new(&format!(r"(?i)\b{}\b", regex::escape(&k)))
                .ok()
                .map(|re| (re, v))
        })
        .collect()
}

fn normalize_med_line(
    line: &str,
    drops: &[Regex],
    routes: &[(Regex, String)],
    freqs: &[(Regex, String)],
) -> String {
    let bullet = MED_BULLET_RE
        .find(line)
        .map(|m| m.as_str())
        .unwrap_or("");
    let mut body = line[bullet.len()..].to_string();
    let status_word = MED_STATUS_RE
        .captures(&body)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().to_lowercase());
    body = MED_STATUS_RE.replace_all(&body, "").into_owned();
    for drop_re in drops {
        body = drop_re.replace_all(&body, " ").into_owned();
    }

    if let Some(sig) = MED_SIG_RE.captures(&body) {
        let sig_m0 = sig.get(0).unwrap();
        let sig_start = sig_m0.start();
        let sig_end = sig_m0.end();
        if let Some(tot) = sig.name("total") {
            let mut before = body[..sig_start].to_string();
            if let Some(prod) = MED_STRENGTH_RE.find(&before) {
                if !before[prod.end()..].trim_start().starts_with('/') {
                    before.replace_range(prod.start()..prod.end(), "");
                }
            }
            body = format!("{}{} {}", before, tot.as_str().trim(), &body[sig_end..]);
        } else {
            let mut qty = sig
                .name("qty")
                .map(|m| m.as_str().to_string())
                .unwrap_or_default();
            if MED_ONE_TAB_RE.is_match(&qty) && MED_STRENGTH_RE.is_match(&body[..sig_start]) {
                qty.clear();
            }
            body = format!("{}{} {}", &body[..sig_start], qty, &body[sig_end..]);
        }
    }

    for (re, short) in routes {
        body = re.replace_all(&body, short.as_str()).into_owned();
    }
    for (re, short) in freqs {
        body = re.replace_all(&body, short.as_str()).into_owned();
    }

    // Remove bare `tablet` or `capsule` unless preceded by `<digit> `
    static TAB_CAP_RE: Lazy<Regex> =
        Lazy::new(|| Regex::new(r"(?i)\b(?:tablet|capsule)\b").unwrap());
    let mut new_body = String::with_capacity(body.len());
    let mut last = 0usize;
    for m in TAB_CAP_RE.find_iter(&body) {
        let s = m.start();
        let preceded_by_digit_space = s >= 2
            && body.as_bytes()[s - 1] == b' '
            && body.as_bytes()[s - 2].is_ascii_digit();
        if !preceded_by_digit_space {
            new_body.push_str(&body[last..s]);
            last = m.end();
        }
    }
    new_body.push_str(&body[last..]);
    body = new_body;

    let collapsed = body.split_whitespace().collect::<Vec<_>>().join(" ");
    let clean_punc = MED_PUNC_SPACE_RE.replace_all(&collapsed, "$1");
    body = clean_punc
        .trim_matches(|c: char| matches!(c, ' ' | '.' | ';' | ','))
        .to_string();

    if let Some(sw) = status_word {
        if matches!(sw.as_str(), "held" | "hold" | "paused") {
            body.push_str(" (held)");
        } else {
            body.push_str(" (discontinued)");
        }
    }

    if body.is_empty() {
        line.to_string()
    } else {
        let b_trim = bullet.trim_end();
        let sep = if bullet.trim().is_empty() { "" } else { " " };
        format!("{b_trim}{sep}{body}")
    }
}

pub fn run_meds_reconcile(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "med_normalize", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let mcfg = cfg.get("med_normalize");
    let mut drops: Vec<Regex> = DEFAULT_MED_DROPS
        .iter()
        .filter_map(|p| Regex::new(&format!("(?i){p}")).ok())
        .collect();
    if let Some(arr) = mcfg
        .and_then(|c| c.get("drop_fields"))
        .and_then(|v| v.as_array())
    {
        for p in arr {
            if let Some(s) = p.as_str() {
                if let Ok(re) = Regex::new(&format!("(?i){s}")) {
                    drops.push(re);
                }
            }
        }
    }
    let routes = build_med_phrase_map(DEFAULT_ROUTES, mcfg.and_then(|c| c.get("route_map")));
    let freqs = build_med_phrase_map(DEFAULT_FREQS, mcfg.and_then(|c| c.get("frequency_map")));

    let raw_lines: Vec<&str> = text.split('\n').collect();
    let mut out_lines: Vec<Option<String>> = raw_lines.iter().map(|s| Some(s.to_string())).collect();
    let mut in_meds = false;
    let mut changed = 0usize;

    for i in 0..raw_lines.len() {
        let line = raw_lines[i];
        if MED_HEADER_RE.is_match(line) {
            in_meds = true;
            continue;
        }
        if !in_meds {
            continue;
        }
        if line.trim().is_empty() {
            let nxt = raw_lines[i + 1..]
                .iter()
                .find(|l| !l.trim().is_empty())
                .copied()
                .unwrap_or("");
            in_meds = MED_CONT_RE.is_match(nxt);
            continue;
        }
        if MED_OTHER_HEADER_RE.is_match(line)
            && !line.chars().any(|c| c.is_ascii_digit())
            && !MED_COL_HEADER_RE.is_match(line)
        {
            in_meds = false;
            continue;
        }
        if MED_COL_HEADER_RE.is_match(line) {
            out_lines[i] = None;
            changed += 1;
            continue;
        }
        let norm = normalize_med_line(line, &drops, &routes, &freqs);
        if norm != line {
            out_lines[i] = Some(norm);
            changed += 1;
        }
    }

    if changed == 0 {
        return (text.to_string(), 0, json!({}));
    }
    let joined = out_lines
        .into_iter()
        .flatten()
        .collect::<Vec<_>>()
        .join("\n");
    (joined, changed, json!({ "lines": changed }))
}

// ===========================================================================
// 5. vitals_summary
// ===========================================================================

const VITALS_MAP: &[(&str, &str)] = &[
    ("bp", "BP"),
    ("blood pressure", "BP"),
    ("nibp", "BP"),
    ("art bp", "Art BP"),
    ("abp", "Art BP"),
    ("pulse", "HR"),
    ("heart rate", "HR"),
    ("hr", "HR"),
    ("temp", "T"),
    ("temperature", "T"),
    ("tmax", "Tmax"),
    ("resp", "RR"),
    ("respiratory rate", "RR"),
    ("rr", "RR"),
    ("spo2", "SpO2"),
    ("o2 sat", "SpO2"),
    ("sao2", "SpO2"),
    ("pulse ox", "SpO2"),
    ("map", "MAP"),
    ("icp", "ICP"),
    ("cpp", "CPP"),
    ("cvp", "CVP"),
];

static VIT_LABEL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^[ \t]*(?P<label>[A-Za-z][A-Za-z0-9 ]*?)[ \t]*:?[ \t]+(?P<rest>\S.*)$").unwrap()
});
static VIT_BP_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\b(\d{2,3})/(\d{2,3})\b").unwrap());
static VIT_TEMP_C_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(\d{2}(?:\.\d)?)\s*°?\s*C\b").unwrap());
static VIT_TEMP_F_PAREN_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\(\s*\d+(?:\.\d+)?\s*°?\s*F\s*\)").unwrap());
static VIT_NUM_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(\d+(?:\.\d+)?)(?:\s*%)?").unwrap());
static VIT_DATE_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^[ \t]*(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+\d{3,4})?[ \t]*){2,}$"
    )
    .unwrap()
});
static VIT_DATE_COL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?(?:[ \t]+(\d{3,4}|\d{1,2}:\d{2}))?").unwrap()
});
static VIT_IO_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^[ \t]*(?P<kind>Intake|Output|Net)[ \t:]+(?P<sign>[-+]?)(?P<value>\d[\d,]*)\s*(?:ml|mL)\b").unwrap()
});

#[derive(Debug, Clone)]
enum VitalReadings {
    Bp(Vec<(i64, i64)>),
    Num(Vec<f64>),
}

impl VitalReadings {
    fn len(&self) -> usize {
        match self {
            VitalReadings::Bp(v) => v.len(),
            VitalReadings::Num(v) => v.len(),
        }
    }

    fn slice_indices(&self, keep: &[usize]) -> Self {
        if keep.is_empty() {
            return self.clone();
        }
        match self {
            VitalReadings::Bp(v) => VitalReadings::Bp(
                keep.iter().filter_map(|&i| v.get(i).copied()).collect(),
            ),
            VitalReadings::Num(v) => VitalReadings::Num(
                keep.iter().filter_map(|&i| v.get(i).copied()).collect(),
            ),
        }
    }
}

fn parse_vital_row(line: &str) -> Option<(&'static str, VitalReadings)> {
    let caps = VIT_LABEL_RE.captures(line)?;
    let label_key = caps
        .name("label")?
        .as_str()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let &(_, short) = VITALS_MAP.iter().find(|&&(k, _)| k == label_key)?;
    let rest = caps.name("rest")?.as_str();

    if short == "BP" || short == "Art BP" {
        let mut found = Vec::new();
        for c in VIT_BP_RE.captures_iter(rest) {
            let a: i64 = c.get(1)?.as_str().parse().ok()?;
            let b: i64 = c.get(2)?.as_str().parse().ok()?;
            found.push((a, b));
        }
        let leftover = VIT_BP_RE.replace_all(rest, "");
        if leftover.chars().any(|ch| ch.is_ascii_alphanumeric()) || found.len() < 2 {
            return None;
        }
        return Some((short, VitalReadings::Bp(found)));
    }

    if short == "T" || short == "Tmax" {
        let mut found = Vec::new();
        for c in VIT_TEMP_C_RE.captures_iter(rest) {
            found.push(c.get(1)?.as_str().parse::<f64>().ok()?);
        }
        let step1 = VIT_TEMP_C_RE.replace_all(rest, "");
        let leftover = VIT_TEMP_F_PAREN_RE.replace_all(&step1, "");
        if leftover
            .replace('°', "")
            .chars()
            .any(|ch| ch.is_ascii_alphanumeric())
            || found.len() < 2
        {
            return None;
        }
        return Some((short, VitalReadings::Num(found)));
    }

    // Standard numeric vital
    let bytes = rest.as_bytes();
    let mut found = Vec::new();
    let mut leftover = String::with_capacity(rest.len());
    let mut last = 0usize;
    for c in VIT_NUM_RE.captures_iter(rest) {
        let m0 = c.get(0).unwrap();
        let s = m0.start();
        let e = m0.end();
        let prev_bad = s > 0 && (bytes[s - 1].is_ascii_digit() || bytes[s - 1] == b'.');
        let next_bad =
            e < bytes.len() && (bytes[e].is_ascii_digit() || matches!(bytes[e], b'.' | b'/'));
        if prev_bad || next_bad {
            continue;
        }
        found.push(c.get(1)?.as_str().parse::<f64>().ok()?);
        leftover.push_str(&rest[last..s]);
        last = e;
    }
    leftover.push_str(&rest[last..]);
    if leftover
        .replace('°', "")
        .chars()
        .any(|ch| ch.is_ascii_alphanumeric())
        || found.len() < 2
    {
        return None;
    }
    Some((short, VitalReadings::Num(found)))
}

fn summarize_vital(short: &str, readings: &VitalReadings) -> String {
    match readings {
        VitalReadings::Bp(vals) => {
            let lo = vals.iter().min_by_key(|v| v.0).unwrap();
            let hi = vals.iter().max_by_key(|v| v.0).unwrap();
            let last = vals.last().unwrap();
            let span = if lo != hi {
                format!("{}/{}–{}/{}", lo.0, lo.1, hi.0, hi.1)
            } else {
                format!("{}/{}", lo.0, lo.1)
            };
            format!("{short} {span} (last {}/{})", last.0, last.1)
        }
        VitalReadings::Num(vals) => {
            let unit = if short == "T" || short == "Tmax" {
                " °C"
            } else if short == "SpO2" {
                "%"
            } else {
                ""
            };
            let lo = vals.iter().copied().fold(f64::INFINITY, f64::min);
            let hi = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
            let last = *vals.last().unwrap();
            let span = if (lo - hi).abs() > 1e-9 {
                format!("{}–{}", fmt_num(lo), fmt_num(hi))
            } else {
                fmt_num(lo)
            };
            format!("{short} {span}{unit} (last {})", fmt_num(last))
        }
    }
}

fn parse_vitals_col_times(header: &str) -> Option<Vec<NaiveDateTime>> {
    let caps_list: Vec<_> = VIT_DATE_COL_RE.captures_iter(header).collect();
    if caps_list.len() < 2 {
        return None;
    }
    let max_y = caps_list
        .iter()
        .filter_map(|c| c.get(3)?.as_str().parse::<i32>().ok())
        .max()
        .unwrap_or(2000);
    let mut out = Vec::with_capacity(caps_list.len());
    for c in caps_list {
        let month: u32 = c.get(1)?.as_str().parse().ok()?;
        let day: u32 = c.get(2)?.as_str().parse().ok()?;
        let mut year: i32 = c
            .get(3)
            .and_then(|m| m.as_str().parse().ok())
            .unwrap_or(max_y);
        if year < 100 {
            year += 2000;
        }
        let (hh, mm) = if let Some(clk) = c.get(4) {
            let digits = clk.as_str().replace(':', "");
            if digits.len() > 2 {
                let split = digits.len() - 2;
                (
                    digits[..split].parse::<u32>().ok()?,
                    digits[split..].parse::<u32>().ok()?,
                )
            } else {
                (digits.parse::<u32>().ok()?, 0)
            }
        } else {
            (0, 0)
        };
        let d = NaiveDate::from_ymd_opt(year, month, day)?;
        let t = NaiveTime::from_hms_opt(hh, mm, 0)?;
        out.push(NaiveDateTime::new(d, t));
    }
    Some(out)
}

pub fn run_vitals_summary(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "vitals_summary", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let window_hours = cfg
        .get("vitals_summary")
        .and_then(|c| c.get("window_hours"))
        .and_then(|v| v.as_f64())
        .unwrap_or(0.0);

    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut blocks = 0usize;
    let mut i = 0usize;

    while i < lines.len() {
        let header = if VIT_DATE_ROW_RE.is_match(lines[i]) {
            Some(lines[i])
        } else {
            None
        };
        let start = if header.is_some() { i + 1 } else { i };
        let mut rows = Vec::new();
        let mut j = start;
        while j < lines.len() {
            if let Some(row) = parse_vital_row(lines[j]) {
                rows.push(row);
                j += 1;
            } else {
                break;
            }
        }

        if rows.len() >= 2 {
            let readings = rows.iter().map(|(_, v)| v.len()).max().unwrap_or(0);
            let mut label = format!("Vitals ({readings} readings)");
            if window_hours > 0.0 {
                if let Some(hdr) = header {
                    if let Some(times) = parse_vitals_col_times(hdr) {
                        if rows.iter().all(|(_, v)| v.len() == times.len()) {
                            let latest = *times.iter().max().unwrap();
                            let keep: Vec<usize> = times
                                .iter()
                                .enumerate()
                                .filter(|(_, &t)| {
                                    (latest - t).num_seconds() as f64 <= window_hours * 3600.0 + 1.0
                                })
                                .map(|(idx, _)| idx)
                                .collect();
                            let sliced: Vec<(&'static str, VitalReadings)> = rows
                                .iter()
                                .map(|&(s, ref v)| (s, v.slice_indices(&keep)))
                                .collect();
                            if sliced.iter().all(|(_, v)| v.len() >= 2) {
                                let kept = sliced.iter().map(|(_, v)| v.len()).max().unwrap_or(0);
                                rows = sliced;
                                label = format!(
                                    "Vitals ({kept} of {} readings, last {}h)",
                                    times.len(),
                                    fmt_num(window_hours)
                                );
                            }
                        }
                    }
                }
            }
            let summary_parts = rows
                .iter()
                .map(|(s, v)| summarize_vital(s, v))
                .collect::<Vec<_>>()
                .join("; ");
            out.push(format!("{label}: {summary_parts}"));
            blocks += 1;
            i = j;
            continue;
        }

        let mut io: AHashMap<String, i64> = AHashMap::new();
        let mut j_io = i;
        while j_io < lines.len() {
            if let Some(caps) = VIT_IO_RE.captures(lines[j_io]) {
                let kind = caps.name("kind").unwrap().as_str().to_lowercase();
                let sign = if caps.name("sign").map(|m| m.as_str()) == Some("-") {
                    -1i64
                } else {
                    1i64
                };
                if let Ok(val) = caps
                    .name("value")
                    .unwrap()
                    .as_str()
                    .replace(',', "")
                    .parse::<i64>()
                {
                    io.insert(kind, val * sign);
                    j_io += 1;
                    continue;
                }
            }
            break;
        }
        if io.contains_key("intake") && io.contains_key("output") {
            let intake = io["intake"];
            let output = io["output"];
            let net = io.get("net").copied().unwrap_or(intake - output);
            let net_sign = if net >= 0 { "+" } else { "" };
            out.push(format!(
                "I/O: {} in / {} out (net {}{} mL)",
                fmt_comma_i64(intake),
                fmt_comma_i64(output),
                net_sign,
                fmt_comma_i64(net)
            ));
            blocks += 1;
            i = j_io;
            continue;
        }

        out.push(lines[i].to_string());
        i += 1;
    }

    (out.join("\n"), blocks, json!({ "blocks": blocks }))
}

// ===========================================================================
// 6. neuro_summary
// ===========================================================================

const NEURO_LABELS: &[(&str, &str)] = &[
    ("gcs", "GCS"),
    ("gcs total", "GCS"),
    ("glasgow coma scale", "GCS"),
    ("glasgow coma scale score", "GCS"),
    ("gcs eye", "GCS-E"),
    ("eye opening", "GCS-E"),
    ("gcs e", "GCS-E"),
    ("gcs verbal", "GCS-V"),
    ("verbal response", "GCS-V"),
    ("best verbal response", "GCS-V"),
    ("gcs v", "GCS-V"),
    ("gcs motor", "GCS-M"),
    ("motor response", "GCS-M"),
    ("best motor response", "GCS-M"),
    ("gcs m", "GCS-M"),
    ("rass", "RASS"),
    ("nihss", "NIHSS"),
    ("four score", "FOUR"),
    ("four", "FOUR"),
    ("cam-icu", "CAM-ICU"),
    ("cam icu", "CAM-ICU"),
    ("pupils", "Pupils"),
    ("pupil", "Pupils"),
    ("pupil size/reaction", "Pupils"),
    ("left pupil", "L pupil"),
    ("right pupil", "R pupil"),
    ("l pupil", "L pupil"),
    ("r pupil", "R pupil"),
];

static NEURO_DATE_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^[ \t]*(?:(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?|\d{1,2}:?\d{2})(?:[ \t]+\d{1,2}:?\d{2})?[ \t]*){2,}$"
    )
    .unwrap()
});
static NEURO_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?P<label>[A-Za-z][A-Za-z /\-]*?)[ \t]*:?[ \t]+(?P<rest>(?:-?\d|neg|pos|uta|unable)\S*(?:[ \t].*)?)$"
    )
    .unwrap()
});
static NEURO_PUPIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^(?P<size>\d(?:\.\d)?)[ \t]*(?:mm)?(?:[ \t]*/[ \t]*(?P<size2>\d(?:\.\d)?)[ \t]*(?:mm)?)?[ \t]*(?P<react>brisk|sluggish|reactive|non-?reactive|nr|fixed)?$"
    )
    .unwrap()
});
static NEURO_CAM_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"(?i)\b(neg(?:ative)?|pos(?:itive)?|uta|unable to assess)\b").unwrap());
static NEURO_NUMS_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"-?\d+(?:\.\d+)?").unwrap());
static NEURO_SPLIT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"\t+|[ \t]{2,}|[ \t]*[,;|][ \t]*").unwrap());
static NEURO_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^[ \t]*(?:neuro(?:logic(?:al)?)?[ \t]+(?:checks?|assessment|exam)|neuro)[ \t]*:?[ \t]*$").unwrap()
});

#[derive(Debug, Clone)]
enum NeuroValues {
    Strs(Vec<String>),
    Nums(Vec<f64>),
}

impl NeuroValues {
    fn len(&self) -> usize {
        match self {
            NeuroValues::Strs(v) => v.len(),
            NeuroValues::Nums(v) => v.len(),
        }
    }
}

fn parse_neuro_row(line: &str) -> Option<(&'static str, NeuroValues)> {
    let caps = NEURO_ROW_RE.captures(line)?;
    let label_key = caps
        .name("label")?
        .as_str()
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let &(_, short) = NEURO_LABELS.iter().find(|&&(k, _)| k == label_key)?;
    let rest = caps.name("rest")?.as_str();

    if matches!(short, "Pupils" | "L pupil" | "R pupil") {
        let cells: Vec<&str> = NEURO_SPLIT_RE
            .split(rest.trim())
            .filter(|s| !s.is_empty())
            .collect();
        if cells.len() < 2 {
            return None;
        }
        let mut vals = Vec::with_capacity(cells.len());
        for cell in cells {
            if !NEURO_PUPIL_RE.is_match(cell.trim()) {
                return None;
            }
            vals.push(cell.split_whitespace().collect::<Vec<_>>().join(" "));
        }
        return Some((short, NeuroValues::Strs(vals)));
    }

    if short == "CAM-ICU" {
        let found: Vec<String> = NEURO_CAM_RE
            .find_iter(rest)
            .map(|m| {
                let low = m.as_str().to_lowercase();
                let p = &low[..3.min(low.len())];
                if p == "uta" || p == "una" {
                    "UTA".to_string()
                } else {
                    p.to_string()
                }
            })
            .collect();
        let leftover = NEURO_CAM_RE.replace_all(rest, "");
        if found.len() < 2 || leftover.chars().any(|c| c.is_ascii_alphanumeric()) {
            return None;
        }
        return Some((short, NeuroValues::Strs(found)));
    }

    let found: Vec<f64> = NEURO_NUMS_RE
        .find_iter(rest)
        .filter_map(|m| m.as_str().parse::<f64>().ok())
        .collect();
    let leftover = NEURO_NUMS_RE.replace_all(rest, "");
    if found.len() < 2 || leftover.chars().any(|c| c.is_ascii_alphanumeric()) {
        return None;
    }
    Some((short, NeuroValues::Nums(found)))
}

fn span_f64(vals: &[f64]) -> String {
    let lo = vals.iter().copied().fold(f64::INFINITY, f64::min);
    let hi = vals.iter().copied().fold(f64::NEG_INFINITY, f64::max);
    if (lo - hi).abs() > 1e-9 {
        format!("{}–{}", fmt_num(lo), fmt_num(hi))
    } else {
        fmt_num(lo)
    }
}

fn neuro_block(lines: &[&str], i: usize) -> Option<(String, usize)> {
    let start = if NEURO_DATE_ROW_RE.is_match(lines[i]) {
        i + 1
    } else {
        i
    };
    let mut rows = Vec::new();
    let mut j = start;
    while j < lines.len() {
        if let Some(r) = parse_neuro_row(lines[j]) {
            rows.push(r);
            j += 1;
        } else {
            break;
        }
    }
    let readings = rows.iter().map(|(_, v)| v.len()).max().unwrap_or(0);
    if rows.is_empty() || (rows.len() < 2 && readings < 3) {
        return None;
    }
    let parts: Vec<String> = rows
        .into_iter()
        .map(|(short, vals)| match vals {
            NeuroValues::Strs(v) => {
                let mut seq = vec![v[0].clone()];
                for w in v.windows(2) {
                    if w[1] != w[0] {
                        seq.push(w[1].clone());
                    }
                }
                let ch = if seq.len() == 1 {
                    format!("{} (unchanged)", seq[0])
                } else {
                    format!("{} (last {})", seq.join(" → "), v.last().unwrap())
                };
                format!("{short} {ch}")
            }
            NeuroValues::Nums(v) => {
                format!(
                    "{short} {} (last {})",
                    span_f64(&v),
                    fmt_num(*v.last().unwrap())
                )
            }
        })
        .collect();
    Some((
        format!("Neuro checks ({readings} readings): {}", parts.join("; ")),
        j,
    ))
}

static EVD_OUTPUT_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*EVD[ \t]+(?:output|drainage|drain(?:ed)?|out)[ \t]*(?:\(mL\))?[ \t]*:?[ \t]+(?P<rest>[\d.\s,mLl]+)$"
    )
    .unwrap()
});
static EVD_OUTPUT_TIMED_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?\d{1,2}:?\d{2})[ \t]*:?[ \t]+EVD[ \t]+(?:output|drainage|drained|out)[ \t]*:?[ \t]*(?P<value>\d+(?:\.\d+)?)[ \t]*mL[ \t]*$"
    )
    .unwrap()
});
static EVD_LEVEL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*EVD[ \t]+(?:(?:level(?:ed)?|set|height)[ \t]*(?:at|to)?|at|@)[ \t]*:?[ \t]*(?P<level>\d+(?:\.\d+)?)[ \t]*cm[ \t]*H2O[ \t]*(?:above[ \t]+(?:the[ \t]+)?(?:tragus|EAM|foramen))?[ \t]*[,;]?[ \t]*(?P<status>open|clamped|closed)?[ \t]*\.?[ \t]*$"
    )
    .unwrap()
});
static EVD_STATUS_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)^[ \t]*EVD[ \t]+(?:is[ \t]+)?(?P<status>open|clamped|closed)[ \t]*\.?[ \t]*$")
        .unwrap()
});
static EVD_PRESSURE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?P<label>ICP|CPP)[ \t]*(?:\(mmHg\))?[ \t]*:?[ \t]+(?P<rest>[-\d.\s,]+)$"
    )
    .unwrap()
});

#[derive(Debug, Clone)]
enum EvdItem {
    OutputRow(Vec<f64>),
    OutputTimed(String, f64),
    Level(String, String),
    Status(String),
    Pressure(String, Vec<f64>),
}

fn parse_evd_line(line: &str) -> Option<EvdItem> {
    if let Some(c) = EVD_OUTPUT_ROW_RE.captures(line) {
        let rest = c.name("rest")?.as_str();
        let vals: Vec<f64> = NEURO_NUMS_RE
            .find_iter(rest)
            .filter_map(|m| m.as_str().parse().ok())
            .collect();
        let no_ml = Regex::new(r"(?i)\bml\b").unwrap().replace_all(rest, "");
        if vals.len() >= 2 && !no_ml.chars().any(|ch| ch.is_ascii_alphabetic()) {
            return Some(EvdItem::OutputRow(vals));
        }
        return None;
    }
    if let Some(c) = EVD_OUTPUT_TIMED_RE.captures(line) {
        let stamp = c.name("stamp")?.as_str().trim().to_string();
        let val: f64 = c.name("value")?.as_str().parse().ok()?;
        return Some(EvdItem::OutputTimed(stamp, val));
    }
    if let Some(c) = EVD_LEVEL_RE.captures(line) {
        let lvl = c.name("level")?.as_str().to_string();
        let st = c
            .name("status")
            .map(|m| m.as_str().to_lowercase())
            .unwrap_or_default();
        return Some(EvdItem::Level(lvl, st));
    }
    if let Some(c) = EVD_STATUS_RE.captures(line) {
        return Some(EvdItem::Status(c.name("status")?.as_str().to_lowercase()));
    }
    if let Some(c) = EVD_PRESSURE_RE.captures(line) {
        let label = c.name("label")?.as_str().to_uppercase();
        let vals: Vec<f64> = NEURO_NUMS_RE
            .find_iter(c.name("rest")?.as_str())
            .filter_map(|m| m.as_str().parse().ok())
            .collect();
        if !vals.is_empty() {
            return Some(EvdItem::Pressure(label, vals));
        }
    }
    None
}

fn evd_block(lines: &[&str], i: usize, threshold: f64) -> Option<(String, usize)> {
    let mut items = Vec::new();
    let mut j = i;
    while j < lines.len() {
        if let Some(it) = parse_evd_line(lines[j]) {
            items.push(it);
            j += 1;
        } else {
            break;
        }
    }
    let has_row_out = items.iter().any(|x| matches!(x, EvdItem::OutputRow(_)));
    let timed_count = items
        .iter()
        .filter(|x| matches!(x, EvdItem::OutputTimed(_, _)))
        .count();
    let has_output = has_row_out || timed_count >= 2;
    if items.is_empty() || !has_output || (items.len() < 2 && !has_row_out) {
        return None;
    }

    let level = items.iter().find_map(|x| match x {
        EvdItem::Level(l, s) => Some((l.as_str(), s.as_str())),
        _ => None,
    });
    let status = items
        .iter()
        .find_map(|x| match x {
            EvdItem::Status(s) => Some(s.as_str()),
            _ => None,
        })
        .or_else(|| level.map(|(_, s)| s))
        .unwrap_or("");

    let mut outputs = Vec::new();
    let mut stamps = Vec::new();
    for it in &items {
        match it {
            EvdItem::OutputRow(v) => outputs.extend(v.iter().copied()),
            EvdItem::OutputTimed(st, v) => {
                stamps.push(st.as_str());
                outputs.push(*v);
            }
            _ => {}
        }
    }

    let mut parts = Vec::new();
    let mut head_items = Vec::new();
    if let Some((lvl, _)) = level {
        head_items.push(format!("{lvl} cm H2O"));
    }
    if !status.is_empty() {
        head_items.push(status.to_string());
    }
    if !head_items.is_empty() {
        parts.push(head_items.join(", "));
    }

    let window = if stamps.len() >= 2 {
        format!(" {}–{}", stamps[0], stamps.last().unwrap())
    } else {
        String::new()
    };
    let sum_out: f64 = outputs.iter().sum();
    let list_str = outputs.iter().map(|&x| fmt_num(x)).collect::<Vec<_>>().join(", ");
    parts.push(format!(
        "output {} mL{window} over {} readings ({list_str})",
        fmt_num(sum_out),
        outputs.len()
    ));

    for label in ["ICP", "CPP"] {
        let mut vals = Vec::new();
        for it in &items {
            if let EvdItem::Pressure(l, v) = it {
                if l == label {
                    vals.extend(v.iter().copied());
                }
            }
        }
        if vals.is_empty() {
            continue;
        }
        let mut t = format!(
            "{label} {} (last {}",
            span_f64(&vals),
            fmt_num(*vals.last().unwrap())
        );
        if label == "ICP" {
            let high = vals.iter().filter(|&&v| v > threshold).count();
            if high > 0 {
                let s = if high > 1 { "s" } else { "" };
                t.push_str(&format!("; {high} reading{s} >{}", fmt_num(threshold)));
            }
        }
        t.push(')');
        parts.push(t);
    }

    Some((format!("EVD: {}", parts.join("; ")), j))
}

static NA_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:serial[ \t]+)?(?:sodium|na)[ \t]+(?:checks?|levels?|q\d+h?)[ \t]*:?[ \t]*$|^[ \t]*(?:q\d+h?[ \t]+)?(?:sodium|na)[ \t]+checks?[ \t]*:?[ \t]*$"
    )
    .unwrap()
});
static NA_LINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?\d{1,2}:?\d{2})[ \t]*:?[ \t]+)?(?:(?:serum[ \t]+)?(?:sodium|na)[ \t]*:?[ \t]*)?(?P<value>1[0-8]\d)[ \t]*(?:mmol/L|mEq/L)?[ \t]*(?:\(?[HL]\)?|\*)?[ \t]*(?:(?:at|@)[ \t]*(?P<stamp2>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?\d{1,2}:?\d{2}))?[ \t]*$"
    )
    .unwrap()
});
static NA_WORD_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"(?i)sodium|na").unwrap());

fn sodium_block(lines: &[&str], i: usize) -> Option<(String, usize)> {
    let header = NA_HEADER_RE.is_match(lines[i]);
    let mut j = if header { i + 1 } else { i };
    let mut values: Vec<i64> = Vec::new();
    let mut stamps: Vec<String> = Vec::new();

    while j < lines.len() {
        let Some(caps) = NA_LINE_RE.captures(lines[j]) else {
            break;
        };
        let named = NA_WORD_RE.is_match(lines[j]);
        let stamp = caps.name("stamp").or_else(|| caps.name("stamp2"));
        if (!header && !named) || (stamp.is_none() && !named) {
            break;
        }
        values.push(caps.name("value")?.as_str().parse().ok()?);
        if let Some(st) = stamp {
            stamps.push(st.as_str().trim().to_string());
        }
        j += 1;
    }

    if values.len() < 3 {
        return None;
    }
    let window = if stamps.len() == values.len() {
        format!(" {}–{}", stamps[0], stamps.last().unwrap())
    } else {
        String::new()
    };
    let delta = values.last().unwrap() - values[0];
    let seq = values
        .iter()
        .map(|v| v.to_string())
        .collect::<Vec<_>>()
        .join(" → ");
    Some((
        format!("Na checks{window}: {seq} ({} checks, {delta:+})", values.len()),
        j,
    ))
}

static DRIP_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?:(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?\d{1,2}:?\d{2})[ \t]*:?[ \t]+)?(?P<drug>[A-Za-z][A-Za-z\-]{3,})(?:[ \t]+(?:drip|infusion|gtt))?[ \t]*(?:\([^)]*\))?[ \t]*[:,\-]?[ \t]*(?P<pre>rate[ \t]*(?:/[ \t]*dose[ \t]*)?(?:change|verify|verified|changed)|new[ \t]+bag|restarted|started|stopped|paused|held|titrated(?:[ \t]+(?:up|down))?|increased|decreased|off|continued)?[ \t]*(?:to[ \t]*)?(?P<rate>\d+(?:\.\d+)?)[ \t]*(?P<unit>mcg/kg/min|mcg/kg/hr?|mg/kg/hr?|mcg/min|mcg/hr?|mg/hr?|mg/min|units/kg/hr?|units/hr?|units/min|mL/hr?)[ \t]*[,;\-]?[ \t]*(?P<post>rate[ \t]*(?:/[ \t]*dose[ \t]*)?(?:change|verify|verified|changed)|new[ \t]+bag|restarted|started|stopped|paused|held|titrated(?:[ \t]+(?:up|down))?|increased|decreased|off|continued)?[ \t]*\.?[ \t]*$"
    )
    .unwrap()
});

fn drip_block(lines: &[&str], i: usize) -> Option<(String, usize)> {
    let mut rows: Vec<(String, String, f64, Option<String>, String)> = Vec::new();
    let mut j = i;
    while j < lines.len() {
        let Some(caps) = DRIP_RE.captures(lines[j]) else {
            break;
        };
        let drug = caps.name("drug")?.as_str();
        let unit = caps.name("unit")?.as_str();
        if let Some((first_drug, first_unit, _, _, _)) = rows.first() {
            if !drug.eq_ignore_ascii_case(first_drug) || !unit.eq_ignore_ascii_case(first_unit) {
                break;
            }
        }
        let rate: f64 = caps.name("rate")?.as_str().parse().ok()?;
        let stamp = caps.name("stamp").map(|m| m.as_str().trim().to_string());
        let action = format!(
            "{} {}",
            caps.name("pre").map(|m| m.as_str()).unwrap_or(""),
            caps.name("post").map(|m| m.as_str()).unwrap_or("")
        )
        .to_lowercase();
        rows.push((drug.to_string(), unit.to_string(), rate, stamp, action));
        j += 1;
    }
    if rows.len() < 3 {
        return None;
    }
    let rates: Vec<f64> = rows.iter().map(|r| r.2).collect();
    let unit = &rows[0].1;
    let raw_drug = &rows[0].0;
    let mut d_chars = raw_drug.chars();
    let drug = match d_chars.next() {
        Some(c) => format!("{}{}", c.to_uppercase(), d_chars.as_str().to_lowercase()),
        None => raw_drug.clone(),
    };
    let last_action = &rows.last().unwrap().4;
    let stopped = ["stopped", "off", "paused", "held"]
        .iter()
        .any(|w| last_action.contains(w));
    let stamps: Vec<&str> = rows
        .iter()
        .filter_map(|r| r.3.as_deref())
        .collect();
    let window = if stamps.len() == rows.len() {
        format!(" {}–{}", stamps[0], stamps.last().unwrap())
    } else {
        String::new()
    };
    let tail = if stopped { "; stopped)" } else { ")" };
    Some((
        format!(
            "{drug} drip{window}: {} {unit} over {} entries (start {}, last {} {unit}{tail}",
            span_f64(&rates),
            rows.len(),
            fmt_num(rates[0]),
            fmt_num(*rates.last().unwrap())
        ),
        j,
    ))
}

pub fn run_neuro_summary(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "neuro_summary", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let threshold = cfg
        .get("neuro_summary")
        .and_then(|c| c.get("icp_threshold"))
        .and_then(|v| v.as_f64())
        .unwrap_or(20.0);
    let do_checks = get_bool_opt(cfg, "neuro_summary", "neuro_checks", true);
    let do_evd = get_bool_opt(cfg, "neuro_summary", "evd", true);
    let do_na = get_bool_opt(cfg, "neuro_summary", "sodium", true);
    let do_drips = get_bool_opt(cfg, "neuro_summary", "drips", true);

    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut counts: std::collections::BTreeMap<&str, usize> = std::collections::BTreeMap::new();
    let mut i = 0usize;

    while i < lines.len() {
        let mut matched: Option<(&str, String, usize)> = None;
        if do_checks {
            if let Some((s, next_i)) = neuro_block(&lines, i) {
                matched = Some(("neuro_checks", s, next_i));
            }
        }
        if matched.is_none() && do_evd {
            if let Some((s, next_i)) = evd_block(&lines, i, threshold) {
                matched = Some(("evd", s, next_i));
            }
        }
        if matched.is_none() && do_na {
            if let Some((s, next_i)) = sodium_block(&lines, i) {
                matched = Some(("sodium", s, next_i));
            }
        }
        if matched.is_none() && do_drips {
            if let Some((s, next_i)) = drip_block(&lines, i) {
                matched = Some(("drips", s, next_i));
            }
        }

        if let Some((name, summary, next_i)) = matched {
            let indent_len = lines[i].len() - lines[i].trim_start_matches([' ', '\t']).len();
            let indent = &lines[i][..indent_len];
            if name == "neuro_checks"
                && out.last().is_some_and(|last| NEURO_HEADER_RE.is_match(last))
            {
                out.pop();
            }
            out.push(format!("{indent}{summary}"));
            *counts.entry(name).or_insert(0) += 1;
            i = next_i;
        } else {
            out.push(lines[i].to_string());
            i += 1;
        }
    }

    let total: usize = counts.values().sum();
    if total == 0 {
        return (text.to_string(), 0, json!({}));
    }
    (
        out.join("\n"),
        total,
        serde_json::to_value(counts).unwrap_or_default(),
    )
}

// ===========================================================================
// 7. mar_summary
// ===========================================================================

static MAR_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:MAR|Medication[ \t]+Administration[ \t]+Record|(?:PRN[ \t]+)?Medications?[ \t]+(?:Given|Administered)(?:[ \t]*\([^)]*\))?)[ \t]*:?[ \t]*$"
    )
    .unwrap()
});
static MAR_STAMP_FIRST_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))[ \t]*(?:[-–—:][ \t]*|[ \t]+)(?P<drug>[A-Za-z][A-Za-z0-9 /\-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>mcg|mg|g|units?|unit|mEq|mmol|mL|ml)\b(?:[ \t]+(?P<route>IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical)\b)?(?P<tail>[ \tA-Za-z0-9(),/\-]*?)[ \t]*$"
    )
    .unwrap()
});
static MAR_DRUG_FIRST_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?P<drug>[A-Za-z][A-Za-z0-9 /\-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>mcg|mg|g|units?|unit|mEq|mmol|mL|ml)\b(?:[ \t]+(?P<route>IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical)\b)?(?P<mid>[ \tA-Za-z(),/\-]*?)[ \t]+(?:(?:at|@)[ \t]*)?(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))(?P<tail>[ \tA-Za-z(),/\-]*?)[ \t]*$"
    )
    .unwrap()
});
static MAR_INLINE_MULTI_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?P<drug>[A-Za-z][A-Za-z0-9 /\-]*?)[ \t]*(?:\([^)]*\)[ \t]*)?(?:(?P<base_dose>\d+(?:\.\d+)?)[ \t]*(?P<base_unit>mcg|mg|g|units?|unit|mEq|mmol|mL|ml)\b[ \t]*)?(?:(?P<route>IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical)\b[ \t]*)?(?:(?P<prn>PRN)[ \t]*)?:[ \t]+(?P<list>.+)$"
    )
    .unwrap()
});
static MAR_INLINE_ITEM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)(?P<stamp>(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))[ \t]*\([ \t]*(?P<dose>\d+(?:\.\d+)?)[ \t]*(?P<unit>mcg|mg|g|units?|unit|mEq|mmol|mL|ml)(?:[ \t]+(?P<status>Given|Administered|Completed|Done|New[ \t]+Bag|Bag[ \t]+Change|Bolus[ \t]+Given|Held|MAR[ \t]+Hold|Refused|Not[ \t]+Given|Missed|Deferred|Omitted))?[ \t]*\)"
    )
    .unwrap()
});
static MAR_ALLOWED_TAIL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(?:IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical|PRN|as[ \t]+needed|Push|Bolus|breakthrough|one-time|once|Given|Administered|Completed|Done|New[ \t]+Bag|Bag[ \t]+Change|Bolus[ \t]+Given|Held|MAR[ \t]+Hold|Refused|Not[ \t]+Given|Missed|Deferred|Omitted|scheduled|dose|pain|agitation|bp|sbp|hr|fever|nausea|anxiety|shivering|icp|seizure|for|q\d+h|daily|bid|tid|qid)\b|[(),/:\-\s]"
    )
    .unwrap()
});
static MAR_HELD_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:Held|MAR[ \t]+Hold|Refused|Not[ \t]+Given|Missed|Deferred|Omitted)\b")
        .unwrap()
});
static MAR_PRN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\b(?:PRN|as[ \t]+needed|Push|Bolus|IVP|breakthrough|one-time|once)\b").unwrap()
});
static MAR_ROUTE_EXTRACT_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)\b(IV[ \t]+Push|IV[ \t]+Bolus|IVP|IVPB|IV|PO|by[ \t]+mouth|IM|subcut(?:aneous(?:ly)?)?|SQ|SC|NG|GT|PEG|PR|SL|inhaled|INH|neb(?:ulized)?|topical)\b"
    )
    .unwrap()
});
static MAR_DRUG_SUFFIX_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"(?i)\s+(?:injection|tablet|capsule|solution|infusion|oral|iv)$").unwrap()
});
static MAR_CANDIDATE_LINE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[ \t]*(?:[-•*][ \t]*)?(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?[ \t]+)?(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d)[ \t]+[^\n]*\b(?:mcg|mg|g|units?|unit|mEq|mmol|mL|ml)\b"
    )
    .unwrap()
});

#[derive(Debug, Clone)]
struct MarAdmin {
    raw: String,
    drug: String,
    drug_key: String,
    dose: f64,
    unit: String,
    route: String,
    stamp: String,
    held: bool,
    prn: bool,
}

fn mar_norm_unit(u: &str) -> String {
    match u.to_lowercase().as_str() {
        "unit" | "units" => "units".to_string(),
        "ml" => "mL".to_string(),
        "meq" => "mEq".to_string(),
        _ => u.to_string(),
    }
}

fn mar_norm_route(r: Option<&str>, extra: &str) -> (String, bool) {
    let blob = format!("{} {extra}", r.unwrap_or("")).trim().to_string();
    let is_prn = MAR_PRN_RE.is_match(&blob);
    let r_str = match r {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => MAR_ROUTE_EXTRACT_RE
            .captures(extra)
            .and_then(|c| c.get(1))
            .map(|m| m.as_str().to_string())
            .unwrap_or_default(),
    };
    let r_low = r_str.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
    match r_low.as_str() {
        "iv push" | "iv bolus" | "ivp" | "ivpb" | "iv" => (
            "IV".to_string(),
            is_prn || matches!(r_low.as_str(), "iv push" | "iv bolus" | "ivp"),
        ),
        "po" | "by mouth" => ("PO".to_string(), is_prn),
        "subcut" | "subcutaneous" | "subcutaneously" | "sq" | "sc" => {
            ("subcut".to_string(), is_prn)
        }
        "inhaled" | "inh" | "neb" | "nebulized" => ("INH".to_string(), is_prn),
        _ => {
            let norm = if !r_str.is_empty() && r_str.len() <= 4 {
                r_str.to_uppercase()
            } else {
                r_str.to_lowercase()
            };
            (norm, is_prn)
        }
    }
}

fn mar_clean_drug(raw_drug: &str) -> (String, String) {
    let trimmed = raw_drug
        .trim_matches(|c: char| matches!(c, ' ' | '-' | '–' | '—' | ':' | ','))
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ");
    let cleaned = MAR_DRUG_SUFFIX_RE.replace(&trimmed, "").trim().to_string();
    if cleaned.is_empty() {
        return (String::new(), String::new());
    }
    let display = if cleaned.chars().all(|c| c.is_alphabetic()) {
        let mut chars = cleaned.chars();
        match chars.next() {
            Some(f) => format!("{}{}", f.to_uppercase(), chars.as_str().to_lowercase()),
            None => cleaned.clone(),
        }
    } else {
        cleaned.clone()
    };
    (display, cleaned.to_lowercase())
}

fn parse_mar_line(line: &str) -> Option<Vec<MarAdmin>> {
    for rx in [&*MAR_STAMP_FIRST_RE, &*MAR_DRUG_FIRST_RE] {
        if let Some(caps) = rx.captures(line) {
            let (display, drug_key) = mar_clean_drug(caps.name("drug")?.as_str());
            if drug_key.is_empty()
                || matches!(
                    drug_key.as_str(),
                    "bp" | "hr" | "temp" | "rr" | "spo2" | "icp" | "cpp" | "evd" | "na" | "gcs"
                )
            {
                return None;
            }
            let mid = caps.name("mid").map(|m| m.as_str()).unwrap_or("");
            let tail = caps.name("tail").map(|m| m.as_str()).unwrap_or("");
            let extra = format!("{mid} {tail}");
            let leftover = MAR_ALLOWED_TAIL_RE.replace_all(&extra, "");
            if leftover.chars().any(|c| c.is_ascii_alphanumeric()) {
                return None;
            }
            let held = MAR_HELD_RE.is_match(&extra);
            let (route, is_prn) = mar_norm_route(caps.name("route").map(|m| m.as_str()), &extra);
            let unit = mar_norm_unit(caps.name("unit")?.as_str());
            let dose: f64 = caps.name("dose")?.as_str().parse().ok()?;
            let stamp = caps
                .name("stamp")?
                .as_str()
                .split_whitespace()
                .collect::<Vec<_>>()
                .join(" ");
            return Some(vec![MarAdmin {
                raw: line.to_string(),
                drug: display,
                drug_key,
                dose,
                unit,
                route,
                stamp,
                held,
                prn: is_prn,
            }]);
        }
    }

    if let Some(caps) = MAR_INLINE_MULTI_RE.captures(line) {
        let (display, drug_key) = mar_clean_drug(caps.name("drug")?.as_str());
        if drug_key.is_empty() {
            return None;
        }
        let rest = caps.name("list")?.as_str();
        let items: Vec<_> = MAR_INLINE_ITEM_RE.captures_iter(rest).collect();
        if items.len() < 2 {
            return None;
        }
        let leftover = MAR_INLINE_ITEM_RE.replace_all(rest, "");
        if leftover.chars().any(|c| c.is_ascii_alphanumeric()) {
            return None;
        }
        let (route, _) = mar_norm_route(
            caps.name("route").map(|m| m.as_str()),
            caps.name("prn").map(|m| m.as_str()).unwrap_or("PRN"),
        );
        let mut out = Vec::with_capacity(items.len());
        for it in items {
            let held = it
                .name("status")
                .is_some_and(|st| MAR_HELD_RE.is_match(st.as_str()));
            out.push(MarAdmin {
                raw: line.to_string(),
                drug: display.clone(),
                drug_key: drug_key.clone(),
                dose: it.name("dose")?.as_str().parse().ok()?,
                unit: mar_norm_unit(it.name("unit")?.as_str()),
                route: route.clone(),
                stamp: it
                    .name("stamp")?
                    .as_str()
                    .split_whitespace()
                    .collect::<Vec<_>>()
                    .join(" "),
                held,
                prn: true,
            });
        }
        return Some(out);
    }

    None
}

fn summarize_mar_group(entries: &[MarAdmin]) -> String {
    let first = &entries[0];
    let given: Vec<&MarAdmin> = entries.iter().filter(|e| !e.held).collect();
    let held_count = entries.iter().filter(|e| e.held).count();
    let route_part = if first.route.is_empty() {
        String::new()
    } else {
        format!(" {}", first.route)
    };
    let head = format!("{}{route_part}", first.drug);
    let unit = &first.unit;

    let stamps: Vec<&str> = if !given.is_empty() {
        given
            .iter()
            .filter(|e| !e.stamp.is_empty())
            .map(|e| e.stamp.as_str())
            .collect()
    } else {
        entries
            .iter()
            .filter(|e| !e.stamp.is_empty())
            .map(|e| e.stamp.as_str())
            .collect()
    };
    let window = if stamps.len() >= 2 && stamps.first() != stamps.last() {
        format!("{}–{}", stamps[0], stamps.last().unwrap())
    } else {
        stamps.first().copied().unwrap_or("").to_string()
    };

    if given.is_empty() {
        let held_str = if !window.is_empty() {
            format!("{held_count} held ({window})")
        } else {
            format!("{held_count} held")
        };
        return format!("{head}: 0 {unit} given ({held_str})");
    }

    let doses: Vec<f64> = given.iter().map(|e| e.dose).collect();
    let total: f64 = doses.iter().sum();
    let n = doses.len();
    let all_same = doses.windows(2).all(|w| (w[0] - w[1]).abs() < 1e-9);
    let breakdown = if all_same {
        if n > 1 {
            format!("{n} × {} {unit}", fmt_num(doses[0]))
        } else {
            format!("{} {unit}", fmt_num(doses[0]))
        }
    } else {
        format!(
            "{} {unit}",
            doses.iter().map(|&d| fmt_num(d)).collect::<Vec<_>>().join(", ")
        )
    };

    let last = *given.last().unwrap();
    let last_part = if !last.stamp.is_empty() {
        format!("last {} {unit} at {}", fmt_num(last.dose), last.stamp)
    } else {
        format!("last {} {unit}", fmt_num(last.dose))
    };

    let mut meta_parts = vec![breakdown];
    if !window.is_empty() {
        meta_parts.push(window);
    }
    meta_parts.push(last_part);
    if held_count > 0 {
        meta_parts.push(format!("{held_count} held"));
    }
    let dose_word = if n == 1 { "dose" } else { "doses" };
    format!(
        "{head}: {} {unit} total over {n} {dose_word} ({})",
        fmt_num(total),
        meta_parts.join("; ")
    )
}

pub fn run_mar_summary(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "mar_summary", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let min_doses = get_int_opt(cfg, "mar_summary", "min_doses", 2).max(2) as usize;
    let include_scheduled = get_bool_opt(cfg, "mar_summary", "include_scheduled", true);

    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut blocks = 0usize;
    let mut doses_condensed = 0usize;
    let mut i = 0usize;

    while i < lines.len() {
        let has_header = MAR_HEADER_RE.is_match(lines[i]);
        let start = if has_header { i + 1 } else { i };
        let mut j = start;
        let mut parsed_rows: Vec<Vec<MarAdmin>> = Vec::new();
        let mut bad = false;

        while j < lines.len() {
            if let Some(adm) = parse_mar_line(lines[j]) {
                parsed_rows.push(adm);
                j += 1;
            } else if MAR_CANDIDATE_LINE_RE.is_match(lines[j]) {
                bad = true;
                break;
            } else {
                break;
            }
        }

        let flat: Vec<&MarAdmin> = parsed_rows.iter().flatten().collect();
        if !bad && !flat.is_empty() {
            let mut by_key: AHashMap<(String, String, String), Vec<MarAdmin>> = AHashMap::new();
            for a in flat {
                if include_scheduled || a.prn {
                    by_key
                        .entry((a.drug_key.clone(), a.route.clone(), a.unit.clone()))
                        .or_default()
                        .push(a.clone());
                }
            }
            let condensable: AHashSet<(String, String, String)> = by_key
                .iter()
                .filter(|(_, grp)| grp.len() >= min_doses)
                .map(|(k, _)| k.clone())
                .collect();

            if !condensable.is_empty() {
                if has_header {
                    out.push(lines[i].to_string());
                }
                let mut emitted: AHashSet<(String, String, String)> = AHashSet::new();
                for row in &parsed_rows {
                    let first = &row[0];
                    let key = (first.drug_key.clone(), first.route.clone(), first.unit.clone());
                    if (include_scheduled || first.prn) && condensable.contains(&key) {
                        if emitted.insert(key.clone()) {
                            let grp = &by_key[&key];
                            out.push(summarize_mar_group(grp));
                            doses_condensed += grp.len();
                        }
                    } else {
                        out.push(first.raw.clone());
                    }
                }
                blocks += 1;
                i = j;
                continue;
            }
        }

        out.push(lines[i].to_string());
        i += 1;
    }

    (
        out.join("\n"),
        blocks,
        json!({ "blocks": blocks, "doses_condensed": doses_condensed }),
    )
}

// ===========================================================================
// 8. flowsheet_tables
// ===========================================================================

static FS_STAMP_TOKEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))?|(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d)"
    )
    .unwrap()
});
static FS_STAMP_FULL_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))?|(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d)$"
    )
    .unwrap()
});
static FS_CELL_NUM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^[<>+\-]?\d+(?:\.\d+)?(?:/\d+(?:\.\d+)?)?(?:[ \t]*(?:%|°[CF]|cmH2O|mmHg|mL/kg|mL/hr|mL|L/min|L|bpm|mcg/kg/min|mcg/min|mg/hr|units/hr|mEq/L|mmol/L|g/dL))?(?:[ \t]*(?:\(?[HL*]+\)?))?$"
    )
    .unwrap()
});
static FS_CELL_TOKEN_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^(?:AC/VC|AC/PC|VC-AC|PC-AC|SIMV|PSV|CPAP|PRVC|APRV|BiPAP|BIPAP|HFNC|NC|RA|Room[ \t]+Air|Ventimask|NRB|Trach|ETT|Open|Clamped|Clear|Intact|Absent|Present|Pos(?:itive)?|Neg(?:ative)?|Yes|No|[+\-]\d)$"
    )
    .unwrap()
});
static FS_PURE_NUM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?i)^(?P<num>[+\-]?\d+(?:\.\d+)?)(?P<unit>[ \t]*(?:%|°[CF]|cmH2O|mmHg|mL|L/min|L|bpm)?)$"
    )
    .unwrap()
});
static FS_COL_SPLIT_RE: Lazy<Regex> = Lazy::new(|| Regex::new(r"\t+|[ \t]{2,}").unwrap());
static FS_PARAM_NAME_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^[A-Za-z][A-Za-z0-9 /()_%+\-]*$").unwrap());
static FS_COLON_ROW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^[ \t]*(?P<name>[A-Za-z][A-Za-z0-9 /()_\-]*?)[ \t]*:[ \t]+(?P<rest>\S.*)$")
        .unwrap()
});
static FS_CANDIDATE_PARAM_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"^[ \t]*[A-Za-z][A-Za-z0-9 /()_%+\-]{0,31}:?(?:\t|[ \t]{2,})\S").unwrap()
});
static FS_CANDIDATE_STAMP_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"^[ \t]*(?:\d{1,2}/\d{1,2}(?:/\d{2,4})?(?:[ \t]*\((?:HD|POD)#[^)]*\))?(?:[ \t]+(?:(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d))?|(?:[01]?\d|2[0-3]):[0-5]\d|(?:[01]\d|2[0-3])[0-5]\d)(?:\t|[ \t]+)\S"
    )
    .unwrap()
});

fn fs_is_placeholder(c: &str) -> bool {
    matches!(
        c.trim().to_lowercase().as_str(),
        "-" | "--" | "---" | "n/a" | "na" | "none" | ""
    )
}

fn fs_is_generic_corner(c: &str) -> bool {
    matches!(
        c.trim().to_lowercase().as_str(),
        ""
            | "time"
            | "date"
            | "date/time"
            | "datetime"
            | "parameter"
            | "parameters"
            | "component"
            | "flowsheet"
            | "value"
            | "values"
    )
}

fn fs_is_valid_cell(cell: &str) -> bool {
    let c = cell.trim();
    if fs_is_placeholder(c) {
        return true;
    }
    if c.len() > 24 {
        return false;
    }
    FS_CELL_NUM_RE.is_match(c) || FS_CELL_TOKEN_RE.is_match(c)
}

fn fs_is_valid_param_name(name: &str) -> bool {
    let s = name.trim_matches(|c| c == ' ' || c == ':');
    if s.is_empty() || s.len() > 32 || FS_STAMP_FULL_RE.is_match(s) {
        return false;
    }
    FS_PARAM_NAME_RE.is_match(s)
}

fn fs_parse_time_across_header(line: &str) -> Option<(String, Vec<String>)> {
    let parts: Vec<String> = FS_COL_SPLIT_RE
        .split(line.trim())
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() >= 2 && parts.iter().all(|p| FS_STAMP_FULL_RE.is_match(p)) {
        return Some((String::new(), parts));
    }
    if parts.len() >= 3
        && fs_is_valid_param_name(&parts[0])
        && parts[1..].iter().all(|p| FS_STAMP_FULL_RE.is_match(p))
    {
        let corner = parts[0].trim_matches(|c| c == ' ' || c == ':');
        let title = if fs_is_generic_corner(corner) {
            String::new()
        } else {
            corner.to_string()
        };
        return Some((title, parts[1..].to_vec()));
    }
    let tokens: Vec<_> = FS_STAMP_TOKEN_RE.find_iter(line).collect();
    if tokens.len() >= 2 {
        let prefix = line[..tokens[0].start()].trim_matches(|c| matches!(c, ' ' | ':' | '\t'));
        let rest_after = &line[tokens[0].start()..];
        let leftover = FS_STAMP_TOKEN_RE.replace_all(rest_after, "");
        if leftover.trim().is_empty() {
            let stamps: Vec<String> = tokens.iter().map(|m| m.as_str().trim().to_string()).collect();
            if prefix.is_empty() {
                return Some((String::new(), stamps));
            }
            if fs_is_valid_param_name(prefix) {
                let title = if fs_is_generic_corner(prefix) {
                    String::new()
                } else {
                    prefix.to_string()
                };
                return Some((title, stamps));
            }
        }
    }
    None
}

fn fs_parse_param_row(line: &str, ncols: usize) -> Option<(String, Vec<String>)> {
    let mut parts: Vec<String> = FS_COL_SPLIT_RE
        .split(line.trim())
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() != ncols + 1 {
        let caps = FS_COLON_ROW_RE.captures(line)?;
        let name = caps.name("name")?.as_str().trim().to_string();
        let cells: Vec<String> = caps
            .name("rest")?
            .as_str()
            .split_whitespace()
            .map(|c| c.trim().to_string())
            .filter(|c| !c.is_empty())
            .collect();
        if cells.len() != ncols {
            return None;
        }
        parts = std::iter::once(name).chain(cells).collect();
    }
    let param = parts[0].trim_matches(|c| c == ' ' || c == ':').to_string();
    let cells = parts[1..].to_vec();
    if !fs_is_valid_param_name(&param)
        || !cells.iter().all(|c| fs_is_valid_cell(c))
        || cells.iter().all(|c| fs_is_placeholder(c))
    {
        return None;
    }
    Some((param, cells))
}

fn fs_parse_time_down_header(line: &str) -> Option<(String, Vec<String>)> {
    let parts: Vec<String> = FS_COL_SPLIT_RE
        .split(line.trim())
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() < 3 {
        return None;
    }
    let corner = parts[0].trim_matches(|c| c == ' ' || c == ':').to_lowercase();
    if !matches!(corner.as_str(), "time" | "date" | "date/time" | "datetime") {
        return None;
    }
    let params: Vec<String> = parts[1..]
        .iter()
        .map(|p| p.trim_matches(|c| c == ' ' || c == ':').to_string())
        .collect();
    if !params.iter().all(|p| fs_is_valid_param_name(p)) {
        return None;
    }
    Some((String::new(), params))
}

fn fs_parse_time_down_row(line: &str, ncols: usize) -> Option<(String, Vec<String>)> {
    let mut parts: Vec<String> = FS_COL_SPLIT_RE
        .split(line.trim())
        .map(|p| p.trim().to_string())
        .filter(|p| !p.is_empty())
        .collect();
    if parts.len() != ncols + 1 {
        let trimmed = line.trim_start();
        let m = FS_STAMP_TOKEN_RE.find(trimmed)?;
        if m.start() != 0 {
            return None;
        }
        let stamp = m.as_str().trim().to_string();
        let rest = trimmed[m.end()..].trim();
        let cells: Vec<String> = rest.split_whitespace().map(|s| s.to_string()).collect();
        if cells.len() != ncols {
            return None;
        }
        parts = std::iter::once(stamp).chain(cells).collect();
    }
    let stamp = parts[0].clone();
    let cells = parts[1..].to_vec();
    if !FS_STAMP_FULL_RE.is_match(&stamp) || !cells.iter().all(|c| fs_is_valid_cell(c)) {
        return None;
    }
    Some((stamp, cells))
}

fn fs_format_param(
    param: &str,
    raw_cells: &[String],
    style: &str,
    collapse_unchanged: bool,
) -> String {
    let vals: Vec<&str> = raw_cells
        .iter()
        .map(|c| c.trim())
        .filter(|c| !fs_is_placeholder(c))
        .collect();
    if vals.is_empty() {
        return format!("{param} --");
    }
    if vals.len() == 1 {
        return format!("{param} {}", vals[0]);
    }
    let unique_low: AHashSet<String> = vals.iter().map(|v| v.to_lowercase()).collect();
    if collapse_unchanged && unique_low.len() == 1 {
        return format!("{param} {} (unchanged)", vals[0]);
    }
    if style == "range" && vals.len() >= 3 {
        let mut fvals = Vec::with_capacity(vals.len());
        let mut units: AHashSet<String> = AHashSet::new();
        let mut all_num = true;
        for &v in &vals {
            if let Some(caps) = FS_PURE_NUM_RE.captures(v) {
                if let Ok(fv) = caps.name("num").unwrap().as_str().parse::<f64>() {
                    fvals.push(fv);
                    units.insert(caps.name("unit").unwrap().as_str().trim().to_string());
                    continue;
                }
            }
            all_num = false;
            break;
        }
        if all_num && units.len() <= 1 {
            let unit = units.into_iter().next().unwrap_or_default();
            return format!(
                "{param} {}{unit} (last {})",
                span_f64(&fvals),
                vals.last().unwrap()
            );
        }
    }
    format!("{param} {} (last {})", vals.join(" → "), vals.last().unwrap())
}

fn fs_format_grid(
    title: &str,
    stamps: &[String],
    rows: &[(String, Vec<String>)],
    style: &str,
    collapse_unchanged: bool,
) -> String {
    let label = if title.is_empty() { "Flowsheet" } else { title };
    let span = if stamps.len() >= 2 && stamps.first() != stamps.last() {
        format!("{} → {}", stamps[0], stamps.last().unwrap())
    } else {
        stamps[0].clone()
    };
    let series = rows
        .iter()
        .map(|(p, cells)| fs_format_param(p, cells, style, collapse_unchanged))
        .collect::<Vec<_>>()
        .join("; ");
    format!("{label} ({span}, {} readings): {series}", stamps.len())
}

pub fn run_flowsheet_tables(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "flowsheet_tables", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let style_raw = get_str_opt(cfg, "flowsheet_tables", "style", "series");
    let style = if style_raw == "range" {
        "range"
    } else {
        "series"
    };
    let collapse_unchanged = get_bool_opt(cfg, "flowsheet_tables", "collapse_unchanged", true);

    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<String> = Vec::new();
    let mut tables = 0usize;
    let mut skipped = 0usize;
    let mut i = 0usize;

    while i < lines.len() {
        if let Some((title, stamps)) = fs_parse_time_across_header(lines[i]) {
            let ncols = stamps.len();
            let mut rows = Vec::new();
            let mut j = i + 1;
            let mut bad = false;
            while j < lines.len() {
                if let Some(row) = fs_parse_param_row(lines[j], ncols) {
                    rows.push(row);
                    j += 1;
                } else if FS_CANDIDATE_PARAM_RE.is_match(lines[j]) {
                    bad = true;
                    break;
                } else {
                    break;
                }
            }
            if !bad && rows.len() >= 2 {
                out.push(fs_format_grid(&title, &stamps, &rows, style, collapse_unchanged));
                tables += 1;
                i = j;
                continue;
            }
            if bad {
                skipped += 1;
            }
        }

        if let Some((title, params)) = fs_parse_time_down_header(lines[i]) {
            let ncols = params.len();
            let mut t_rows = Vec::new();
            let mut j = i + 1;
            let mut bad = false;
            while j < lines.len() {
                if let Some(tr) = fs_parse_time_down_row(lines[j], ncols) {
                    t_rows.push(tr);
                    j += 1;
                } else if FS_CANDIDATE_STAMP_RE.is_match(lines[j]) {
                    bad = true;
                    break;
                } else {
                    break;
                }
            }
            if !bad && t_rows.len() >= 2 {
                let stamps: Vec<String> = t_rows.iter().map(|(s, _)| s.clone()).collect();
                let transposed: Vec<(String, Vec<String>)> = (0..ncols)
                    .map(|col_idx| {
                        (
                            params[col_idx].clone(),
                            t_rows.iter().map(|(_, c)| c[col_idx].clone()).collect(),
                        )
                    })
                    .collect();
                out.push(fs_format_grid(
                    &title,
                    &stamps,
                    &transposed,
                    style,
                    collapse_unchanged,
                ));
                tables += 1;
                i = j;
                continue;
            }
            if bad {
                skipped += 1;
            }
        }

        out.push(lines[i].to_string());
        i += 1;
    }

    let mut details = json!({ "tables": tables });
    if skipped > 0 {
        details["skipped"] = json!(skipped);
    }
    (out.join("\n"), tables, details)
}

// ===========================================================================
// 9. note_order
// ===========================================================================

static NOTE_HEADER_RE: Lazy<Regex> = Lazy::new(|| {
    let dt = r"(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+\d{1,2}(?:,?\s+\d{4})?)(?:[ \t]*\((?:HD|POD)#[^)]*\))?";
    Regex::new(&format!(
        r"(?im)^(?:(?:Progress|Consult(?:ation)?|Admission|H&P|History\s+and\s+Physical|Discharge|Daily|SOAP|ICU)\s+Notes?(?:\s+by\s+[^\n]+)?|(?:Note\s+Date|Date\s+of\s+Service|DOS)\s*[:#]\s*{dt}[^\n]*|{dt}[ \t]+(?:[-–—][ \t]+)?(?:Progress\s+Note|Daily\s+Note|SOAP\s+Note|Consult\s+Note|ICU\s+Note|Note)\b[^\n]*|[A-Z][a-zA-Z,.\s\-]+?\s+at\s+{dt}[ \t]+\d{{1,2}}:\d{{2}}[^\n]*)$"
    ))
    .unwrap()
});
static NOTE_STRIP_DATE_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}[/\-]\d{1,2}(?:[/\-]\d{2,4})?\b").unwrap()
});
static NOTE_TIME_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"\b(?P<h>[01]?\d|2[0-3]):(?P<m>[0-5]\d)(?:\s*(?P<ampm>[APap]\.?[Mm]\.?))?\b|\b(?P<mil>(?:[01]\d|2[0-3])[0-5]\d)\s*(?:hrs?|hours?)?\b"
    )
    .unwrap()
});

fn extract_note_time(header_line: &str) -> NaiveTime {
    let stripped = NOTE_STRIP_DATE_RE.replace_all(header_line, " ");
    let Some(caps) = NOTE_TIME_RE.captures(&stripped) else {
        return NaiveTime::from_hms_opt(0, 0, 0).unwrap();
    };
    if let Some(mil) = caps.name("mil") {
        let s = mil.as_str();
        let hh: u32 = s[..2].parse().unwrap_or(0);
        let mm: u32 = s[2..].parse().unwrap_or(0);
        return NaiveTime::from_hms_opt(hh, mm, 0).unwrap_or_default();
    }
    let mut hh: u32 = caps
        .name("h")
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let mm: u32 = caps
        .name("m")
        .and_then(|m| m.as_str().parse().ok())
        .unwrap_or(0);
    let ampm = caps
        .name("ampm")
        .map(|m| m.as_str().replace('.', "").to_lowercase())
        .unwrap_or_default();
    if ampm == "pm" && hh < 12 {
        hh += 12;
    } else if ampm == "am" && hh == 12 {
        hh = 0;
    }
    NaiveTime::from_hms_opt(hh, mm, 0).unwrap_or_default()
}

pub fn run_note_order(text: &str, cfg: &Value) -> (String, usize, Value) {
    if !get_bool_opt(cfg, "note_order", "enabled", false) {
        return (text.to_string(), 0, json!({}));
    }
    let dir_raw = get_str_opt(cfg, "note_order", "direction", "oldest_first");
    let direction = if dir_raw == "newest_first" {
        "newest_first"
    } else {
        "oldest_first"
    };

    let matches: Vec<_> = NOTE_HEADER_RE.find_iter(text).collect();
    if matches.len() < 2 {
        return (text.to_string(), 0, json!({}));
    }

    let ref_d = reference_date(text, None);
    let preamble = text[..matches[0].start()].trim_matches('\n');
    let mut notes: Vec<(usize, NaiveDateTime, &str)> = Vec::with_capacity(matches.len());

    for (i, m) in matches.iter().enumerate() {
        let start = m.start();
        let end = if i + 1 < matches.len() {
            matches[i + 1].start()
        } else {
            text.len()
        };
        let chunk = text[start..end].trim_matches('\n');
        let header_line = m.as_str().trim().lines().next().unwrap_or("");
        // Also normalize `-` separated dates like `10-02-2026` to `/` for `parse_date`
        let norm_header = header_line.replace('-', "/");
        let d = parse_date(header_line, Some(ref_d)).or_else(|| parse_date(&norm_header, Some(ref_d)));
        let Some(date) = d else {
            return (text.to_string(), 0, json!({ "skipped": "undated_note" }));
        };
        let time = extract_note_time(header_line);
        notes.push((i, NaiveDateTime::new(date, time), chunk));
    }

    let mut ordered = notes.clone();
    if direction == "oldest_first" {
        ordered.sort_by(|a, b| a.1.cmp(&b.1).then_with(|| a.0.cmp(&b.0)));
    } else {
        ordered.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.cmp(&b.0)));
    }

    let unchanged = ordered.iter().zip(&notes).all(|(a, b)| a.0 == b.0);
    let n = notes.len();
    if unchanged {
        return (
            text.to_string(),
            0,
            json!({ "notes": n, "reordered": false }),
        );
    }

    let body = ordered
        .iter()
        .map(|(_, _, chunk)| *chunk)
        .collect::<Vec<_>>()
        .join("\n\n");
    let mut rebuilt = if preamble.is_empty() {
        body
    } else {
        format!("{preamble}\n\n{body}")
    };
    if text.ends_with('\n') && !rebuilt.ends_with('\n') {
        rebuilt.push('\n');
    }
    (
        rebuilt,
        n,
        json!({ "notes": n, "reordered": true, "direction": direction }),
    )
}
