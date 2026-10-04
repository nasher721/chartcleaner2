"""Phase 7 group 4: bed tags, daily-note mode, multi-patient split, note templates."""

from __future__ import annotations

import io
import sys

import pytest

from chartcleaner import daily_note, note_templates, patients, recent_charts, service
from chartcleaner.batch import run_texts
from chartcleaner.engine import load_default_config, save_config, validate_config

PREV = """Date of Service: 09/14/2026
Labs: Na 136, K 4.0
GCS 13.
Plan:
- Nimodipine 60 mg q4h.
"""
TODAY = """Date of Service: 09/15/2026
Overnight events:
- Febrile to 38.6.

Labs: Na 131, K 4.0
GCS 13.
EVD placed 09/12/2026.
Plan:
- Nimodipine 60 mg q4h.
- Salt tabs 2 g TID.
"""


def _cfg():
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["wrap_output"] = False
    return cfg


# --- bed tags --------------------------------------------------------------------

def test_tags_are_stored_inside_the_encrypted_record():
    path = recent_charts.remember(PREV, "t", {}, tag="G20-1")
    assert "G20" not in path.name and b"G20" not in path.read_bytes()
    recent_charts.remember("Another bed's chart, unrelated.", "t", {}, tag="H22-2")
    assert recent_charts.tags() == ["H22-2", "G20-1"]
    assert [r["tag"] for r in recent_charts.load(tag="g20-1")] == ["G20-1"]
    assert recent_charts.latest_for("G20-1")["text"] == PREV
    assert recent_charts.latest_for("G20-1", exclude_text=PREV) is None


def test_set_tag_and_invalid_tags():
    path = recent_charts.remember(PREV, "t", {})
    assert recent_charts.set_tag(path, "Bed 4") and recent_charts.load()[0]["tag"] == "Bed 4"
    assert recent_charts.set_tag(path, "") and "tag" not in recent_charts.load()[0]
    assert recent_charts.clean_tag("  G20-1 ") == "G20-1"
    assert recent_charts.clean_tag("<script>") == "" and recent_charts.clean_tag("x" * 40) == ""
    assert not recent_charts.set_tag("/etc/passwd", "x")


def test_recleaning_the_same_chart_adds_the_tag():
    recent_charts.remember(PREV, "t", {})
    recent_charts.remember(PREV, "t", {}, tag="G20-1")
    assert recent_charts.count() == 1 and recent_charts.load()[0]["tag"] == "G20-1"


# --- daily note ------------------------------------------------------------------

def test_new_lines_keep_headings_and_skip_repeats():
    lines = daily_note.new_lines(TODAY, PREV)
    assert "Labs: Na 131, K 4.0" in lines
    assert "GCS 13." not in lines and "- Nimodipine 60 mg q4h." not in lines
    assert lines[lines.index("- Salt tabs 2 g TID.") - 1] == "Plan:"
    assert not any(line.startswith("Date of Service") for line in lines)


def test_daily_note_blocks():
    note = daily_note.build(TODAY, PREV)
    text = note.to_text()
    assert text.startswith("Daily update 09/15/2026 (compared with 09/14/2026)")
    assert "Na: 136 → 131" in text and "Overnight events" in text and "EVD" in text
    assert "What's new since the previous chart" in text
    assert note.to_dict()["previous_date"] == "09/14/2026"


def test_daily_note_when_nothing_changed():
    assert "(nothing" in daily_note.build(PREV, PREV).to_text()


def test_service_daily_note_finds_previous_by_tag():
    with pytest.raises(ValueError):
        service.daily_note(TODAY, tag="G20-1", config=_cfg())
    recent_charts.remember(PREV, "t", {}, tag="G20-1")
    out = service.daily_note(TODAY, tag="G20-1", config=_cfg())
    assert "Na: 136 → 131" in out["text"]


# --- patient split ---------------------------------------------------------------

LIST = """NSICU census 10/04 — rounding list for the team

G20-1 65M SAH HH3, EVD day 4. Na 131.
  - nimodipine 60 mg q4h
G20-2 72F ICH, SBP goal <140.
H22-1 50M TBI, ICP 18. MRN: 12345678
"""


def test_split_on_bed_labels_keeps_header():
    chunks = patients.split(LIST)
    assert [c.label for c in chunks] == ["Header", "G20-1", "G20-2", "H22-1"]
    assert "nimodipine" in chunks[1].text and chunks[1].text.startswith("G20-1")


@pytest.mark.parametrize("text,labels", [
    ("Pt A stable\n-----\nPt B febrile\n-----\nPt C ok\n", ["Patient 1", "Patient 2", "Patient 3"]),
    ("Patient: Bed four\nstable\nPatient: Bed five\nfebrile\n", ["Bed four", "Bed five"]),
    ("1) SAH stable\n2) ICH febrile\n3) TBI ok\n", ["1)", "2)", "3)"]),
    ("Bed 4 SAH\nBed 12 ICH\n", ["Bed 4", "Bed 12"]),
])
def test_split_styles(text, labels):
    assert [c.label for c in patients.split(text)] == labels


def test_single_chart_is_one_chunk():
    assert [c.label for c in patients.split("Just one note.")] == ["Patient 1"]
    assert patients.split("   ") == []


def test_run_texts_cleans_each_and_isolates_summaries():
    calls = []

    def summarize(chart):
        calls.append(chart)
        if "ICH" in chart:
            raise RuntimeError("model down")
        return "One-liner."

    chunks = patients.split(LIST)
    res = run_texts([(c.label, c.text) for c in chunks], _cfg(), summarize=summarize)
    assert [r.status for r in res] == ["ok"] * 4
    assert res[3].cleaned and "12345678" not in res[3].cleaned
    assert res[1].summary == "One-liner." and res[2].summary == ""
    assert len(calls) == 4


def test_service_clean_patients():
    out = service.clean_patients(LIST, config=_cfg())
    assert [p["label"] for p in out] == ["Header", "G20-1", "G20-2", "H22-1"]
    assert all(p["status"] == "ok" for p in out)


# --- note templates --------------------------------------------------------------

def test_systems_group_lines():
    groups = note_templates.systems(TODAY)
    assert "GCS 13." in groups["N"]
    assert any("Na 131" in line for line in groups["R/GU"])
    assert any("Febrile" in line for line in groups["ID"])


def test_render_builtin_and_custom_templates():
    text = note_templates.render("Systems note ([N] [CV] [R] …)", TODAY)
    assert "[N]\n- GCS 13." in text and "[ID]" in text and "{{" not in text
    cfg = {"note_templates": [{"name": "Mine", "template":
                               "{{system:CV}}\n{{section:Plan}}\n{{overnight}}\n{{nope}}"}]}
    out = note_templates.render("mine", TODAY, cfg)
    assert "[CV]\n- not documented" in out
    assert "Salt tabs" in out and "Overnight events" in out and "{{nope}}" in out
    with pytest.raises(KeyError):
        note_templates.render("missing", TODAY)


def test_note_templates_are_validated():
    cfg = load_default_config()
    cfg["note_templates"] = [{"name": "", "template": "x"}]
    errors, _ = validate_config(cfg)
    assert any("note_templates[0]" in e for e in errors)


def test_service_note():
    out = service.note(TODAY, "Interval note", config=_cfg(), record=False)
    assert out["text"].startswith("Interval note")


# --- CLI / API / MCP -------------------------------------------------------------

def _cli(monkeypatch, capsys, argv, stdin):
    import medical_cleaner
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "argv", ["clean-chart", "--stdin", "--stdout", "--no-wrap", *argv])
    medical_cleaner.main()
    return capsys.readouterr()


def test_cli_split_and_template(monkeypatch, capsys):
    out = _cli(monkeypatch, capsys, ["--split-patients"], LIST).out
    assert "=== G20-1 ===" in out and "=== H22-1 ===" in out
    out = _cli(monkeypatch, capsys, ["--template", "Systems note ([N] [CV] [R] …)"], TODAY).out
    assert "[N]" in out


def test_cli_tag_compares_with_yesterday(monkeypatch, capsys, tmp_path):
    first = _cli(monkeypatch, capsys, ["--tag", "G20-1"], PREV)
    assert "No earlier chart" in first.err
    second = _cli(monkeypatch, capsys, ["--tag", "G20-1"], TODAY)
    assert second.out.startswith("Daily update") and "Na: 136 → 131" in second.out
    prev_file = tmp_path / "prev.txt"
    prev_file.write_text(PREV, encoding="utf-8")
    third = _cli(monkeypatch, capsys, ["--daily-note", str(prev_file)], TODAY)
    assert third.out.startswith("Daily update")


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from chartcleaner import api, store
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    path = tmp_path / "config.json"
    save_config(_cfg(), path)
    monkeypatch.setattr(store, "CONFIG_PATH", path)
    app = FastAPI()
    api.register(app)
    c = TestClient(app, client=("127.0.0.1", 50000))
    c.headers["Authorization"] = f"Bearer {api.get_token()}"
    return c


def test_api_routes(client):
    assert "[N]" in client.post("/api/v1/note", json={
        "text": TODAY, "template": "Systems note ([N] [CV] [R] …)"}).json()["text"]
    assert client.post("/api/v1/note", json={"text": TODAY, "template": "nope"}).status_code == 404
    pts = client.post("/api/v1/patients", json={"text": LIST}).json()["patients"]
    assert len(pts) == 4
    dn = client.post("/api/v1/daily-note", json={"text": TODAY, "previous": PREV}).json()
    assert dn["text"].startswith("Daily update")
    assert client.post("/api/v1/daily-note", json={"text": TODAY, "tag": "none"}).status_code == 400


def test_mcp_tools(client):
    from chartcleaner import mcp_server
    assert "Interval note" in mcp_server.list_note_templates()
    assert mcp_server.fill_note_template(TODAY, "Interval note").startswith("Interval note")
    assert [p["label"] for p in mcp_server.clean_patient_list(LIST)][1] == "G20-1"
