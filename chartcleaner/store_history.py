"""Run history, dashboard aggregation and audit-hit suggestions (split from ``store.py``).

One line per cleaning run (JSONL), per-day aggregation for the Statistics page,
and the audit-signature counts behind Pipeline-page rule suggestions.

Path constants live on the :mod:`chartcleaner.store` facade and are read
through it (never copied here) so test isolation via ``monkeypatch.setattr``
keeps working exactly as before.
"""

from __future__ import annotations

import json
import threading
import time

__all__ = [
    "append_run",
    "load_runs",
    "clear_runs",
    "summarize",
    "append_audit_hits",
    "load_audit_hits",
    "dismiss_suggestion",
    "get_suggestions",
]


def _store():
    """The store facade (deferred: store.py re-exports this module)."""
    from . import store
    return store


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def append_run(record: dict) -> None:
    _store().ensure_dirs()
    with _store().STATS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class _RunsCache:
    """Parsed history, extended by only the lines appended since the last read.

    History is append-only, so every page that reads it (Statistics, Pipeline,
    Rules) would otherwise re-parse every run ever recorded — a cost that grows
    with each clean. The cache remembers how many bytes it has parsed and a
    fingerprint of the bytes just before that point; if the file was replaced
    or rewritten (cleared, edited, a different path) it parses from scratch.
    """

    FINGERPRINT = 256

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.reset(None)

    def reset(self, path) -> None:
        self.path = path
        self.offset = 0
        self.head = b""
        self.tail = b""
        self.runs: list[dict] = []
        self.bad = 0

    def read(self, path) -> tuple[list[dict], int]:
        with self.lock, path.open("rb") as f:
            size = f.seek(0, 2)
            if path != self.path or size < self.offset or not self._unchanged(f):
                self.reset(path)
            f.seek(self.offset)
            data = f.read()
            # Only complete lines are cached; a half-written last line is parsed
            # for this read alone (as before) and again once it is finished.
            done = data.rfind(b"\n") + 1
            runs, bad = _parse_runs(data[:done])
            self.runs.extend(runs)
            self.bad += bad
            self.offset += done
            if self.offset:
                f.seek(0)
                self.head = f.read(min(self.offset, self.FINGERPRINT))
                f.seek(self.offset - len(self.head))
                self.tail = f.read(len(self.head))
            partial, partial_bad = _parse_runs(data[done:])
            return self.runs + partial, self.bad + partial_bad

    def _unchanged(self, f) -> bool:
        if not self.offset:
            return True
        f.seek(0)
        if f.read(len(self.head)) != self.head:
            return False
        f.seek(self.offset - len(self.tail))
        return f.read(len(self.tail)) == self.tail


def _parse_runs(data: bytes) -> tuple[list[dict], int]:
    runs: list[dict] = []
    bad = 0
    for line in data.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict):
                runs.append(obj)
        except json.JSONDecodeError:
            bad += 1
    return runs, bad


_RUNS = _RunsCache()


def load_runs() -> list[dict]:
    path = _store().STATS_FILE
    try:
        cached, bad = _RUNS.read(path)
    except FileNotFoundError:
        return []
    # Fresh dicts: callers may annotate a run without touching the cache.
    runs = [dict(r) for r in cached]
    if bad:
        runs.append({"ts": "", "source": f"({bad} corrupt history line(s) skipped)",
                     "chars_before": 0, "chars_after": 0})
    return runs


def clear_runs() -> int:
    """Delete the history file; returns how many runs were removed."""
    count = len(load_runs())
    if _store().STATS_FILE.exists():
        _store().STATS_FILE.unlink()
    with _RUNS.lock:
        _RUNS.reset(None)
    return count


def summarize(runs: list[dict]) -> dict:
    totals = {
        "runs": len(runs),
        "chars_before": 0,
        "chars_after": 0,
        "words_before": 0,
        "words_after": 0,
        "phi": 0,
        "duration_ms": 0.0,
        "fact_runs": 0,
        "fact_flagged": 0,
    }
    fact_losses: dict[str, int] = {}
    phi_by_type: dict[str, int] = {}
    stage_totals: dict[str, int] = {}
    stage_runs: dict[str, int] = {}
    stage_by_day: dict[str, dict[str, int]] = {}
    by_day: dict[str, dict[str, float]] = {}

    for r in runs:
        totals["chars_before"] += r.get("chars_before", 0) or 0
        totals["chars_after"] += r.get("chars_after", 0) or 0
        totals["words_before"] += r.get("words_before", 0) or 0
        totals["words_after"] += r.get("words_after", 0) or 0
        totals["duration_ms"] += r.get("duration_ms", 0) or 0

        day = (r.get("ts") or "")[:10]
        for s in r.get("stages", []):
            if s.get("skipped") or s.get("error"):
                continue
            # batch runs record "before"/"after"; Clean-page runs "chars_before"/"chars_after"
            before = s.get("chars_before", s.get("before", 0)) or 0
            after = s.get("chars_after", s.get("after", 0)) or 0
            delta = before - after
            if delta > 0:
                label = s.get("label") or s.get("id") or "?"
                stage_totals[label] = stage_totals.get(label, 0) + delta
                stage_runs[label] = stage_runs.get(label, 0) + 1
                if day:
                    per = stage_by_day.setdefault(day, {})
                    per[label] = per.get(label, 0) + delta
            for k, v in (s.get("details") or {}).get("phi", {}).items():
                phi_by_type[k] = phi_by_type.get(k, 0) + int(v)
                totals["phi"] += int(v)

        if day:
            bucket = by_day.setdefault(day, {"runs": 0, "chars_removed": 0, "chars_before": 0})
            bucket["runs"] += 1
            bucket["chars_before"] += r.get("chars_before", 0) or 0
            bucket["chars_removed"] += max(0, (r.get("chars_before", 0) or 0) - (r.get("chars_after", 0) or 0))
        fc = r.get("fact_check") or {}
        for sid, n in (fc.get("lost_by_stage") or {}).items():
            fact_losses[sid] = fact_losses.get(sid, 0) + int(n)
        if fc:
            totals["fact_runs"] += 1
            if fc.get("status") != "ok":
                totals["fact_flagged"] += 1

    totals["chars_removed"] = max(0, totals["chars_before"] - totals["chars_after"])
    totals["avg_reduction"] = (
        round(100.0 * (1 - totals["chars_after"] / totals["chars_before"]), 1)
        if totals["chars_before"] else 0.0
    )
    totals["avg_duration_ms"] = round(totals["duration_ms"] / totals["runs"], 0) if totals["runs"] else 0.0

    top_stages = sorted(stage_totals.items(), key=lambda kv: kv[1], reverse=True)[:10]
    days = sorted(by_day)[-30:]
    all_removed = sum(stage_totals.values()) or 1
    stage_share = [{"label": label, "removed": n, "share": round(100.0 * n / all_removed, 1),
                    "runs": stage_runs.get(label, 0),
                    "avg_per_run": round(n / max(1, stage_runs.get(label, 0)))}
                   for label, n in sorted(stage_totals.items(), key=lambda kv: -kv[1])]

    return {
        **totals,
        "phi_by_type": dict(sorted(phi_by_type.items(), key=lambda kv: kv[1], reverse=True)),
        "top_stages": top_stages,
        "days": days,
        "by_day": {d: {**by_day[d], "reduction": round(100.0 * by_day[d]["chars_removed"]
                                                       / by_day[d]["chars_before"], 1)
                       if by_day[d]["chars_before"] else 0.0} for d in days},
        "fact_losses_by_stage": dict(sorted(fact_losses.items(), key=lambda kv: -kv[1])),
        # characters each stage removed, per day (noise reduction over time) and overall
        "stage_by_day": {d: dict(stage_by_day.get(d, {})) for d in days},
        "stage_share": stage_share,
    }


def append_audit_hits(signatures: list[str]) -> None:
    """Record the audit signatures seen in one run (one history line per run)."""
    sigs = sorted({s for s in signatures if s})
    if not sigs:
        return
    _store().ensure_dirs()
    with _store().AUDIT_HITS_FILE.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": _now_iso(), "sigs": sigs}, ensure_ascii=False) + "\n")


def load_audit_hits() -> list[dict]:
    if not _store().AUDIT_HITS_FILE.exists():
        return []
    hits: list[dict] = []
    for line in _store().AUDIT_HITS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if isinstance(obj, dict) and isinstance(obj.get("sigs"), list):
                hits.append(obj)
        except json.JSONDecodeError:
            continue
    return hits


def _load_suggestions_state() -> dict:
    if not _store().SUGGESTIONS_STATE_FILE.exists():
        return {"dismissed": {}}
    try:
        state = json.loads(_store().SUGGESTIONS_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(state, dict) and isinstance(state.get("dismissed"), dict):
            return state
    except (json.JSONDecodeError, OSError):
        pass
    return {"dismissed": {}}


def dismiss_suggestion(signature: str) -> None:
    state = _load_suggestions_state()
    state["dismissed"][signature] = _now_iso()
    _store().ensure_dirs()
    _store().SUGGESTIONS_STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def get_suggestions(days: int = 30, min_runs: int = 3) -> list[dict]:
    """Aggregate recent audit hits into suggestion candidates.

    Returns [{signature, runs, last}] for signatures seen in >= min_runs runs
    within the last `days` days, excluding dismissed ones. Most frequent first.
    """
    cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - days * 86400))
    dismissed = set(_load_suggestions_state().get("dismissed", {}))
    runs_by_sig: dict[str, int] = {}
    last_by_sig: dict[str, str] = {}
    for hit in load_audit_hits():
        ts = str(hit.get("ts") or "")
        if ts and ts < cutoff:
            continue
        for sig in hit.get("sigs", []):
            runs_by_sig[sig] = runs_by_sig.get(sig, 0) + 1
            if sig not in last_by_sig or ts > last_by_sig[sig]:
                last_by_sig[sig] = ts
    out = [
        {"signature": sig, "runs": n, "last": last_by_sig.get(sig, "")}
        for sig, n in runs_by_sig.items()
        if n >= min_runs and sig not in dismissed
    ]
    out.sort(key=lambda s: (-s["runs"], s["signature"]))
    return out
