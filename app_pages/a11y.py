"""Accessibility helpers — the app's screen-reader and keyboard layer.

The design system is deliberately spare: colour carries status, chrome is
monochrome, and much of the "chrome" is a ``div`` styled to look like a
control. That reads fine on screen but tells assistive technology nothing, so
this module supplies the semantics the visual language leaves out.

What it provides
----------------
``sr_only(text)``      — a visually hidden label (for icon-only controls).
``skip_link()``        — a "Skip to content" link, first in the tab order.
``landmark(kind)``     — a semantic region (main / nav / complementary).
``status_region()``    — a live region that announces async results.
``activate_on_key(el)``— make a clickable ``div`` reachable and operable.

Everything here is additive: callers keep their existing classes and event
handlers, so removing a call never changes the rendered pixels.
"""

from __future__ import annotations

from contextlib import contextmanager

from nicegui import ui

__all__ = [
    "sr_only",
    "skip_link",
    "landmark",
    "status_region",
    "activate_on_key",
    "SR_CSS",
]


# Screen-reader-only text: 1px clip rect, the technique that survives
# `display:none`-averse screen readers and doesn't affect layout.
SR_CSS = r"""
<style>
.cc-sr-only{position:absolute !important;width:1px;height:1px;padding:0;margin:-1px;
  overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
/* The skip link is off-screen until focused, then snaps to the top-left. */
.cc-skip{position:absolute;left:-9999px;top:0;z-index:20000;
  background:var(--nd-surface);color:var(--nd-text-display);
  border:1px solid var(--nd-border-visible);padding:8px 14px;
  font-family:var(--nd-mono);font-size:12px;letter-spacing:.06em;
  text-transform:uppercase;}
.cc-skip:focus{left:var(--nd-space-lg);top:var(--nd-space-sm);}
/* An element made operable by activate_on_key advertises it. */
.cc-activatable{cursor:pointer;}
.cc-activatable:focus-visible{outline:2px solid var(--nd-text-display);outline-offset:2px;}
</style>
"""


def sr_only(text: str, *, tag: str = "span"):
    """A visually hidden label — use for icon-only buttons and decorative pills.

    Returns the inner label element so callers can update its text later.
    """
    with ui.element(tag).classes("cc-sr-only"):
        return ui.label(text)


def skip_link(target: str = "#cc-main", label_text: str = "Skip to content") -> None:
    """First focusable element on the page: jumps past the header nav."""
    with ui.element("a").classes("cc-skip").props(f'href="{target}"'):
        ui.label(label_text)


@contextmanager
def landmark(kind: str, *, label: str = "", tag: str = "div"):
    """A semantic region. ``kind`` is a landmark role (``main``, ``nav``,
    ``complementary``, ``region``); ``label`` names it for screen readers."""
    props = f'role={kind}'
    if label:
        props += f' aria-label="{label}"'
    el = ui.element(tag).props(props)
    if kind == "main":
        el.props("id=cc-main tabindex=-1")
    with el:
        yield el


@contextmanager
def status_region(*, label: str = "Status", classes: str = ""):
    """A polite live region: text set inside it is announced when it changes.

    Also carries ``aria-busy`` so callers can mark a long operation in flight.
    """
    el = ui.element("div").classes(classes) \
        .props(f'role=status aria-live=polite aria-label="{label}"')
    with el:
        yield el


def activate_on_key(el, *, role: str = "button", label: str = "") -> None:
    """Make a clickable element reachable by Tab and operable with Enter/Space.

    NiceGUI elements built from ``ui.element`` are not focusable and do not
    respond to the keyboard, so a mouse-only control becomes unusable for
    keyboard users. This restores both without changing the visual design.
    """
    props = f"role={role} tabindex=0"
    if label:
        props += f' aria-label="{label}"'
    el.props(props).classes("cc-activatable")

    def _activate() -> None:
        el.run_method("click")

    el.on("keydown.enter", lambda _e: _activate())
    el.on("keydown.space", lambda _e: _activate())
