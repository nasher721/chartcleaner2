"""Post-run audit: find PHI and EMR noise that survived the cleaning pipeline.

``run_audit`` is read-only — it never modifies text. It scans the cleaned
output for things worth a second look before sharing: leftover MRN-style
numbers, DOB-adjacent dates, phones/emails, values after identity labels,
and known Epic chrome lines. Findings carry a ready-made suggested rule so
the app can turn them into pipeline patterns with one click.

Configured from config.json's optional ``"audit"`` section (see
``DEFAULT_AUDIT_CONFIG`` for the full shape). Every check is isolated: a
broken pattern in one check can never fail the others or the run itself.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "DEFAULT_AUDIT_CONFIG",
    "Finding",
    "AuditResult",
    "run_audit",
    "get_audit_config",
    "ensure_audit_section",
    "suggestion_for_signature",
]

DEFAULT_AUDIT_CONFIG: dict = {
    "enabled": True,
    "max_findings": 50,
    "checks": {
        "long_digits": {"enabled": True, "min_digits": 6},
        "date_like": {"enabled": True},
        "phone_email": {"enabled": True},
        "label_names": {"enabled": True, "labels": []},      # [] = built-in label set
        "residual_chrome": {"enabled": True, "patterns": []}, # [] = built-in patterns
        "clinical_identifiers": {"enabled": True},            # NPI, DEA, UDI
    },
}

CHECK_LABELS = {
    "long_digits": "Long digit runs",
    "date_like": "Label-adjacent dates",
    "phone_email": "Phone / email",
    "label_names": "Names after labels",
    "residual_chrome": "Residual EMR chrome",
    "clinical_identifiers": "Clinical identifiers (NPI/DEA/UDI)",
}

AUDIT_CHECK_IDS = frozenset(CHECK_LABELS)

CHECK_DESCRIPTIONS = {
    "long_digits": "Standalone numbers of 6+ digits (MRNs, accession or account numbers) that survived.",
    "date_like": "DOB-style dates on lines that start with a birth-date label.",
    "phone_email": "Phone numbers or email addresses still present in the output.",
    "label_names": "Values after identity labels (Patient:, Next of Kin:, …) that are not placeholders.",
    "residual_chrome": "Known Epic noise patterns (editor, pager, version lines) the rules did not remove.",
    "clinical_identifiers": "Verified National Provider IDs (NPI), DEA numbers, or UDI medical device codes.",
}

DEFAULT_NAME_LABELS = [
    "Patient Name", "Patient", "Name", "Next of Kin",
    "Emergency Contact", "Primary Emergency Contact", "Contact Person",
]

DEFAULT_CHROME_PATTERNS = [
    r"^\s*Version \d+ of \d+\s*$",
    r"^\s*Editor\s*:.*$",
    r"^\s*Author\s*:.*$",
    r"^\s*Author Type\s*:.*$",
    r"^\s*Cosigner\s*:.*$",
    r"^\s*Cosign Required\s*:.*$",
    r"^\s*Auth\.?\s*provider\s*:.*$",
    r"^\s*Related Notes\s*:.*$",
    r"\bPager\s*:\s*v?\d+",
    r"(?i)electronically signed by:",
    r"^\s*Signed by\s*:.*$",
    r"^\s*Dictated by\s*:.*$",
    r"^\s*Filed\s*:.*$",
]

_PHONE_RE = r"(?<!\d)(?:(?:\+?1[\s.\-])|(?:\+1))?(?:\(\d{3}\)|\d{3})[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)"
_EMAIL_RE = r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
_DATE_LABEL_RE = r"(?im)^\s*(?:DOB|D\.O\.B\.|Date of Birth|Born\s*:)[^\n]{0,60}$"

_PLACEHOLDER_RE = re.compile(r"\[\s*REDACT[^\]]*\]", re.IGNORECASE)
_JUNK_VALUE_RE = re.compile(r"^[\W\d]+$")


def get_audit_config(cfg: dict) -> dict:
    """Merge the user's optional 'audit' section over the shipped defaults."""
    merged: dict = {
        "enabled": DEFAULT_AUDIT_CONFIG["enabled"],
        "max_findings": DEFAULT_AUDIT_CONFIG["max_findings"],
        "checks": {},
    }
    for cid, cdflt in DEFAULT_AUDIT_CONFIG["checks"].items():
        merged["checks"][cid] = dict(cdflt)
    user = cfg.get("audit")
    if isinstance(user, dict):
        if isinstance(user.get("enabled"), bool):
            merged["enabled"] = user["enabled"]
        if isinstance(user.get("max_findings"), int) and user["max_findings"] > 0:
            merged["max_findings"] = user["max_findings"]
        checks = user.get("checks")
        if isinstance(checks, dict):
            for cid, ucfg in checks.items():
                if cid in merged["checks"] and isinstance(ucfg, dict):
                    # merge everything: optional keys like `patterns` / `labels`
                    # are not part of the defaults but must survive the merge
                    merged["checks"][cid].update(ucfg)
    return merged


def ensure_audit_section(cfg: dict) -> dict:
    """Return cfg['audit'], populated with defaults so a UI can edit it in place."""
    section = cfg.get("audit")
    if not isinstance(section, dict):
        section = {}
    section.setdefault("enabled", DEFAULT_AUDIT_CONFIG["enabled"])
    section.setdefault("max_findings", DEFAULT_AUDIT_CONFIG["max_findings"])
    checks = section.setdefault("checks", {})
    for cid, cdflt in DEFAULT_AUDIT_CONFIG["checks"].items():
        cur = checks.setdefault(cid, {})
        if not isinstance(cur, dict):
            checks[cid] = dict(cdflt)
            continue
        for k, v in cdflt.items():
            cur.setdefault(k, v)
    cfg["audit"] = section
    return section


# ---------------------------------------------------------------------------
# findings
# ---------------------------------------------------------------------------

@dataclass
class Finding:
    check: str            # check id, e.g. "long_digits"
    line: int             # 1-based line number in the audited text
    excerpt: str          # the flagged text, truncated for display
    signature: str        # normalized id used for cross-run suggestions
    suggested_regex: str = ""
    suggested_replacement: str | None = None
    suggested_stage: str = "phi_patterns"

    def to_dict(self) -> dict:
        return {
            "check": self.check, "line": self.line, "excerpt": self.excerpt,
            "signature": self.signature, "suggested_regex": self.suggested_regex,
            "suggested_replacement": self.suggested_replacement,
            "suggested_stage": self.suggested_stage,
        }


@dataclass
class AuditResult:
    findings: list[Finding] = field(default_factory=list)
    counts: dict = field(default_factory=dict)   # check id -> total matches (pre-cap)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False                        # audit disabled for this run

    @property
    def total(self) -> int:
        return sum(self.counts.values())


# ---------------------------------------------------------------------------
# check finders: fn(lines) -> list[Finding]
# ---------------------------------------------------------------------------

def _mk_excerpt(s: str, limit: int = 120) -> str:
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _find_long_digits(lines: list[str], opts: dict) -> list[Finding]:
    min_d = int(opts.get("min_digits", 6) or 6)
    pat = re.compile(rf"\b\d{{{min_d},}}\b")
    out: list[Finding] = []
    for i, line in enumerate(lines, 1):
        for m in pat.finditer(line):
            out.append(Finding(
                check="long_digits", line=i, excerpt=_mk_excerpt(m.group(0)),
                signature="long_digits",
                suggested_regex=rf"\b\d{{{min_d},}}\b",
                suggested_replacement="[REDACTED_NUMBER]",
                suggested_stage="phi_patterns",
            ))
    return out


def _find_date_like(lines: list[str], opts: dict) -> list[Finding]:
    pat = re.compile(_DATE_LABEL_RE)
    out: list[Finding] = []
    for i, line in enumerate(lines, 1):
        m = pat.search(line)
        if m and re.search(r"\d", line):
            out.append(Finding(
                check="date_like", line=i, excerpt=_mk_excerpt(line),
                signature="date_like",
                suggested_regex=r"(?im)^\s*(?:DOB|D\.O\.B\.|Date of Birth|Born\s*:)[^\n]*$",
                suggested_replacement="[REDACTED_DOB_LINE]",
                suggested_stage="phi_patterns",
            ))
    return out


def _find_phone_email(lines: list[str], opts: dict) -> list[Finding]:
    phone = re.compile(_PHONE_RE)
    email = re.compile(_EMAIL_RE)
    out: list[Finding] = []
    for i, line in enumerate(lines, 1):
        for m in phone.finditer(line):
            out.append(Finding(
                check="phone_email", line=i, excerpt=_mk_excerpt(m.group(0)),
                signature="phone_email:phone",
                suggested_regex=_PHONE_RE, suggested_replacement="[REDACTED_PHONE]",
                suggested_stage="phi_patterns",
            ))
        for m in email.finditer(line):
            out.append(Finding(
                check="phone_email", line=i, excerpt=_mk_excerpt(m.group(0)),
                signature="phone_email:email",
                suggested_regex=_EMAIL_RE, suggested_replacement="[REDACTED_EMAIL]",
                suggested_stage="phi_patterns",
            ))
    return out


def _find_label_names(lines: list[str], opts: dict) -> list[Finding]:
    labels = opts.get("labels")
    if not isinstance(labels, list) or not labels:
        labels = DEFAULT_NAME_LABELS
    alt = "|".join(re.escape(str(lb)) for lb in labels)
    pat = re.compile(rf"(?im)^\s*({alt})\s*[:#]\s*(.+?)\s*$")
    out: list[Finding] = []
    for i, line in enumerate(lines, 1):
        m = pat.search(line)
        if not m:
            continue
        label, value = m.group(1), m.group(2)
        if _PLACEHOLDER_RE.search(value) or _JUNK_VALUE_RE.match(value):
            continue
        if value.strip().lower() in {"unknown", "none", "n/a", "na", "see above", "—", "-"}:
            continue
        out.append(Finding(
            check="label_names", line=i, excerpt=_mk_excerpt(line),
            signature=f"label_names:{label.strip().lower()}",
            suggested_regex=rf"(?im)^\s*{re.escape(label.strip())}\s*[:#][^\n]*$",
            suggested_replacement="[REDACTED_NAME_LINE]",
            suggested_stage="phi_patterns",
        ))
    return out


def _line_shape(line: str) -> str:
    shape = re.sub(r"\d+", "9", line.strip().lower())
    shape = re.sub(r"\s+", " ", shape)
    return shape[:48]


def _find_residual_chrome(lines: list[str], opts: dict) -> list[Finding]:
    patterns = opts.get("patterns")
    if not isinstance(patterns, list) or not patterns:
        patterns = DEFAULT_CHROME_PATTERNS
    compiled: list[tuple[re.Pattern, str]] = []
    for p in patterns:
        if not isinstance(p, str) or not p:
            continue
        flags = re.IGNORECASE | re.MULTILINE
        if "(?i)" in p[:6] or "(?im)" in p[:8]:
            flags = re.MULTILINE
        compiled.append((re.compile(p, flags), p))
    out: list[Finding] = []
    for i, line in enumerate(lines, 1):
        for rx, pat in compiled:
            if rx.search(line):
                out.append(Finding(
                    check="residual_chrome", line=i, excerpt=_mk_excerpt(line),
                    signature=f"residual_chrome:{_line_shape(line)}",
                    suggested_regex=_chrome_rule_for(line),
                    suggested_replacement=None,
                    suggested_stage="emr_line_metadata",
                ))
                break
    return out


def _chrome_rule_for(line: str) -> str:
    """Draft a whole-line deletion regex from one surviving chrome line."""
    body = line.strip()
    body = re.sub(r"\d+", r"\\d+", body)          # digit runs -> \d+ (pre-escape form)
    body = re.escape(body).replace(r"\\d\+", r"\d+")  # restore \d+ after escaping
    return rf"(?i)^\s*{body}\s*$"


def _find_clinical_identifiers(lines: list[str], opts: dict) -> list[Finding]:
    from .clinical_identifiers import scan_clinical_identifiers
    text = "\n".join(lines)
    entities = scan_clinical_identifiers(text)
    out: list[Finding] = []

    line_starts = [0]
    for m in re.finditer(r"\n", text):
        line_starts.append(m.end())

    import bisect

    for ent in entities:
        line_idx = bisect.bisect_right(line_starts, ent.start)
        out.append(
            Finding(
                check="clinical_identifiers",
                line=line_idx,
                excerpt=_mk_excerpt(f"{ent.entity_type}: {ent.value}"),
                signature=f"clinical_identifier:{ent.entity_type.lower()}",
                suggested_regex=rf"\b{re.escape(ent.value)}\b",
                suggested_replacement=f"[REDACTED_{ent.entity_type}]",
                suggested_stage="phi_patterns",
            )
        )
    return out


FINDERS = {
    "long_digits": _find_long_digits,
    "date_like": _find_date_like,
    "phone_email": _find_phone_email,
    "label_names": _find_label_names,
    "residual_chrome": _find_residual_chrome,
    "clinical_identifiers": _find_clinical_identifiers,
}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run_audit(text: str, cfg: dict) -> AuditResult:
    """Scan cleaned text for leftovers. Never raises — check errors are reported."""
    result = AuditResult()
    acfg = get_audit_config(cfg)
    if not acfg.get("enabled", True) or not text or not text.strip():
        result.skipped = True
        return result

    lines = text.split("\n")
    for cid, finder in FINDERS.items():
        copts = acfg["checks"].get(cid, {})
        if not copts.get("enabled", True):
            continue
        try:
            found = finder(lines, copts)
        except Exception as e:  # one broken check must not fail the run
            result.errors.append(f"{CHECK_LABELS.get(cid, cid)}: {type(e).__name__}: {e}")
            continue
        result.counts[cid] = len(found)
        result.findings.extend(found)

    cap = int(acfg.get("max_findings", 50) or 50)
    if len(result.findings) > cap:
        result.findings = result.findings[:cap]
    return result


# ---------------------------------------------------------------------------
# signature -> concrete rule suggestion (shared by the app's suggestion cards)
# ---------------------------------------------------------------------------

def suggestion_for_signature(sig: str) -> dict:
    """Turn a stored audit signature into a draft rule for the Pipeline editor."""
    if sig == "long_digits":
        return {"title": "Standalone long digit runs",
                "detail": "Numbers of 6+ digits (MRN / accession style) keep surviving your runs.",
                "regex": r"\b\d{6,}\b", "replacement": "[REDACTED_NUMBER]",
                "stage": "phi_patterns"}
    if sig == "date_like":
        return {"title": "Birth-date lines still present",
                "detail": "DOB-labelled lines with dates keep surviving your runs.",
                "regex": r"(?im)^\s*(?:DOB|D\.O\.B\.|Date of Birth|Born\s*:)[^\n]*$",
                "replacement": "[REDACTED_DOB_LINE]", "stage": "phi_patterns"}
    if sig == "phone_email:phone":
        return {"title": "Phone numbers still present",
                "detail": "Phone-shaped numbers keep surviving your runs.",
                "regex": _PHONE_RE, "replacement": "[REDACTED_PHONE]", "stage": "phi_patterns"}
    if sig == "phone_email:email":
        return {"title": "Email addresses still present",
                "detail": "Email addresses keep surviving your runs.",
                "regex": _EMAIL_RE, "replacement": "[REDACTED_EMAIL]", "stage": "phi_patterns"}
    if sig.startswith("label_names:"):
        label = sig.split(":", 1)[1].strip()
        return {"title": f"Values after “{label}” still present",
                "detail": "Identity-labelled lines are not being redacted.",
                "regex": rf"(?im)^\s*{re.escape(label)}\s*[:#][^\n]*$",
                "replacement": "[REDACTED_NAME_LINE]", "stage": "phi_patterns"}
    if sig.startswith("residual_chrome:"):
        return {"title": "Residual Epic chrome line",
                "detail": "A known noise-line shape keeps surviving your runs.",
                "regex": "", "replacement": None, "stage": "emr_line_metadata"}
    if sig.startswith("clinical_identifier:"):
        id_type = sig.split(":", 1)[1].upper()
        return {"title": f"Surviving {id_type} identifier",
                "detail": f"Verified {id_type} medical/provider identifier detected in output.",
                "regex": "", "replacement": f"[REDACTED_{id_type}]", "stage": "phi_patterns"}
    return {"title": sig, "detail": "This finding keeps surviving your runs.",
            "regex": "", "replacement": None, "stage": "phi_patterns"}
