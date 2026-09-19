"""Option coverage for the engine's per-stage knobs and the five newer stages.

Every test starts from a minimal valid config so stage behavior can be
asserted in isolation; the shipped default config is exercised by the golden
file test.
"""

import pytest

from chartcleaner.engine import clean_text, validate_config


def base(**over) -> dict:
    cfg = {
        "emr_line_metadata": [],
        "boilerplate": [],
        "epic_phi_patterns": [],
        "literal_replacements": [],
        "clinical_headers": [],
        # deterministic tests: no model-dependent NLP stage (same as golden)
        "nlp_redaction": {"enabled": False},
    }
    cfg.update(over)
    return cfg


def clean(text: str, cfg: dict) -> str:
    return clean_text(text, cfg, wrap=False).text


# ---------------------------------------------------------------------------
# defaults reproduce the original hardcoded behavior
# ---------------------------------------------------------------------------

def test_whitespace_default_matches_original():
    assert clean("a   \n\n\n\nb  \n", base()) == "a\n\nb"


def test_bullets_default_dash():
    assert clean("• one\n* two\n- three", base()) == "- one\n- two\n- three"


def test_headers_default_h2():
    assert clean("Vitals\nHR 80", base(clinical_headers=["Vitals"])) == "## Vitals\nHR 80"


# ---------------------------------------------------------------------------
# whitespace options
# ---------------------------------------------------------------------------

def test_whitespace_crlf_and_spaces_and_tabs():
    cfg = base(whitespace={"crlf_to_lf": True, "collapse_spaces": True,
                           "normalize_tabs": True, "strip_leading": True})
    assert clean("  a\tb   c\r\n  d", cfg) == "a b c\nd"


def test_whitespace_final_trim_off():
    cfg = base(whitespace={"final_trim": False})
    assert clean("x\n\n", cfg) == "x\n\n"


def test_stage_can_be_disabled_via_stage_options():
    cfg = base(stage_options={"whitespace": {"enabled": False}})
    assert clean("a   \n\n\n\n", cfg) == "a   \n\n\n\n"


# ---------------------------------------------------------------------------
# bullets options
# ---------------------------------------------------------------------------

def test_bullets_style_and_numbered_and_empty():
    cfg = base(bullets={"style": "* ", "normalize_numbered": True, "skip_empty": True})
    assert clean("• one\n1. two\n•\n", cfg) == "* one\n* two\n"


def test_bullets_extra_glyphs():
    cfg = base(bullets={"extra_glyphs": ["›"]})
    assert clean("› item", cfg) == "- item"


def test_bullets_style_strip_marker():
    cfg = base(bullets={"style": ""})
    assert clean("• item", cfg) == "item"


# ---------------------------------------------------------------------------
# header options
# ---------------------------------------------------------------------------

def test_headers_level_bold_colon():
    cfg = base(clinical_headers=["Vitals"], header_options={"level": 3, "keep_colon": True})
    assert clean("Vitals:\nx", cfg) == "### Vitals:\nx"


def test_headers_bold_style():
    cfg = base(clinical_headers=["Plan"], header_options={"bold": True})
    assert clean("Plan\ny", cfg) == "**Plan**\ny"


def test_headers_uppercase_only_skips_mixed_case():
    cfg = base(clinical_headers=["Vitals"], header_options={"uppercase_only": True})
    text = "Vitals\nHR 80\nVITALS\nBP 120"
    assert clean(text, cfg) == "Vitals\nHR 80\n## VITALS\nBP 120"


# ---------------------------------------------------------------------------
# unicode normalization
# ---------------------------------------------------------------------------

def test_unicode_quotes_dashes_ellipsis():
    cfg = base(unicode_normalize={"quotes": True, "dashes": True, "ellipsis": True})
    assert clean("\u201chere\u201d \u2014 wait\u2026", cfg) == '"here" - wait...'


def test_unicode_nbsp_zero_width_ligatures():
    cfg = base(unicode_normalize={"nbsp": True, "zero_width": True, "ligatures": True})
    assert clean("a\u00a0b\u200b\ufeffc\ufb01n", cfg) == "a bcfin"


def test_unicode_off_by_default():
    assert clean("\u201cq\u201d", base()) == "\u201cq\u201d"


# ---------------------------------------------------------------------------
# timestamp removal
# ---------------------------------------------------------------------------

def test_timestamps_disabled_by_default():
    assert clean("on 2024-03-05", base()) == "on 2024-03-05"


def test_timestamps_builtin_patterns_and_replacement():
    cfg = base(timestamp_removal={"enabled": True, "replacement": "[DATE]"})
    out = clean("a 2024-03-05 b 03/05/2024 c Mar 5, 2024", cfg)
    assert out == "a [DATE] b [DATE] c [DATE]"


def test_timestamps_clock_times_opt_in():
    cfg = base(timestamp_removal={"enabled": True, "remove_clock_times": True})
    # the pattern consumes the trailing space, avoiding a double space
    assert clean("at 08:30 sharp", cfg) == "at sharp"


def test_timestamps_extra_patterns():
    cfg = base(timestamp_removal={"enabled": True, "builtin_patterns": False,
                                  "extra_patterns": [r"\bQ\d+h\b"]})
    assert clean("rounds Q4h", cfg) == "rounds"


# ---------------------------------------------------------------------------
# section keep/drop
# ---------------------------------------------------------------------------

SECTION_TEXT = "Preamble.\nVitals\nHR 80\nLabs\nK 4.0\nImaging\nCT head"


def test_sections_drop_removes_listed():
    cfg = base(clinical_headers=["Vitals", "Labs", "Imaging"],
               section_filter={"mode": "drop", "sections": ["Labs"]})
    assert clean(SECTION_TEXT, cfg) == "Preamble.\n## Vitals\nHR 80\n## Imaging\nCT head"


def test_sections_keep_only_listed():
    cfg = base(clinical_headers=["Vitals", "Labs", "Imaging"],
               section_filter={"mode": "keep", "sections": ["Labs"]})
    out = clean(SECTION_TEXT, cfg)
    assert "K 4.0" in out and "HR 80" not in out and "CT head" not in out


def test_sections_keep_preamble_false():
    cfg = base(clinical_headers=["Vitals", "Labs", "Imaging"],
               section_filter={"mode": "drop", "sections": ["Labs"], "keep_preamble": False})
    out = clean(SECTION_TEXT, cfg)
    assert "Preamble." not in out and "K 4.0" not in out


def test_sections_boundary_headers_override():
    # A section runs until the next boundary header, so the trailing "C/three"
    # block (not a boundary) belongs to B's section and is dropped with it.
    cfg = base(section_filter={"mode": "drop", "sections": ["B"],
                               "boundary_headers": ["A", "B"], "keep_preamble": False})
    assert clean("A\none\nB\ntwo\nC\nthree", cfg) == "A\none"


def test_sections_off_by_default():
    cfg = base(clinical_headers=["Vitals"],
               stage_options={"headers": {"enabled": False}})
    assert clean(SECTION_TEXT, cfg) == SECTION_TEXT


# ---------------------------------------------------------------------------
# caps normalization
# ---------------------------------------------------------------------------

def test_caps_off_by_default():
    assert clean("ALL CAPS LINE THAT IS QUITE LONG INDEED YES", base()) == \
        "ALL CAPS LINE THAT IS QUITE LONG INDEED YES"


def test_caps_sentence_mode():
    cfg = base(caps_normalize={"mode": "sentence"})
    out = clean("THE PATIENT WAS SEEN ON ROUNDS THIS MORNING AND REMAINED NEUROLOGICALLY STABLE", cfg)
    assert out == "The patient was seen on rounds this morning and remained neurologically stable"


def test_caps_min_chars_and_preserved_acronyms():
    cfg = base(caps_normalize={"mode": "sentence", "min_chars": 10,
                               "preserve_words": ["MRI"]})
    text = "SHORT\nAN MRI OF THE BRAIN WAS PERFORMED TODAY"
    out = clean(text, cfg)
    assert out == "SHORT\nAn MRI of the brain was performed today"


# ---------------------------------------------------------------------------
# line length
# ---------------------------------------------------------------------------

def test_line_length_off_by_default():
    long = "x" * 500
    assert clean(long, base()) == long


def test_line_length_truncate():
    cfg = base(line_length={"mode": "truncate", "max_chars": 10, "marker": ">>"})
    assert clean("a" * 25, cfg) == "a" * 10 + ">>"


def test_line_length_wrap_keeps_indent():
    # whitespace's final trim would eat the leading indent first, so turn it off
    cfg = base(line_length={"mode": "wrap", "max_chars": 12},
               stage_options={"whitespace": {"enabled": False}})
    out = clean("    word word word word", cfg)
    lines = out.split("\n")
    assert all(len(line) <= 12 for line in lines)
    assert all(line.startswith("    ") for line in lines)
    assert sum(line.count("word") for line in lines) == 4


# ---------------------------------------------------------------------------
# per-stage case sensitivity
# ---------------------------------------------------------------------------

def test_regex_pairs_case_sensitive_option():
    cfg = base(literal_replacements=[[r"\bhr\b", "heart"]])
    assert clean("HR hr", cfg) == "heart heart"
    cfg2 = base(literal_replacements=[[r"\bhr\b", "heart"]],
                stage_options={"literal_replacements": {"case_sensitive": True}})
    assert clean("HR hr", cfg2) == "HR heart"


def test_regex_lines_case_sensitive_option():
    cfg = base(emr_line_metadata=[r"^signed by"],
               stage_options={"metadata_lines": {"case_sensitive": True}})
    assert clean("Signed by Dr X\nkeep", cfg) == "Signed by Dr X\nkeep"


def test_stage_options_generic_enable_toggles_any_builtin():
    cfg = base(stage_options={"bullets": {"enabled": False}})
    assert clean("• x", cfg) == "• x"


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_validate_accepts_all_new_groups():
    cfg = base(
        whitespace={"collapse_spaces": True},
        bullets={"style": "- ", "normalize_numbered": True},
        header_options={"level": 3, "bold": False},
        unicode_normalize={"quotes": True},
        timestamp_removal={"enabled": True, "extra_patterns": [r"\bQ\dh\b"]},
        section_filter={"mode": "keep", "sections": ["Vitals"]},
        caps_normalize={"mode": "sentence", "preserve_words": ["ICU"]},
        line_length={"mode": "wrap", "max_chars": 120},
        stage_options={"metadata_lines": {"case_sensitive": True}},
    )
    errors, warnings = validate_config(cfg)
    assert errors == []
    # the minimal base leaves two lists empty, which earns a known warning
    assert not [w for w in warnings if "empty list" not in w]


def test_validate_flags_bad_values():
    errors, warnings = validate_config(base(
        whitespace={"collapse_spaces": "yes"},
        section_filter={"mode": "destroy"},
        caps_normalize={"mode": "shout"},
        line_length={"mode": "squish", "max_chars": 3},
        header_options={"level": 9},
        timestamp_removal={"extra_patterns": ["(["]},
        stage_options={"bullets": {"case_sensitive": "maybe"}},
        nonsense_group={"x": 1},
    ))
    assert any("whitespace.collapse_spaces" in e for e in errors)
    assert any("section_filter.mode" in e for e in errors)
    assert any("caps_normalize.mode" in e for e in errors)
    assert any("line_length.mode" in e for e in errors)
    assert any("header_options.level" in w for w in warnings)
    assert any("timestamp_removal.extra_patterns[0]" in e for e in errors)
    assert any("case_sensitive" in e for e in errors)
    assert any("unknown config key 'nonsense_group'" in w for w in warnings)


def test_validate_flags_unknown_sub_option():
    errors, warnings = validate_config(base(whitespace={"make_pretty": True}))
    assert errors == []
    assert any("whitespace.make_pretty" in w for w in warnings)
