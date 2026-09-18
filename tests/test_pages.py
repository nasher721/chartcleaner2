"""Every app page must build without raising (NiceGUI builds on client connect).

The ``user`` fixture from nicegui.testing runs the real page functions against
the real config/data folders, so these tests catch runtime errors that an
HTTP status check would miss. The ``module_under_test`` marker makes NiceGUI
re-register all pages after it resets its global app state between tests.
"""

import pytest

import app as cc_app
from nicegui.testing import User

pytestmark = pytest.mark.module_under_test(cc_app)


async def test_clean_page_builds(user: User):
    await user.open('/')
    await user.should_see('Clean a chart')


async def test_pipeline_page_builds(user: User):
    await user.open('/pipeline')
    await user.should_see('Pipeline & Rules')
    await user.should_see('Review checks (post-run audit)')


async def test_pipeline_shows_pending_rule_card(user: User):
    cc_app.PENDING_RULE.clear()
    cc_app.PENDING_RULE.update(pattern=r"\b\d{6,}\b",
                               replacement="[REDACTED_NUMBER]", stage="phi_patterns")
    try:
        await user.open('/pipeline')
        await user.should_see('Draft rule from an audit finding')
    finally:
        cc_app.PENDING_RULE.clear()


async def test_stats_page_builds(user: User):
    await user.open('/stats')
    await user.should_see('Statistics')


async def test_scripts_page_builds(user: User):
    await user.open('/scripts')
    await user.should_see('Custom Scripts')


async def test_settings_page_builds(user: User):
    await user.open('/settings')
    await user.should_see('Config backups')
