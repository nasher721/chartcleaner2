"""Property-based invariants for safety-critical pipeline behavior."""

from pathlib import Path

from hypothesis import HealthCheck, given, settings, strategies as st

from chartcleaner.engine import Pipeline, load_config


TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), max_codepoint=0x2FFF),
    min_size=0,
    max_size=500,
)
SAFE_SETTINGS = settings(max_examples=20, deadline=None,
                         suppress_health_check=(HealthCheck.too_slow,))


def config():
    cfg = load_config(Path(__file__).parents[1] / "config.json")
    cfg.setdefault("nlp_redaction", {})["enabled"] = False
    cfg.setdefault("fact_check", {})["enabled"] = False
    return cfg


@SAFE_SETTINGS
@given(TEXT)
def test_shadow_is_byte_identical_to_validation_off(text):
    cfg = config()
    legacy = Pipeline(cfg).run(text, wrap=False, validation="off", fact_check=False)
    shadow = Pipeline(cfg).run(text, wrap=False, validation="shadow", fact_check=False)
    assert shadow.text == legacy.text
    assert shadow.wrapped == legacy.wrapped


@SAFE_SETTINGS
@given(TEXT)
def test_arbitrary_unicode_has_bounded_source_provenance(text):
    result = Pipeline(config()).run(text, wrap=False, validation="enforce", fact_check=False)
    assert isinstance(result.text, str)
    assert result.ledger is not None
    assert result.source_digest
    for proposal in result.ledger.proposals:
        assert proposal.decision in ("accept", "retain_original", "withhold_release")
        for span in proposal.source_spans:
            assert 0 <= span.start <= span.end <= len(text)


@SAFE_SETTINGS
@given(st.lists(st.text(min_size=0, max_size=40), min_size=0, max_size=12))
def test_line_endings_and_large_line_shapes_never_crash(lines):
    text = "\n".join(lines)
    result = Pipeline(config()).run(text, wrap=False, validation="shadow", fact_check=False)
    assert isinstance(result.text, str)
    assert result.validation is not None
    assert result.validation.coverage.status in ("complete", "partial", "unavailable", "failed")
