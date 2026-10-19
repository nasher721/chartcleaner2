"""Rotating, PHI-safe logging for the desktop app.

Why this exists
---------------
Before this module the only log was ``data/app.log``: stdout redirected into a
file that never rotated and never redacted. On a clinical workstation that is
two problems at once — the file grows until the disk complains, and anything a
page happened to print (a traceback quoting a line of chart text, a stray
``print``) stays on disk forever.

What this gives an IT department
--------------------------------
* **Bounded size** — 2 MB per file, 3 rotated backups, so the log cannot fill a
  disk no matter how long the app runs.
* **Redaction at the formatter** — SSNs, phone numbers, emails, MRNs and long
  digit runs are masked on the way out. This is defence in depth, not a licence
  to log chart text: the app does not log chart text, and this filter makes a
  mistake survivable rather than permanent.
* **Truncation** — a single record is capped, so one oversized message cannot
  bury the surrounding context.
* **A stable read path** — :func:`tail` is what the Doctor page and the
  diagnostics bundle use.

The logger tree is rooted at ``"chartcleaner"``; page code uses
``logging.getLogger("chartcleaner.ui")`` and library code uses
``logging.getLogger("chartcleaner.<module>")``.
"""

from __future__ import annotations

import io
import logging
import logging.handlers
import re
from pathlib import Path

__all__ = [
    "LOG_FILENAME",
    "MAX_BYTES",
    "BACKUPS",
    "MAX_MESSAGE",
    "setup_logging",
    "log_path",
    "tail",
    "write_traceback",
    "capture_console",
    "redact",
]

LOG_FILENAME = "chartcleaner.log"
MAX_BYTES = 2 * 1024 * 1024
BACKUPS = 3
MAX_MESSAGE = 4000

_ROOT = "chartcleaner"
_HANDLER_MARK = "_chartcleaner_handler"

# Order matters: the specific shapes run before the catch-all digit rule.
_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[SSN]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"), "[phone]"),
    (re.compile(r"\b(?:MRN|MR#|Acct(?:ount)?)\s*[:#]?\s*\d{4,}\b", re.IGNORECASE), "[identifier]"),
    (re.compile(r"\b\d{7,}\b"), "[number]"),
)


def redact(text: str) -> str:
    """Mask identifier-shaped substrings in a log message."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class _RedactingFormatter(logging.Formatter):
    """Redact, then cap the size of every record."""

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        rendered = redact(rendered)
        if len(rendered) > MAX_MESSAGE:
            omitted = len(rendered) - MAX_MESSAGE
            rendered = rendered[:MAX_MESSAGE] + f" … [{omitted} chars truncated]"
        return rendered


def log_path(data_dir: str | Path | None = None) -> Path:
    """Where the current log file lives (``store.LOG_FILE`` unless overridden)."""
    if data_dir is not None:
        return Path(data_dir) / LOG_FILENAME
    from . import store

    return getattr(store, "LOG_FILE", store.DATA_DIR / LOG_FILENAME)


class _LazyRotatingFileHandler(logging.handlers.RotatingFileHandler):
    """Create the log directory on first write, not when the app starts.

    In production ``store.ensure_dirs()`` has already made the data directory.
    Tests and portable installs point the log at a path whose parent may not
    exist yet, and creating it eagerly would leave an empty directory behind
    even when nothing is ever logged.
    """

    def emit(self, record: logging.LogRecord) -> None:
        if not self.stream:
            try:
                Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass
        super().emit(record)


def setup_logging(
    data_dir: str | Path | None = None,
    level: int = logging.INFO,
) -> Path:
    """Install the rotating handler on the ``chartcleaner`` logger (idempotent)."""
    path = log_path(data_dir)
    logger = logging.getLogger(_ROOT)
    logger.setLevel(level)
    logger.propagate = False

    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            if Path(getattr(handler, "baseFilename", "")) == path.resolve():
                return path
            logger.removeHandler(handler)
            handler.close()

    try:
        handler = _LazyRotatingFileHandler(
            path,
            maxBytes=MAX_BYTES,
            backupCount=BACKUPS,
            encoding="utf-8",
            delay=True,  # do not create the file until something is logged
        )
    except OSError:
        # A read-only or full data directory must not stop the app from starting;
        # logging falls back to the last-resort handler Python installs.
        return path

    setattr(handler, _HANDLER_MARK, True)
    handler.setFormatter(_RedactingFormatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger.addHandler(handler)
    return path


def write_traceback(title: str, text: str) -> None:
    """Record a traceback through the redacting formatter.

    Used by the page error boundary and the error dialog; both used to append
    raw text straight to ``app.log``. The handler is re-bound first so a
    relocated data directory (tests, portable installs) is respected.
    """
    setup_logging()
    logging.getLogger(f"{_ROOT}.ui").error("%s\n%s", title, text.rstrip())


def tail(lines: int = 200, data_dir: str | Path | None = None) -> str:
    """Last ``lines`` of the log, newest file first, for support bundles."""
    path = log_path(data_dir)
    if not path.is_file():
        return ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            return "".join(handle.readlines()[-max(1, int(lines)):])
    except OSError:
        return ""


class _LoggerStream(io.TextIOBase):
    """A file-like object that forwards whole lines to a logger.

    Windowed bundles have no console, so ``sys.stdout``/``sys.stderr`` used to be
    pointed straight at ``data/app.log``. That file never rotated. Routing the
    streams through the logger instead means stray ``print`` calls and uvicorn's
    own output land in the same rotating, redacted file.
    """

    def __init__(self, logger: logging.Logger, level: int):
        self._logger = logger
        self._level = level
        self._buffer = ""

    def write(self, text: str) -> int:  # type: ignore[override]
        if not isinstance(text, str):
            text = str(text)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            if line.strip():
                self._logger.log(self._level, line)
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self._logger.log(self._level, self._buffer.rstrip())
        self._buffer = ""

    def writable(self) -> bool:
        return True

    def isatty(self) -> bool:
        return False


def capture_console(level: int = logging.INFO) -> io.TextIOBase:
    """A stream to assign to ``sys.stdout`` / ``sys.stderr`` in frozen builds."""
    return _LoggerStream(logging.getLogger(f"{_ROOT}.console"), level)
