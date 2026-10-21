"""Rule contracts: what a rule is *allowed* to do, declared beside the rule.

A contract authorizes an operation, not arbitrary fact loss. It names the
sections and note types the rule may touch, whether a match may cross a
section boundary, which validation checks the operation needs, and which
specific losses are intentional (``allowed_losses``). Legacy rules have no
contract and stay usable in shadow mode, visibly unreviewed.

Identity is :func:`contract_key` — a hash of stage, pattern, replacement and
the effective regex flags — so the same pattern with a different replacement
is a different contract, while the legacy ``rule_id`` (pattern only) that
history and the UI use is untouched. Contracts live in a versioned sidecar::

    "rule_contracts": {"schema_version": 1, "contracts": {"<key>": {
        "stage_id": "boilerplate", "pattern": "...", "replacement": "", "flags": 26,
        "operation": "remove_boilerplate", "sections": ["social_history"], ...}}}

``reviewed`` is granted locally after the rule's examples pass a full-pipeline
trial; editing the rule or importing a rules file always clears it.
"""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = ["CHECKS", "DEFAULT_CHECKS", "OPERATIONS", "SCHEMA_VERSION", "STAGE_FLAGS",
           "STAGE_OPERATIONS", "ContractExample", "RuleContract", "contract_for_pair",
           "contract_key", "default_contract", "effective_flags", "load_contracts",
           "mark_unreviewed", "on_rule_edited", "remove_contract", "set_contract",
           "validate_contract_entry"]

SCHEMA_VERSION = 1
CONFIG_KEY = "rule_contracts"

OPERATIONS = ("format", "abbreviate", "remove_boilerplate", "redact", "reduce")

# Validation checks a contract may require. ``content_preserved`` is the cheap
# proof that formatting changed nothing but whitespace; the others adapt the
# clinical checks. Later phases add relationship checks under their own names.
CHECKS = ("content_preserved", "facts", "meaning", "introduced", "scope", "statements", "do_not_use")

# Per operation: the checks that must complete before an edit is accepted.
# Formatting that provably preserves content needs no clinical parsing; a
# semantic removal needs every clinical check it can get.
DEFAULT_CHECKS: dict[str, tuple[str, ...]] = {
    "format": ("content_preserved",),
    "abbreviate": ("facts", "meaning", "introduced", "statements", "do_not_use"),
    "remove_boilerplate": ("facts", "meaning", "scope", "statements", "do_not_use"),
    "redact": ("facts", "meaning", "introduced", "statements"),
    "reduce": ("facts", "meaning", "introduced", "statements", "do_not_use"),
}


@dataclass(frozen=True)
class ContractExample:
    """A text the rule must change (``kind="match"``) or must leave alone (``"keep"``)."""

    text: str
    expected: str
    kind: str = "match"


@dataclass(frozen=True)
class RuleContract:
    key: str
    operation: str
    version: int = 1
    sections: tuple[str, ...] = ()
    note_types: tuple[str, ...] = ()
    allow_cross_section: bool = False
    required_checks: tuple[str, ...] = ()
    allowed_losses: tuple[str, ...] = ()
    examples: tuple[ContractExample, ...] = ()
    reviewed: bool = False
    # An implicit contract for a rule nobody declared one for (legacy/default
    # rules, whole-candidate checks). It cannot be "reviewed", so the review gate
    # for unverified deletions does not apply to it — definite violations still do.
    legacy: bool = False

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise ValueError(f"operation must be one of {OPERATIONS}")
        unknown = set(self.required_checks) - set(CHECKS)
        if unknown:
            raise ValueError(f"unknown validation check(s): {', '.join(sorted(unknown))}")
        if not self.required_checks:
            object.__setattr__(self, "required_checks", DEFAULT_CHECKS[self.operation])
        if not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")

    @property
    def destructive(self) -> bool:
        return self.operation in ("remove_boilerplate", "reduce", "redact")

    def to_dict(self) -> dict:
        return {"key": self.key, "operation": self.operation, "version": self.version,
                "sections": list(self.sections), "note_types": list(self.note_types),
                "allow_cross_section": self.allow_cross_section,
                "required_checks": list(self.required_checks),
                "allowed_losses": list(self.allowed_losses),
                "examples": [{"text": e.text, "expected": e.expected, "kind": e.kind}
                             for e in self.examples],
                "reviewed": self.reviewed}


# The operation each builtin stage performs when no contract is declared for
# its rules. Legacy stages validate under these defaults, visibly unreviewed.
STAGE_OPERATIONS: dict[str, str] = {
    "whitespace": "format", "bullets": "format", "headers": "format", "caps_normalize": "format",
    "line_length": "format", "unicode_normalize": "format", "note_order": "format",
    "medical_abbreviations": "abbreviate", "expand_abbreviations": "abbreviate",
    "phi_patterns": "redact", "nlp_redaction": "redact", "tokenize_phi": "redact",
    "clinical_identifiers": "redact",
    "metadata_lines": "remove_boilerplate", "boilerplate": "remove_boilerplate",
    "learned_rules": "remove_boilerplate", "literal_replacements": "remove_boilerplate",
    "timestamps": "remove_boilerplate",
    "sections": "reduce", "imaging_impression": "reduce", "lab_compaction": "reduce",
    "med_normalize": "reduce", "vitals_summary": "reduce", "neuro_summary": "reduce",
    "mar_summary": "reduce", "flowsheet_tables": "reduce", "vent_summary": "reduce",
    "hospital_day": "reduce", "duplicate_notes": "reduce", "fuzzy_dedup": "reduce",
    "fuzzy_line_dedup": "reduce", "wrapper": "reduce", "candidate": "reduce",
}

# Stages whose job is dropping content: their losses are intentional and are
# disclosed as such (the facts check's ``by_design`` category), not alarms.
# ``statement:`` covers every recognized statement kind for the same reason.
_ALL_LOSSES = ("n:", "drug:", "kw:", "statement:")
_ALLOWED_LOSSES: dict[str, tuple[str, ...]] = {
    "sections": _ALL_LOSSES, "imaging_impression": _ALL_LOSSES, "lab_compaction": _ALL_LOSSES,
    "vitals_summary": _ALL_LOSSES, "neuro_summary": _ALL_LOSSES, "mar_summary": _ALL_LOSSES,
    "flowsheet_tables": _ALL_LOSSES, "vent_summary": _ALL_LOSSES,
    # a medication compactor may drop brand names, never doses
    "med_normalize": ("drug:", "kw:", "statement:finding"),
    "duplicate_notes": _ALL_LOSSES, "fuzzy_dedup": _ALL_LOSSES, "fuzzy_line_dedup": _ALL_LOSSES,
    # computed labels and the wrapper never remove statements on purpose either
    "hospital_day": ("statement:",), "note_order": ("statement:",), "wrapper": ("statement:",),
}
# Stages that rewrite dates/labels or wrap the chart: numbers they add are
# computed (hospital day), not clinical claims, so the introduced check is noise.
_CHECK_OVERRIDES: dict[str, tuple[str, ...]] = {
    "hospital_day": ("facts", "meaning"),
    "wrapper": ("facts",),
    "note_order": ("facts", "meaning"),
    # µ → u and ° removal change letters on purpose; judge the clinical content instead
    "unicode_normalize": ("facts", "meaning", "statements"),
}


def default_contract(stage_id: str, rule_key: str | None = None) -> RuleContract:
    """The implicit contract of a legacy rule: its stage's operation, unreviewed.

    Legacy rules may cross sections (today's behaviour); only an explicit
    contract turns that off by default.
    """
    operation = STAGE_OPERATIONS.get(stage_id)
    if operation is None:
        operation = "remove_boilerplate" if stage_id.startswith("custom:") else "reduce"
    return RuleContract(
        key=f"{stage_id}:{rule_key or '*'}", operation=operation,
        required_checks=_CHECK_OVERRIDES.get(stage_id, ()),
        allowed_losses=_ALLOWED_LOSSES.get(stage_id, ()),
        allow_cross_section=True,
        reviewed=False,
        legacy=True,
    )


# --- identity ------------------------------------------------------------------

# The flags each regex stage compiles with (mirrors stages.RUNNERS); the
# ``case_sensitive`` stage option clears IGNORECASE at run time.
STAGE_FLAGS: dict[str, int] = {
    "metadata_lines": re.IGNORECASE | re.MULTILINE,
    "boilerplate": re.IGNORECASE | re.DOTALL | re.MULTILINE,
    "phi_patterns": re.IGNORECASE,
    "literal_replacements": re.IGNORECASE,
    "learned_rules": re.IGNORECASE,
}
STAGE_KEYS: dict[str, str] = {
    "metadata_lines": "emr_line_metadata", "boilerplate": "boilerplate",
    "phi_patterns": "epic_phi_patterns", "literal_replacements": "literal_replacements",
    "learned_rules": "learned_rules",
}


def effective_flags(cfg: dict, stage_id: str) -> int:
    flags = STAGE_FLAGS.get(stage_id, re.IGNORECASE)
    so = cfg.get("stage_options") if isinstance(cfg, dict) else None
    opts = so.get(stage_id) if isinstance(so, dict) else None
    if isinstance(opts, dict) and opts.get("case_sensitive"):
        flags &= ~re.IGNORECASE
    return flags


def contract_key(stage_id: str, pattern: str, replacement: str, flags: int) -> str:
    """Stable identity of one rule *as it runs*: stage, pattern, replacement, flags."""
    raw = "\x00".join((stage_id, pattern, replacement, str(int(flags))))
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


# --- the sidecar ---------------------------------------------------------------

_ENTRY_FIELDS = ("stage_id", "pattern", "replacement", "flags", "operation", "version", "sections",
                 "note_types", "allow_cross_section", "required_checks", "allowed_losses",
                 "examples", "reviewed")


def _sidecar(cfg: dict) -> dict:
    group = cfg.get(CONFIG_KEY) if isinstance(cfg, dict) else None
    return group if isinstance(group, dict) else {}


def _entries(cfg: dict) -> dict[str, dict]:
    contracts = _sidecar(cfg).get("contracts")
    return contracts if isinstance(contracts, dict) else {}


def _contract_from_entry(key: str, entry: dict) -> RuleContract:
    examples = tuple(
        ContractExample(str(e.get("text", "")), str(e.get("expected", "")), str(e.get("kind", "match")))
        for e in entry.get("examples", ()) if isinstance(e, dict))
    return RuleContract(
        key=key, operation=str(entry.get("operation", "remove_boilerplate")),
        version=int(entry.get("version", 1)),
        sections=tuple(str(s) for s in entry.get("sections", ())),
        note_types=tuple(str(s) for s in entry.get("note_types", ())),
        allow_cross_section=bool(entry.get("allow_cross_section", False)),
        required_checks=tuple(str(c) for c in entry.get("required_checks", ())),
        allowed_losses=tuple(str(c) for c in entry.get("allowed_losses", ())),
        examples=examples, reviewed=bool(entry.get("reviewed", False)))


def load_contracts(cfg: dict) -> dict[str, RuleContract]:
    """``{key: RuleContract}`` from the sidecar; malformed entries are skipped (the
    validator reports them)."""
    out: dict[str, RuleContract] = {}
    for key, entry in _entries(cfg).items():
        if not isinstance(key, str) or not isinstance(entry, dict):
            continue
        try:
            out[key] = _contract_from_entry(key, entry)
        except (ValueError, TypeError):
            continue
    return out


def _entry_for(stage_id: str, pattern: str, replacement: str, flags: int, contract: RuleContract) -> dict:
    data = contract.to_dict()
    data.pop("key", None)
    return {"stage_id": stage_id, "pattern": pattern, "replacement": replacement, "flags": int(flags),
            **data}


def set_contract(cfg: dict, stage_id: str, pattern: str, replacement: str, flags: int | None = None,
                 **fields: Any) -> dict:
    """Copy of ``cfg`` with a contract for the rule saved (replacing any older one).

    ``flags=None`` uses the stage's effective flags under ``cfg``. ``fields`` are
    :class:`RuleContract` fields (``operation`` is required).
    """
    if flags is None:
        flags = effective_flags(cfg, stage_id)
    key = contract_key(stage_id, pattern, replacement, flags)
    contract = RuleContract(key=key, **fields)
    out = dict(cfg)
    group = copy.deepcopy(_sidecar(cfg)) or {"schema_version": SCHEMA_VERSION, "contracts": {}}
    group.setdefault("schema_version", SCHEMA_VERSION)
    contracts = dict(group.get("contracts") or {})
    contracts[key] = _entry_for(stage_id, pattern, replacement, flags, contract)
    group["contracts"] = contracts
    out[CONFIG_KEY] = group
    return out


def remove_contract(cfg: dict, key: str) -> dict:
    out = dict(cfg)
    group = copy.deepcopy(_sidecar(cfg))
    contracts = dict(group.get("contracts") or {})
    contracts.pop(key, None)
    if contracts:
        group["contracts"] = contracts
        out[CONFIG_KEY] = group
    else:
        out.pop(CONFIG_KEY, None)
    return out


def mark_unreviewed(cfg: dict, keys: list[str] | None = None) -> dict:
    """Copy of ``cfg`` with ``reviewed`` cleared on the given (default: all) contracts."""
    entries = _entries(cfg)
    if not entries:
        return cfg
    out = dict(cfg)
    group = copy.deepcopy(_sidecar(cfg))
    for key, entry in group.get("contracts", {}).items():
        if (keys is None or key in keys) and isinstance(entry, dict):
            entry["reviewed"] = False
    out[CONFIG_KEY] = group
    return out


def contract_for_pair(cfg: dict, stage_id: str, pair: list[str] | tuple[str, str]) -> RuleContract | None:
    key = contract_key(stage_id, pair[0], pair[1], effective_flags(cfg, stage_id))
    return load_contracts(cfg).get(key)


def on_rule_edited(cfg: dict, stage_id: str, old_pair: list[str] | tuple[str, str],
                   new_pair: list[str] | tuple[str, str]) -> dict:
    """Carry a contract over to an edited rule — unreviewed, since the rule changed."""
    flags = effective_flags(cfg, stage_id)
    old_key = contract_key(stage_id, old_pair[0], old_pair[1], flags)
    entry = _entries(cfg).get(old_key)
    if not isinstance(entry, dict) or list(old_pair) == list(new_pair):
        return cfg
    out = remove_contract(cfg, old_key)
    fields = {k: v for k, v in entry.items() if k not in ("stage_id", "pattern", "replacement", "flags")}
    fields["reviewed"] = False
    fields["examples"] = tuple(ContractExample(str(e.get("text", "")), str(e.get("expected", "")),
                                               str(e.get("kind", "match")))
                               for e in fields.get("examples", ()) if isinstance(e, dict))
    fields["sections"] = tuple(fields.get("sections", ()))
    fields["note_types"] = tuple(fields.get("note_types", ()))
    fields["required_checks"] = tuple(fields.get("required_checks", ()))
    fields["allowed_losses"] = tuple(fields.get("allowed_losses", ()))
    return set_contract(out, stage_id, new_pair[0], new_pair[1], flags, **fields)


def validate_contract_entry(key: str, entry: Any) -> list[str]:
    """Human-readable problems with one sidecar entry (empty when it is fine)."""
    label = f"rule_contracts.contracts[{key!r}]"
    if not isinstance(entry, dict):
        return [f"{label}: must be an object"]
    problems: list[str] = []
    if entry.get("operation") not in OPERATIONS:
        problems.append(f"{label}.operation: must be one of " + ", ".join(f'"{o}"' for o in OPERATIONS))
    for name in ("sections", "note_types", "required_checks", "allowed_losses"):
        value = entry.get(name, [])
        if not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            problems.append(f"{label}.{name}: must be a list of strings")
    checks = entry.get("required_checks", [])
    if isinstance(checks, list):
        unknown = [c for c in checks if isinstance(c, str) and c not in CHECKS]
        if unknown:
            problems.append(f"{label}.required_checks: unknown check(s) {', '.join(unknown)}")
    for name in ("allow_cross_section", "reviewed"):
        if name in entry and not isinstance(entry[name], bool):
            problems.append(f"{label}.{name}: must be true/false")
    version = entry.get("version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        problems.append(f"{label}.version: must be a positive integer")
    flags = entry.get("flags", 0)
    if not isinstance(flags, int) or isinstance(flags, bool) or flags < 0:
        problems.append(f"{label}.flags: must be a non-negative integer")
    for name in ("stage_id", "pattern"):
        if not isinstance(entry.get(name), str) or not entry.get(name):
            problems.append(f"{label}.{name}: must be non-empty text")
    if "replacement" in entry and not isinstance(entry["replacement"], str):
        problems.append(f"{label}.replacement: must be text")
    examples = entry.get("examples", [])
    if not isinstance(examples, list):
        problems.append(f"{label}.examples: must be a list")
    else:
        for i, ex in enumerate(examples):
            if (not isinstance(ex, dict) or not isinstance(ex.get("text"), str)
                    or not isinstance(ex.get("expected"), str)
                    or ex.get("kind", "match") not in ("match", "keep")):
                problems.append(f"{label}.examples[{i}]: must be {{text, expected, kind: match|keep}}")
    if not problems and all(isinstance(entry.get(n), str) for n in ("stage_id", "pattern")):
        expected = contract_key(entry["stage_id"], entry["pattern"], entry.get("replacement", ""),
                                int(flags))
        if expected != key:
            problems.append(f"{label}: key does not match its stage/pattern/replacement/flags "
                            f"(expected {expected})")
    return problems
