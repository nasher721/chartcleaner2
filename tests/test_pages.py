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


async def test_scripts_page_builds(user: User):
    await user.open('/scripts')
    await user.should_see('Custom Scripts')


async def test_settings_page_builds(user: User):
    await user.open('/settings')
    await user.should_see('Config backups')
