"""Label dates with the hospital day and post-operative day (stage ``hospital_day``).

``admit_date`` is ``"auto"`` (the earliest "Admission Date:" / "Admit Date:" /
"Date of Admission:" / "Admitted:" value in the chart) or an explicit
YYYY-MM-DD. Hospital day 1 is the admission day; POD#0 is the day of surgery
(the latest of ``surgery_dates`` on or before the date). Style ``append``
gives "10/02/2026 (HD#3, POD#1)", ``replace`` gives "HD#3 (POD#1)". Dates
before admission are left alone. US month/day order is assumed.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

DEFAULTS: dict[str, Any] = {"enabled": False, "admit_date": "auto", "surgery_dates": [],
                            "style": "append"}

_DATE = re.compile(
    r"(?<![\d/.-])(?:(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})"
    r"|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4}|\d{2}))(?![\d/])"
    r"(?![ \t]*\((?:HD|POD)#)")  # already labelled
_ADMIT = re.compile(r"(?:Admission\s+Date|Admit\s+Date|Date\s+of\s+Admission|Admitted(?:\s+on)?)"
                    r"\s*[:\-]?\s*", re.IGNORECASE)


def _to_date(match: re.Match) -> date | None:
    try:
        if match.group("y"):
            return date(int(match.group("y")), int(match.group("m")), int(match.group("d")))
        year = int(match.group("y2"))
        year += 2000 if year < 100 else 0
        return date(year, int(match.group("m2")), int(match.group("d2")))
    except ValueError:
        return None


def _parse_iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def find_admit_date(text: str) -> date | None:
    found = []
    for m in _ADMIT.finditer(text):
        d = _DATE.match(text, m.end())
        if d and (parsed := _to_date(d)):
            found.append(parsed)
    return min(found) if found else None


def run(text: str, cfg: dict, ctx: Any = None) -> tuple[str, int, dict]:
    options = {**DEFAULTS, **(cfg.get("hospital_day") or {})}
    if not options.get("enabled"):
        return text, 0, {}
    admit_opt = str(options.get("admit_date") or "auto")
    admit = find_admit_date(text) if admit_opt == "auto" else _parse_iso(admit_opt)
    if admit is None:
        return text, 0, {"note": "No admission date found; set one in the stage options."}
    surgeries = sorted(d for d in (_parse_iso(s) for s in options.get("surgery_dates") or []) if d)
    replace = options.get("style") == "replace"
    labelled = 0

    def label(match: re.Match) -> str:
        nonlocal labelled
        when = _to_date(match)
        if when is None or when < admit:
            return match.group(0)
        tags = [f"HD#{(when - admit).days + 1}"]
        prior = [s for s in surgeries if s <= when]
        if prior:
            tags.append(f"POD#{(when - prior[-1]).days}")
        labelled += 1
        if replace:
            return tags[0] + (f" ({tags[1]})" if len(tags) > 1 else "")
        return f"{match.group(0)} ({', '.join(tags)})"

    out = _DATE.sub(label, text)
    return out, labelled, {"admit_date": admit.isoformat()}
