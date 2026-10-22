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
        "Plan: continue current regimen. Repeat CXR in AM.\n"
        "This message is confidential and intended only for the recipient.\n\n")


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
            "coverage": val.coverage.status if val else "off",
            "unresolved_chars": len(getattr(val.coverage, "unresolved_spans", ()) or ()),
            "proposals": len(result.ledger.proposals) if result.ledger else 0,
            "issues": len(val.issues) if val else 0}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--sizes", type=int, nargs="+", default=[100_000, 1_000_000, 5_000_000])
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--repeat", type=int, default=1, help="warm runs to take the median of")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    base = load_default_config()
    base["nlp_redaction"] = {"enabled": False}
    report: dict = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "python": sys.version.split()[0],
                    "platform": sys.platform, "sizes": {}, "notes": "synthetic charts only"}
    for size in args.sizes:
        text = synth(size)
        entry: dict = {"chars": len(text), "modes": {}}
        for mode in ("off", "shadow", "enforce"):
            cfg = {**base, "cleaning_validation": {"mode": mode, "schema_version": 1}}
            # Time without tracemalloc (it inflates wall time); measure peak
            # memory in a separate pass.
            cold = _run(cfg, text, None)
            cold.update(_run(cfg, text, None, measure_memory=True))
            warm_runs = [_run(cfg, text, StageCache()) for _ in range(args.repeat)]
            warm = {k: (round(statistics.median(r[k] for r in warm_runs), 1)
                        if isinstance(warm_runs[0][k], (int, float)) and warm_runs[0][k] is not None
                        else warm_runs[0][k])
                    for k in warm_runs[0]}
            warm.update(_run(cfg, text, StageCache(), measure_memory=True))
            entry["modes"][mode] = {"cold": cold, "warm": warm}
            if not args.quiet:
                print(f"{size:>9,} {mode:>7}: cold {cold['ms']:>9.0f} ms / {cold['peak_mb']:>7.1f} MB"
                      f" · warm {warm['ms']:>9.0f} ms / {warm['peak_mb']:>7.1f} MB"
                      f" · coverage {warm['coverage']}")
        report["sizes"][str(size)] = entry
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if not args.quiet:
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
