"""Every app page must build without raising (NiceGUI builds on client connect).

The ``user`` fixture from nicegui.testing runs the real page functions against
the real config/data folders, so these tests catch runtime errors that an
HTTP status check would miss. pytest.ini sets ``main_file = app.py`` so the
fixture re-runs app.py (re-registering every page) after it resets NiceGUI's
global app state between tests.
"""

import pytest

import app as cc_app
from chartcleaner.appstate import PENDING_RULE
from nicegui.testing import User


async def test_clean_page_builds(user: User):
    await user.open('/')
    await user.should_see('Clean a chart')
    await user.should_see('Full clean')
    await user.should_see('Abbreviations only')


async def test_abbreviations_only_mode_and_switch_back(user: User, monkeypatch, tmp_path):
    from chartcleaner import store
    from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
    from chartcleaner.engine import load_default_config, save_config
    from nicegui import ui

    cfg = load_default_config()
    cfg.setdefault('nlp_redaction', {})['enabled'] = False
    cfg['audit'] = {'enabled': False}
    config_path = tmp_path / 'config.json'
    save_config(cfg, config_path)
    monkeypatch.setattr(store, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(store, 'append_run', lambda _record: None)
    monkeypatch.setattr(store, 'append_audit_hits', lambda _hits: None)
    monkeypatch.setattr(store, 'load_prefs', lambda: dict(store.DEFAULT_PREFS, auto_clean=False))
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    raw = '  MRN: 1234567\n• Anterior cerebral artery  \n'
    CLEAN_STATE.update(input=raw, mode='clean', result=None, result_text='', audit=None)
    try:
        await user.open('/')
        with user.client:
            next(iter(user.find(ui.toggle).elements)).set_value('abbreviations')
        await user.should_see('Apply abbreviations')
        user.find('Apply abbreviations').click()
        await user.should_see('Abbreviations only — other text and formatting preserved.')
        assert CLEAN_STATE['result_text'] == '  MRN: 1234567\n• ACA  \n'
        assert CLEAN_STATE['audit'] is None
        assert [s.id for s in CLEAN_STATE['result'].stages] == ['medical_abbreviations']
        await user.should_see('Copy result')
        await user.should_see('Download .txt')
        await user.should_not_see('Local AI summary')

        with user.client:
            next(iter(user.find(ui.toggle).elements)).set_value('clean')
        assert CLEAN_STATE['result'] is None
        assert AUTO_LAST['text'] is None
        assert CLEAN_STATE['input'] == raw
        user.find(marker='run-clean').click()
        await user.should_see('Local AI summary', retries=50)
        assert 'ACA' in CLEAN_STATE['result_text']
        assert '1234567' not in CLEAN_STATE['result_text']
        assert CLEAN_STATE['result'].wrapped
    finally:
        CLEAN_STATE.clear()
        CLEAN_STATE.update(before)
        AUTO_LAST.clear()
        AUTO_LAST.update(auto_before)


async def test_summary_panel_hidden_before_first_clean(user: User):
    # The Local AI summary expansion only appears once a clean run exists.
    await user.open('/')
    await user.should_not_see('Local AI summary')


@pytest.mark.nicegui_main_file('')
@pytest.mark.parametrize('available,models,saved_model,expected_options,expected_value', [
    (False, [], '', [], None),
    (True, [], '', [], None),
    (True, ['llama3.2:latest'], 'llama3.1', ['llama3.2:latest', 'llama3.1'], 'llama3.1'),
    (True, ['llama3.2:latest'], '', ['llama3.2:latest'], 'llama3.2:latest'),
    (True, ['llama3.2:latest'], 'llama3.2:latest', ['llama3.2:latest'], 'llama3.2:latest'),
    (False, [], 'llama3.1', ['llama3.1'], 'llama3.1'),
])
async def test_clean_renders_with_available_or_saved_models(
        user: User, monkeypatch, tmp_path,
        available, models, saved_model, expected_options, expected_value):
    from chartcleaner import store
    from chartcleaner.appstate import AUTO_LAST, CLEAN_STATE
    from chartcleaner.engine import load_config, load_default_config, save_config
    from chartcleaner.local_llm import LocalLlmClient
    from nicegui import ui

    cfg = load_default_config()
    cfg.setdefault('nlp_redaction', {})['enabled'] = False
    cfg['audit'] = {'enabled': False}
    cfg['local_llm'] = {'model': saved_model}
    config_path = tmp_path / 'config.json'
    save_config(cfg, config_path)
    monkeypatch.setattr(cc_app, 'CONFIG_PATH', config_path)
    monkeypatch.setattr(store, 'append_run', lambda _record: None)
    monkeypatch.setitem(cc_app.PREFS, 'auto_clean', False)
    monkeypatch.setattr(LocalLlmClient, 'is_available', lambda self: available)
    monkeypatch.setattr(LocalLlmClient, 'list_models', lambda self: list(models))
    before, auto_before = dict(CLEAN_STATE), dict(AUTO_LAST)
    CLEAN_STATE.update(input='Patient is stable.\n', mode='clean', result=None,
                       result_text='', audit=None, summary=None, qa=[])
    try:
        @ui.page('/')
        async def clean_fixture():
            await cc_app.clean_page()

        await user.open('/')
        user.find(marker='run-clean').click()
        await user.should_see('Ask this chart', retries=50)
        await user.should_see('Copy result')
        await user.should_see('Download .txt')
        await user.should_not_see('Cleaning failed')
        model_select = next(e for e in user.find(ui.select).elements if e.label == 'Model')
        assert model_select.options == expected_options
        assert model_select.value == expected_value
        assert 'Patient is stable.' in CLEAN_STATE['result_text']
        assert load_config(config_path)['local_llm']['model'] == saved_model
    finally:
        CLEAN_STATE.clear()
        CLEAN_STATE.update(before)
        AUTO_LAST.clear()
        AUTO_LAST.update(auto_before)


def test_summary_panel_widget_signatures():
    """Regression: ui.textarea's first positional arg is `label`, so the panel's
    old `ui.textarea('', label='...')` raised TypeError (multiple values for
    'label') the first time a clean run rendered the panel."""
    import inspect

    from nicegui import ui

    inspect.signature(ui.textarea).bind("Custom prompt (replaces the preset)", value="x")
    inspect.signature(ui.textarea).bind("")  # output box: empty label positional


async def test_pipeline_page_builds(user: User):
    await user.open('/pipeline')
    await user.should_see('Pipeline & Rules')
    await user.should_see('Review checks (post-run audit)')


async def test_pipeline_shows_pending_rule_card(user: User):
    PENDING_RULE.clear()
    PENDING_RULE.update(pattern=r"\b\d{6,}\b",
                        replacement="[REDACTED_NUMBER]", stage="phi_patterns")
    try:
        await user.open('/pipeline')
        await user.should_see('Draft rule from an audit finding')
    finally:
        PENDING_RULE.clear()


async def test_stats_page_builds(user: User):
    await user.open('/stats')
    await user.should_see('Statistics')


async def test_batch_page_builds(user: User):
    await user.open('/batch')
    await user.should_see('Batch clean')
    await user.should_see('No files queued yet.')
    await user.should_see('Clean queued files')


async def test_scripts_page_builds(user: User):
    await user.open('/scripts')
    await user.should_see('Custom Scripts')


async def test_settings_page_builds(user: User):
    await user.open('/settings')
    await user.should_see('Config backups')


async def test_settings_shows_update_controls_and_local_data_boundary(user: User):
    await user.open('/settings')
    await user.should_see('Check for updates')
    await user.should_see('Clinical data stays on this computer')


async def test_source_checkout_update_status_is_local():
    import app as cc_app

    before = dict(cc_app.PREFS)
    try:
        _manifest, status = await cc_app._check_for_updates(automatic=False, force=True)
        assert status["state"] == "source"
        assert cc_app.PREFS["update_status_code"] == "source_mode"
    finally:
        cc_app.PREFS.clear()
        cc_app.PREFS.update(before)


async def test_ai_panels_render_after_clean_run(user: User):
    """The Local AI summary + Ask-this-chart expansions only appear once a clean
    run exists; drive render_results() with a real Pipeline result so the panel
    code (a past TypeError source) actually executes."""
    from chartcleaner.appstate import CLEAN_STATE
    from chartcleaner.engine import Pipeline, load_default_config

    result = Pipeline(load_default_config()).run("MRN: 1234567\nPatient is stable.\n")
    CLEAN_STATE.update(input="x", result=result, result_text=result.text, audit=None,
                       summary=None, qa=[
                           {"q": "Active meds?", "a": "The chart does not say.",
                            "g": {"score": 100.0, "safe": True, "total": 0},
                            "model": "fake", "ms": 1},
                       ])
    try:
        await user.open('/')
        await user.should_see('Local AI summary')
        await user.should_see('Ask this chart')
        await user.should_see('Q: Active meds?')
        await user.should_see('No checkable facts')
    finally:
        CLEAN_STATE.update(input="", result=None, result_text="", audit=None,
                           summary=None, qa=[])


async def test_update_confirmation_requires_confirm_click(user: User):
    from nicegui import ui

    events = []

    @ui.page('/update-confirmation-fixture')
    def confirmation_fixture():
        ui.button('Install update', on_click=lambda: cc_app.confirm_dialog(
            'Download and install Chart Cleaner v2.4.0?', lambda: events.append('handoff')))

    await user.open('/update-confirmation-fixture')
    user.find('Install update').click()
    await user.should_see('Download and install Chart Cleaner v2.4.0?')
    assert events == []
    user.find('Cancel').click()
    assert events == []
    user.find('Install update').click()
    user.find('Confirm').click()
    assert events == ['handoff']
