"""Process-wide UI state shared across app.py namespaces.

NiceGUI's test harness re-executes app.py via runpy, which creates a fresh
module namespace — module-level dicts in app.py would silently fork between
the test's `import app` and the pages actually being served. Keeping the
cross-page glue here gives both namespaces the same objects.
"""

from __future__ import annotations

__all__ = ["PENDING_RULE", "CLEAN_STATE", "AUTO_LAST", "PIPE_TEST"]

# Draft rule handed from the Clean page / suggestion cards to the Pipeline editor
PENDING_RULE: dict = {}

# Last clean-run state kept for the Clean page across client reconnects
CLEAN_STATE: dict = {"input": "", "result": None, "result_text": "", "audit": None}

# Guard for auto-clean (don't re-clean unchanged text)
AUTO_LAST: dict = {"text": None}

# Test text shared by the Pipeline pattern counters / script tester
PIPE_TEST: dict = {"text": ""}
