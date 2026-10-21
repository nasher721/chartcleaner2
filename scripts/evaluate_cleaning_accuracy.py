"""Run the cleaning-accuracy benchmark and write an aggregate JSON report.

    python scripts/evaluate_cleaning_accuracy.py --split development --mode off --report out.json

The report carries counts, denominators, rates (``null`` when undefined),
fixture/config/evaluator digests, runtime and peak memory — never chart text.
``--split held-out`` is for baseline and release checkpoints only; tune rules
against ``development``. Exit status is 0 when the run completed (the gate
check lives in ``tests/test_accuracy_release.py``), 2 on bad arguments.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chartcleaner.accuracy_eval import (  # noqa: E402
    MODES, SPLITS, evaluate_accuracy, load_cases, minimal_config,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--split", choices=SPLITS, default="development")
    parser.add_argument("--mode", choices=MODES, default="off")
    parser.add_argument("--report", required=True, type=Path, help="where to write the JSON report")
    parser.add_argument("--config", type=Path, default=None,
                        help="base pipeline config for config-driven cases (default: a minimal config)")
    parser.add_argument("--cases", type=Path, default=None, help="alternate cases.json")
    parser.add_argument("--splits", type=Path, default=None, help="alternate splits.json")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.config is not None:
        from chartcleaner.engine import load_config
        cfg = load_config(args.config)
    else:
        cfg = minimal_config()
    cases = load_cases(args.cases, split=args.split, splits_path=args.splits)
    report = evaluate_accuracy(cases, cfg, mode=args.mode, split=args.split)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report.to_dict(), indent=1, sort_keys=True) + "\n",
                           encoding="utf-8")
    if not args.quiet:
        print(f"[{args.split} / {args.mode}] {report.gate_summary()}")
        print(f"coverage: {report.coverage}  labels: {report.label_status}")
        print(f"fixture {report.fixture_digest[:12]}  config {report.config_digest[:12]}  "
              f"{report.runtime_ms:.0f} ms, peak {report.peak_memory_bytes / 1024:.0f} KiB")
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
