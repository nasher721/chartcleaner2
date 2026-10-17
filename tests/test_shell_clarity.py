"""Shared chrome exposes clear states and semantic page navigation."""
import pytest
from nicegui import ui
from nicegui.testing import User
from app_pages import common

pytestmark = pytest.mark.nicegui_main_file("")

async def test_shell_has_heading_and_route_links(user: User):
    @ui.page('/shell-clarity')
    def page():
        with common.shell('Clean — Prepare a chart', 'clean'):
            ui.label('body')
    await user.open('/shell-clarity')
    elements = list(user.find().elements)
    assert any(e.tag == 'h1' for e in elements)
    links = [e for e in elements if 'cc-navlink' in e._classes]
    assert links and all(e.tag == 'a' and e._props.get('href') for e in links)
    assert not any(e._props.get('role') == 'tab' for e in links)
    strip = next(e for e in elements if 'status-strip' in e._markers)
    assert all(e._props.get('role') != 'button' for e in strip.default_slot.children)
    labels = [getattr(e, '_text', '') for e in elements]
    assert 'Clipboard monitoring off' in labels
    assert 'Stored data encrypted' in labels
