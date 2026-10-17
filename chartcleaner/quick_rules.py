"""Plain-text rule entry: "Remove text" and "Add abbreviations" quick modes.

No regex needed. The Rules page "Quick add" tab, the Clean page selection
modes and the CLI (``--add-remove`` / ``--add-abbreviation``) all go through
here, so every path builds the same rules:

* a phrase to remove becomes a literal learned rule (``highlight_rules.make_rule``),
  exactly what highlighting text on the Clean page saves;
* an abbreviation becomes a custom ``abbreviations.custom`` entry, checked
  against the Do Not Use lists (blocked ones are skipped, never saved).

:func:`export_text` / :func:`parse_text` round-trip a readable plain-text
rules file (``# Remove`` / ``# Replace`` / ``# Abbreviations`` sections) that
is easy to edit by hand and share by email.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .abbreviation_safety import check, needs_override
from .abbreviations import normalize_settings
from .highlight_rules import describe_rule, make_rule

# Longest first so "=>" wins over "=" and "->" over "-".
ABBREVIATION_SEPARATORS = ("=>", "->", "→", "\t", "=")
REPLACE_SEPARATOR = "=>"
TEXT_HEADER = "# Chart Cleaner rules (plain text)"
SECTIONS = {"remove": "# Remove", "replace": "# Replace", "abbreviations": "# Abbreviations"}


def _lines(text: str) -> list[str]:
    """Non-empty, non-comment lines, stripped."""
    out = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            out.append(line)
    return out


def parse_removals(text: str) -> list[str]:
    """One phrase per line; blank lines and ``#`` comments are ignored."""
    seen: set[str] = set()
    phrases = []
    for line in _lines(text):
        key = line.casefold()
        if key not in seen:
            seen.add(key)
            phrases.append(line)
    return phrases


def _split_pair(line: str, separators: tuple[str, ...]) -> tuple[str, str] | None:
    for sep in separators:
        if sep in line:
            left, right = line.split(sep, 1)
            if left.strip() and right.strip():
                return left.strip(), right.strip()
            return None
    return None


def parse_abbreviations(text: str) -> tuple[list[tuple[str, str]], list[str]]:
    """``full term = abbreviation`` per line → ``(pairs, errors)``.

    Also accepts ``=>``, ``->``, ``→`` or a tab (pasted from a spreadsheet).
    A later line for the same term wins.
    """
    pairs: dict[str, tuple[str, str]] = {}
    errors = []
    for i, raw in enumerate((text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        pair = _split_pair(raw.strip("\r\n "), ABBREVIATION_SEPARATORS)
        if pair is None:
            errors.append(f"Line {i}: write it as “full term = abbreviation” — got “{line[:60]}”")
            continue
        term, abbreviation = pair
        if term.casefold() == abbreviation.casefold():
            errors.append(f"Line {i}: “{term}” is the same as its abbreviation")
            continue
        pairs[term.casefold()] = (term, abbreviation)
    return list(pairs.values()), errors


def _literal_hits(chart: str, phrase: str, *, whole_words: bool, case_sensitive: bool) -> int:
    pattern = make_rule(phrase, case_sensitive=case_sensitive, whole_words=whole_words)[0]
    return len(re.findall(pattern, chart or ""))


def preview_removals(chart: str, phrases: list[str], *, whole_words: bool = True,
                     case_sensitive: bool = False) -> list[dict]:
    """``[{"text", "hits"}]`` — how often each phrase occurs in ``chart``."""
    return [{"text": p, "hits": _literal_hits(chart, p, whole_words=whole_words,
                                              case_sensitive=case_sensitive)}
            for p in phrases]


def preview_abbreviations(chart: str, pairs: list[tuple[str, str]], cfg: dict | None = None) -> list[dict]:
    """``[{"term", "abbreviation", "hits", "status", "message"}]`` without saving.

    ``status`` is ``ok``, ``warn`` (saved, but e.g. the abbreviation already
    means something else) or ``blocked`` (Do Not Use — will be skipped).
    """
    rows = []
    for term, abbreviation in pairs:
        issues = check(term, abbreviation, cfg)
        status = "blocked" if needs_override(issues) else ("warn" if issues else "ok")
        rows.append({"term": term, "abbreviation": abbreviation,
                     "hits": _literal_hits(chart, term, whole_words=True, case_sensitive=False),
                     "status": status,
                     "message": " ".join(i.message for i in issues)})
    return rows


@dataclass
class AddReport:
    """What a quick add changed (``added`` + ``updated`` are saved)."""
    added: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    existing: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    blocked: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.added or self.updated)

    def summary(self, noun: str) -> str:
        parts = [f"{len(self.added)} {noun}(s) added"]
        if self.updated:
            parts.append(f"{len(self.updated)} updated")
        if self.existing:
            parts.append(f"{len(self.existing)} already saved")
        if self.conflicts:
            parts.append(f"{len(self.conflicts)} skipped (already replaced by another rule)")
        if self.blocked:
            parts.append(f"{len(self.blocked)} blocked (Do Not Use)")
        return ", ".join(parts) + "."


def add_removals(cfg: dict, phrases: list[str], *, whole_words: bool = True,
                 case_sensitive: bool = False) -> tuple[dict, AddReport]:
    """Return a copy of ``cfg`` with a literal remove rule per phrase.

    Turns the learned-rules stage on when anything was added. A phrase that
    already has a *replace* rule is reported as a conflict, not overwritten.
    """
    report = AddReport()
    out = dict(cfg)
    rules = [list(p) for p in cfg.get("learned_rules") or []]
    by_pattern = {p[0]: p[1] for p in rules if isinstance(p, list) and len(p) == 2}
    for phrase in phrases:
        pair = make_rule(phrase, "", case_sensitive=case_sensitive, whole_words=whole_words)
        if pair[0] in by_pattern:
            (report.existing if by_pattern[pair[0]] == "" else report.conflicts).append(phrase)
            continue
        rules.append(pair)
        by_pattern[pair[0]] = ""
        report.added.append(phrase)
    out["learned_rules"] = rules
    if report.added:
        options = dict(out.get("stage_options") or {})
        options["learned_rules"] = {**(options.get("learned_rules") or {}), "enabled": True}
        out["stage_options"] = options
    return out, report


def add_replacements(cfg: dict, pairs: list[tuple[str, str]], *, whole_words: bool = True,
                     case_sensitive: bool = False) -> tuple[dict, AddReport]:
    """Like :func:`add_removals` for ``(text, replacement)`` pairs; existing
    rules for the same text are updated in place (order is preserved)."""
    report = AddReport()
    out = dict(cfg)
    rules = [list(p) for p in cfg.get("learned_rules") or []]
    index = {p[0]: i for i, p in enumerate(rules) if isinstance(p, list) and len(p) == 2}
    for text, replacement in pairs:
        pair = make_rule(text, replacement, case_sensitive=case_sensitive, whole_words=whole_words)
        if pair[0] in index:
            i = index[pair[0]]
            if rules[i][1] == pair[1]:
                report.existing.append(text)
            else:
                rules[i] = pair
                report.updated.append(text)
            continue
        index[pair[0]] = len(rules)
        rules.append(pair)
        report.added.append(text)
    out["learned_rules"] = rules
    if report.changed:
        options = dict(out.get("stage_options") or {})
        options["learned_rules"] = {**(options.get("learned_rules") or {}), "enabled": True}
        out["stage_options"] = options
    return out, report


def add_abbreviations(cfg: dict, pairs: list[tuple[str, str]]) -> tuple[dict, AddReport]:
    """Return a copy of ``cfg`` with each ``(term, abbreviation)`` saved.

    Do Not Use pairs are skipped (``report.blocked``) — allowing one stays a
    deliberate, one-at-a-time choice in the abbreviation editor. A term that
    is already custom is updated (and re-enabled); a bundled term the user had
    turned off is turned back on through the custom entry.
    """
    report = AddReport()
    out = dict(cfg)
    group = normalize_settings(cfg.get("abbreviations"))
    by_term = {c["term"].casefold(): c for c in group["custom"]}
    for term, abbreviation in pairs:
        issues = check(term, abbreviation, out)
        if needs_override(issues):
            report.blocked.append(term)
            continue
        report.warnings.extend(f"{term}: {i.message}" for i in issues)
        key = term.casefold()
        old = by_term.get(key)
        if old is None:
            by_term[key] = {"term": term, "replacement": abbreviation, "enabled": True}
            report.added.append(term)
        elif old["replacement"] == abbreviation and old.get("enabled", True):
            report.existing.append(term)
        else:
            by_term[key] = {**old, "replacement": abbreviation, "enabled": True}
            by_term[key].pop("acknowledged", None)  # the override was for the old pair
            report.updated.append(term)
        group["disabled"] = [d for d in group["disabled"] if d.casefold() != key]
        out["abbreviations"] = normalize_settings({**group, "custom": list(by_term.values())})
    return out, report


# ---------------------------------------------------------------------------
# Plain-text export / import
# ---------------------------------------------------------------------------

def current_rules(cfg: dict) -> dict:
    """The user's rules split into readable kinds.

    ``remove`` / ``replace`` hold literal rules (text the user typed or
    highlighted); learned rules that are hand-written regexes go to
    ``regex`` (kept verbatim — they can't be shown as plain text).
    """
    remove, replace, regex = [], [], []
    for pair in cfg.get("learned_rules") or []:
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2):
            continue
        described = describe_rule(list(pair))
        if described is None:
            regex.append(list(pair))
        elif described["replacement"] == "":
            remove.append(described)
        else:
            replace.append(described)
    group = normalize_settings(cfg.get("abbreviations"))
    return {"remove": remove, "replace": replace, "regex": regex,
            "abbreviations": [c for c in group["custom"] if c.get("enabled", True)]}


def _options_note(rule: dict) -> str:
    notes = []
    if rule["case_sensitive"]:
        notes.append("match case")
    if not rule["whole_words"]:
        notes.append("inside words")
    return f"  # [{', '.join(notes)}]" if notes else ""


def export_text(cfg: dict) -> str:
    """A human-readable rules file :func:`parse_text` reads back."""
    rules = current_rules(cfg)
    lines = [TEXT_HEADER,
             "# One rule per line. Lines starting with # are comments.",
             "", SECTIONS["remove"]]
    lines += [r["text"].replace("\n", " ") + _options_note(r) for r in rules["remove"]]
    lines += ["", SECTIONS["replace"] + f"  (text {REPLACE_SEPARATOR} replacement)"]
    lines += [f"{r['text']} {REPLACE_SEPARATOR} {r['replacement']}".replace("\n", " ") + _options_note(r)
              for r in rules["replace"]]
    lines += ["", SECTIONS["abbreviations"] + "  (full term = abbreviation)"]
    lines += [f"{c['term']} = {c['replacement']}" for c in rules["abbreviations"]]
    if rules["regex"]:
        lines += ["", "# Advanced regex rules are not included here — use the JSON export for them."]
    return "\n".join(lines) + "\n"


_OPTIONS_NOTE = re.compile(r"\s+#\s*\[([^\]]*)\]\s*$")


def _with_options(line: str) -> tuple[str, dict]:
    match = _OPTIONS_NOTE.search(line)
    if not match:
        return line, {"case_sensitive": False, "whole_words": True}
    notes = {n.strip() for n in match.group(1).split(",")}
    return line[:match.start()].rstrip(), {"case_sensitive": "match case" in notes,
                                           "whole_words": "inside words" not in notes}


def parse_text(text: str) -> dict:
    """Parse :func:`export_text` output → ``{"remove", "replace", "abbreviations", "errors"}``.

    Lines before any section header are treated as text to remove, so a
    bare list of phrases is a valid file too.
    """
    result: dict = {"remove": [], "replace": [], "abbreviations": [], "errors": []}
    section = "remove"
    headers = {v.casefold(): k for k, v in SECTIONS.items()}
    for i, raw in enumerate((text or "").splitlines(), 1):
        stripped = raw.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            head = stripped.split("(")[0].strip().casefold()
            section = headers.get(head, section)
            continue
        line, options = _with_options(stripped)
        if section == "remove":
            result["remove"].append({"text": line, **options})
        elif section == "replace":
            pair = _split_pair(line, (REPLACE_SEPARATOR,))
            if pair is None:
                result["errors"].append(f"Line {i}: write it as “text {REPLACE_SEPARATOR} replacement”")
            else:
                result["replace"].append({"text": pair[0], "replacement": pair[1], **options})
        else:
            pairs, errors = parse_abbreviations(line)
            result["abbreviations"].extend(pairs)
            result["errors"].extend(e.replace("Line 1:", f"Line {i}:") for e in errors)
    return result


def apply_text(cfg: dict, text: str) -> tuple[dict, dict[str, AddReport], list[str]]:
    """Merge a plain-text rules file into a copy of ``cfg``.

    Returns ``(config, {"remove"|"replace"|"abbreviations": AddReport}, errors)``.
    """
    parsed = parse_text(text)
    reports: dict[str, AddReport] = {}
    out = cfg
    total = AddReport()
    for item in parsed["remove"]:
        out, rep = add_removals(out, [item["text"]], whole_words=item["whole_words"],
                                case_sensitive=item["case_sensitive"])
        for name in ("added", "existing", "conflicts"):
            getattr(total, name).extend(getattr(rep, name))
    reports["remove"] = total
    total = AddReport()
    for item in parsed["replace"]:
        out, rep = add_replacements(out, [(item["text"], item["replacement"])],
                                    whole_words=item["whole_words"],
                                    case_sensitive=item["case_sensitive"])
        for name in ("added", "updated", "existing"):
            getattr(total, name).extend(getattr(rep, name))
    reports["replace"] = total
    out, reports["abbreviations"] = add_abbreviations(out, parsed["abbreviations"])
    return out, reports, parsed["errors"]
