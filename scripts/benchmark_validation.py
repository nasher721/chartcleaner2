"""Cold/warm time and peak memory for cleaning + validation at scale.

    python scripts/benchmark_validation.py --sizes 100000 1000000 5000000 --report out.json

Synthetic charts only (repeated varied notes). Each size is cleaned in every
mode (off, shadow, enforce) twice — cold (fresh process state) then warm
(shared stage cache, like the app's) — with wall time and tracemalloc peak
recorded per run. The report carries counts and numbers only, never chart
text. Timing gates belong in scheduled jobs, not per-PR assertions.
"""
from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from chartcleaner.engine import Pipeline, load_default_config  # noqa: E402
from chartcleaner.stage_cache import StageCache  # noqa: E402

NOTE = ("Progress Note {n}\n"
        "Electronically signed by Dr. Example on 03/14/2024 08:12\n"
        "Labs: Na {na} mmol/L, K {k} mmol/L, Hgb {hgb} g/dL. Vitals: BP {sys}/{dia}, HR {hr}.\n"
        "Meds: aspirin 81 mg daily. Metoprolol 25 mg BID — held for hypotension.\n"
        "Assessment: Possible pneumonia. Denies shortness of breath. No fever.\n"
        "Plan: continue current regimen. Repeat CXR in AM.\n")


def synth(chars: int) -> str:
    out = []
    total = 0
    n = 0
    while total < chars:
        n += 1
        block = NOTE.format(n=n, na=130 + n % 20, k=3 + n % 4, hgb=9 + n % 5, sys=110 + n % 40,
                            dia=60 + n % 30, hr=70 + n % 30)
        out.append(block)
        total += len(block)
    text = "".join(out)
    return text[:chars].rsplit("\n", 1)[0] + "\n" if len(text) > chars else text


def _run(cfg: dict, text: str, cache: StageCache | None, *, measure_memory: bool = False) -> dict:
    pipe = Pipeline(cfg)
    started = time.perf_counter()
    cpu_started = time.process_time()
    hits, misses = (cache.hits, cache.misses) if cache is not None else (0, 0)
    if measure_memory:
        tracemalloc.start()
    result = pipe.run(text, wrap=False, cache=cache)
    peak = 0
    if measure_memory:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    ms = (time.perf_counter() - started) * 1000
    val = result.validation
    return {"ms": round(ms, 1), "peak_mb": round(peak / 1_048_576, 1) if measure_memory else None,
            "cpu_ms": round((time.process_time() - cpu_started) * 1000, 1),
            "cache_hits": cache.hits - hits if cache is not None else 0,
            "cache_misses": cache.misses - misses if cache is not None else 0,
            "output_chars": len(result.text),
            "coverage": val.coverage.status if val else "off",
            "unresolved_chars": val.coverage.to_dict()["unresolved_chars"] if val else 0,
            "proposals": len(result.ledger.proposals) if result.ledger else 0,
            "issues": len(val.issues) if val else 0}


def measure_case(size: int, mode: str, repeat: int) -> dict:
    """Run once in a fresh worker, then reuse that exact cache for warm timing."""
    cfg = load_default_config()
    cfg["nlp_redaction"] = {"enabled": False}
    cfg["cleaning_validation"] = {"mode": mode, "schema_version": 1}
    text = synth(size)
    cache = StageCache()
    cold = _run(cfg, text, cache)
    warm_runs = [_run(cfg, text, cache) for _ in range(repeat)]
    warm = dict(warm_runs[-1])
    for key in ("ms", "cpu_ms"):
        warm[key] = round(statistics.median(r[key] for r in warm_runs), 1)
    # Memory instrumentation must never replace ordinary timing or hit counts.
    cold["peak_mb"] = _run(cfg, text, StageCache(), measure_memory=True)["peak_mb"]
    warm["peak_mb"] = _run(cfg, text, cache, measure_memory=True)["peak_mb"]
    return {"chars": len(text), "cold": cold, "warm": warm}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[100_000, 1_000_000, 5_000_000])
    parser.add_argument("--report", type=Path)
    parser.add_argument("--repeat", type=int, default=1, help="warm runs to take the median of")
    parser.add_argument("--quiet", action="store_true")
    parser.add_argument("--worker", choices=("off", "shadow", "enforce"), help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.repeat < 1 or any(size < 1 for size in args.sizes):
        parser.error("sizes and repeat must be positive")
    if args.worker:
        print(json.dumps(measure_case(args.sizes[0], args.worker, args.repeat)))
        return 0
    if args.report is None:
        parser.error("--report is required")

    report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "python": sys.version.split()[0],
                    "platform": sys.platform, "sizes": {},
                    "notes": "Synthetic charts; NLP off. Fresh-process pipeline timing excludes imports. "
                             "Memory is measured separately as peak allocations during a run; "
                             "UI presentation and pre-existing cached objects are not included."}
    for size in args.sizes:
        entry = {"modes": {}}
        for mode in ("off", "shadow", "enforce"):
            worker = subprocess.run(
                [sys.executable, str(Path(__file__).resolve()), "--worker", mode,
                 "--sizes", str(size), "--repeat", str(args.repeat)],
                capture_output=True, text=True, check=True)
            result = json.loads(worker.stdout)
            entry["chars"] = result.pop("chars")
            entry["modes"][mode] = result
            if not args.quiet:
                print(f"{size:>9,} {mode:>7}: cold {result['cold']['ms']:>9.0f} ms"
                      f" · warm {result['warm']['ms']:>9.0f} ms"
                      f" · hits {result['warm']['cache_hits']}")
        report["sizes"][str(size)] = entry
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not args.quiet:
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
