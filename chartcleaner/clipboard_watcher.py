"""Clean Epic text as soon as it is copied (Settings → Clipboard watcher).

While on, the clipboard is checked every 0.75 s. Text that looks like an Epic
chart — at least 200 characters and either two EMR chrome lines matched by the
user's ``emr_line_metadata`` rules, two clinical section headers, or a
confidently detected note type — is cleaned with the current rules and the
clipboard is replaced with the result (action ``"auto"``, the default), or
only announced (``"notify"``). The original is kept so :meth:`undo` can put it
back. The watcher never re-cleans its own output.

The clipboard functions are injectable so tests never touch the real one.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from . import service
from .note_type import detect

__all__ = ["looks_like_chart", "ClipboardWatcher", "WatchEvent", "desktop_notify"]

POLL_SECONDS = 0.75
MIN_CHARS = 200


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()


def looks_like_chart(text: str, cfg: dict) -> bool:
    if len(text) < MIN_CHARS:
        return False
    chrome = 0
    for pattern in cfg.get("emr_line_metadata") or []:
        try:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                chrome += 1
        except re.error:
            continue
        if chrome >= 2:
            return True
    headers = 0
    for header in cfg.get("clinical_headers") or []:
        try:
            if re.search(rf"^\s*{header}\s*:?\s*$", text, re.IGNORECASE | re.MULTILINE):
                headers += 1
        except re.error:
            continue
        if headers >= 2:
            return True
    return detect(text, cfg).note_type is not None


def desktop_notify(title: str, message: str) -> None:
    """Best-effort system notification (macOS); silent elsewhere."""
    if sys.platform == "darwin":
        def quote(value: str) -> str:
            return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'
        script = f"display notification {quote(message)} with title {quote(title)}"
        try:
            subprocess.run(["osascript", "-e", script], timeout=5, check=False,
                           capture_output=True)
        except Exception:
            pass


@dataclass
class WatchEvent:
    when: float
    message: str
    chars_before: int = 0
    chars_after: int = 0


@dataclass
class ClipboardWatcher:
    paste: Callable[[], str]
    copy: Callable[[str], None]
    action: str = "auto"
    notify: Callable[[str, str], None] = desktop_notify
    load_config: Callable[[], dict] = field(default=lambda: service.load_active_config())
    events: list[WatchEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_seen = ""
        self._last_output = ""
        self._original: str | None = None

    # -- one step (also what tests drive directly) ---------------------------------
    def check_once(self) -> WatchEvent | None:
        try:
            text = self.paste() or ""
        except Exception:
            return None
        digest = _digest(text)
        if digest in (self._last_seen, self._last_output):
            return None
        self._last_seen = digest
        cfg = self.load_config()
        if not looks_like_chart(text, cfg):
            return None
        if self.action != "auto":
            event = WatchEvent(time.time(), "Epic text copied — open Chart Cleaner to clean it.",
                               len(text))
        else:
            out = service.clean(text, config=cfg, wrap=False, source="clipboard")
            self._original = text
            self._last_output = _digest(out["text"])
            self.copy(out["text"])
            event = WatchEvent(time.time(), f"Chart cleaned on the clipboard ({out['summary']}). "
                               "Undo in Chart Cleaner → Settings.", len(text), len(out["text"]))
        self.events = (self.events + [event])[-20:]
        self.notify("Chart Cleaner", event.message)
        return event

    def undo(self) -> bool:
        """Put the original (uncleaned) text back on the clipboard."""
        if self._original is None:
            return False
        self._last_seen = _digest(self._original)  # don't clean it again
        self.copy(self._original)
        self._original = None
        return True

    # -- background thread -------------------------------------------------------
    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        try:  # whatever is on the clipboard when watching starts is not "newly copied"
            self._last_seen = _digest(self.paste() or "")
        except Exception:
            pass
        self._thread = threading.Thread(target=self._loop, daemon=True, name="clipboard-watcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(POLL_SECONDS):
            try:
                self.check_once()
            except Exception:
                continue  # a bad clipboard read must never kill the watcher
