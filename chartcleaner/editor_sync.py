"""Per-page monotonic guard for full editor snapshots."""
from dataclasses import dataclass


@dataclass
class EditorRevisions:
    current: int = 0

    def accept(self, revision) -> bool:
        if type(revision) is not int or revision <= self.current:
            return False
        self.current = revision
        return True
