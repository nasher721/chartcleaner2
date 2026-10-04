"""Reuse a stage's output when neither its input nor its own settings changed.

Changing one late option (bullets, line length…) used to re-run every stage,
including the slow NLP redaction. A :class:`StageCache` handed to
``Pipeline.run(cache=...)`` keys each builtin stage on

* the exact text the stage receives (so any upstream change misses), and
* a fingerprint of the config *minus* the option groups other stages own
  exclusively (``OWNED_KEYS``) and keys no stage reads (``IGNORED_KEYS``).

So editing the bullets group leaves the fingerprint of every other stage
untouched, while a change to a shared key (``clinical_headers``,
``epic_phi_patterns``…) invalidates every stage. Custom script stages are never
cached (they can read files or anything else). Entries are in memory only and
hold chart text; nothing is written to disk.
"""

from __future__ import annotations

import copy
import hashlib
import json
import threading
from collections import OrderedDict
from typing import Any

__all__ = ["OWNED_KEYS", "IGNORED_KEYS", "StageCache", "fingerprint"]

# stage id -> top-level config keys only that stage reads (verified by grep over
# stages.py, compactors/ and abbreviations.py). A key read by two stages must
# NOT be listed here: listing it would let one stage miss the other's change.
OWNED_KEYS: dict[str, tuple[str, ...]] = {
    "metadata_lines": ("emr_line_metadata",),
    "boilerplate": ("boilerplate",),
    "clinical_identifiers": ("clinical_identifiers",),
    "nlp_redaction": ("nlp_redaction", "nlp_allow_list"),
    "tokenize_phi": ("tokenization",),
    "literal_replacements": ("literal_replacements",),
    "learned_rules": ("learned_rules",),
    "unicode_normalize": ("unicode_normalize",),
    "hospital_day": ("hospital_day",),
    "timestamps": ("timestamp_removal",),
    "sections": ("section_filter",),
    "imaging_impression": ("imaging_impression",),
    "lab_compaction": ("lab_compaction",),
    "med_normalize": ("med_normalize",),
    "vitals_summary": ("vitals_summary",),
    "neuro_summary": ("neuro_summary",),
    "whitespace": ("whitespace",),
    "duplicate_notes": ("duplicate_note_detection",),
    "fuzzy_dedup": ("fuzzy_dedup",),
    "headers": ("header_options", "headers_engine"),
    "caps_normalize": ("caps_normalize",),
    "bullets": ("bullets",),
    "medical_abbreviations": ("abbreviations",),
    "line_length": ("line_length",),
}
# Read by no stage runner (wrapper, LLM, prompts, audit, fact check, ordering).
IGNORED_KEYS = frozenset({"local_llm", "prompt_templates", "audit", "fact_check",
                          "wrap_output", "wrap_tag", "stage_order", "custom_rules"})


def _digest(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def fingerprint(cfg: dict, sid: str) -> str:
    """Hash of everything in ``cfg`` stage ``sid`` could read.

    Only stages listed in ``OWNED_KEYS`` get the narrowed view; any other stage
    (e.g. the ``expand_abbreviations`` single pass, which reads the abbreviations
    group) is keyed on the whole config.
    """
    if sid not in OWNED_KEYS:
        return _digest({k: v for k, v in cfg.items() if k not in IGNORED_KEYS})
    foreign = {k for other, keys in OWNED_KEYS.items() if other != sid for k in keys}
    view = {k: v for k, v in cfg.items() if k not in foreign and k not in IGNORED_KEYS}
    opts = view.get("stage_options")
    if isinstance(opts, dict):
        view["stage_options"] = opts.get(sid)
    return _digest(view)


class StageCache:
    """Small LRU of ``(stage, input, settings) -> (output, matches, details)``."""

    def __init__(self, max_entries: int = 256, max_chars: int = 4_000_000):
        self.max_entries = max_entries
        self.max_chars = max_chars
        self._items: OrderedDict[str, tuple[str, int, dict]] = OrderedDict()
        self._chars = 0
        self.hits = 0
        self.misses = 0
        # One cache is shared process-wide (service.result_cache()) and cleans run
        # in worker threads, so every read-modify of _items happens under this lock.
        self._lock = threading.Lock()

    @staticmethod
    def key(sid: str, text: str, cfg: dict, track_changes: bool) -> str:
        h = hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()
        return f"{sid}|{int(track_changes)}|{h}|{fingerprint(cfg, sid)}"

    def get(self, key: str) -> tuple[str, int, dict] | None:
        with self._lock:
            item = self._items.get(key)
            if item is None:
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
        out, n, details = item
        return out, n, copy.deepcopy(details)

    def put(self, key: str, out: str, matches: int, details: dict) -> None:
        if len(out) > self.max_chars:
            return
        item = (out, matches, copy.deepcopy(details))
        with self._lock:
            if key in self._items:
                self._chars -= len(self._items.pop(key)[0])
            self._items[key] = item
            self._chars += len(out)
            while self._items and (len(self._items) > self.max_entries or self._chars > self.max_chars):
                _k, (old, _n, _d) = self._items.popitem(last=False)
                self._chars -= len(old)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
            self._chars = 0

    def __len__(self) -> int:
        return len(self._items)
