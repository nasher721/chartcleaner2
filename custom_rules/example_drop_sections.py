"""Example custom rule: drop whole note sections by header.

Removes everything from a section header line (below) until the next
header-like line or the end of the note. Edit SECTION_HEADERS to taste, or
duplicate this file to make a second variant.
"""

LABEL = "Drop selected sections"
DESCRIPTION = "Deletes entire sections whose header matches SECTION_HEADERS (case-insensitive)."
PLACEHOLDER = False

import re

SECTION_HEADERS = (
    "Risk Factors",
    "Patient Instructions",
    "Discharge Instructions",
    "Social Work Note",
)

# A header is a short line that (optionally) ends with a colon. A new section
# starts at the next header-like line or a "Progress Notes by ..." line.
_HEADER_LINE = re.compile(r"^[A-Z][A-Za-z0-9 ,/&'()-]{2,60}:?\s*$")
_NOTE_START = re.compile(r"^(?:Progress Notes by .+|Attestation signed by .+)$")


def _is_target(line: str) -> bool:
    stripped = line.strip().rstrip(":").strip()
    return any(stripped.lower() == h.lower() for h in SECTION_HEADERS)


def clean(text: str, ctx) -> str:
    lines = text.split("\n")
    out: list[str] = []
    dropping = False
    removed = 0

    for line in lines:
        stripped = line.strip()
        if dropping:
            if _NOTE_START.match(stripped) or (_HEADER_LINE.match(stripped) and not _is_target(line)):
                dropping = False  # next section begins; keep from here on
            else:
                removed += len(line) + 1
                continue
        if _is_target(line):
            dropping = True
            removed += len(line) + 1
            continue
        out.append(line)

    if removed:
        ctx.count("chars_removed_by_sections", removed)
        ctx.log(f"Removed {len(SECTION_HEADERS)}-matching section(s), ~{removed:,} chars.")
    return "\n".join(out)
