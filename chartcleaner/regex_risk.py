"""Spot regexes that can backtrack catastrophically before they are saved.

Python's ``re`` can't be interrupted, so a rule like ``(\\w+\\s?)+:`` that
takes minutes on a long line would freeze a clean. :func:`risks` reads the
parsed pattern (no matching) and reports the shapes that cause exponential or
heavy polynomial backtracking:

* **nested** — an unbounded repeat inside another unbounded repeat
  (``(a+)+``, ``(\\w+\\s)*``).
* **overlapping alternation** — a repeated group whose alternatives can match
  the same text (``(a|ab)*``, ``(\\w|\\d)+``).
* **many wildcards** — three or more ``.*`` / ``.+`` in one pattern (each
  one multiplies the work on lines that almost match).

The check is heuristic: it errs toward flagging. Measured timings come from
real runs (``details["slow_rules"]``, see :mod:`chartcleaner.stages`).
"""

from __future__ import annotations

try:  # Python 3.11+: the parser moved to a private module
    from re import _constants as _c  # type: ignore[attr-defined]
    from re import _parser as _p  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - older Pythons
    import sre_constants as _c  # type: ignore[no-redef]
    import sre_parse as _p  # type: ignore[no-redef]

__all__ = ["risks", "is_risky"]

_REPEATS = {_c.MAX_REPEAT, _c.MIN_REPEAT}
_POSSESSIVE = getattr(_c, "POSSESSIVE_REPEAT", None)
_UNBOUNDED = _c.MAXREPEAT


def _children(op, av):
    if op in _REPEATS or op == _POSSESSIVE:
        return [av[2]]
    if op == _c.SUBPATTERN:
        return [av[-1]]
    if op == _c.BRANCH:
        return list(av[1])
    if op in (_c.ASSERT, _c.ASSERT_NOT):
        return [av[1]]
    if op == getattr(_c, "ATOMIC_GROUP", None):
        return [av]
    return []


def _has_unbounded(sub) -> bool:
    for op, av in sub:
        if op in _REPEATS and av[1] == _UNBOUNDED:
            return True
        if any(_has_unbounded(ch) for ch in _children(op, av)):
            return True
    return False


def _first_chars(sub) -> set | None:
    """Rough set of what the subpattern can start with (None = anything)."""
    for op, av in sub:
        if op == _c.LITERAL:
            return {("lit", av)}
        if op == _c.ANY:
            return None
        if op == _c.IN:
            out = set()
            for iop, iav in av:
                if iop == _c.LITERAL:
                    out.add(("lit", iav))
                elif iop == _c.CATEGORY:
                    out.add(("cat", iav))
                else:
                    return None
            return out
        if op in _REPEATS:
            first = _first_chars(av[2])
            if av[0] == 0:
                return None  # optional: the next item can start too
            return first
        if op == _c.SUBPATTERN:
            return _first_chars(av[-1])
        if op == _c.BRANCH:
            out = set()
            for alt in av[1]:
                f = _first_chars(alt)
                if f is None:
                    return None
                out |= f
            return out
        if op in (_c.AT,):
            continue
        return None
    return set()


def _overlap(a: set | None, b: set | None) -> bool:
    if a is None or b is None:
        return True
    if a & b:
        return True
    cats_a = {x for x in a if x[0] == "cat"}
    cats_b = {x for x in b if x[0] == "cat"}
    # A category (\w, \d, \s) overlaps literals it can contain; be generous.
    if cats_a and b or cats_b and a:
        return True
    return False


def _branches(body) -> list | None:
    """The alternatives when ``body`` is a single (possibly grouped) alternation."""
    while len(body) == 1 and body[0][0] == _c.SUBPATTERN:
        body = list(body[0][1][-1])
    if len(body) == 1 and body[0][0] == _c.BRANCH:
        return list(body[0][1][1])
    if len(body) == 1 and body[0][0] == _c.IN:
        return None  # a character class is a single choice, not an alternation
    return None


def _has_anchor_literal(body) -> bool:
    """True when every pass through ``body`` must consume a fixed literal.

    ``(?:\\s*,\\s*[A-Z]{2})+`` repeats safely: each round needs its comma,
    so the engine can't split the same text between rounds in many ways.
    """
    for op, av in body:
        if op == _c.LITERAL:
            return True
        if op == _c.SUBPATTERN and _has_anchor_literal(av[-1]):
            return True
        if op in _REPEATS and av[0] >= 1 and av[1] != _UNBOUNDED and _has_anchor_literal(av[2]):
            return True
    return False


def _walk(sub, found: list[str]) -> None:
    for op, av in sub:
        if op in _REPEATS:
            _lo, hi, body = av
            unbounded = hi == _UNBOUNDED
            if unbounded and _has_unbounded(body) and not _has_anchor_literal(body):
                found.append("nested")
            alts = _branches(list(body)) if unbounded else None
            if alts:
                firsts = [_first_chars(a) for a in alts]
                if any(_overlap(firsts[i], firsts[j])
                       for i in range(len(firsts)) for j in range(i + 1, len(firsts))):
                    found.append("overlapping alternation")
            _walk(body, found)
        else:
            for ch in _children(op, av):
                _walk(ch, found)


def _wildcards(sub) -> int:
    n = 0
    for op, av in sub:
        if op in _REPEATS and av[1] == _UNBOUNDED and len(av[2]) == 1 and av[2][0][0] == _c.ANY:
            n += 1
        for ch in _children(op, av):
            n += _wildcards(ch)
    return n


_MESSAGES = {
    "nested": "a repeat inside a repeat (like (a+)+) can take exponential time on long lines",
    "overlapping alternation": "a repeated group whose choices overlap (like (a|ab)*) backtracks heavily",
    "many wildcards": "three or more .* / .+ in one rule get very slow on lines that almost match",
}


def risks(pattern: str, flags: int = 0) -> list[str]:
    """Human-readable backtracking risks in ``pattern`` ([] = none found)."""
    try:
        parsed = _p.parse(pattern, flags)
    except Exception:
        return []  # invalid regexes are reported by the validator
    found: list[str] = []
    _walk(list(parsed), found)
    if _wildcards(list(parsed)) >= 3 and not pattern.lstrip().startswith(("^", "\\A")):
        found.append("many wildcards")
    seen: list[str] = []
    for kind in found:
        if kind not in seen:
            seen.append(kind)
    return [_MESSAGES[k] for k in seen]


def is_risky(pattern: str, flags: int = 0) -> bool:
    return bool(risks(pattern, flags))
