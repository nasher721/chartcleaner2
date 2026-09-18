"""Folder watcher: drop chart files into a folder, get cleaned copies out.

Built on watchdog (the standard Python filesystem-events library). Each new
or modified file with a watched extension is ingested, run through the
pipeline, and written to the output folder as ``<stem>_cleaned.txt`` — the
watched folder itself is never written to. Run history records the batch as
``watch:<folder>`` so the Statistics dashboard shows the watcher's work.

Used by the Settings page card (background thread, app lifetime) and by
``clean-chart --watch DIR`` (foreground until Ctrl+C).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

__all__ = ["WatchSpec", "FolderWatcher", "watchdog_available"]

_DEBOUNCE_SECONDS = 1.0


def watchdog_available() -> bool:
    try:
        from importlib.util import find_spec
        return find_spec("watchdog") is not None
    except (ImportError, ValueError):
        return False


@dataclass
class WatchSpec:
    watch_dir: Path
    out_dir: Path
    exts: tuple[str, ...] = (".txt", ".md", ".docx", ".pdf")
    recursive: bool = False


@dataclass
class _Status:
    state: str = "stopped"  # stopped | starting | watching | error
    processed: int = 0
    last_file: str = ""
    last_ts: str = ""
    errors: list[str] = field(default_factory=list)


class FolderWatcher:
    """Watch a folder and clean files as they appear. Run in a daemon thread."""

    def __init__(self, spec: WatchSpec, config: dict, custom_dir: str | Path | None = None):
        if not watchdog_available():
            raise RuntimeError("watchdog is not installed (pip install watchdog).")
        if not spec.watch_dir.is_dir():
            raise NotADirectoryError(str(spec.watch_dir))
        self.spec = spec
        self.config = config
        self.custom_dir = custom_dir
        self.status = _Status()
        self._observer = None
        self._lock = threading.Lock()
        self._pending: dict[Path, float] = {}
        self._stop = threading.Event()

    # -- public API ----------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._observer is not None:
                return
            from watchdog.events import PatternMatchingEventHandler
            from watchdog.observers import Observer

            watcher = self

            class Handler(PatternMatchingEventHandler):
                def __init__(self):
                    super().__init__(
                        patterns=[f"*{e}" for e in watcher.spec.exts],
                        ignore_patterns=[f"*{_CLEANED_SUFFIX}*"],
                        ignore_directories=True,
                    )

                def on_any_event(self, event) -> None:
                    if event.event_type not in ("created", "modified", "moved"):
                        return
                    src = Path(getattr(event, "dest_path", "") or event.src_path)
                    with watcher._lock:
                        watcher._pending[src.resolve()] = time.monotonic()

            self.status.state = "starting"
            self.spec.out_dir.mkdir(parents=True, exist_ok=True)
            self._observer = Observer()
            self._observer.schedule(Handler(), str(self.spec.watch_dir),
                                    recursive=self.spec.recursive)
            self._observer.daemon = True
            self._observer.start()
            self.status.state = "watching"
            threading.Thread(target=self._flush_loop, daemon=True,
                             name="chartcleaner-watcher").start()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            if self._observer is not None:
                self._observer.stop()
                self._observer = None
            self.status.state = "stopped"

    # -- internals -------------------------------------------------------------

    def _flush_loop(self) -> None:
        """Every 0.5 s, process files that have been stable for a full debounce."""
        while not self._stop.is_set():
            self._stop.wait(0.5)
            now = time.monotonic()
            ready: list[Path] = []
            with self._lock:
                for path, seen in list(self._pending.items()):
                    if not path.exists():
                        del self._pending[path]
                    elif now - seen >= _DEBOUNCE_SECONDS:
                        del self._pending[path]
                        ready.append(path)
            for path in ready:
                try:
                    self._process(path)
                except Exception as e:  # one bad file must not kill the watcher
                    self._record_error(path, e)

    def _process(self, path: Path) -> None:
        from .engine import Pipeline
        from . import store

        try:
            if path.resolve().is_relative_to(self.spec.out_dir.resolve()):
                return  # never re-clean our own output
        except (ValueError, OSError):
            return
        from .ingest import IngestError, load_file

        try:
            ing = load_file(path, self.config)
        except IngestError as e:
            self._record_error(path, e)
            return
        pipeline = Pipeline(self.config, custom_dir=self.custom_dir)
        result = pipeline.run(ing.text)
        out_name = f"{path.stem}{_CLEANED_SUFFIX}.txt"
        self.spec.out_dir.mkdir(parents=True, exist_ok=True)
        (self.spec.out_dir / out_name).write_text(result.text, encoding="utf-8")
        store.append_run(result.to_history_dict(f"watch:{self.spec.watch_dir.name}"))
        self.status.processed += 1
        self.status.last_file = path.name
        self.status.last_ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        if ing.warnings:
            self.status.errors.append(f"{path.name}: " + " | ".join(ing.warnings))
            del self.status.errors[:-20]

    def _record_error(self, path: Path, err: Exception) -> None:
        self.status.errors.append(f"{path.name}: {err}")
        del self.status.errors[:-20]


_CLEANED_SUFFIX = "_cleaned"
