"""Evaluation harness: a report card for how well the pipeline cleans.

presidio-research-inspired: given labeled samples (charts with known PHI),
run the configured pipeline on each and check which PHI values are gone.
A value counts as **caught** when it no longer appears in the cleaned output;
anything still present is listed as **missed** so rules can be tightened.

Includes AI engineering evaluation metrics:
- Demographic fairness & cohort-level recall (auditing for disparate impact)
- Non-PHI clinical term preservation rate (specificity check)
- F1 and F2 clinical safety scores (F2 prioritizes recall over precision)

Results are cached to ``data/evaluation.json`` for the Statistics page.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .engine import Pipeline

__all__ = ["evaluate", "load_last_evaluation", "save_evaluation", "evaluation_file"]


def _present(value: str, text: str) -> bool:
    """Case/whitespace-insensitive containment check with digits squashed."""
    def norm(s: str) -> str:
        return re.sub(r"\s+", " ", str(s)).strip().lower()
    return norm(value) in norm(text)


def evaluate(config: dict, samples: list[dict[str, Any]],
             custom_dir: str | Path | None = None) -> dict[str, Any]:
    """Run the pipeline over labeled samples; return a recall & fairness report card."""
    report: dict[str, Any] = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "samples": len(samples),
        "items": 0,
        "caught": 0,
        "by_type": {},
        "missed": [],
        "errors": [],
        "demographics": {},
        "clinical_preservation": {
            "tested_terms": 0,
            "preserved_terms": 0,
            "preservation_rate": 100.0,
        },
        "fairness": {
            "disparate_impact_ratio": 1.0,
            "equity_status": "Equitable",
        },
        "f1": 0.0,
        "f2": 0.0,
    }

    pipeline = Pipeline(config, custom_dir=custom_dir)

    for sample in samples:
        try:
            result = pipeline.run(sample["text"], wrap=False)
        except Exception as e:
            report["errors"].append(f"{sample.get('name')}: {e}")
            continue

        cohort = sample.get("cohort", "standard")
        c_bucket = report["demographics"].setdefault(
            cohort, {"items": 0, "caught": 0, "name_items": 0, "name_caught": 0}
        )

        for item in sample.get("phi", []):
            report["items"] += 1
            vtype = item["type"]
            bucket = report["by_type"].setdefault(vtype, {"items": 0, "caught": 0})
            bucket["items"] += 1
            c_bucket["items"] += 1

            is_name = (vtype == "name")
            if is_name:
                c_bucket["name_items"] += 1

            if _present(item["value"], result.text):
                bucket.setdefault("missed_values", [])
                if len([m for m in report["missed"] if m["type"] == vtype]) < 25:
                    report["missed"].append({
                        "type": vtype,
                        "value": item["value"],
                        "sample": sample.get("name", ""),
                        "cohort": cohort,
                    })
            else:
                report["caught"] += 1
                bucket["caught"] += 1
                c_bucket["caught"] += 1
                if is_name:
                    c_bucket["name_caught"] += 1

        # Check preservation of planted non-PHI clinical terms
        for term in sample.get("clinical_terms", []):
            report["clinical_preservation"]["tested_terms"] += 1
            if _present(term, result.text):
                report["clinical_preservation"]["preserved_terms"] += 1

    # Base recall
    total_items = report["items"]
    caught_items = report["caught"]
    report["recall"] = round(100.0 * caught_items / total_items, 1) if total_items else 0.0

    report["by_type"] = {
        t: {**b, "recall": round(100.0 * b["caught"] / b["items"], 1) if b["items"] else 0.0}
        for t, b in sorted(report["by_type"].items(), key=lambda kv: kv[1]["items"], reverse=True)
    }

    # Demographics and equity analysis
    cohort_recalls = []
    for cname, cstat in report["demographics"].items():
        c_recall = round(100.0 * cstat["caught"] / cstat["items"], 1) if cstat["items"] else 0.0
        name_recall = round(100.0 * cstat["name_caught"] / cstat["name_items"], 1) if cstat["name_items"] else 0.0
        cstat["recall"] = c_recall
        cstat["name_recall"] = name_recall
        cohort_recalls.append(c_recall)

    if cohort_recalls and max(cohort_recalls) > 0:
        min_r = min(cohort_recalls)
        max_r = max(cohort_recalls)
        disparate_ratio = round(min_r / max_r, 3)
        report["fairness"]["disparate_impact_ratio"] = disparate_ratio
        # Four-fifths rule (EEOC standard: ratio >= 0.8 is equitable)
        if disparate_ratio >= 0.90:
            report["fairness"]["equity_status"] = "Optimal Parity (≥ 90%)"
        elif disparate_ratio >= 0.80:
            report["fairness"]["equity_status"] = "Acceptable Equity (≥ 80%)"
        else:
            report["fairness"]["equity_status"] = "Disparate Impact Detected (< 80%)"

    # Clinical term preservation rate (specificity proxy)
    cp = report["clinical_preservation"]
    if cp["tested_terms"] > 0:
        cp["preservation_rate"] = round(100.0 * cp["preserved_terms"] / cp["tested_terms"], 1)

    # Compute F1 and F2 Scores (clinical safety metric)
    # False positives = clinical terms incorrectly wiped out
    false_positives = cp["tested_terms"] - cp["preserved_terms"]
    true_positives = caught_items
    false_negatives = total_items - caught_items

    prec_denom = true_positives + false_positives
    precision = (true_positives / prec_denom) if prec_denom > 0 else 1.0
    recall = (caught_items / total_items) if total_items > 0 else 0.0

    if (precision + recall) > 0:
        report["precision"] = round(100.0 * precision, 1)
        report["f1"] = round(100.0 * (2 * precision * recall) / (precision + recall), 1)
        # F2-score: (1 + 2^2) * (P * R) / (4*P + R)
        report["f2"] = round(100.0 * (5 * precision * recall) / (4 * precision + recall), 1)
    else:
        report["precision"] = 0.0
        report["f1"] = 0.0
        report["f2"] = 0.0

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
