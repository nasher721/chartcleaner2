"""Source-linked edit provenance (``edit_provenance``): every proposed edit
knows the original source characters it touches, even after earlier edits
changed the text's length, and a rejected proposal never moves anything."""

from __future__ import annotations

import pytest

from chartcleaner import stage_core
from chartcleaner.edit_provenance import EditLedger, EditProposal, SourceSpan
from chartcleaner.engine import Pipeline

SOURCE = "Na 141 mmol/L and K 4.0 mmol/L"


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [],
           "nlp_redaction": {"enabled": False}, "wrap_output": False,
           "duplicate_note_detection": {"enabled": False}, "fuzzy_dedup": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}}}
    cfg.update(over)
    return cfg


# --- the four plan sentinels --------------------------------------------------

def test_two_edits_keep_original_offsets():
    ledger = EditLedger(SOURCE)
    first = ledger.propose("lab_compaction", "r1", 0, len("Na 141 mmol/L"), "Na 141")
    ledger.resolve(first, "accept")
    assert ledger.current == "Na 141 and K 4.0 mmol/L"
    start = ledger.current.index("K 4.0 mmol/L")
    second = ledger.propose("lab_compaction", "r1", start, start + len("K 4.0 mmol/L"), "K 4.0")
    # The second edit is 7 characters further left in the current text, yet it
    # still points at the exact original substring.
    assert second.source_spans == (SourceSpan(18, 30),)
    assert SOURCE[18:30] == "K 4.0 mmol/L" == second.before
    assert second.mapping == "exact"
    assert first.source_spans == (SourceSpan(0, 13),)
    assert ledger.source == SOURCE, "the source never changes"
    ledger.resolve(second, "accept")
    assert ledger.current == "Na 141 and K 4.0"


def test_rejected_edit_does_not_shift_next_edit():
    ledger = EditLedger(SOURCE)
    bad = ledger.propose("boilerplate", "kill", 0, 13, "")
    ledger.resolve(bad, "retain_original")
    assert ledger.current == SOURCE
    assert bad.decision == "retain_original"
    nxt = ledger.propose("lab_compaction", "r1", 18, 30, "K 4.0")
    assert nxt.source_spans == (SourceSpan(18, 30),)
    assert nxt.before == "K 4.0 mmol/L"
    ledger.resolve(nxt, "accept")
    assert ledger.current == "Na 141 mmol/L and K 4.0"
    assert [p.decision for p in ledger.proposals] == ["retain_original", "accept"]


def test_unicode_crlf_offsets_use_python_character_indices():
    source = "Temp 38.2 °C\r\nNa 141\r\nK 4.0 µmol/L\r\n"
    ledger = EditLedger(source)
    start = source.index("Na 141")
    p = ledger.propose("whitespace", None, start, start + 6, "Na 141")
    assert p.source_spans == (SourceSpan(start, start + 6),)
    assert source[p.source_spans[0].start:p.source_spans[0].end] == "Na 141"
    # CRLF is two characters; the degree sign and µ are one each (not UTF-8 bytes).
    crlf = source.index("\r\n")
    q = ledger.propose("whitespace", None, crlf, crlf + 2, "\n")
    assert q.before == "\r\n" and q.source_spans == (SourceSpan(crlf, crlf + 2),)
    ledger.resolve(q, "accept")
    assert ledger.current == source.replace("\r\n", "\n", 1)
    mu = ledger.current.index("µ")
    r = ledger.propose("unicode_normalize", None, mu, mu + 1, "u")
    assert source[r.source_spans[0].start] == "µ"


def test_tracking_limit_does_not_limit_validation():
    cfg = base(literal_replacements=[["foo", "bar"]])
    text = "foo foo foo foo foo"
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(stage_core, "MAX_TRACKED_CHANGES", 1)
        result = Pipeline(cfg).run(text, track_changes=True, provenance=True)
    stat = next(s for s in result.stages if s.id == "literal_replacements")
    assert len(stat.details["changes"]) == 1, "the UI detail cap still applies to details"
    proposals = [p for p in result.ledger.proposals if p.stage_id == "literal_replacements"]
    assert len(proposals) == 5, "every match is a proposal, whatever the display cap"
    assert all(p.decision == "accept" for p in proposals)
    assert [p.source_spans for p in proposals] == [(SourceSpan(i * 4, i * 4 + 3),) for i in range(5)]
    assert result.text == "bar bar bar bar bar" == result.ledger.current


# --- mapping details ---------------------------------------------------------

def test_edit_inside_inserted_text_is_coarse_with_an_anchor():
    ledger = EditLedger("A B")
    ins = ledger.propose("s", None, 1, 2, " new words ")
    ledger.resolve(ins, "accept")
    assert ledger.current == "A new words B"
    inner = ledger.current.index("words")
    p = ledger.propose("s", None, inner, inner + 5, "text")
    assert p.mapping == "coarse"
    assert p.source_spans == (SourceSpan(1, 2),), "anchored to the source it replaced"
    assert p.before == "words"


def test_pure_insertion_records_an_anchor_not_source_text():
    ledger = EditLedger("Plan: PT")
    p = ledger.propose("headers", None, 0, 0, "ASSESSMENT\n")
    assert p.mapping == "insertion"
    assert p.source_spans == (SourceSpan(0, 0),) and p.before == ""
    ledger.resolve(p, "accept")
    assert ledger.current == "ASSESSMENT\nPlan: PT"
    q = ledger.propose("whitespace", None, 11, 15, "Plan")
    assert q.source_spans == (SourceSpan(0, 4),) and q.mapping == "exact"


def test_span_crossing_original_and_inserted_text_merges_source_ranges():
    ledger = EditLedger("one two three")
    ledger.resolve(ledger.propose("s", None, 4, 7, "2"), "accept")  # "one 2 three"
    p = ledger.propose("s", None, 0, 7, "")  # "one 2 t"
    assert p.mapping == "coarse"
    assert p.source_spans == (SourceSpan(0, 9),), "one contiguous enclosing source span"


def test_resolve_many_applies_a_batch_against_one_text():
    text = "a1 b2 c3"
    ledger = EditLedger(text)
    props = [ledger.propose("s", "digits", m.start(), m.end(), "#")
             for m in __import__("re").finditer(r"\d", text)]
    ledger.resolve_many([(props[0], "accept"), (props[1], "retain_original"), (props[2], "accept")])
    assert ledger.current == "a# b2 c#"
    assert [p.source_spans for p in props] == [(SourceSpan(1, 2),), (SourceSpan(4, 5),), (SourceSpan(7, 8),)]


def test_overlapping_batch_is_refused():
    ledger = EditLedger("abcdef")
    a = ledger.propose("s", None, 0, 3, "")
    b = ledger.propose("s", None, 2, 5, "")
    with pytest.raises(ValueError):
        ledger.resolve_many([(a, "accept"), (b, "accept")])


def test_reconcile_opaque_stage_yields_one_coarse_proposal():
    ledger = EditLedger("Labs:\nSodium 141 mmol/L\nPotassium 4.0 mmol/L\nPlan: PT\n")
    after = "Labs:\nNa 141, K 4.0\nPlan: PT\n"
    p = ledger.reconcile("lab_compaction", after)
    assert p is not None and p.mapping == "coarse" and p.rule_key is None
    assert p.before == "Sodium 141 mmol/L\nPotassium 4.0 mmol/L" and p.after == "Na 141, K 4.0"
    assert p.source_spans == (SourceSpan(6, 44),)
    assert ledger.current == after
    assert ledger.reconcile("whitespace", after) is None, "no change, no proposal"


def test_proposal_offsets_must_be_in_range():
    ledger = EditLedger("abc")
    with pytest.raises(ValueError):
        ledger.propose("s", None, 2, 5, "")
    with pytest.raises(ValueError):
        ledger.propose("s", None, 2, 1, "")


def test_ledger_summary_is_text_free():
    ledger = EditLedger("Secret chart line\nAnother line\n")
    p = ledger.propose("boilerplate", "x", 0, 17, "")
    ledger.resolve(p, "retain_original")
    summary = ledger.summary()
    assert summary["proposals"] == 1 and summary["accepted"] == 0 and summary["retain_original"] == 1
    assert "Secret" not in str(summary)
    assert isinstance(p, EditProposal) and "Secret" not in repr(p.to_summary())


def test_pipeline_without_provenance_has_no_ledger_and_same_output():
    cfg = base(literal_replacements=[["foo", "bar"]])
    plain = Pipeline(cfg).run("foo x foo", validation="off")
    traced = Pipeline(cfg).run("foo x foo", validation="off", provenance=True)
    assert plain.ledger is None and traced.ledger is not None
    assert plain.text == traced.text == "bar x bar"
    assert "ledger" not in traced.to_history_dict("test")


def test_opaque_stage_and_regex_stage_in_one_run_stay_aligned():
    cfg = base(literal_replacements=[["mmol/L", ""]], lab_compaction={"enabled": True})
    text = "Labs:\nSodium 141 mmol/L\nPotassium 4.0 mmol/L\nPlan: PT\n"
    result = Pipeline(cfg).run(text, provenance=True)
    assert result.ledger.current == result.text
    assert all(p.decision == "accept" for p in result.ledger.proposals)
    assert {p.stage_id for p in result.ledger.proposals} >= {"literal_replacements"}
