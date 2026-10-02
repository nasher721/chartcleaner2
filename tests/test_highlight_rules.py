import re

import pytest

from chartcleaner.highlight_rules import describe_rule, make_rule, replace_selection


@pytest.mark.parametrize("text,replacement", [
    ("Editor: (test)", ""), ("two\nlines", r"literal \1 and \n"),
    ("+ test?", "replacement"), ("😀 term", "new"), (" term ", " "),
])
@pytest.mark.parametrize("case_sensitive,whole_words", [(False, True), (True, False)])
def test_literal_rule_roundtrip(text, replacement, case_sensitive, whole_words):
    opts = dict(text=text, replacement=replacement, case_sensitive=case_sensitive,
                whole_words=whole_words)
    pair = make_rule(**opts)
    assert describe_rule(pair) == opts
    assert re.sub(pair[0], pair[1], text, flags=re.IGNORECASE) == replacement


def test_rule_boundaries_and_case_are_independent_of_stage_flags():
    pair = make_rule("no", "yes")
    assert re.sub(*pair, "NO normal nobody") == "yes normal nobody"
    pair = make_rule("no", "yes", case_sensitive=True, whole_words=False)
    assert re.sub(*pair, "NO normal", flags=re.IGNORECASE) == "NO yesrmal"
    assert describe_rule([r"\bterm\b", r"\1"]) is None


def test_selection_edits_only_selected_occurrence_and_refuses_stale_offsets():
    text = "😀 keep remove keep remove"
    selection = dict(start=7, end=13, text="remove", value=text)
    assert replace_selection(text, selection, "new") == "😀 keep new keep remove"
    with pytest.raises(ValueError, match="changed"):
        replace_selection("prefix " + text, selection, "")
    with pytest.raises(ValueError, match="changed"):
        replace_selection(text, {**selection, "end": 12}, "")
    with pytest.raises(ValueError):
        make_rule("  ")
