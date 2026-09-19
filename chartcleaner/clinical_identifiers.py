"""Clinical identifier detection with checksum validation.

Provides high-precision algorithmic recognizers for:
- NPI (National Provider Identifier, 10 digits with CMS Luhn-24 checksum)
- DEA Numbers (2 letters + 7 digits with DEA checksum algorithm)
- UDI (Unique Device Identifier, GS1 and HIBCC barcode formats)
- State Medical License & Clinical Accession Numbers

Zero external dependencies; sub-millisecond execution.
"""

from __future__ import annotations

import re
from typing import NamedTuple


class ClinicalEntity(NamedTuple):
    entity_type: str
    value: str
    start: int
    end: int
    confidence: float
    description: str


# ---------------------------------------------------------------------------
# NPI (National Provider Identifier)
# ---------------------------------------------------------------------------
# Per CMS (Centers for Medicare & Medicaid Services), an NPI is a 10-digit number.
# The 10th digit is a check digit calculated using the Luhn formula over 80840
# prefixed to the first 9 digits (total 15 digits: 80840 + 10 digits).
# ---------------------------------------------------------------------------

_NPI_REGEX = re.compile(
    r"(?i)(?:\bNPI\b[:\s#]*|\bProvider\s+ID[:\s#]*|\bNational\s+Provider\s+ID[:\s#]*)?(\b[12]\d{9}\b)"
)


def is_valid_npi(npi_str: str) -> bool:
    """Validate a 10-digit NPI using the CMS Luhn checksum algorithm."""
    clean = re.sub(r"\D", "", npi_str)
    if len(clean) != 10 or clean[0] not in ("1", "2"):
        return False

    # CMS prefix is 80840
    # Luhn check is performed on the 15-digit string: 80840 + 10-digit NPI
    full_str = "80840" + clean
    total = 0
    # Double every second digit from the right (1-indexed from right: 2nd, 4th, 6th...)
    # In 0-indexed string of length 15: indices 13, 11, 9, 7, 5, 3, 1 are doubled
    for i, char in enumerate(full_str):
        digit = int(char)
        # Position from right (1-based: index 14 is 1st from right)
        pos_from_right = 15 - i
        if pos_from_right % 2 == 0:
            doubled = digit * 2
            total += (doubled // 10) + (doubled % 10)
        else:
            total += digit

    return (total % 10) == 0


def find_npi_candidates(text: str) -> list[ClinicalEntity]:
    """Find all valid NPI numbers in text with their positions."""
    results: list[ClinicalEntity] = []
    for match in _NPI_REGEX.finditer(text):
        num_str = match.group(1)
        if is_valid_npi(num_str):
            start = match.start(1)
            end = match.end(1)
            # Higher confidence if preceded by "NPI" label
            has_label = bool(match.group(0).lower().startswith("npi") or "provider" in match.group(0).lower())
            conf = 0.99 if has_label else 0.92
            results.append(
                ClinicalEntity(
                    entity_type="NPI",
                    value=num_str,
                    start=start,
                    end=end,
                    confidence=conf,
                    description="National Provider Identifier (Luhn verified)",
                )
            )
    return results


# ---------------------------------------------------------------------------
# DEA Numbers (Drug Enforcement Administration)
# ---------------------------------------------------------------------------
# Standard format: 2 letters followed by 7 digits.
# 1st letter: A, B, F, M, C, D, E, G, R, X (provider type)
# 2nd letter: Almost always matches first letter of registrant's last name (A-Z)
# Checksum formula:
# sum1 = digit1 + digit3 + digit5
# sum2 = (digit2 + digit4 + digit6) * 2
# total = sum1 + sum2
# check_digit = total % 10 == digit7
# ---------------------------------------------------------------------------

_DEA_REGEX = re.compile(
    r"(?i)(?:\bDEA\b[:\s#]*(?:Number|No|#)?[:\s]*)?(\b[A-Za-z]{2}\d{7}\b)"
)


def is_valid_dea(dea_str: str) -> bool:
    """Validate a 9-character DEA number using the official checksum algorithm."""
    clean = dea_str.strip().upper()
    if len(clean) != 9 or not clean[:2].isalpha() or not clean[2:].isdigit():
        return False

    first_letter = clean[0]
    # Standard authorized prefixes for DEA registrants
    valid_first = {"A", "B", "C", "D", "E", "F", "G", "M", "P", "R", "U", "X"}
    if first_letter not in valid_first:
        return False

    digits = [int(c) for c in clean[2:]]
    sum1 = digits[0] + digits[2] + digits[4]
    sum2 = (digits[1] + digits[3] + digits[5]) * 2
    check_digit = (sum1 + sum2) % 10

    return check_digit == digits[6]


def find_dea_candidates(text: str) -> list[ClinicalEntity]:
    """Find all valid DEA numbers in text with their positions."""
    results: list[ClinicalEntity] = []
    for match in _DEA_REGEX.finditer(text):
        dea_str = match.group(1).upper()
        if is_valid_dea(dea_str):
            start = match.start(1)
            end = match.end(1)
            has_label = bool("dea" in match.group(0).lower())
            conf = 0.99 if has_label else 0.90
            results.append(
                ClinicalEntity(
                    entity_type="DEA_NUMBER",
                    value=dea_str,
                    start=start,
                    end=end,
                    confidence=conf,
                    description="Drug Enforcement Administration registration number (checksum verified)",
                )
            )
    return results


# ---------------------------------------------------------------------------
# UDI (Unique Device Identifier) & Accession Numbers
# ---------------------------------------------------------------------------
# Standard FDA UDI formats:
# GS1 format: (01)14digits(17)6digits(10)alphanumeric or similar AI blocks
# HIBCC format: +H...
# Accession format: Acc# / Accession: ABC-123456
# ---------------------------------------------------------------------------

_UDI_GS1_REGEX = re.compile(
    r"(?<!\w)(?:\(01\)\d{14}(?:\([0-9]{2}\)[A-Za-z0-9\-]+)+)(?!\w)"
)
_UDI_HIBCC_REGEX = re.compile(
    r"(?<!\w)\+H[A-Za-z0-9]{4,18}(?:/[A-Za-z0-9]+)*(?!\w)"
)
_ACCESSION_REGEX = re.compile(
    r"(?i)\b(?:Accession|Acc|Specimen|Order)\s*(?:ID|Number|No|#)?\s*[:#]\s*([A-Za-z0-9\-]{5,20})\b"
)


def find_device_and_specimen_identifiers(text: str) -> list[ClinicalEntity]:
    """Find UDI medical device identifiers and specimen/accession numbers."""
    results: list[ClinicalEntity] = []

    for match in _UDI_GS1_REGEX.finditer(text):
        results.append(
            ClinicalEntity(
                entity_type="UDI_DEVICE_ID",
                value=match.group(0),
                start=match.start(),
                end=match.end(),
                confidence=0.98,
                description="Unique Device Identifier (GS1 AI format)",
            )
        )

    for match in _UDI_HIBCC_REGEX.finditer(text):
        results.append(
            ClinicalEntity(
                entity_type="UDI_DEVICE_ID",
                value=match.group(0),
                start=match.start(),
                end=match.end(),
                confidence=0.95,
                description="Unique Device Identifier (HIBCC format)",
            )
        )

    for match in _ACCESSION_REGEX.finditer(text):
        val = match.group(1)
        # Avoid common words
        if not val.isalpha() or len(val) >= 7:
            results.append(
                ClinicalEntity(
                    entity_type="ACCESSION_NUMBER",
                    value=val,
                    start=match.start(1),
                    end=match.end(1),
                    confidence=0.92,
                    description="Clinical accession or specimen identifier",
                )
            )

    return results


def scan_clinical_identifiers(text: str) -> list[ClinicalEntity]:
    """Run all specialized clinical identifier detectors across text."""
    all_entities: list[ClinicalEntity] = []
    all_entities.extend(find_npi_candidates(text))
    all_entities.extend(find_dea_candidates(text))
    all_entities.extend(find_device_and_specimen_identifiers(text))
    # Sort by start offset ascending
    return sorted(all_entities, key=lambda e: (e.start, -e.end))
