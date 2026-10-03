"""Phrase miner: suggest abbreviations for repeated phrases."""

from chartcleaner.abbreviations import with_custom
from chartcleaner.phrase_miner import mine

CHART = """Day 1: left side weakness unchanged.
Exam notable for left side weakness and facial droop.
Day 2: persistent left side weakness, improving dysarthria.
Day 2: persistent left side weakness, improving dysarthria.
Spoke with Sarah Chen. Sarah Chen agrees. Called Sarah Chen.
Started levetiracetam for seizure prophylaxis.
Continue seizure prophylaxis today.
Discussed seizure prophylaxis duration.
Hypertension controlled. Hypertension stable. Hypertension noted.
"""


def test_finds_repeated_terms_ranked_by_savings():
    found = mine(CHART)
    assert [f["phrase"] for f in found] == ["seizure prophylaxis", "left side weakness"]
    assert found[1] == {"phrase": "left side weakness", "count": 3, "suggestion": "LSW", "saves": 45}


def test_copied_forward_lines_count_once():
    assert {f["phrase"]: f["count"] for f in mine(CHART)}["left side weakness"] == 3


def test_skips_names_known_terms_custom_and_dismissed():
    phrases = {f["phrase"] for f in mine(CHART)}
    assert "sarah chen" not in phrases and "hypertension" not in phrases
    cfg = with_custom({}, "left side weakness", "LSW")
    cfg["abbreviations"]["rejected_suggestions"] = ["seizure prophylaxis"]
    assert mine(CHART, cfg) == []


def test_sentence_fragments_are_not_terms():
    text = "\n".join(f"ordered for tomorrow to evaluate case {i}" for i in range(4))
    assert all("to" not in f["phrase"].split() for f in mine(text))


def test_min_count_and_accepts_list():
    assert mine([CHART], min_count=4) == []
