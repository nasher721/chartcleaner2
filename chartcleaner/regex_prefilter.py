"""Required-literal prefilter: skip a regex rule when it cannot match.

Most of the ~170 config rules never match a given chart, yet every one of them
still scans the whole text. A rule like ``^[ \\t]*Present on Admission:.*$``
can only match where the literal ``Present on Admission:`` appears, and a
substring check (``in``) is far cheaper than a regex scan — so
:func:`may_match` answers "could this rule match this text at all?" from the
literal text the pattern *requires*, and :mod:`stage_core` skips the scan when
the answer is no.

The check is conservative: it only ever says "no" when the pattern provably
needs a literal that is absent. Anything it can't reason about (look-arounds,
character classes, back-references, LOCALE patterns, non-ASCII literals under
IGNORECASE) counts as "might match", so the rule runs exactly as before.
"""

from __future__ import annotations

import re
from functools import lru_cache

try:  # Python 3.11+
    from re import _constants as _sre_c
    from re import _parser as _sre_parse
except ImportError:  # pragma: no cover - Python 3.10
    import sre_constants as _sre_c  # type: ignore[no-redef]
    import sre_parse as _sre_parse  # type: ignore[no-redef]

__all__ = ["requirements", "may_match", "TextView"]

# One requirement is a tuple of alternatives: (literal, case_insensitive). The
# requirement holds when any alternative occurs in the text; a rule may match
# only when every requirement holds.
Requirement = tuple[tuple[str, bool], ...]

_LITERAL = _sre_c.LITERAL
_SUBPATTERN = _sre_c.SUBPATTERN
_BRANCH = _sre_c.BRANCH
_REPEATS = tuple(
    op for op in (getattr(_sre_c, n, None) for n in ("MAX_REPEAT", "MIN_REPEAT", "POSSESSIVE_REPEAT"))
    if op is not None
)
_ATOMIC = getattr(_sre_c, "ATOMIC_GROUP", None)
_I = re.IGNORECASE.value
_L = re.LOCALE.value

# Non-ASCII characters that IGNORECASE matching (or str.lower) relates to an
# ASCII letter: dotted/dotless I, long s and the Kelvin sign. If a text holds
# any of them, case-insensitive literal checks can't rule a match out.
# tests/test_regex_prefilter.py derives this set from the regex engine itself.
CASE_TROUBLE = frozenset("İıſK")

_MAX_REQUIREMENTS = 4


def _score(req: Requirement) -> int:
    """How selective a requirement is: its shortest alternative's length."""
    return min(len(lit) for lit, _ in req)


def _sequence(items, icase: bool) -> list[Requirement]:
    reqs: list[Requirement] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            reqs.append((("".join(run), icase),))
            run.clear()

    for op, av in items:
        if op is _LITERAL:
            run.append(chr(av))
            continue
        flush()
        if op is _SUBPATTERN:
            _group, add_flags, del_flags, sub = av
            inner = icase
            if add_flags & _I:
                inner = True
            if del_flags & _I:
                inner = False
            reqs.extend(_sequence(sub, inner))
        elif op in _REPEATS:
            low, _high, sub = av
            if low >= 1:
                reqs.extend(_sequence(sub, icase))
        elif op is _BRANCH:
            _, alternatives = av
            options: list[tuple[str, bool]] = []
            for alt in alternatives:
                alt_reqs = _sequence(alt, icase)
                if not alt_reqs:
                    options = []
                    break
                options.extend(max(alt_reqs, key=_score))
            if options:
                reqs.append(tuple(options))
        elif _ATOMIC is not None and op is _ATOMIC:
            reqs.extend(_sequence(av, icase))
        # everything else (classes, ANY, anchors, look-arounds, group refs)
        # requires no particular literal: the current run just ends.
    flush()
    return reqs


@lru_cache(maxsize=4096)
def requirements(pattern: str, flags: int = 0) -> tuple[Requirement, ...]:
    """Literal requirements of ``pattern`` (empty when none can be proven)."""
    try:
        parsed = _sre_parse.parse(pattern, flags)
        effective = flags | parsed.state.flags
    except Exception:
        return ()
    if effective & _L:
        return ()
    reqs = _sequence(parsed, bool(effective & _I))
    # A non-ASCII literal under IGNORECASE may match characters str.lower()
    # doesn't relate to it (µ/μ, ς/σ, …): treat such an alternative as unknown,
    # which makes its whole requirement unprovable.
    usable = [r for r in reqs if all(lit.isascii() or not ic for lit, ic in r)]
    usable.sort(key=_score, reverse=True)
    return tuple(usable[:_MAX_REQUIREMENTS])


class TextView:
    """A text plus its lazily lower-cased copy, for repeated :func:`may_match` calls."""

    __slots__ = ("text", "_lower", "_trouble")

    def __init__(self, text: str) -> None:
        self.text = text
        self._lower: str | None = None
        self._trouble: bool | None = None

    @property
    def lower(self) -> str:
        if self._lower is None:
            self._lower = self.text.lower()
        return self._lower

    @property
    def trouble(self) -> bool:
        if self._trouble is None:
            self._trouble = not self.text.isascii() and any(c in self.text for c in CASE_TROUBLE)
        return self._trouble


def may_match(pattern: str, flags: int, view: TextView | str) -> bool:
    """False only when ``pattern`` provably cannot match the text."""
    reqs = requirements(pattern, flags)
    if not reqs:
        return True
    if isinstance(view, str):
        view = TextView(view)
    for req in reqs:
        for lit, icase in req:
            if icase:
                if view.trouble or lit.lower() in view.lower:
                    break
            elif lit in view.text:
                break
        else:
            return False
    return True
