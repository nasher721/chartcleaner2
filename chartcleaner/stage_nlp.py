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
    "warm",
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

            _offline_tldextract()
            try:
                nlp_engine = SpacyNlpEngine(models=[{"lang_code": "en", "model_name": "en_core_web_sm"}])
                analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
                _disable_expensive_spacy_pipes(analyzer.nlp_engine, lean=threshold is None)
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


def _offline_tldextract() -> None:
    """Make Presidio's email recognizer use tldextract's bundled suffix list.

    By default tldextract downloads the public suffix list from the internet
    the first time it runs (here: the first clean that sees an email-shaped
    string). Behind a hospital proxy that request can hang for many seconds —
    and this app promises to stay offline. The snapshot shipped inside the
    package is used instead (plus any list already cached on disk).
    """
    try:
        import tldextract
        from tldextract import tldextract as tld_module
    except Exception:
        return
    try:
        offline = tldextract.TLDExtract(suffix_list_urls=())
    except Exception:
        return
    tld_module.TLD_EXTRACTOR = offline
    tldextract.extract = offline


# Pipes that only feed token lemmas. en_core_web_sm's NER has its own
# embedding layer (it doesn't listen to the shared tok2vec), so its entities
# are identical without them.
_LEMMA_PIPES = ("tok2vec", "tagger", "attribute_ruler", "lemmatizer")
_LOWER_LEMMA = "chartcleaner_lower_lemma"


def _register_lower_lemma() -> None:
    from spacy.language import Language

    if not Language.has_factory(_LOWER_LEMMA):
        @Language.component(_LOWER_LEMMA)
        def _lower_lemma(doc):
            for token in doc:
                token.lemma_ = token.lower_
            return doc


def _disable_expensive_spacy_pipes(nlp_engine: Any, lean: bool = False) -> None:
    """Drop spaCy pipes the redaction stage doesn't need.

    The stage consumes PERSON/PHONE_NUMBER/EMAIL entities and token lemmas
    (Presidio's context enhancement); none of them depend on the parser, and
    skipping it cuts spaCy annotation time by ~25% on long charts.

    ``lean`` (no ``score_threshold``) also drops the tagger chain that only
    produces lemmas — about 3/4 of the remaining spaCy time. Lemmas then
    become the lowercased token: context words still match in the form they
    are written ("Phone:", "cell"), only inflected ones ("called") stop
    boosting. With no threshold a boost never decides whether something is
    redacted, so the redacted spans are unchanged; with a threshold set the
    full pipeline stays.
    """
    try:
        for nlp in (getattr(nlp_engine, "nlp", None) or {}).values():
            if "parser" in nlp.pipe_names:
                nlp.disable_pipe("parser")
            if lean and "ner" in nlp.pipe_names:
                _register_lower_lemma()
                for name in _LEMMA_PIPES:
                    if name in nlp.pipe_names:
                        nlp.disable_pipe(name)
                if _LOWER_LEMMA not in nlp.pipe_names:
                    nlp.add_pipe(_LOWER_LEMMA, last=True)
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


def _engines(cfg: dict):
    """``(analyzer, anonymizer, entities, allow, threshold)`` for this config.

    The one place the engine cache key is built: the startup warm-up and the
    Doctor page must ask for exactly what a clean asks for, or the next clean
    rebuilds the engines (seconds of model loading).
    """
    ncfg = cfg.get("nlp_redaction") or {}
    entities: dict[str, str] = dict(ncfg.get("entities") or DEFAULT_NLP_ENTITIES)
    allow = frozenset(t.lower() for t in (cfg.get("nlp_allow_list") or []))
    threshold = ncfg.get("score_threshold")
    analyzer, anonymizer = _PRESIDIO_CACHE.get_engines(
        tuple(sorted(entities.items())), allow, threshold, _phone_regions(ncfg)
    )
    return analyzer, anonymizer, entities, allow, threshold


def warm(cfg: dict, *, analyze: bool = True) -> None:
    """Load the engines ``run_nlp`` will use for ``cfg``; ``analyze`` also runs
    one tiny analysis so spaCy's first-call setup isn't paid by the first clean."""
    analyzer, _anonymizer, entities, _allow, _threshold = _engines(cfg)
    if analyze:
        analyzer.analyze(text="Seen by Dr. John Smith; call 617-555-0123 or js@example.org.",
                         entities=list(entities), language="en")


def run_nlp(text: str, cfg: dict, ctx: CleanContext) -> tuple[str, int, dict]:
    """Redact PHI entities using Presidio Analyzer and Anonymizer."""
    from presidio_anonymizer.entities import OperatorConfig

    ncfg = cfg.get("nlp_redaction") or {}
    analyzer, anonymizer, entities, allow, threshold = _engines(cfg)

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
