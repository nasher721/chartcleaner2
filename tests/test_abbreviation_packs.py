"""Specialty abbreviation packs and CSV import/export."""

import pytest

from chartcleaner import abbreviation_packs as packs
from chartcleaner.abbreviation_safety import _bundled_pairs, check
from chartcleaner.abbreviations import abbreviate, with_custom

EXPECTED = {"Cardiology", "Critical Care", "Medicine", "Neuro ICU", "Nursing"}


def test_all_five_packs_ship_with_entries():
    listed = {p["name"]: p for p in packs.list_packs()}
    assert set(listed) == EXPECTED
    assert all(p["count"] >= 10 for p in listed.values())


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_pack_entries_are_safe_unique_and_new(name):
    bundled_terms = {t.casefold() for t, _ in _bundled_pairs()}
    terms = [e["term"].casefold() for e in packs.load_pack(name)]
    assert len(terms) == len(set(terms))
    for entry in packs.load_pack(name):
        assert entry["term"].casefold() not in bundled_terms
        assert not any(i.level == "block" for i in check(entry["term"], entry["replacement"]))


def test_install_tags_entries_and_uninstall_removes_only_them():
    cfg = with_custom({}, "my own term", "MOT")
    cfg = with_custom(cfg, "transcatheter aortic valve replacement", "TAVRx")  # user entry wins
    cfg, added, skipped = packs.install(cfg, "Cardiology")
    assert added == len(packs.load_pack("Cardiology")) - 1
    assert skipped == ["transcatheter aortic valve replacement"]
    assert packs.installed(cfg) == {"Cardiology": added}
    assert abbreviate("planned transcatheter aortic valve replacement", cfg)[0] == "planned TAVRx"
    assert abbreviate("acute coronary syndrome", cfg)[0] == "ACS"
    cfg, removed = packs.uninstall(cfg, "Cardiology")
    assert removed == added
    assert [c["term"] for c in cfg["abbreviations"]["custom"]] == [
        "my own term", "transcatheter aortic valve replacement"]


def test_unknown_pack():
    with pytest.raises(KeyError):
        packs.load_pack("Dermatology")


def test_csv_round_trip_and_preview_statuses():
    cfg = with_custom({}, "left side weakness", "LSW")
    cfg, _, _ = packs.install(cfg, "Nursing")
    text = packs.export_csv(cfg)
    assert text.splitlines()[0] == "Abbreviation,Expanded version,Enabled,Pack"
    rows = packs.preview_import(text, cfg)
    assert {r["status"] for r in rows} == {"same"}

    incoming = ("Abbreviation,Expanded version\nLSWk,left side weakness\nRSW,right side weakness\n"
                "U,units\n,missing\nRSW,right side weakness\n")
    rows = packs.preview_import(incoming, cfg)
    assert [r["status"] for r in rows] == ["changed", "new", "blocked", "invalid", "invalid"]
    new_cfg, applied = packs.apply_import(cfg, rows)
    assert applied == 2
    assert abbreviate("left side weakness and right side weakness", new_cfg)[0] == "LSWk and RSW"


def test_csv_needs_columns():
    with pytest.raises(ValueError):
        packs.preview_import("a,b\n1,2\n", {})
