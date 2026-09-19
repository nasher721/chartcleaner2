"""Tests for autonomous EMR rule miner."""

import pytest
from chartcleaner.rule_miner import mine_chrome_rules


def test_mine_chrome_rules_basic():
    doc1 = (
        "Version 1 of 2\n"
        "Editor: SYSTEM, (build 4.2)\n"
        "Patient seen on 10/12/2026 at 14:30\n"
        "Assessment & Plan: Chest pain resolved."
    )
    doc2 = (
        "Version 2 of 3\n"
        "Editor: SYSTEM, (build 4.2)\n"
        "Patient seen on 10/13/2026 at 09:15\n"
        "Assessment & Plan: Discharged home."
    )

    candidates = mine_chrome_rules([doc1, doc2], min_occurrence=2)
    assert len(candidates) >= 1

    # Should have identified the Version or Editor chrome lines
    patterns = [c.pattern for c in candidates]
    assert any("Editor" in p or "Version" in p for p in patterns)
