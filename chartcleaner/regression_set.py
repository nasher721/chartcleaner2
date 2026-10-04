"""Your own "known good" charts, re-checked whenever the rules change.

``tests/golden`` protects the shipped defaults; this protects *your* setup.
On the Clean page, *Mark as known good* stores the input and the output you
approved (encrypted, in ``data/known_good/``). :func:`check` re-cleans every
stored chart with a config and lists the ones whose output changed, with a
line diff — run it from Settings, or before saving a config change.

Known-good charts are curated on purpose, so the retention purge leaves them
alone; *Delete stored chart data now* removes them, as does :func:`remove`.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import store

__all__ = ["KnownGood", "Regression", "add", "approve", "list_charts", "list_ids", "remove", "check", "MAX_CHARTS"]

MAX_CHARTS = 50


@dataclass
class KnownGood:
    id: str
    label: str
    ts: str
    input: str
    expected: str
    mode: str = "clean"
    preset: str = ""


@dataclass
class Regression:
    id: str
    label: str
    changed: bool
    error: str = ""
    diff: list[str] = field(default_factory=list)


def _dir() -> Path:
    return store.KNOWN_GOOD_DIR


def _path(chart_id: str) -> Path:
    safe = "".join(c for c in chart_id if c.isalnum() or c in "-_")
    return _dir() / f"{safe}.enc"


def add(input_text: str, expected: str, label: str = "", *, mode: str = "clean",
        preset: str = "") -> KnownGood:
    from . import secure_store

    if len(list_ids()) >= MAX_CHARTS:
        raise ValueError(f"You already keep {MAX_CHARTS} known-good charts; remove one first.")
    digest = hashlib.sha256(input_text.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    chart_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{digest}"
    item = KnownGood(id=chart_id, label=label.strip() or f"Chart {time.strftime('%b %d %H:%M')}",
                     ts=time.strftime("%Y-%m-%dT%H:%M:%S"), input=input_text, expected=expected,
                     mode=mode, preset=preset)
    _dir().mkdir(parents=True, exist_ok=True)
    secure_store.write_text(_path(chart_id), json.dumps(item.__dict__, ensure_ascii=False))
    return item


def list_ids() -> list[str]:
    return sorted(p.stem for p in _dir().glob("*.enc")) if _dir().exists() else []


def list_charts() -> list[KnownGood]:
    from . import secure_store

    out = []
    for chart_id in list_ids():
        try:
            data = json.loads(secure_store.read_text(_path(chart_id)))
            out.append(KnownGood(**{k: data[k] for k in KnownGood.__dataclass_fields__ if k in data}))
        except Exception:
            continue  # another machine's key, or damaged
    return out


def remove(chart_id: str) -> bool:
    p = _path(chart_id)
    if p.exists():
        p.unlink()
        return True
    return False


def check(cfg: dict, custom_dir: str | Path | None = None,
          charts: list[KnownGood] | None = None) -> list[Regression]:
    """Re-clean every known-good chart with ``cfg``; one :class:`Regression` each."""
    from .engine import Pipeline

    results = []
    for kg in list_charts() if charts is None else charts:
        try:
            pipe = Pipeline(cfg, custom_dir=custom_dir, mode=kg.mode)
            now = pipe.run(kg.input, fact_check=False).text
        except Exception as e:
            results.append(Regression(kg.id, kg.label, True, error=f"{type(e).__name__}: {e}"))
            continue
        if now == kg.expected:
            results.append(Regression(kg.id, kg.label, False))
            continue
        diff = list(difflib.unified_diff(kg.expected.splitlines(), now.splitlines(),
                                         "approved", "now", lineterm="", n=1))
        results.append(Regression(kg.id, kg.label, True, diff=diff[:200]))
    return results


def approve(chart_id: str, expected: str) -> bool:
    """Accept ``expected`` as the new approved output for ``chart_id``."""
    from . import secure_store

    p = _path(chart_id)
    if not p.exists():
        return False
    data = json.loads(secure_store.read_text(p))
    data["expected"] = expected
    data["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    secure_store.write_text(p, json.dumps(data, ensure_ascii=False))
    return True
