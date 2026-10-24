"""Quick rule modes: plain-text Remove text / Add abbreviations, rule export."""

from __future__ import annotations

import json

import pytest

import app as cc_app
from app_pages import common
from chartcleaner import quick_rules, rule_sharing, service, store
from chartcleaner.engine import Pipeline, load_config, load_default_config, save_config, validate_config
from chartcleaner.highlight_rules import make_rule


@pytest.fixture()
def cfg():
    out = load_default_config()
    out["nlp_redaction"] = {"enabled": False}
    out["audit"] = {"enabled": False}
    return out


# ---- parsing ---------------------------------------------------------------

def test_parse_removals_skips_blanks_comments_and_duplicates():
    text = "Electronically signed by\n\n# a comment\n  Printed on  \nelectronically SIGNED by\n"
    assert quick_rules.parse_removals(text) == ["Electronically signed by", "Printed on"]


@pytest.mark.parametrize("line", ["subarachnoid hemorrhage = SAH", "subarachnoid hemorrhage=>SAH",
                                  "subarachnoid hemorrhage -> SAH", "subarachnoid hemorrhage → SAH",
                                  "subarachnoid hemorrhage\tSAH"])
def test_parse_abbreviations_accepts_every_separator(line):
    assert quick_rules.parse_abbreviations(line) == ([("subarachnoid hemorrhage", "SAH")], [])


def test_parse_abbreviations_reports_bad_lines_with_numbers():
    pairs, errors = quick_rules.parse_abbreviations("as needed = PRN\nno separator here\nPRN = prn")
    assert pairs == [("as needed", "PRN")]
    assert errors[0].startswith("Line 2:") and errors[1].startswith("Line 3:")


# ---- adding ----------------------------------------------------------------

def test_add_removals_builds_literal_rules_and_enables_stage(cfg):
    cfg["stage_options"] = {"learned_rules": {"enabled": False}}
    out, report = quick_rules.add_removals(cfg, ["Printed on", "Pt. (stable)"])
    assert report.added == ["Printed on", "Pt. (stable)"]
    assert out["learned_rules"][-2:] == [make_rule("Printed on"), make_rule("Pt. (stable)")]
    assert out["stage_options"]["learned_rules"]["enabled"] is True
    assert cfg["stage_options"]["learned_rules"]["enabled"] is False  # input untouched
    assert validate_config(out)[0] == []
    cleaned = Pipeline(out).run("Printed on 10/1\nPt. (stable) today", wrap=False).text
    assert "Printed on" not in cleaned and "Pt. (stable)" not in cleaned


def test_add_removals_reports_existing_and_conflicting_rules(cfg):
    cfg["learned_rules"] = [make_rule("old"), make_rule("swap", "SWAPPED")]
    out, report = quick_rules.add_removals(cfg, ["old", "swap", "new"])
    assert (report.existing, report.conflicts, report.added) == (["old"], ["swap"], ["new"])
    assert make_rule("swap", "SWAPPED") in out["learned_rules"]


def test_add_abbreviations_saves_updates_and_blocks_do_not_use(cfg):
    cfg["abbreviations"] = {"disabled": ["external ventricular drain"],
                            "custom": [{"term": "as needed", "replacement": "prn", "enabled": False}]}
    out, report = quick_rules.add_abbreviations(cfg, [
        ("left side weakness", "LSW"), ("as needed", "PRN"),
        ("external ventricular drain", "EVD"), ("every day", "QD")])
    assert report.added == ["left side weakness", "external ventricular drain"]
    assert report.updated == ["as needed"]
    assert report.blocked == ["every day"]
    group = out["abbreviations"]
    assert "external ventricular drain" not in group["disabled"]
    terms = {c["term"]: c for c in group["custom"]}
    assert terms["as needed"] == {"term": "as needed", "replacement": "PRN", "enabled": True}
    assert "every day" not in terms
    assert validate_config(out)[0] == []


def test_preview_counts_hits_and_flags_blocked(cfg):
    chart = "Printed on 10/1. printed on 10/2. Imprinted on"
    assert quick_rules.preview_removals(chart, ["Printed on"]) == [{"text": "Printed on", "hits": 2}]
    assert quick_rules.preview_removals(chart, ["Printed on"], whole_words=False)[0]["hits"] == 3
    assert quick_rules.preview_removals(chart, ["Printed on"], case_sensitive=True)[0]["hits"] == 1
    rows = quick_rules.preview_abbreviations("every day", [("every day", "QD")], cfg)
    assert rows[0]["status"] == "blocked" and rows[0]["hits"] == 1


# ---- plain-text export / import -------------------------------------------

def test_text_export_round_trips(cfg):
    cfg["learned_rules"] = [make_rule("Printed on"), make_rule("Exact", case_sensitive=True),
                            make_rule("pre", whole_words=False), make_rule("pt", "patient"),
                            [r"\bfoo\d+", ""]]
    cfg["abbreviations"] = {"disabled": [], "custom": [
        {"term": "left side weakness", "replacement": "LSW", "enabled": True}]}
    text = quick_rules.export_text(cfg)
    assert "Exact  # [match case]" in text and "pre  # [inside words]" in text
    assert "pt => patient" in text and "left side weakness = LSW" in text
    assert "Advanced regex rules are not included" in text

    fresh = load_default_config()
    out, reports, errors = quick_rules.apply_text(fresh, text)
    assert errors == []
    assert len(reports["remove"].added) == 3 and reports["replace"].added == ["pt"]
    assert reports["abbreviations"].added == ["left side weakness"]
    assert out["learned_rules"] == cfg["learned_rules"][:3] + [make_rule("pt", "patient")]
    # Importing the same file again changes nothing.
    _, again, _ = quick_rules.apply_text(out, text)
    assert not any(r.changed for r in again.values())


def test_bare_phrase_list_is_a_valid_text_file(cfg):
    out, reports, errors = quick_rules.apply_text(cfg, "Printed on\nVoice recognition used\n")
    assert errors == [] and reports["remove"].added == ["Printed on", "Voice recognition used"]


def test_text_import_reports_bad_replace_line(cfg):
    _, _, errors = quick_rules.apply_text(cfg, "# Replace\nno arrow here\n")
    assert errors and errors[0].startswith("Line 2:")


# ---- complete JSON export --------------------------------------------------

def test_complete_export_carries_stage_rules_and_exceptions(cfg):
    cfg["stage_options"] = {"boilerplate": {"exceptions": ["K 6.1"]}}
    payload = rule_sharing.payload_from_config(cfg, complete=True)
    assert payload["stage_rules"]["boilerplate"] == cfg["boilerplate"]
    assert payload["stage_rules"]["literal_replacements"] == cfg["literal_replacements"]
    assert payload["exceptions"] == {"boilerplate": ["K 6.1"]}
    assert "stage_rules" not in rule_sharing.payload_from_config(cfg)

    target = load_default_config()
    target["boilerplate"] = target["boilerplate"][:3]
    preview = rule_sharing.preview_import(target, payload)
    assert preview["stage_rules_added"] == len(cfg["boilerplate"]) - 3
    assert preview["exceptions_added"] == 1
    merged = rule_sharing.apply_import(target, payload)
    assert merged["boilerplate"] == cfg["boilerplate"]
    assert merged["stage_options"]["boilerplate"]["exceptions"] == ["K 6.1"]
    assert validate_config(merged)[0] == []


def test_complete_import_replace_overwrites_only_included_lists(cfg):
    payload = rule_sharing.payload_from_config(cfg, complete=True)
    payload["stage_rules"] = {"boilerplate": ["only this"]}
    target = load_default_config()
    out = rule_sharing.apply_import(target, payload, "replace")
    assert out["boilerplate"] == ["only this"]
    assert out["emr_line_metadata"] == target["emr_line_metadata"]


@pytest.mark.parametrize("bad", [{"stage_rules": {"nope": []}},
                                 {"stage_rules": {"boilerplate": ["("]}},
                                 {"stage_rules": {"literal_replacements": [["x"]]}},
                                 {"exceptions": {"boilerplate": [""]}}])
def test_complete_import_rejects_malformed_sections(bad):
    with pytest.raises(ValueError):
        rule_sharing.validate_payload({"kind": rule_sharing.KIND, "version": 1, "removals": [],
                                       "replacements": [], **bad})


def test_signed_complete_export_verifies(cfg, tmp_path):
    pytest.importorskip("cryptography")
    path = rule_sharing.export_json(cfg, tmp_path / "r.json", sign=True, signer="Test", complete=True)
    payload, check = rule_sharing.read_signed(json.loads(path.read_text(encoding="utf-8")))
    assert check is not None and check.status != "invalid"
    assert payload["stage_rules"]


# ---- service, store and CLI -------------------------------------------------

@pytest.fixture()
def config_file(cfg, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    monkeypatch.setattr(store, "EXPORTS_DIR", tmp_path / "exports")
    return path


def test_service_add_rules_saves_and_reports(config_file):
    report = service.add_rules(remove=["Printed on"],
                               abbreviations=[("left side weakness", "LSW"), ("every day", "QD")])
    assert report["saved"]
    assert report["remove"]["added"] == ["Printed on"]
    assert report["abbreviations"]["blocked"] == ["every day"]
    saved = load_config(config_file)
    assert make_rule("Printed on") in saved["learned_rules"]
    assert service.list_rules()["remove"][0]["text"] == "Printed on"
    assert "Printed on" in service.export_rules("txt")
    assert json.loads(service.export_rules("json", complete=True))["stage_rules"]
    assert service.add_rules(remove=["Printed on"])["saved"] is False


def test_store_txt_export_and_import(config_file, tmp_path):
    service.add_rules(remove=["Printed on"], abbreviations=[("left side weakness", "LSW")])
    path, counts = store.export_rules(tmp_path / "rules.txt", "txt")
    assert counts["removals"] == 1 and counts["abbreviations"] == 1
    cfg = load_config(config_file)
    cfg["learned_rules"] = []
    cfg.pop("abbreviations", None)
    save_config(cfg, config_file)
    ok, message = store.import_rules(path)
    assert ok, message
    assert make_rule("Printed on") in load_config(config_file)["learned_rules"]
    path.write_text("# Replace\nbroken\n", encoding="utf-8")
    before = config_file.read_text(encoding="utf-8")
    ok, message = store.import_rules(path)
    assert not ok and "nothing was changed" in message
    assert config_file.read_text(encoding="utf-8") == before


def test_cli_rule_commands(config_file, tmp_path, capsys):
    import medical_cleaner

    with pytest.raises(SystemExit) as done:
        medical_cleaner.main(["--add-remove", "Printed on", "--add-abbreviation",
                              "left side weakness = LSW", "--add-abbreviation", "every day=QD"])
    assert done.value.code == 0
    out = capsys.readouterr()
    assert "Removals: 1 added" in out.out and "Do Not Use" in out.err

    with pytest.raises(SystemExit):
        medical_cleaner.main(["--list-rules"])
    assert "left side weakness = LSW" in capsys.readouterr().out

    dest = tmp_path / "all.json"
    with pytest.raises(SystemExit) as done:
        medical_cleaner.main(["--export-rules", str(dest), "--complete"])
    assert done.value.code == 0 and json.loads(dest.read_text(encoding="utf-8"))["stage_rules"]

    with pytest.raises(SystemExit) as done:
        medical_cleaner.main(["--export-rules", str(tmp_path / "rules.xlsx")])
    assert done.value.code == 1

    with pytest.raises(SystemExit) as done:
        medical_cleaner.main(["--import-rules", str(dest)])
    assert done.value.code == 0 and "Imported" in capsys.readouterr().out

    with pytest.raises(SystemExit) as done:
        medical_cleaner.main(["--add-abbreviation", "no separator"])
    assert done.value.code == 1


# ---- UI ----------------------------------------------------------------------

@pytest.fixture()
def ui_config(cfg, tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    save_config(cfg, path)
    monkeypatch.setattr(common, "CONFIG_PATH", path)
    monkeypatch.setitem(common.PREFS, "auto_clean", False)
    monkeypatch.setitem(common.PREFS, "selection_mode", "ask")
    return path


def _selection(value: str, text: str) -> dict:
    start = value.index(text)
    return {"start": start, "end": start + len(text), "text": text, "value": value}


def _textarea(user, prefix):
    matches = [e for e in user.find().elements if str(e.props.get("label", "")).startswith(prefix)]
    assert len(matches) == 1
    return matches[0]


async def _open_clean(user, path):
    from nicegui import ui

    @ui.page(path)
    async def fixture_page():
        await cc_app.clean_page()

    await user.open(path)


@pytest.mark.nicegui_main_file("")
async def test_remove_text_mode_removes_on_select(user, ui_config):
    text = "Keep Printed on today"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    common.PREFS["selection_mode"] = "remove"
    await _open_clean(user, "/quick-remove-mode")
    user.find(marker="advanced-input-tools").click()
    await user.should_see("Remove text mode")
    user.find(marker="highlight-source").trigger("mouseup", _selection(text, "Printed on"))
    assert _textarea(user, "Chart text").value == "Keep  today"
    assert load_config(ui_config)["learned_rules"] == [make_rule("Printed on")]
    user.find("Undo last highlight").click()
    assert _textarea(user, "Chart text").value == text


@pytest.mark.nicegui_main_file("")
async def test_remove_text_mode_ignores_select_all(user, ui_config):
    text = "line one\nline two\nline three\nline four"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    common.PREFS["selection_mode"] = "remove"
    await _open_clean(user, "/quick-remove-all")
    user.find(marker="advanced-input-tools").click()
    user.find(marker="highlight-source").trigger("mouseup", _selection(text, text))
    await user.should_see("That selection is long")
    assert _textarea(user, "Chart text").value == text
    assert load_config(ui_config)["learned_rules"] == []
    assert next(iter(user.find(marker="selection-bar").elements)).visible


@pytest.mark.nicegui_main_file("")
async def test_abbreviate_mode_opens_dialog_on_select(user, ui_config):
    text = "Pt has left side weakness today"
    cc_app.CLEAN_STATE.update(input=text, mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    common.PREFS["selection_mode"] = "abbreviate"
    await _open_clean(user, "/quick-abbreviate-mode")
    user.find(marker="highlight-source").trigger("mouseup", _selection(text, "left side weakness"))
    await user.should_see("Abbreviate highlighted text")
    user.find("Abbreviate & remember").click()
    assert _textarea(user, "Chart text").value == "Pt has LSW today"


@pytest.mark.nicegui_main_file("")
async def test_selection_mode_toggle_saves_pref(user, ui_config):
    cc_app.CLEAN_STATE.update(input="x", mode="clean", result=None, result_text="", audit=None,
                              summary=None, qa=[])
    await _open_clean(user, "/quick-mode-toggle")
    user.find(marker="advanced-input-tools").click()
    toggle = next(iter(user.find(marker="selection-mode").elements))
    with user.client:
        toggle.set_value("remove")
    assert common.PREFS["selection_mode"] == "remove"
    await user.should_see("Remove text mode")
    with user.client:
        toggle.set_value("ask")
    assert common.PREFS["selection_mode"] == "ask"


@pytest.mark.nicegui_main_file("")
async def test_rules_page_quick_add_saves_removals_and_abbreviations(user, ui_config):
    from nicegui import ui

    @ui.page("/quick-add")
    def fixture_page():
        cc_app.text_rules_page()

    await user.open("/quick-add")
    await user.should_see("Remove text")
    with user.client:
        next(iter(user.find(marker="quick-remove-input").elements)).set_value("Printed on\nVoice dictated")
    await user.should_see("2 phrase(s)")
    user.find(marker="quick-remove-save").click()
    saved = load_config(ui_config)
    assert make_rule("Printed on") in saved["learned_rules"]
    assert make_rule("Voice dictated") in saved["learned_rules"]

    with user.client:
        next(iter(user.find(marker="quick-abbreviate-input").elements)).set_value(
            "left side weakness = LSW\nevery day = QD")
    user.find(marker="quick-abbreviate-save").click()
    custom = {c["term"]: c["replacement"] for c in load_config(ui_config)["abbreviations"]["custom"]}
    assert custom == {"left side weakness": "LSW"}
    # The blocked line stays in the box so it can be fixed.
    assert next(iter(user.find(marker="quick-abbreviate-input").elements)).value == "every day = QD"
