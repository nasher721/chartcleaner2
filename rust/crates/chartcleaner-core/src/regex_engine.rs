//! Hybrid linear-DFA (`regex`) and backtracking/lookaround (`fancy-regex`) engine
//! with memoized compilation, SHA-1 rule IDs, line-span binary search, and tracked changes.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, LazyLock, RwLock};
use std::time::Instant;

use fancy_regex::Regex as FancyRegex;
use regex::{Regex, RegexBuilder};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::config::{get_stage_options, Config};
use crate::section_parser::parse_clinical_sections;

/// Cap on recorded changes per stage so tracking stays cheap on huge charts.
pub const MAX_TRACKED_CHANGES: usize = 2000;

/// A rule taking at least this long (in ms) on one chart lands in `details["slow_rules"]`.
pub const SLOW_RULE_MS: f64 = 25.0;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct TrackedChange {
    pub rule: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rule_id: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub source: Option<String>,
    pub before: String,
    pub after: String,
    pub line: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub start: Option<usize>,
}

pub type ChangeRecord = TrackedChange;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuleSpec {
    pub pattern: String,
    pub replacement: String,
}

/// Compute the stable 10-hex-char SHA-1 rule identifier (identical to Python `stage_core.rule_id`).
pub fn rule_id(pattern: &str) -> String {
    let mut hasher = Sha1::new();
    hasher.update(pattern.as_bytes());
    let digest = hasher.finalize();
    format!(
        "{:02x}{:02x}{:02x}{:02x}{:02x}",
        digest[0], digest[1], digest[2], digest[3], digest[4]
    )
}

/// Normalize Python regex idioms (such as `\Z` end-of-string anchor) into Rust regex syntax.
pub fn normalize_python_regex(pattern: &str) -> String {
    let mut out = String::with_capacity(pattern.len() + 8);
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            let next = chars[i + 1];
            if next == 'Z' {
                out.push_str("\\z");
                i += 2;
                continue;
            }
            out.push(chars[i]);
            out.push(next);
            i += 2;
            continue;
        }
        out.push(chars[i]);
        i += 1;
    }
    out
}

#[derive(Clone)]
pub enum CompiledRegex {
    Standard(Regex),
    Fancy(FancyRegex),
}

#[derive(Debug, Clone)]
pub struct MatchSpan {
    pub start: usize,
    pub end: usize,
    pub groups: Vec<Option<(usize, usize)>>,
}

impl CompiledRegex {
    pub fn compile(
        pattern: &str,
        ignore_case: bool,
        multi_line: bool,
        dot_all: bool,
    ) -> Result<Self, String> {
        let norm = normalize_python_regex(pattern);
        // Try standard linear-time DFA regex first
        let mut builder = RegexBuilder::new(&norm);
        builder
            .case_insensitive(ignore_case)
            .multi_line(multi_line)
            .dot_matches_new_line(dot_all);
        if let Ok(re) = builder.build() {
            return Ok(CompiledRegex::Standard(re));
        }

        // Fall back to fancy-regex for lookarounds (?=...), (?<!...), or backreferences
        let mut flags = String::new();
        if ignore_case {
            flags.push('i');
        }
        if multi_line {
            flags.push('m');
        }
        if dot_all {
            flags.push('s');
        }
        let wrapped = if flags.is_empty() {
            norm
        } else {
            format!("(?{flags}){norm}")
        };
        FancyRegex::new(&wrapped)
            .map(CompiledRegex::Fancy)
            .map_err(|e| e.to_string())
    }

    pub fn is_match(&self, text: &str) -> bool {
        match self {
            CompiledRegex::Standard(re) => re.is_match(text),
            CompiledRegex::Fancy(re) => re.is_match(text).unwrap_or(false),
        }
    }

    pub fn find_all(&self, text: &str) -> Vec<MatchSpan> {
        match self {
            CompiledRegex::Standard(re) => re
                .captures_iter(text)
                .filter_map(|caps| {
                    let m0 = caps.get(0)?;
                    let groups = (0..caps.len())
                        .map(|i| caps.get(i).map(|m| (m.start(), m.end())))
                        .collect();
                    Some(MatchSpan {
                        start: m0.start(),
                        end: m0.end(),
                        groups,
                    })
                })
                .collect(),
            CompiledRegex::Fancy(re) => {
                let mut out = Vec::new();
                let mut pos = 0;
                while pos <= text.len() {
                    let Ok(Some(caps)) = re.captures_from_pos(text, pos) else {
                        break;
                    };
                    let Some(m0) = caps.get(0) else {
                        break;
                    };
                    let start = m0.start();
                    let end = m0.end();
                    let groups = (0..caps.len())
                        .map(|i| caps.get(i).map(|m| (m.start(), m.end())))
                        .collect();
                    out.push(MatchSpan { start, end, groups });
                    if end > start {
                        pos = end;
                    } else if end < text.len() {
                        pos = end + text[end..].chars().next().map(|c| c.len_utf8()).unwrap_or(1);
                    } else {
                        break;
                    }
                }
                out
            }
        }
    }

    pub fn find_spans(&self, text: &str) -> Vec<(usize, usize)> {
        self.find_all(text)
            .into_iter()
            .map(|m| (m.start, m.end))
            .collect()
    }

    pub fn replace_all(&self, text: &str, replacement: &str) -> (String, usize) {
        subn_tracked(self, replacement, text, None, "", &[], None, None)
    }
}

type CacheKey = (String, bool, bool, bool);

static REGEX_CACHE: LazyLock<RwLock<HashMap<CacheKey, Arc<CompiledRegex>>>> =
    LazyLock::new(|| RwLock::new(HashMap::with_capacity(512)));

pub fn compile_cached(
    pattern: &str,
    ignore_case: bool,
    multi_line: bool,
    dot_all: bool,
) -> Result<Arc<CompiledRegex>, String> {
    let key = (pattern.to_string(), ignore_case, multi_line, dot_all);
    if let Ok(guard) = REGEX_CACHE.read() {
        if let Some(found) = guard.get(&key) {
            return Ok(Arc::clone(found));
        }
    }
    let compiled = Arc::new(CompiledRegex::compile(
        pattern,
        ignore_case,
        multi_line,
        dot_all,
    )?);
    if let Ok(mut guard) = REGEX_CACHE.write() {
        if guard.len() > 4096 {
            guard.clear();
        }
        guard.insert(key, Arc::clone(&compiled));
    }
    Ok(compiled)
}

/// Expand a Python-style replacement string (`\1`..`\9`, `\g<1>`, `\n`, `\t`, `\\`, or `$1`).
pub fn expand_replacement(text: &str, span: &MatchSpan, replacement: &str) -> String {
    if !replacement.contains('\\') && !replacement.contains('$') {
        return replacement.to_string();
    }
    let mut out = String::with_capacity(replacement.len());
    let chars: Vec<char> = replacement.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '\\' && i + 1 < chars.len() {
            let next = chars[i + 1];
            match next {
                '\\' => {
                    out.push('\\');
                    i += 2;
                }
                'n' => {
                    out.push('\n');
                    i += 2;
                }
                't' => {
                    out.push('\t');
                    i += 2;
                }
                'r' => {
                    out.push('\r');
                    i += 2;
                }
                '1'..='9' => {
                    let idx = (next as usize) - ('0' as usize);
                    if let Some(Some((s, e))) = span.groups.get(idx) {
                        out.push_str(&text[*s..*e]);
                    }
                    i += 2;
                }
                'g' if i + 2 < chars.len() && chars[i + 2] == '<' => {
                    if let Some(close) = chars[i + 3..].iter().position(|&c| c == '>') {
                        let num_str: String = chars[i + 3..i + 3 + close].iter().collect();
                        if let Ok(idx) = num_str.parse::<usize>() {
                            if let Some(Some((s, e))) = span.groups.get(idx) {
                                out.push_str(&text[*s..*e]);
                            }
                        }
                        i += 4 + close;
                    } else {
                        out.push(chars[i]);
                        i += 1;
                    }
                }
                _ => {
                    out.push(chars[i]);
                    out.push(next);
                    i += 2;
                }
            }
        } else if chars[i] == '$' && i + 1 < chars.len() && chars[i + 1].is_ascii_digit() {
            let idx = (chars[i + 1] as usize) - ('0' as usize);
            if let Some(Some((s, e))) = span.groups.get(idx) {
                out.push_str(&text[*s..*e]);
            }
            i += 2;
        } else {
            out.push(chars[i]);
            i += 1;
        }
    }
    out
}

/// O(len(text)) progressive line counter for left-to-right matches.
pub struct LineCounter<'a> {
    text: &'a str,
    pos: usize,
    line: usize,
}

impl<'a> LineCounter<'a> {
    pub fn new(text: &'a str) -> Self {
        Self {
            text,
            pos: 0,
            line: 1,
        }
    }

    pub fn line_at(&mut self, target: usize) -> usize {
        let clamped = target.min(self.text.len());
        if clamped > self.pos {
            self.line += self.text.as_bytes()[self.pos..clamped]
                .iter()
                .filter(|&&b| b == b'\n')
                .count();
            self.pos = clamped;
        }
        self.line
    }
}

/// Lazy line-start index for O(log lines) line-span lookup on exception/table checks.
pub struct LineSpanIndex<'a> {
    text: &'a str,
    starts: Option<Vec<usize>>,
}

impl<'a> LineSpanIndex<'a> {
    pub fn new(text: &'a str) -> Self {
        Self { text, starts: None }
    }

    pub fn line_span(&mut self, pos: usize) -> (usize, usize) {
        let starts = self.starts.get_or_insert_with(|| {
            let mut s = vec![0];
            for (idx, b) in self.text.bytes().enumerate() {
                if b == b'\n' {
                    s.push(idx + 1);
                }
            }
            s
        });
        let idx = starts.partition_point(|&s| s <= pos).saturating_sub(1);
        let start = starts[idx];
        let end = if idx + 1 < starts.len() {
            starts[idx + 1] - 1
        } else {
            self.text.len()
        };
        (start, end)
    }

    pub fn line_span_at(&mut self, pos: usize) -> (usize, usize) {
        self.line_span(pos)
    }
}

/// Check whether a line looks like a table (contains `|`, `\t`, or >= 3 runs of 2+ spaces).
pub fn is_table_line(line: &str) -> bool {
    if line.contains('|') || line.contains('\t') {
        return true;
    }
    let bytes = line.as_bytes();
    let mut runs = 0;
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b' ' {
            let start = i;
            while i < bytes.len() && bytes[i] == b' ' {
                i += 1;
            }
            if i - start >= 2 {
                runs += 1;
                if runs >= 3 {
                    return true;
                }
            }
        } else {
            i += 1;
        }
    }
    false
}

pub fn table_line_starts(text: &str) -> HashSet<usize> {
    let mut set = HashSet::new();
    let mut pos = 0;
    for line in text.split('\n') {
        if is_table_line(line) {
            set.insert(pos);
        }
        pos += line.len() + 1;
    }
    set
}

fn build_scope_predicate(cfg: &Config, sid: Option<&str>, text: &str) -> Option<Box<dyn Fn(usize) -> bool>> {
    let sid = sid?;
    let scope_obj = get_stage_options(cfg, sid)?.get("scope")?.as_object()?;
    let mode = scope_obj.get("mode").and_then(|v| v.as_str()).unwrap_or("all");
    if mode == "all" {
        return None;
    }
    let sections: HashSet<String> = scope_obj
        .get("sections")?
        .as_array()?
        .iter()
        .filter_map(|v| v.as_str())
        .map(|s| s.trim().to_lowercase())
        .filter(|s| !s.is_empty())
        .collect();
    if sections.is_empty() {
        return None;
    }
    let parsed = parse_clinical_sections(text);
    let spans: Vec<(usize, usize, String)> = parsed
        .sections
        .into_iter()
        .map(|s| (s.start_pos, s.end_pos, s.domain))
        .collect();
    let only_mode = mode == "only";
    Some(Box::new(move |pos: usize| {
        let mut domain = "preamble";
        for (start, end, dom) in &spans {
            if *start <= pos && pos < *end {
                domain = dom.as_str();
                break;
            }
        }
        let inside = sections.contains(domain);
        if only_mode {
            inside
        } else {
            !inside
        }
    }))
}

/// Apply a single compiled regex with optional exceptions, section scope, table line protection, and change tracking.
#[allow(clippy::too_many_arguments)]
pub fn subn_tracked(
    regex: &CompiledRegex,
    replacement: &str,
    text: &str,
    mut changes: Option<&mut Vec<TrackedChange>>,
    pattern: &str,
    exceptions: &[String],
    scope: Option<&dyn Fn(usize) -> bool>,
    table_lines: Option<&HashSet<usize>>,
) -> (String, usize) {
    let matches = regex.find_all(text);
    if matches.is_empty() {
        return (text.to_string(), 0);
    }

    let rid = rule_id(pattern);
    let keep_lower: Vec<String> = exceptions.iter().map(|e| e.to_lowercase()).collect();
    let mut line_counter = LineCounter::new(text);
    let mut line_index = LineSpanIndex::new(text);

    let mut out = String::with_capacity(text.len());
    let mut last_end = 0;
    let mut applied = 0;

    for m in &matches {
        if m.start < last_end {
            continue;
        }
        out.push_str(&text[last_end..m.start]);
        let found = &text[m.start..m.end];

        let mut skip = false;
        if !keep_lower.is_empty() {
            let (ls, le) = line_index.line_span(m.start);
            let line_lower = text[ls..le].to_lowercase();
            if keep_lower.iter().any(|k| line_lower.contains(k)) {
                skip = true;
            }
        }
        if !skip {
            if let Some(scope_fn) = scope {
                if !scope_fn(m.start) {
                    skip = true;
                }
            }
        }
        if !skip {
            if let Some(tl) = table_lines {
                let (ls, _) = line_index.line_span(m.start);
                if tl.contains(&ls) {
                    skip = true;
                }
            }
        }

        if skip {
            out.push_str(found);
        } else {
            let after = expand_replacement(text, m, replacement);
            if let Some(ref mut ch_list) = changes {
                if ch_list.len() < MAX_TRACKED_CHANGES {
                    ch_list.push(TrackedChange {
                        rule: pattern.to_string(),
                        rule_id: Some(rid.clone()),
                        source: None,
                        before: found.to_string(),
                        after: after.clone(),
                        line: line_counter.line_at(m.start),
                        start: None,
                    });
                }
            }
            out.push_str(&after);
            applied += 1;
        }
        last_end = m.end;
    }
    out.push_str(&text[last_end..]);
    (out, applied)
}

/// Apply an ordered list of `(pattern, replacement)` rules, recording `rule_hits`, `slow_rules`, and `changes`.
#[allow(clippy::too_many_arguments)]
pub fn apply_rules(
    text: &str,
    cfg: &Config,
    track_changes: bool,
    rules: &[(String, String)],
    mut ignore_case: bool,
    multi_line: bool,
    dot_all: bool,
    sid: Option<&str>,
    phi: bool,
) -> (String, usize, Map<String, Value>) {
    if let Some(s) = sid {
        if get_stage_options(cfg, s)
            .and_then(|o| o.get("case_sensitive"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false)
        {
            ignore_case = false;
        }
    }

    let exceptions: Vec<String> = sid
        .and_then(|s| get_stage_options(cfg, s))
        .and_then(|o| o.get("exceptions"))
        .and_then(|v| v.as_array())
        .map(|arr| {
            arr.iter()
                .filter_map(|x| x.as_str())
                .map(|s| s.trim().to_string())
                .filter(|s| !s.is_empty())
                .collect()
        })
        .unwrap_or_default();

    let scope = build_scope_predicate(cfg, sid, text);
    let preserve_tables = sid
        .and_then(|s| get_stage_options(cfg, s))
        .and_then(|o| o.get("preserve_tables"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false);
    let table_lines = if preserve_tables {
        Some(table_line_starts(text))
    } else {
        None
    };

    let mut current = text.to_string();
    let mut changes: Option<Vec<TrackedChange>> = if track_changes {
        Some(Vec::new())
    } else {
        None
    };
    let mut hits: Map<String, Value> = Map::new();
    let mut slow: Map<String, Value> = Map::new();
    let mut total = 0usize;

    for (pattern, replacement) in rules {
        let Ok(compiled) = compile_cached(pattern, ignore_case, multi_line, dot_all) else {
            continue;
        };
        let started = Instant::now();
        let (next_text, n) = subn_tracked(
            &compiled,
            replacement,
            &current,
            changes.as_mut(),
            pattern,
            &exceptions,
            scope.as_deref(),
            table_lines.as_ref(),
        );
        let ms = started.elapsed().as_secs_f64() * 1000.0;
        if ms >= SLOW_RULE_MS {
            slow.insert(rule_id(pattern), json!((ms * 10.0).round() / 10.0));
        }
        if n > 0 {
            let rid = rule_id(pattern);
            let prev = hits.get(&rid).and_then(|v| v.as_u64()).unwrap_or(0) as usize;
            hits.insert(rid, json!(prev + n));
            total += n;
        }
        current = next_text;
    }

    let mut details = Map::new();
    if phi {
        details.insert("phi".to_string(), json!({ "pattern_redactions": total }));
    }
    if !hits.is_empty() {
        details.insert("rule_hits".to_string(), Value::Object(hits));
    }
    if !slow.is_empty() {
        details.insert("slow_rules".to_string(), Value::Object(slow));
    }
    if let Some(ch) = changes {
        details.insert(
            "changes".to_string(),
            serde_json::to_value(ch).unwrap_or(Value::Array(vec![])),
        );
    }

    (current, total, details)
}
