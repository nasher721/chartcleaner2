"""Wrap a cleaned chart in a ready-to-paste prompt ("Copy as prompt").

Templates are ``{"name", "template", "format"}``. ``template`` may use three
placeholders: ``{chart}`` (the cleaned chart in ``format``: text, markdown or
xml), ``{delta}`` (only what changed between daily notes, or the chart when
there is a single note) and ``{date}`` (today, YYYY-MM-DD). They are replaced
literally, so braces inside a chart are never a problem. ``prompt_templates``
in config.json adds templates or replaces a built-in one with the same name.
"""

from __future__ import annotations

import re
from datetime import date as _date

__all__ = ["DEFAULT_TEMPLATES", "FORMATS", "templates", "get", "render"]

FORMATS = ("text", "markdown", "xml")

_GUARD = ("Use only what the chart says. If something needed is missing, write "
          "\"not documented\" instead of guessing. Keep abbreviations as written.")

DEFAULT_TEMPLATES: list[dict] = [
    {"name": "Progress note", "format": "xml", "template":
        "Write today's ({date}) daily progress note from the chart below.\n"
        "Sections: one-liner; interval events (use the changes since the last note); "
        "exam; pertinent data; assessment and plan by problem.\n" + _GUARD + "\n\n"
        "Changes since the last note:\n{delta}\n\nFull chart:\n{chart}"},
    {"name": "Sign-out / handoff", "format": "text", "template":
        "Write an I-PASS sign-out for the patient below: illness severity, patient summary "
        "(3 sentences), action list for tonight with if/then contingencies, situation "
        "awareness, and what the receiver should confirm back.\n" + _GUARD + "\n\n{chart}"},
    {"name": "Assessment & plan only", "format": "text", "template":
        "Write only the assessment and plan, problem by problem, most acute first. For each "
        "problem: one line of assessment, then the plan as short bullets.\n" + _GUARD +
        "\n\n{chart}"},
    {"name": "Discharge summary", "format": "markdown", "template":
        "Draft a discharge summary: admission diagnosis, discharge diagnoses, brief hospital "
        "course by problem, procedures, discharge medications with changes marked "
        "(new / changed / stopped), follow-up, and pending results.\n" + _GUARD + "\n\n{chart}"},
]


def templates(cfg: dict | None = None) -> list[dict]:
    """Built-ins, then the user's (same name replaces the built-in)."""
    out = {t["name"]: dict(t) for t in DEFAULT_TEMPLATES}
    for t in ((cfg or {}).get("prompt_templates") or []) if isinstance(cfg, dict) else []:
        if (isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"].strip()
                and isinstance(t.get("template"), str)):
            fmt = t.get("format") if t.get("format") in FORMATS else "text"
            out[t["name"].strip()] = {"name": t["name"].strip(), "template": t["template"], "format": fmt}
    return list(out.values())


def get(name: str, cfg: dict | None = None) -> dict:
    for t in templates(cfg):
        if t["name"].casefold() == name.strip().casefold():
            return t
    raise KeyError(f"Unknown prompt template: {name}")


def render(name: str, chart: str, cfg: dict | None = None, *, today: _date | None = None) -> str:
    """The prompt for ``chart`` (already cleaned) using template ``name``."""
    from .service import format_output

    template = get(name, cfg)
    body = template["template"]
    formatted = format_output(chart, template["format"])[0] if "{chart}" in body else ""
    delta = format_output(chart, "text", delta=True)[0] if "{delta}" in body else ""
    values = {"date": (today or _date.today()).isoformat(), "delta": delta, "chart": formatted}
    # One pass, so placeholder-like text inside the chart is never substituted.
    return re.sub(r"\{(chart|delta|date)\}", lambda m: values[m.group(1)], body)
