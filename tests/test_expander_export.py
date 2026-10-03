"""Text-expander exports of the abbreviation dictionary."""

import csv
import io
import plistlib

import pytest

from chartcleaner.abbreviations import with_custom
from chartcleaner.expander_export import FORMATS, entries, export_filename, render


def test_entries_use_prefix_skip_unsafe_and_prefer_custom():
    pairs = dict(entries({}))
    assert pairs[";htn"] == "hypertension"
    assert ";u" not in pairs and ";sc" not in pairs        # Do Not Use / single letter
    assert pairs[";ij"] == "internal jugular"               # safe meaning kept
    cfg = with_custom({}, "my phrase", "HTN")
    assert dict(entries(cfg))[";htn"] == "my phrase"


def test_custom_only_and_prefix():
    cfg = with_custom({}, "left side weakness", "LSW")
    assert entries(cfg, prefix="//", include_bundled=False) == [("//lsw", "left side weakness")]


def test_acknowledged_unsafe_custom_is_exported():
    cfg = with_custom({}, "subcutaneous", "SQ", acknowledged=True)
    assert ("x:sq", "subcutaneous") in entries(cfg, prefix="x:", include_bundled=False)


PAIRS = [(";a", 'say "hi"; ok'), (";b", "back`tick")]


def test_plist_round_trip():
    data = plistlib.loads(render("plist", PAIRS))
    assert data[0] == {"phrase": 'say "hi"; ok', "shortcut": ";a"}


def test_espanso_is_valid_yaml_shape():
    text = render("espanso", PAIRS).decode()
    assert '  - trigger: ";a"\n    replace: "say \\"hi\\"; ok"' in text


def test_textexpander_csv():
    rows = list(csv.reader(io.StringIO(render("textexpander", PAIRS).decode())))
    assert rows[0][:2] == [";a", 'say "hi"; ok']


def test_ahk_escapes_replacement():
    text = render("ahk", PAIRS).decode()
    assert ':T:;a::say "hi"`; ok' in text and ":T:;b::back``tick" in text


def test_unknown_format_and_filenames():
    with pytest.raises(ValueError):
        render("docx", PAIRS)
    assert {export_filename(f) for f in FORMATS} == {
        "chartcleaner-abbreviations.plist", "chartcleaner.yml",
        "chartcleaner-abbreviations.csv", "chartcleaner-abbreviations.ahk"}
