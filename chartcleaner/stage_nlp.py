"""Privacy runners: Presidio NLP redaction, checksum-verified clinical
identifiers and reversible tokenization (split from ``stages.py``)."""

from __future__ import annotations

import re
import threading
from importlib.util import find_spec
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .engine import CleanContext

__all__ = [
    "NlpUnavailable",
    "DEFAULT_NLP_ENTITIES",
    "DEFAULT_PHONE_REGIONS",
    "DEFAULT_CLINICAL_IDENTIFIERS",
    "PresidioEngineCache",
    "run_nlp",
    "run_clinical_identifiers",
    "run_tokenize",
]

DEFAULT_NLP_ENTITIES = {
    "PERSON": "[REDACTED_NAME]",
    "PHONE_NUMBER": "[REDACTED_PHONE]",
    "EMAIL_ADDRESS": "[REDACTED_EMAIL]",
}

# Presidio's own PhoneRecognizer default set. Each region costs one full scan of
# the chart, so a US-only chart can set nlp_redaction.phone_regions to ["US"] to
# skip the other seven scans (see _limit_phone_regions).
DEFAULT_PHONE_REGIONS = ("US", "GB", "DE", "FR", "IL", "IN", "CA", "BR")

DEFAULT_CLINICAL_IDENTIFIERS = {
    "enabled": False,
    "replacement": None,  # None defaults to [REDACTED_{TYPE}]
    "redact_npi": True,
    "redact_dea": True,
    "redact_udi": True,
}


class NlpUnavailable(Exception):
    """Raised when the Presidio/spaCy stack is not usable on this machine."""


class PresidioEngineCache:
    """Manages cached Presidio analyzer and anonymizer engines."""

    def __init__(self):
        self._signature: tuple | None = None
        self._analyzer: Any = None
        self._anonymizer: Any = None
        self._lock = threading.Lock()

    def get_engines(self, entities_key: tuple, allow: frozenset, threshold: float | None,
                    phone_regions: tuple[str, ...] | None = None):
        sig = (entities_key, allow, threshold, phone_regions)
        if self._signature == sig:
            return self._analyzer, self._anonymizer
        with self._lock:
            if self._signature == sig:
                return self._analyzer, self._anonymizer

        try:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import SpacyNlpEngine
            from presidio_anonymizer import AnonymizerEngine
        except Exception as e:
            raise NlpUnavailable(
                f"Presidio could not be imported ({e}). Run install.sh / install.bat to add it."
            ) from e

        if find_spec("en_core_web_sm") is None:
            raise NlpUnavailable("spaCy model 'en_core_web_sm' is not installed in this environment.")

        try:
            nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
            analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
            _disable_expensive_spacy_pipes(analyzer.nlp_engine)
            if phone_regions:
                _limit_phone_regions(analyzer, phone_regions)
            anonymizer = AnonymizerEngine()
        except Exception as e:
            raise NlpUnavailable(f"Could not initialize the NLP engine ({e}).") from e

        self._signature = sig
        self._analyzer = analyzer
        self._anonymizer = anonymizer
        return self._analyzer, self._anonymizer

    def clear(self) -> None:
        self._signature = None
        self._analyzer = None
        self._anonymizer = None


def _disable_expensive_spacy_pipes(nlp_engine: Any) -> None:
    """Drop the spaCy dependency parser from the loaded model.

    The redaction stage consumes PERSON/PHONE_NUMBER/EMAIL entities and token
    lemmas (for context enhancement); none of them depend on the parser, and
    skipping it cuts spaCy annotation time by ~25% on long charts.
    """
    try:
        for nlp in (getattr(nlp_engine, "nlp", None) or {}).values():
            if "parser" in nlp.pipe_names:
                nlp.disable_pipe("parser")
    except Exception:
        pass  # fall back to the untouched full pipeline


def _phone_regions(ncfg: dict) -> tuple[str, ...] | None:
    """``nlp_redaction.phone_regions`` normalized, or None for Presidio's default.

    Every configured region adds a full scan of the chart to the phone
    recognizer, so ``["US"]`` is a large speed-up for a US-only chart. A missing
    or empty value keeps every region Presidio ships with, and a value equal to
    that default is treated as "unset" so the engine cache stays shared.
    """
    raw = ncfg.get("phone_regions")
    if not isinstance(raw, (list, tuple)):
        return None
    regions = tuple(str(r).strip().upper() for r in raw if str(r).strip())
    if not regions or regions == DEFAULT_PHONE_REGIONS:
        return None
    return regions


def _limit_phone_regions(analyzer: Any, regions: tuple[str, ...]) -> None:
    """Point Presidio's PhoneRecognizer at ``regions`` only.

    Unknown region codes are dropped; if none survive, the recognizer is left
    untouched so the stage still works.
    """
    try:
        import phonenumbers
        from presidio_analyzer.predefined_recognizers import PhoneRecognizer
    except Exception:
        return
    valid = set(phonenumbers.SUPPORTED_REGIONS)
    chosen = tuple(r for r in regions if r in valid)
    if not chosen:
        return
    for rec in getattr(getattr(analyzer, "registry", None), "recognizers", []):
        if isinstance(rec, PhoneRecognizer):
            rec.supported_regions = chosen


_PRESIDIO_CACHE = PresidioEngineCache()


_ID_LABEL = re.compile(
    r"\b(?:accession|acc|mrn|csn|fin|har|order|referral|ref|encounter|account|acct|specimen|"
    r"case|req(?:uisition)?|id)\b\s*(?:no\.?|number|num|#)?\s*[:#]?\s*\(?$", re.IGNORECASE)


def run_nlp(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Redact PHI entities using Presidio Analyzer and Anonymizer."""
    from presidio_anonymizer.entities import OperatorConfig

    ncfg = cfg.get("nlp_redaction") or {}
    entities: dict[str, str] = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
    allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
    threshold = ncfg.get("score_threshold")

    analyzer, anonymizer = _PRESIDIO_CACHE.get_engines(
        tuple(sorted(entities.items())), allow, threshold, _phone_regions(ncfg)
    )

    results = analyzer.analyze(text=text, entities=list(entities), language="en")
    if threshold is not None:
        results = [r for r in results if r.score >= threshold]

    from .clinical_whitelist import is_clinical_term, is_non_person_text

    protect_clinical = bool(ncfg.get("protect_clinical_terms", True))
    filtered = [
        r for r in results
        if text[r.start:r.end].lower() not in allow
        and not (
            protect_clinical
            and r.entity_type == "PERSON"
            and (is_clinical_term(text[r.start:r.end]) or is_non_person_text(text[r.start:r.end]))
        )
    ]

    operators = {
        etype: OperatorConfig("replace", {"new_value": repl})
        for etype, repl in entities.items()
    }
    # A labelled record number ("Accession 8847362519") is phone-shaped but isn't
    # a phone; call it an ID so the output doesn't say a CT has a phone number.
    if "PHONE_NUMBER" in entities:
        for r in filtered:
            if r.entity_type == "PHONE_NUMBER" and _ID_LABEL.search(text, max(0, r.start - 30), r.start):
                r.entity_type = "ID_NUMBER"
                operators["ID_NUMBER"] = OperatorConfig("replace", {"new_value": "[REDACTED_ID]"})
    new_text = anonymizer.anonymize(text=text, analyzer_results=filtered, operators=operators).text

    counts: dict[str, int] = {}
    for r in filtered:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    return new_text, len(filtered), {"phi": counts}


def run_clinical_identifiers(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Redact algorithmic clinical identifiers (NPI, DEA, UDI) with checksum verification."""
    options = {**DEFAULT_CLINICAL_IDENTIFIERS, **(cfg.get("clinical_identifiers") or {})}
    if not options.get("enabled"):
        return text, 0, {}

    from .clinical_identifiers import scan_clinical_identifiers

    entities = scan_clinical_identifiers(text)
    if not entities:
        return text, 0, {}

    redact_npi = bool(options.get("redact_npi", True))
    redact_dea = bool(options.get("redact_dea", True))
    redact_udi = bool(options.get("redact_udi", True))
    custom_repl = options.get("replacement")

    counts: dict[str, int] = {}
    out = text
    matched = 0

    # Sort descending by start to safely replace in-place
    for ent in sorted(entities, key=lambda e: -e.start):
        if ent.entity_type == "NPI" and not redact_npi:
            continue
        if ent.entity_type == "DEA_NUMBER" and not redact_dea:
            continue
        if ent.entity_type in ("UDI_DEVICE_ID", "ACCESSION_NUMBER") and not redact_udi:
            continue

        repl = str(custom_repl) if custom_repl is not None else f"[REDACTED_{ent.entity_type}]"
        out = out[:ent.start] + repl + out[ent.end:]
        counts[ent.entity_type] = counts.get(ent.entity_type, 0) + 1
        matched += 1

    return out, matched, {"phi": counts} if counts else {}


def run_tokenize(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Replace PHI with reversible tokens ([[Tn]]) and store token mapping."""
    from .tokens import DEFAULT_TOKEN_CONFIG, tokenize

    tcfg = {**DEFAULT_TOKEN_CONFIG, **(cfg.get("tokenization") or {})}
    labels: list[str] = []
    try:
        from .audit import DEFAULT_NAME_LABELS, get_audit_config
        labels = list(get_audit_config(cfg)["checks"]["label_names"].get("labels") or DEFAULT_NAME_LABELS)
    except Exception:
        pass

    new_text, mapping = tokenize(
        text,
        cfg.get("epic_phi_patterns") or [],
        labels=labels,
        prefix=str(tcfg.get("prefix") or "T"),
    )
    details: dict = {"tokens_issued": len(mapping)}
    if mapping:
        details["token_map"] = mapping
        details["phi"] = {"tokenized": len(mapping)}
    return new_text, len(mapping), details
