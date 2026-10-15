"""Dates written in charts (``09/15/2026``, ``9/15``, ``2026-09-15``, ``Sep 15``).

Shared by the device, antibiotic and overnight extractors. A date without a
year takes the year of the chart's reference date (or the year before when
that would put it in the future). The *reference date* is the latest full
date in the chart — "today" as far as the chart knows — so day counts match
the chart rather than the computer's clock.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta

__all__ = ["DATE_RE", "find_dates", "parse_date", "reference_date", "day_number"]

_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}

DATE_RE = re.compile(
    r"(?<![\d/])(?P<iso>\d{4}-\d{2}-\d{2})(?!\d)"
    r"|(?<![\d/.])(?P<m>0?[1-9]|1[0-2])/(?P<d>0?[1-9]|[12]\d|3[01])(?:/(?P<y>\d{4}|\d{2}))?(?![\d/])"
    # Only real month spellings: "Decadron 4 mg" / "Octreotide 25" are not dates.
    r"|(?<![A-Za-z])(?P<mon>Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?"
    r"|Aug(?:ust)?|Sep(?:t(?:ember)?)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)(?![A-Za-z])"
    r"\.?\s+(?P<d2>\d{1,2})"
    r"(?:,?\s+(?P<y2>\d{4}))?(?!\d)")


def _build(y: int | None, m: int, d: int, ref: date | None) -> date | None:
    try:
        if y is not None:
            if y < 100:  # "3/4/52" is 1952, "9/15/26" is 2026
                y += 2000 if y <= date.today().year % 100 + 1 else 1900
            return date(y, m, d)
        base = ref or date.today()
        found = date(base.year, m, d)
        if found > base:
            found = date(base.year - 1, m, d)
        return found
    except ValueError:
        return None


def _from_match(m: re.Match, ref: date | None) -> tuple[date | None, bool]:
    """(date, had a year)."""
    if m.group("iso"):
        try:
            return date.fromisoformat(m.group("iso")), True
        except ValueError:
            return None, True
    if m.group("m"):
        y = int(m.group("y")) if m.group("y") else None
        return _build(y, int(m.group("m")), int(m.group("d")), ref), y is not None
    y = int(m.group("y2")) if m.group("y2") else None
    return _build(y, _MONTHS[m.group("mon")[:3].lower()], int(m.group("d2")), ref), y is not None


def find_dates(text: str, ref: date | None = None) -> list[tuple[date, int, int]]:
    """Every date in ``text``: ``(date, start, end)``."""
    out = []
    for m in DATE_RE.finditer(text):
        found, _ = _from_match(m, ref)
        if found:
            out.append((found, m.start(), m.end()))
    return out


def parse_date(raw: str, ref: date | None = None) -> date | None:
    m = DATE_RE.search(raw)
    return _from_match(m, ref)[0] if m else None


def reference_date(text: str, fallback: date | None = None) -> date:
    """The latest date that carries a year (the chart's "today").

    Dates after today's real date are skipped: a scheduled follow-up
    ("f/u 12/01/2026") is not when the chart was written.
    """
    latest_possible = datetime.now().date() + timedelta(days=1)
    full = []
    for m in DATE_RE.finditer(text):
        found, had_year = _from_match(m, None)
        if found and had_year and 1990 <= found.year and found <= latest_possible:
            full.append(found)
    if full:
        return max(full)
    return fallback or datetime.now().date()


def day_number(start: date, ref: date) -> int:
    """Day count with the start day as day 1 (inserted today = day 1)."""
    return (ref - start).days + 1
