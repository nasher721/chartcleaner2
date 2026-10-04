"""Phase 7 group 5: learning from edits, signed rule files, per-stage noise stats."""

from __future__ import annotations

import json

import pytest

from chartcleaner import edit_log, regression_set, rule_inbox, rule_sharing, rule_signing, store
from chartcleaner.engine import clean_text, load_default_config

RESULT = ("Reviewed by Smith on 10/01/2026\nPatient stable overnight.\nNa 141 this morning.\n"
          "Electronically routed to inbox 4471\n")


# --- edit log ------------------------------------------------------------------

def test_deleted_lines_counts_repeats():
    assert edit_log.deleted_lines("a\nb\nb\nc", "b\nc") == ["a", "b"]


def test_record_skips_clinical_lines_and_encrypts():
    edited = "Patient stable overnight.\n"
    assert edit_log.record(RESULT, edited) == 2   # the Na line has a clinical value: not recorded
    raw = store.EDIT_LOG_FILE.read_bytes()
    assert b"Reviewed" not in raw and b"inbox" not in raw
    shapes = edit_log.entries()
    assert any("<DATE>" in k for k in shapes)
    assert not any("Na" in k for k in shapes)


def test_three_deletions_become_an_inbox_suggestion():
    cfg = load_default_config()
    for day in ("10/01/2026", "10/02/2026", "10/03/2026"):
        before = RESULT.replace("10/01/2026", day)
        edit_log.record(before, "Patient stable overnight.\nNa 141 this morning.\n")
    cands = edit_log.candidates()
    assert {c["count"] for c in cands} == {3}
    items = rule_inbox.suggestions(cfg, [])   # works before any recent charts exist
    titles = [i.title for i in items]
    assert any(t.startswith("You deleted “Reviewed by Smith on 10/03/2026”") for t in titles)
    item = next(i for i in items if "Reviewed" in i.title)
    updated = rule_inbox.accept(cfg, item)
    out = clean_text("Reviewed by Smith on 11/12/2026\nPatient walking.\n", updated, wrap=False).text
    assert "Reviewed" not in out and "Patient walking." in out
    rule_inbox.dismiss(item.id)
    assert not any("Reviewed" in i.title for i in rule_inbox.suggestions(cfg, []))


def test_edit_log_retention_and_clear():
    edit_log.record(RESULT, "", retention_days=1)
    data = json.loads(__import__("chartcleaner.secure_store", fromlist=["x"]).read_text(store.EDIT_LOG_FILE))
    for e in data["entries"].values():
        e["last"] = 0
    __import__("chartcleaner.secure_store", fromlist=["x"]).write_text(store.EDIT_LOG_FILE, json.dumps(data))
    edit_log.record("Another chrome line here\n", "", retention_days=1)
    assert len(edit_log.entries()) == 1
    assert store.EDIT_LOG_FILE in store._chart_data_paths()
    edit_log.clear()
    assert edit_log.entries() == {}


# --- signing -------------------------------------------------------------------

def _payload():
    cfg = load_default_config()
    cfg["learned_rules"] = [[r"(?im)^Reviewed by .*$", ""]]
    return rule_sharing.payload_from_config(cfg)


def test_sign_and_verify_own_file():
    signed = rule_signing.sign(_payload(), "Nash")
    check = rule_signing.verify(signed)
    assert check.status == "trusted" and check.trusted_name == "you"
    assert rule_signing.fingerprint(rule_signing.public_key()) == check.fingerprint
    assert b"PRIVATE" not in store.SIGNING_KEY_FILE.read_bytes()


def test_tampering_is_detected_and_refused():
    signed = rule_signing.sign(_payload(), "Nash")
    signed["rules"][0]["pattern"] = ".*"
    assert rule_signing.verify(signed).status == "invalid"
    with pytest.raises(ValueError, match="changed after signing"):
        rule_sharing.read_signed(signed)


def test_colleague_key_untrusted_then_trusted(monkeypatch, tmp_path):
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    other = Ed25519PrivateKey.generate()
    monkeypatch.setattr(rule_signing, "_private_key", lambda: other)
    signed = rule_signing.sign(_payload(), "Colleague")
    key = signed["signature"]["key"]
    monkeypatch.undo()
    check = rule_signing.verify(signed)
    assert check.status == "untrusted" and "haven't trusted" in check.message
    fp = rule_signing.trust_key("Dr. Lee", key)
    assert rule_signing.verify(signed).status == "trusted" and fp in rule_signing.verify(signed).message
    assert rule_signing.untrust_key(key) and rule_signing.verify(signed).status == "untrusted"
    with pytest.raises(ValueError):
        rule_signing.trust_key("x", "not-a-key")


def test_unsigned_files_still_import_and_export_signed(tmp_path):
    payload, check = rule_sharing.read_signed(_payload())
    assert check.status == "unsigned" and payload["rules"]
    path = rule_sharing.export_json(load_default_config(), tmp_path / "r.json", sign=True, signer="Me")
    raw = json.loads(path.read_text())
    assert raw["signature"]["signer"] == "Me"
    assert rule_sharing.read_signed(raw)[1].status == "trusted"


def test_known_good_impact_preview():
    cfg = load_default_config()
    text = "Reviewed by Smith on 10/01/2026\nPatient stable today, eating well.\n"
    regression_set.add(text, clean_text(text, cfg).text, "rounds")
    impact = rule_sharing.known_good_impact(cfg, _payload())
    assert impact == {"checked": 1, "changed": ["rounds"]}


# --- per-stage noise -----------------------------------------------------------

def test_summarize_reports_noise_per_stage_and_day():
    runs = [
        {"ts": "2026-10-01T09:00:00", "chars_before": 100, "chars_after": 60,
         "stages": [{"label": "Boilerplate", "chars_before": 100, "chars_after": 70},
                    {"label": "Whitespace", "chars_before": 70, "chars_after": 60},
                    {"label": "Bullets", "chars_before": 60, "chars_after": 60}]},
        {"ts": "2026-10-02T09:00:00", "chars_before": 50, "chars_after": 40,
         "stages": [{"label": "Boilerplate", "before": 50, "after": 40}]},   # batch-style keys
    ]
    s = store.summarize(runs)
    assert s["stage_by_day"]["2026-10-01"] == {"Boilerplate": 30, "Whitespace": 10}
    assert s["stage_by_day"]["2026-10-02"] == {"Boilerplate": 10}
    first = s["stage_share"][0]
    assert first == {"label": "Boilerplate", "removed": 40, "share": 80.0, "runs": 2, "avg_per_run": 20}
    assert all(r["label"] != "Bullets" for r in s["stage_share"])
