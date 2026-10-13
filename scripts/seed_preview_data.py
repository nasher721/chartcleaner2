"""Dev helper: write synthetic run history so the Statistics page has something
to draw while reviewing the UI. Not part of the test suite.

    CHARTCLEANER_DATA_DIR=<dir> .venv/bin/python scripts/seed_preview_data.py
"""
from __future__ import annotations

import json
import random
import os
from datetime import datetime, timedelta
from pathlib import Path

random.seed(7)

DATA_DIR = Path(os.environ.get("CHARTCLEANER_DATA_DIR", ".pytest_tmp/preview_data"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

STAGES = [
    ("phi_redaction", "PHI redaction"),
    ("header_furniture", "Header & furniture"),
    ("result_blocks", "Result blocks"),
    ("duplicate_lines", "Duplicate lines"),
    ("abbreviations", "Abbreviations"),
    ("clinical_noise", "Clinical noise"),
    ("lab_units", "Lab units"),
    ("trailing_whitespace", "Whitespace"),
]
PHI_TYPES = {"MRN": 3, "DOB": 2, "PHONE": 2, "NAME": 4, "ADDRESS": 2, "EMAIL": 1}

start = datetime.now() - timedelta(days=13)
runs: list[dict] = []
for i in range(35):
    when = start + timedelta(days=i * 13 / 35, hours=random.uniform(8, 19))
    before = random.randint(7_500, 24_000)
    reduction = random.uniform(18.0, 44.0)
    after = int(before * (1 - reduction / 100))
    stages = []
    remaining_before, remaining_after = before, after
    for sid, label in STAGES:
        cut = int(remaining_before * random.uniform(0.02, 0.22))
        s_after = max(0, remaining_before - cut)
        stage = {"id": sid, "label": label, "chars_before": remaining_before,
                 "chars_after": s_after, "skipped": False}
        if sid == "phi_redaction":
            stage["details"] = {"phi": dict(PHI_TYPES)}
        stages.append(stage)
        remaining_before = s_after
    runs.append({
        "ts": when.strftime("%Y-%m-%d %H:%M:%S"),
        "source": random.choice(["pasted", "pasted", "upload", "watcher", "batch"]),
        "chars_before": before,
        "chars_after": after,
        "words_before": before // 6,
        "words_after": after // 6,
        "reduction": round(reduction, 1),
        "duration_ms": random.randint(120, 980),
        "stages": stages,
        "fact_check": {"status": "ok" if i % 7 else "flagged",
                       "lost_by_stage": {"clinical_noise": 2, "duplicate_lines": 1}},
    })

(DATA_DIR / "stats.jsonl").write_text(
    "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in runs), encoding="utf-8")
print(f"wrote {len(runs)} runs to {DATA_DIR / 'stats.jsonl'}")
