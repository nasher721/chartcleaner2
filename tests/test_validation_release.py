"""One release decision for every output path (``review_state.release_decision``).

A failed mandatory redaction is *blocked*: no ordinary cleaned-output payload
carries the text — UI copy, export, clipboard watcher, CLI, API, MCP and batch
all refuse — and the only way to see it is an explicit local review retrieval
marked raw/unverified. Acknowledgements are keyed on source, result, config and
validator version, so an edit, a config change or a stale async answer cannot
release a result nobody looked at.
"""

from __future__ import annotations

import json
import types

import pytest

from chartcleaner import batch, exporters, review_state, service
from chartcleaner.clipboard_watcher import ClipboardWatcher
from chartcleaner.engine import Pipeline, load_default_config
from chartcleaner.review_state import ReleaseDecision, acknowledgement_key, release_decision, support_text

CHART = "MRN: 12345678 Na 141 mmol/L\nPlan: aspirin 81 mg daily.\n"
CLEAN_CHART = "Dictated but not read.\nPlan: aspirin 81 mg daily.\n"


def base(**over) -> dict:
    cfg = {"emr_line_metadata": [], "boilerplate": [], "epic_phi_patterns": [],
           "literal_replacements": [], "clinical_headers": [], "nlp_redaction": {"enabled": False},
           "wrap_output": False, "duplicate_note_detection": {"enabled": False},
           "fuzzy_dedup": {"enabled": False}, "audit": {"enabled": False},
           "stage_options": {"medical_abbreviations": {"enabled": False}},
           "cleaning_validation": {"mode": "enforce", "schema_version": 1}}
    cfg.update(over)
    return cfg


# A PHI pattern that swallows a lab value: the redaction cannot be accepted and
# keeping the original leaves the identifier in — release must be withheld.
BLOCKED = base(epic_phi_patterns=[[r"MRN: \d+ Na 141 mmol/L", "[REDACTED_MRN]"]])
REVIEW = base(boilerplate=[r"^Plan: aspirin 81 mg daily\.\n"])  # a harmful edit, retained → review
CLEAN = base(boilerplate=[r"^Dictated but not read\.\n"])


def _result(cfg: dict, text: str = CHART):
    return Pipeline(cfg).run(text)


# --- the decision itself -----------------------------------------------------------

def test_blocked_cannot_be_acknowledged_into_ready():
    result = _result(BLOCKED)
    key = acknowledgement_key(result, result.text)
    d = release_decision(result, output_text=result.text, acknowledgement=key)
    assert isinstance(d, ReleaseDecision)
    assert d.status == "blocked" and d.deidentification_complete is False
    assert "redaction_failed" in d.reasons
    assert d.acknowledgement_key == key, "the key is reported, it just cannot override"


def test_review_then_ready_with_the_right_key():
    result = _result(REVIEW)
    d = release_decision(result, output_text=result.text)
    assert d.status == "review" and "edits_retained" in d.reasons and d.deidentification_complete
    ok = release_decision(result, output_text=result.text, acknowledgement=d.acknowledgement_key)
    assert ok.status == "ready"
    assert release_decision(result, output_text=result.text, acknowledgement="nonsense").status == "review"


def test_clean_result_is_ready_without_acknowledgement():
    result = _result(CLEAN, CLEAN_CHART)
    d = release_decision(result, output_text=result.text)
    assert d.status == "ready" and d.deidentification_complete
    assert "coverage_partial" in d.reasons, "partial relation coverage is visible, not blocking"


def test_manual_edit_invalidates_acknowledgement():
    result = _result(REVIEW)
    key = release_decision(result, output_text=result.text).acknowledgement_key
    assert release_decision(result, output_text=result.text, acknowledgement=key).status == "ready"
    edited = result.text + "\nWarfarin 500 mg daily."
    d = release_decision(result, output_text=edited, acknowledgement=key, source=CHART)
    assert d.status != "ready"
    assert d.acknowledgement_key != key
    assert "edited_output" in d.reasons


def test_new_config_invalidates_acknowledgement():
    first = _result(REVIEW)
    key = release_decision(first, output_text=first.text).acknowledgement_key
    changed = {**REVIEW, "literal_replacements": [["zzz", "yyy"]]}  # same text, other config
    second = _result(changed)
    assert second.text == first.text
    assert acknowledgement_key(second, second.text) != key
    assert release_decision(second, output_text=second.text, acknowledgement=key).status == "review"


def test_newer_result_cannot_inherit_old_async_review():
    older = _result(REVIEW)
    stale_key = release_decision(older, output_text=older.text).acknowledgement_key
    newer = _result(REVIEW, CHART + "Denies chest pain.\n")  # a new paste, result came back later
    d = release_decision(newer, output_text=newer.text, acknowledgement=stale_key)
    assert d.status == "review", "an answer about the older result releases nothing newer"


def test_no_recognized_facts_does_not_show_full_support():
    result = _result(base(), "Patient seems comfortable today.\n")
    text = support_text(result)
    assert result.validation.coverage.recognized_statements == 0
    assert "no clinical statements recognized" in text.lower() and "unchecked" in text.lower()
    assert "100" not in text and "all " not in text.lower()
    off = Pipeline({**base(), "cleaning_validation": {"mode": "off", "schema_version": 1}}).run("Na 141.\n")
    assert "off" in support_text(off).lower()


def test_validator_version_is_part_of_the_key(monkeypatch):
    result = _result(REVIEW)
    key = acknowledgement_key(result, result.text)
    result.validator_version = result.validator_version + ",statements=2"
    assert acknowledgement_key(result, result.text) != key


# --- every output surface --------------------------------------------------------

def _surface_ui_copy(cfg):
    from app_pages import clean as clean_mod  # noqa: F401  (import check only; the UI test file drives it)
    out = service.clean(CHART, config=cfg, record=False)
    return out["text"], out


def _surface_export(cfg):
    result = _result(cfg)
    with pytest.raises(exporters.ReleaseBlocked):
        exporters.release_or_raise(result, result.text)
    return "", {"release": {"status": "blocked"}}


def _surface_clipboard(cfg):
    from chartcleaner import clipboard_watcher
    copied: list[str] = []
    notes: list[str] = []
    w = ClipboardWatcher(paste=lambda: CHART, copy=copied.append, action="auto",
                         notify=lambda t, m: notes.append(m), load_config=lambda: cfg)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(clipboard_watcher, "looks_like_chart", lambda text, cfg: True)  # the short fixture
        event = w.check_once()
    assert not copied, "a blocked result never reaches the clipboard"
    assert event is not None and "blocked" in event.message.lower()
    return "", {"release": {"status": "blocked"}}


def _surface_cli(cfg, tmp_path, capsys):
    import medical_cleaner as cli
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    cleaner = cli.MedicalCleaner(config_path=path, wrap_output=False)
    args = types.SimpleNamespace(stdin=False, file=None, format="text", split_patients=False, delta=False,
                                 trends=False, insights=False, prompt=None, template=None, daily_note=None,
                                 tag=None, stdout=True)
    import pyperclip
    original = pyperclip.paste
    pyperclip.paste = lambda: CHART
    try:
        with pytest.raises(SystemExit) as exc:
            cli._run_pipe(args, cleaner)
    finally:
        pyperclip.paste = original
    assert exc.value.code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "withheld" in captured.err.lower() or "blocked" in captured.err.lower()
    return "", {"release": {"status": "blocked"}}


def _surface_api(cfg, tmp_path):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    from chartcleaner import api, store
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "DATA_DIR", tmp_path)
        mp.setattr(store, "CONFIG_PATH", path)
        mp.setattr(store, "CUSTOM_RULES_DIR", tmp_path / "custom_rules")
        app = FastAPI()
        api.register(app)
        client = TestClient(app, client=("127.0.0.1", 50000))
        client.headers["Authorization"] = f"Bearer {api.get_token()}"
        resp = client.post("/api/v1/clean", json={"text": CHART})
    assert resp.status_code == 409, resp.text
    body = resp.json()
    return body.get("text", ""), body


def _surface_mcp(cfg, tmp_path):
    from chartcleaner import mcp_server, store
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(store, "DATA_DIR", tmp_path)
        mp.setattr(store, "CONFIG_PATH", path)
        mp.setattr(store, "CUSTOM_RULES_DIR", tmp_path / "custom_rules")
        out = mcp_server.clean_chart(CHART)
    return out["text"], out


def _surface_batch(cfg):
    results = batch.run_texts([("chart", CHART)], cfg)
    assert results[0].status == "blocked"
    return results[0].cleaned, {"release": {"status": results[0].release}}


SURFACES = {
    "ui_copy": lambda cfg, tmp_path, capsys: _surface_ui_copy(cfg),
    "export": lambda cfg, tmp_path, capsys: _surface_export(cfg),
    "clipboard": lambda cfg, tmp_path, capsys: _surface_clipboard(cfg),
    "cli": lambda cfg, tmp_path, capsys: _surface_cli(cfg, tmp_path, capsys),
    "api": lambda cfg, tmp_path, capsys: _surface_api(cfg, tmp_path),
    "mcp": lambda cfg, tmp_path, capsys: _surface_mcp(cfg, tmp_path),
    "batch": lambda cfg, tmp_path, capsys: _surface_batch(cfg),
}


@pytest.mark.parametrize("surface", sorted(SURFACES))
def test_release_policy_consistent_across_surfaces(surface, tmp_path, capsys):
    text, payload = SURFACES[surface](BLOCKED, tmp_path, capsys)
    assert "12345678" not in (text or ""), f"{surface}: identifying text left an ordinary payload"
    assert "Na 141" not in (text or "")
    assert payload["release"]["status"] == "blocked"
    dumped = json.dumps(payload, default=str)
    assert "12345678" not in dumped, f"{surface}: the payload carries the chart text somewhere"


def test_local_review_retrieval_is_marked_raw_and_unverified():
    out = service.clean(CHART, config=BLOCKED, record=False, include_raw=True)
    assert out["text"] == "" and out["release"]["status"] == "blocked"
    assert out["raw"]["unverified"] is True and out["raw"]["raw"] is True
    assert "12345678" in out["raw"]["text"]
    plain = service.clean(CHART, config=BLOCKED, record=False)
    assert "raw" not in plain


def test_review_status_result_still_ships_text_with_the_decision_attached():
    out = service.clean(CHART, config=REVIEW, record=False)
    assert out["release"]["status"] == "review" and out["text"]
    assert out["validation"]["rejected"] == 1
    results = batch.run_texts([("c", CHART)], REVIEW)
    assert results[0].status == "ok" and results[0].release == "review" and results[0].needs_review
