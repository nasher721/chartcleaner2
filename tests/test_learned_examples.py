"""Learned-rule examples: rules keep doing what they were taught.

Besides unit tests, ``test_my_learned_rules_still_hold`` replays the examples
saved in this folder's data/rule_examples.jsonl against this folder's
config.json, so running the tests on your own machine also checks your
learned rules. It is skipped when there are no saved examples.
"""

import pytest

from chartcleaner import rule_examples, store
from chartcleaner.engine import load_config
from chartcleaner.highlight_rules import make_rule


@pytest.fixture()
def data(tmp_path):
    return tmp_path  # conftest already points rule_examples at tmp_path


def test_record_and_check_pass(data):
    pair = make_rule("Printed by Epic")
    cfg = {"learned_rules": [pair]}
    example = rule_examples.record(pair, "Printed by Epic", cfg)
    assert example["expected"] == ""
    assert rule_examples.check(cfg) == {"checked": 1, "failures": [], "stage_disabled": False}


def test_later_rule_that_changes_a_lesson_is_reported(data):
    first = make_rule("pt", "patient")
    cfg = {"learned_rules": [first]}
    rule_examples.record(first, "pt", cfg)
    cfg["learned_rules"].append(make_rule("patient", "PATIENT"))
    report = rule_examples.check(cfg)
    assert report["failures"][0]["expected"] == "patient"
    assert report["failures"][0]["now"] == "PATIENT"


def test_deleted_rules_are_skipped_and_disabled_stage_flagged(data):
    pair = make_rule("noise")
    rule_examples.record(pair, "noise", {"learned_rules": [pair]})
    assert rule_examples.check({"learned_rules": []})["checked"] == 0
    off = {"learned_rules": [pair], "stage_options": {"learned_rules": {"enabled": False}}}
    report = rule_examples.check(off)
    assert report["stage_disabled"] and report["failures"]


def test_clear_and_bad_lines(data):
    (data / "rule_examples.jsonl").write_text("not json\n{}\n", encoding="utf-8")
    assert rule_examples.load() == []
    assert rule_examples.clear() == 0 and not (data / "rule_examples.jsonl").exists()


def test_my_learned_rules_still_hold():
    examples = rule_examples.load(store.DATA_DIR / "rule_examples.jsonl")
    if not examples:
        pytest.skip("no learned-rule examples saved on this machine")
    report = rule_examples.check(load_config(store.CONFIG_PATH), examples)
    assert not report["failures"], report["failures"][:5]
