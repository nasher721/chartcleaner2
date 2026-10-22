"""The machine-readable release gate for the cleaning-accuracy workflow.

    python scripts/check_accuracy_release.py --report enforce.json \
        --baseline tests/accuracy/baseline-development.json [--require NAME ...] --out gate.json

Exits nonzero when a required gate check fails. Without ``--require`` every
check must pass (full certification, which needs clinician-reviewed labels);
with ``--require`` only the named checks gate the exit — the scheduled job
hard-fails on new critical escapes alone and records the rest.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chartcleaner.accuracy_eval import check_release  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report", required=True, type=Path, help="candidate AccuracyReport JSON")
    parser.add_argument("--baseline", required=True, type=Path,
                        help="the frozen baseline report (same split, mode off)")
    parser.add_argument("--labels", type=Path, default=None,
                        help="labels JSON like {\"status\": \"reviewed\", \"reviewed_by\": ...}")
    parser.add_argument("--require", action="append", default=None, metavar="NAME",
                        help="gate only on these checks (repeatable); default: all")
    parser.add_argument("--out", type=Path, default=None, help="where to write the gate JSON")
    args = parser.parse_args(argv)

    report = json.loads(args.report.read_text(encoding="utf-8"))
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    labels = json.loads(args.labels.read_text(encoding="utf-8")) if args.labels else None

    from chartcleaner.accuracy_eval import AccuracyReport, CaseOutcome
    def _as_report(data: dict) -> AccuracyReport:
        outcomes = [CaseOutcome(**o) for o in data.get("outcomes", [])]
        return AccuracyReport(split=data.get("split", "?"), mode=data.get("mode", "?"),
                              cases=data.get("cases", 0), by_category=data.get("by_category", {}),
                              totals=data.get("totals", {}), label_status=data.get("label_status", {}),
                              coverage=data.get("coverage", {}), fixture_digest=data.get("fixture_digest", ""),
                              config_digest=data.get("config_digest", ""),
                              evaluator_version=data.get("evaluator_version", 0),
                              runtime_ms=data.get("runtime_ms", 0.0),
                              peak_memory_bytes=data.get("peak_memory_bytes", 0), outcomes=outcomes)

    gate = check_release(_as_report(report), _as_report(baseline), labels=labels)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(gate, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    for check in gate["checks"]:
        state = "PASS" if check["ok"] else "FAIL"
        print(f"{state}  {check['name']}: observed {check['observed']} (threshold {check['threshold']})")
    required = args.require
    failed = [c["name"] for c in gate["checks"] if not c["ok"]]
    if required:
        blocking = [n for n in failed if n in required]
    else:
        blocking = failed
    if blocking:
        print(f"release gate failed: {', '.join(blocking)}", file=sys.stderr)
        return 1
    print("release gate: " + ("all checks passed" if not failed else
                              f"passed required checks (non-blocking: {', '.join(failed)})"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
