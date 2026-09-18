"""Evaluation harness: a report card for how well the pipeline cleans.

presidio-research-inspired: given labeled samples (charts with known PHI),
run the configured pipeline on each and check which PHI values are gone.
A value counts as **caught** when it no longer appears in the cleaned output;
anything still present is listed as **missed** so rules can be tightened.
Results are cached to ``data/evaluation.json`` for the Statistics page.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .engine import Pipeline

__all__ = ["evaluate", "load_last_evaluation", "save_evaluation", "evaluation_file"]


def _present(value: str, text: str) -> bool:
    """Case/whitespace-insensitive containment check with digits squashed."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", s).strip().lower()
    return norm(value) in norm(text)


def evaluate(config: dict, samples: list[dict],
             custom_dir: str | Path | None = None) -> dict:
    """Run the pipeline over labeled samples; return a recall report card."""
    report = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "samples": len(samples),
        "items": 0,
        "caught": 0,
        "by_type": {},
        "missed": [],
        "errors": [],
    }
    pipeline = Pipeline(config, custom_dir=custom_dir)
    for sample in samples:
        try:
            result = pipeline.run(sample["text"], wrap=False)
        except Exception as e:
            report["errors"].append(f"{sample.get('name')}: {e}")
            continue
        for item in sample.get("phi", []):
            report["items"] += 1
            vtype = item["type"]
            bucket = report["by_type"].setdefault(vtype, {"items": 0, "caught": 0})
            bucket["items"] += 1
            if _present(item["value"], result.text):
                bucket.setdefault("missed_values", [])
                if len([m for m in report["missed"] if m["type"] == vtype]) < 25:
                    report["missed"].append({"type": vtype, "value": item["value"],
                                             "sample": sample.get("name", "")})
            else:
                report["caught"] += 1
                bucket["caught"] += 1
    report["recall"] = round(100.0 * report["caught"] / report["items"], 1) if report["items"] else 0.0
    report["by_type"] = {
        t: {**b, "recall": round(100.0 * b["caught"] / b["items"], 1) if b["items"] else 0.0}
        for t, b in sorted(report["by_type"].items(), key=lambda kv: kv[1]["items"], reverse=True)
    }
    return report


def evaluation_file() -> Path:
    from . import store
    return store.DATA_DIR / "evaluation.json"


def save_evaluation(report: dict) -> Path:
    from . import store
    store.ensure_dirs()
    path = evaluation_file()
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_last_evaluation() -> dict | None:
    path = evaluation_file()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and "recall" in data else None
    except (json.JSONDecodeError, OSError):
        return None
