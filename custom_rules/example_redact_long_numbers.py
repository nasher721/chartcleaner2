"""Example custom rule: redact long ID numbers.

Any standalone number with 6+ digits (accession numbers, account numbers,
device serials, …) that survived the earlier PHI stages gets masked.
Delete this file, gut it, or write your own — see the Scripts page.
"""

LABEL = "Redact long ID numbers"
DESCRIPTION = "Masks standalone 6+ digit numbers (accession/account/serial numbers) as [REDACTED_NUMBER]."
PLACEHOLDER = False

import re

PATTERN = re.compile(r"\b\d{6,}\b")


def clean(text: str, ctx) -> str:
    new_text, n = PATTERN.subn("[REDACTED_NUMBER]", text)
    ctx.count("numbers_redacted", n)
    if n:
        ctx.log(f"Masked {n} long number(s).")
    return new_text
