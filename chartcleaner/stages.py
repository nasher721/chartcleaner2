"""Built-in stage runners for the Chart Cleaner pipeline.

Each stage runner implements the contract:
    runner(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]
returning (new_text, match_count, stage_details).

The runners live in per-group modules (``stage_core``, ``stage_regex``,
``stage_cleanup``, ``stage_sections``, ``stage_dedup``, ``stage_nlp``); this
module re-exports the public names and owns the ``RUNNERS`` /
``KIND_TO_RUNNER`` dispatch tables so existing imports keep working.
"""

from __future__ import annotations

import re
from typing import Callable

from .abbreviations import MAX_TRACKED_CHANGES, abbreviate, expand
from .compactors import hospital_day as hospital_day_compactor
from .compactors import imaging as imaging_compactor
from .compactors import labs as labs_compactor
from .compactors import meds as meds_compactor
from .compactors import neuro as neuro_compactor
from .compactors import vitals as vitals_compactor
from .stage_cleanup import (
    DEFAULT_BULLETS,
    DEFAULT_CAPS,
    DEFAULT_HEADER_OPTIONS,
    DEFAULT_LINE_LENGTH,
    DEFAULT_UNICODE,
    DEFAULT_WHITESPACE,
    UNICODE_REPLACEMENTS,
    run_bullets,
    run_caps,
    run_headers,
    run_line_length,
    run_unicode,
    run_whitespace,
)
from .stage_core import (
    SLOW_RULE_MS,
    _tracking,
    get_stage_options,
    rule_id,
)
from .stage_dedup import (
    DEFAULT_FUZZY_LINE,
    DEFAULT_NOTE_SPLIT,
    fuzz_ratio,
    run_duplicate_notes,
    run_fuzzy_dedup,
    run_fuzzy_line_dedup,
)
from .stage_nlp import (
    DEFAULT_CLINICAL_IDENTIFIERS,
    DEFAULT_NLP_ENTITIES,
    DEFAULT_PHONE_REGIONS,
    NlpUnavailable,
    PresidioEngineCache,
    _PRESIDIO_CACHE,
    run_clinical_identifiers,
    run_nlp,
    run_tokenize,
    warm as warm_nlp,
)
from .stage_regex import run_learned, run_regex_list, run_regex_pairs
from .stage_sections import (
    BUILTIN_TIMESTAMP_PATTERNS,
    CLOCK_TIME_PATTERN,
    DEFAULT_SECTIONS,
    DEFAULT_TIMESTAMPS,
    run_sections,
    run_timestamps,
)

__all__ = [
    "MAX_TRACKED_CHANGES",
    "NlpUnavailable",
    "SLOW_RULE_MS",
    "BUILTIN_TIMESTAMP_PATTERNS",
    "CLOCK_TIME_PATTERN",
    "DEFAULT_BULLETS",
    "DEFAULT_CAPS",
    "DEFAULT_CLINICAL_IDENTIFIERS",
    "DEFAULT_FUZZY_LINE",
    "DEFAULT_HEADER_OPTIONS",
    "DEFAULT_LINE_LENGTH",
    "DEFAULT_NLP_ENTITIES",
    "DEFAULT_NOTE_SPLIT",
    "DEFAULT_PHONE_REGIONS",
    "DEFAULT_SECTIONS",
    "DEFAULT_TIMESTAMPS",
    "DEFAULT_UNICODE",
    "DEFAULT_WHITESPACE",
    "UNICODE_REPLACEMENTS",
    "KIND_TO_RUNNER",
    "RUNNERS",
    "_PRESIDIO_CACHE",
    "fuzz_ratio",
    "get_stage_options",
    "rule_id",
]

# ---------------------------------------------------------------------------
# Stage Dispatch Mapping
# ---------------------------------------------------------------------------

RUNNERS: dict[str, Callable] = {
    "regex_lines": lambda t, c, x: run_regex_list(t, c, x, "emr_line_metadata", re.IGNORECASE | re.MULTILINE, sid="metadata_lines"),
    "regex_lines_dotall": lambda t, c, x: run_regex_list(t, c, x, "boilerplate", re.IGNORECASE | re.DOTALL | re.MULTILINE, sid="boilerplate"),
    "regex_pairs_phi": lambda t, c, x: run_regex_pairs(t, c, x, "epic_phi_patterns", phi=True, sid="phi_patterns"),
    "clinical_identifiers": run_clinical_identifiers,
    "nlp": run_nlp,
    "tokenize": run_tokenize,
    "abbreviations": lambda t, c, x: abbreviate(t, c, changes=[] if _tracking(x) else None),
    "expand": lambda t, c, x: expand(t, c, changes=[] if _tracking(x) else None),
    "regex_pairs": lambda t, c, x: run_regex_pairs(t, c, x, "literal_replacements", sid="literal_replacements"),
    "learned": run_learned,
    "unicode": run_unicode,
    "timestamps": run_timestamps,
    "hospital_day": hospital_day_compactor.run,
    "imaging": imaging_compactor.run,
    "labs": labs_compactor.run,
    "meds": meds_compactor.run,
    "vitals": vitals_compactor.run,
    "neuro": neuro_compactor.run,
    "sections": run_sections,
    "whitespace": run_whitespace,
    "dedup_notes": run_duplicate_notes,
    "fuzzy": run_fuzzy_dedup,
    "fuzzy_line": run_fuzzy_line_dedup,
    "headers": run_headers,
    "caps": run_caps,
    "bullets": run_bullets,
    "line_length": run_line_length,
}

KIND_TO_RUNNER = {
    "metadata_lines": "regex_lines",
    "boilerplate": "regex_lines_dotall",
    "phi_patterns": "regex_pairs_phi",
    "clinical_identifiers": "clinical_identifiers",
    "nlp_redaction": "nlp",
    "tokenize_phi": "tokenize",
    "medical_abbreviations": "abbreviations",
    "literal_replacements": "regex_pairs",
    "learned_rules": "learned",
    "unicode_normalize": "unicode",
    "timestamps": "timestamps",
    "hospital_day": "hospital_day",
    "imaging_impression": "imaging",
    "lab_compaction": "labs",
    "med_normalize": "meds",
    "vitals_summary": "vitals",
    "neuro_summary": "neuro",
    "sections": "sections",
    "whitespace": "whitespace",
    "duplicate_notes": "dedup_notes",
    "fuzzy_dedup": "fuzzy",
    "fuzzy_line_dedup": "fuzzy_line",
    "headers": "headers",
    "caps_normalize": "caps",
    "bullets": "bullets",
    "line_length": "line_length",
}
