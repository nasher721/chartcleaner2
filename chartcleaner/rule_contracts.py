"""Rule contracts: what a rule is *allowed* to do, declared beside the rule.

A contract authorizes an operation, not arbitrary fact loss. It names the
sections and note types the rule may touch, whether a match may cross a
section boundary, which validation checks the operation needs, and which
specific losses are intentional (``allowed_losses``). Legacy rules have no
contract and stay usable in shadow mode, visibly unreviewed.

This module holds the shared record (Task 2); loading, versioned identity and
the contract sidecar in configuration are added by the rule-contract task.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = ["CHECKS", "DEFAULT_CHECKS", "OPERATIONS", "ContractExample", "RuleContract"]

OPERATIONS = ("format", "abbreviate", "remove_boilerplate", "redact", "reduce")

# Validation checks a contract may require. ``content_preserved`` is the cheap
# proof that formatting changed nothing but whitespace; the others adapt the
# clinical checks. Later phases add relationship checks under their own names.
CHECKS = ("content_preserved", "facts", "meaning", "introduced", "scope", "statements")

# Per operation: the checks that must complete before an edit is accepted.
# Formatting that provably preserves content needs no clinical parsing; a
# semantic removal needs every clinical check it can get.
DEFAULT_CHECKS: dict[str, tuple[str, ...]] = {
    "format": ("content_preserved",),
    "abbreviate": ("facts", "meaning", "introduced"),
    "remove_boilerplate": ("facts", "meaning", "scope"),
    "redact": ("facts", "meaning", "introduced"),
    "reduce": ("facts", "meaning", "introduced"),
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
