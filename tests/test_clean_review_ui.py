import pytest
from nicegui.testing import User
from app_pages import clean as clean_mod, common
from chartcleaner.appstate import CLEAN_STATE
from tests.test_safety_stale_ui import _clean, _force_alert
from tests.test_phase8_ui import page  # noqa: F401

pytestmark = pytest.mark.nicegui_main_file('')


async def test_critical_copy_cannot_be_disabled_by_preference(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, 'copy_to_clipboard', lambda *args: copied.append(args))
    monkeypatch.setitem(common.PREFS, 'copy_guard', False)
    _force_alert(monkeypatch)
    await _clean(user, '/review-mandatory')
    await user.should_see('Blocked · review before copying')
    await user.should_not_see('No leftover PHI patterns')
    user.find(marker='copy-as').click()
    user.find(marker='copy-text').click()
    await user.should_see(marker='copy-guard')
    assert not copied


async def test_unknown_note_type_can_be_selected(user: User, page):
    await _clean(user, '/review-note-picker')
    user.find(marker='note-chip').click()
    await user.should_see(marker='note-type-picker')


async def test_edit_invalidates_acknowledgement_before_copy(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, 'copy_to_clipboard', lambda *args: copied.append(args))
    await _clean(user, '/review-edited-copy')
    CLEAN_STATE['fact_ack'] = True
    user.find(marker='edit-result').click()
    out = next(iter(user.find(marker='result-output').elements))
    out.set_value('No clinical values remain.')
    user.find(marker='copy-as').click()
    user.find(marker='copy-text').click()
    await user.should_see(marker='copy-guard')
    assert not copied
    assert CLEAN_STATE['fact_ack'] is False


async def test_edit_introduced_dose_requires_review(user: User, page, monkeypatch):
    copied = []
    monkeypatch.setattr(clean_mod, 'copy_to_clipboard', lambda *args: copied.append(args))
    await _clean(user, '/review-added-dose')
    user.find(marker='edit-result').click()
    out = next(iter(user.find(marker='result-output').elements))
    out.set_value(CLEAN_STATE['result_text'] + '\nWarfarin 500 mg daily.')
    user.find(marker='copy-as').click()
    user.find(marker='copy-text').click()
    assert not copied
    assert CLEAN_STATE['result'].fact_check.introduced
    assert CLEAN_STATE['result'].fact_check.implausible_new
    await user.should_see('Blocked · review before copying')


async def test_redaction_chip_requires_explicit_confirmation(user: User, page, monkeypatch):
    import app as cc_app
    from chartcleaner.engine import Pipeline, load_default_config
    from tests.test_phase8_ui import _route
    text = 'Patient Name: John Smith\nHeart rate 80 bpm.'
    cfg = load_default_config()
    cfg['nlp_redaction'] = {'enabled': False}
    result = Pipeline(cfg).run(text, wrap=False)
    CLEAN_STATE.update(input=text, result=result, result_text=result.text, mode='clean', result_mode='clean')
    await _route(user, '/redaction-inspect', cc_app.clean_page)
    user.find(marker='redaction-chip').click()
    await user.should_see(marker='redaction-inspector')
    assert 'John Smith' not in CLEAN_STATE['result_text']
    user.find(marker='restore-redaction').click()
    assert 'John Smith' in CLEAN_STATE['result_text']
    assert CLEAN_STATE['fact_ack'] is False
