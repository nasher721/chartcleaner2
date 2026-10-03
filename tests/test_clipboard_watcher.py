"""Clipboard watcher with a fake clipboard."""

from pathlib import Path

from chartcleaner.clipboard_watcher import ClipboardWatcher, looks_like_chart
from chartcleaner.engine import load_default_config

ROOT = Path(__file__).resolve().parent.parent
CHART = (ROOT / "sample_chart.txt").read_text(encoding="utf-8")


def _cfg() -> dict:
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    return cfg


class FakeClipboard:
    def __init__(self, text: str = ""):
        self.text = text
        self.writes = 0

    def paste(self) -> str:
        return self.text

    def copy(self, text: str) -> None:
        self.text = text
        self.writes += 1


def _watcher(clip: FakeClipboard, action: str = "auto"):
    notes: list[str] = []
    w = ClipboardWatcher(paste=clip.paste, copy=clip.copy, action=action,
                         notify=lambda _t, m: notes.append(m), load_config=_cfg)
    return w, notes


def test_chart_detection():
    assert looks_like_chart(CHART, _cfg())
    assert not looks_like_chart("Meeting at 3pm, bring the slides. " * 10, _cfg())
    assert not looks_like_chart("Progress Note\nSubjective:\n", _cfg())  # too short


def test_auto_cleans_once_and_undo_restores():
    clip = FakeClipboard(CHART)
    w, notes = _watcher(clip)
    event = w.check_once()
    assert event and clip.text != CHART and "Chart cleaned" in notes[0]
    assert w.check_once() is None and clip.writes == 1  # its own output is not re-cleaned
    assert w.undo() and clip.text == CHART
    assert w.check_once() is None  # the restored original is not cleaned again
    assert not w.undo()


def test_notify_mode_leaves_the_clipboard_alone():
    clip = FakeClipboard(CHART)
    w, notes = _watcher(clip, action="notify")
    assert w.check_once() and clip.text == CHART and clip.writes == 0 and notes


def test_ordinary_text_is_ignored_and_start_skips_existing_clipboard():
    clip = FakeClipboard("hello " * 50)
    w, notes = _watcher(clip)
    assert w.check_once() is None and not notes
    clip.text = CHART
    w.start()  # whatever is already on the clipboard is not "newly copied"
    try:
        assert w.check_once() is None and clip.writes == 0
    finally:
        w.stop()
    assert not w.running


def test_clipboard_errors_are_swallowed():
    def broken() -> str:
        raise RuntimeError("no clipboard")
    w = ClipboardWatcher(paste=broken, copy=lambda _t: None, notify=lambda *_: None, load_config=_cfg)
    assert w.check_once() is None
