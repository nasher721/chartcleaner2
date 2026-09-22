"""Every app page must build without raising (NiceGUI builds on client connect).

The ``user`` fixture from nicegui.testing runs the real page functions against
the real config/data folders, so these tests catch runtime errors that an
HTTP status check would miss. pytest.ini sets ``main_file = app.py`` so the
fixture re-runs app.py (re-registering every page) after it resets NiceGUI's
global app state between tests.
"""

import app as cc_app
from chartcleaner.appstate import PENDING_RULE
from nicegui.testing import User


async def test_clean_page_builds(user: User):
    await user.open('/')
    await user.should_see('Clean a chart')


async def test_summary_panel_hidden_before_first_clean(user: User):
    # The Local AI summary expansion only appears once a clean run exists.
    await user.open('/')
    await user.should_not_see('Local AI summary')


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
