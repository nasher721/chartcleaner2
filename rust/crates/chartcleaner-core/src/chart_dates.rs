//! Dates written in clinical charts (`09/15/2026`, `9/15`, `2026-09-15`, `Sep 15`).

use chrono::{Datelike, Duration, Local, NaiveDate};
use once_cell::sync::Lazy;
use regex::Regex;

static DATE_RAW_RE: Lazy<Regex> = Lazy::new(|| {
    Regex::new(
        r"(?P<iso>\d{4}-\d{2}-\d{2})|(?P<m>0?[1-9]|1[0-2])/(?P<d>0?[1-9]|[12]\d|3[01])(?:/(?P<y>\d{4}|\d{2}))?|(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\.?\s+(?P<d2>\d{1,2})(?:,?\s+(?P<y2>\d{4}))?"
    )
    .unwrap()
});

fn month_num(mon: &str) -> u32 {
    match &mon.to_lowercase()[..3] {
        "jan" => 1,
        "feb" => 2,
        "mar" => 3,
        "apr" => 4,
        "may" => 5,
        "jun" => 6,
        "jul" => 7,
        "aug" => 8,
        "sep" => 9,
        "oct" => 10,
        "nov" => 11,
        "dec" => 12,
        _ => 1,
    }
}

fn build_date(y: Option<i32>, m: u32, d: u32, ref_date: Option<NaiveDate>) -> Option<NaiveDate> {
    let today = Local::now().date_naive();
    if let Some(mut year) = y {
        if year < 100 {
            let cutoff = (today.year() % 100) + 1;
            year += if year <= cutoff { 2000 } else { 1900 };
        }
        return NaiveDate::from_ymd_opt(year, m, d);
    }
    let base = ref_date.unwrap_or(today);
    let mut found = NaiveDate::from_ymd_opt(base.year(), m, d)?;
    if found > base {
        found = NaiveDate::from_ymd_opt(base.year() - 1, m, d)?;
    }
    Some(found)
}

fn check_boundaries_and_parse(
    text: &str,
    caps: &regex::Captures,
    ref_date: Option<NaiveDate>,
) -> Option<(NaiveDate, bool, usize, usize)> {
    let m0 = caps.get(0)?;
    let start = m0.start();
    let end = m0.end();
    let bytes = text.as_bytes();

    if let Some(iso) = caps.name("iso") {
        let prev_bad = start > 0 && (bytes[start - 1].is_ascii_digit() || bytes[start - 1] == b'/');
        let next_bad = end < bytes.len() && bytes[end].is_ascii_digit();
        if prev_bad || next_bad {
            return None;
        }
        let d = NaiveDate::parse_from_str(iso.as_str(), "%Y-%m-%d").ok()?;
        return Some((d, true, start, end));
    }

    if let Some(m_grp) = caps.name("m") {
        let prev_bad = start > 0
            && (bytes[start - 1].is_ascii_digit()
                || bytes[start - 1] == b'/'
                || bytes[start - 1] == b'.');
        let next_bad = end < bytes.len() && (bytes[end].is_ascii_digit() || bytes[end] == b'/');
        if prev_bad || next_bad {
            return None;
        }
        let m: u32 = m_grp.as_str().parse().ok()?;
        let d: u32 = caps.name("d")?.as_str().parse().ok()?;
        let y: Option<i32> = caps.name("y").and_then(|g| g.as_str().parse().ok());
        let date = build_date(y, m, d, ref_date)?;
        return Some((date, y.is_some(), start, end));
    }

    if let Some(mon_grp) = caps.name("mon") {
        let prev_bad = start > 0 && bytes[start - 1].is_ascii_alphabetic();
        let mon_end = mon_grp.end();
        let after_mon_bad = mon_end < bytes.len() && bytes[mon_end].is_ascii_alphabetic();
        let next_bad = end < bytes.len() && bytes[end].is_ascii_digit();
        if prev_bad || after_mon_bad || next_bad {
            return None;
        }
        let m = month_num(mon_grp.as_str());
        let d: u32 = caps.name("d2")?.as_str().parse().ok()?;
        let y: Option<i32> = caps.name("y2").and_then(|g| g.as_str().parse().ok());
        let date = build_date(y, m, d, ref_date)?;
        return Some((date, y.is_some(), start, end));
    }

    None
}

/// Every date in `text`: `(date, start_byte, end_byte)`.
pub fn find_dates(text: &str, ref_date: Option<NaiveDate>) -> Vec<(NaiveDate, usize, usize)> {
    let mut out = Vec::new();
    for caps in DATE_RAW_RE.captures_iter(text) {
        if let Some((d, _, s, e)) = check_boundaries_and_parse(text, &caps, ref_date) {
            out.push((d, s, e));
        }
    }
    out
}

pub fn parse_date(raw: &str, ref_date: Option<NaiveDate>) -> Option<NaiveDate> {
    for caps in DATE_RAW_RE.captures_iter(raw) {
        if let Some((d, _, _, _)) = check_boundaries_and_parse(raw, &caps, ref_date) {
            return Some(d);
        }
    }
    None
}

pub fn reference_date(text: &str, fallback: Option<NaiveDate>) -> NaiveDate {
    let today = Local::now().date_naive();
    let latest_possible = today + Duration::days(1);
    let mut max_date: Option<NaiveDate> = None;

    for caps in DATE_RAW_RE.captures_iter(text) {
        if let Some((d, had_year, _, _)) = check_boundaries_and_parse(text, &caps, None) {
            if had_year && d.year() >= 1990 && d <= latest_possible {
                max_date = Some(match max_date {
                    Some(prev) => prev.max(d),
                    None => d,
                });
            }
        }
    }

    max_date.or(fallback).unwrap_or(today)
}

pub fn day_number(start: NaiveDate, ref_date: NaiveDate) -> i64 {
    (ref_date - start).num_days() + 1
}
