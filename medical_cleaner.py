#!/usr/bin/env python3
"""Clean and structure Epic-style EMR text for LLMs (CLI).

This is the command-line entry point. It builds on the same engine as the
local desktop app (``app.py``): config.json rules + custom_rules/*.py script
stages, with per-stage statistics printed after each run.
"""

import argparse
import sys
import time
from pathlib import Path

import pyperclip
from tqdm import tqdm

from chartcleaner import __version__
from chartcleaner.audit import run_audit
from chartcleaner.engine import (
    ConfigError,
    Pipeline,
    load_config,
    validate_config,
)

_SCRIPT_DIR = Path(__file__).resolve().parent


class MedicalCleaner:
    """Backwards-compatible wrapper around the chartcleaner engine."""

    def __init__(self, config_path=None, wrap_output: bool = True):
        path = (_SCRIPT_DIR / "config.json") if config_path is None else Path(config_path)
        try:
            self.config = load_config(path)
        except ConfigError as e:
            print(f"Error: {e}", file=sys.stderr)
            sys.exit(1)
        errors, _warnings = validate_config(self.config)
        if errors:
            print(f"Invalid config ({path}):\n- " + "\n- ".join(errors), file=sys.stderr)
            sys.exit(1)
        self._custom_dir = _SCRIPT_DIR / "custom_rules"
        self._wrap = wrap_output
        self._pipeline = Pipeline(self.config, custom_dir=self._custom_dir)

    def clean(self, text: str) -> str:
        return self.clean_detailed(text).text

    def clean_detailed(self, text: str):
        """Returns a RunResult with .text plus per-stage statistics."""
        return self._pipeline.run(text, wrap=self._wrap)


def _print_summary(result, label: str):
    print(f"{label}: {result.summary()}")
    active = [
        s for s in result.stages
        if not s.skipped and not s.error and s.matches and s.kind != "wrapper"
    ]
    for s in active[:6]:
        extra = f", {sum(s.details.get('phi', {}).values())} PHI" if s.details.get("phi") else ""
        print(f"  - {s.label}: {s.matches} match(es), "
              f"{s.chars_before - s.chars_after:+,} chars{extra}")
    for w in result.warnings:
        print(f"  ! {w}", file=sys.stderr)


def _print_audit(audit, limit: int = 10) -> None:
    for err in audit.errors:
        print(f"  ! audit check failed: {err}", file=sys.stderr)
    total = sum(audit.counts.values())
    if not total:
        print("  Audit: clean — no leftover PHI patterns flagged.")
        return
    print(f"  Audit: {total} finding(s) to double-check:")
    for f in audit.findings[:limit]:
        print(f"    - [{f.check}] line {f.line}: {f.excerpt}")
    if len(audit.findings) > limit:
        print(f"    … {len(audit.findings) - limit} more")


def _format_output(text: str, delta: bool, fmt: str) -> str:
    out = text
    if delta:
        from chartcleaner.delta_engine import extract_note_deltas
        delta_res = extract_note_deltas(out)
        out = delta_res.compact_text
        if delta_res.notes_found > 1:
            print(f"  [Delta Engine] {delta_res.notes_found} notes analyzed: {delta_res.compression_ratio}% copy-forward bloat removed")

    if fmt != "text":
        from chartcleaner.section_parser import parse_clinical_sections
        parsed = parse_clinical_sections(out)
        if fmt == "markdown":
            out = parsed.to_markdown()
        elif fmt == "json":
            out = parsed.to_json()
        elif fmt == "xml":
            out = parsed.to_llm_xml()
    return out


def process_file(file_path: Path, cleaner: MedicalCleaner, output_dir: Path,
                 audit: bool = False, delta: bool = False, out_format: str = "text") -> None:
    """Processes a single file (.txt/.md/.docx/.pdf) and saves the output."""
    try:
        from chartcleaner.ingest import IngestError, load_file
        ing = load_file(file_path, cleaner.config)
        if ing.engine != "text":
            print(f"  [{file_path.name}] ingested via {ing.engine}")
        for w in ing.warnings:
            print(f"  ! {w}", file=sys.stderr)
        result = cleaner.clean_detailed(ing.text)
        final_text = _format_output(result.text, delta, out_format)

        ext = ".json" if out_format == "json" else (".xml" if out_format == "xml" else ".txt")
        out_path = output_dir / f"{file_path.stem}_cleaned{ext}"
        out_path.write_text(final_text, encoding="utf-8")
        _print_summary(result, file_path.name)
        if audit:
            _print_audit(run_audit(result.text, cleaner.config))
    except IngestError as e:
        print(f"Cannot ingest {file_path.name}: {e}", file=sys.stderr)
    except Exception as e:
        print(f"Failed to process {file_path.name}: {e}", file=sys.stderr)


def _run_watch(watch_dir: Path, out_dir: Path | None, cleaner: MedicalCleaner) -> None:
    from chartcleaner.watcher import FolderWatcher, WatchSpec

    out = out_dir or (watch_dir / "cleaned")
    spec = WatchSpec(watch_dir=watch_dir, out_dir=out)
    fw = FolderWatcher(spec, cleaner.config, custom_dir=cleaner._custom_dir)
    print(f"Watching {watch_dir} — cleaned copies go to {out}. Ctrl+C to stop.")
    fw.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        fw.stop()
        print(f"\nStopped. Processed {fw.status.processed} file(s).")


def _run_evaluate(n: int, cleaner: MedicalCleaner) -> None:
    from chartcleaner import benchmark, evaluate as eval_mod

    samples = benchmark.generate(n=n)
    print(f"Generating {n} synthetic labeled charts and running the pipeline…")
    report = eval_mod.evaluate(cleaner.config, samples,
                               custom_dir=cleaner._custom_dir)
    eval_mod.save_evaluation(report)
    print(f"\nRecall: {report['recall']}%  ({report['caught']}/{report['items']} PHI items caught)")
    print(f"Safety F2-Score: {report.get('f2', 0.0)}%  |  F1-Score: {report.get('f1', 0.0)}%")
    cp = report.get("clinical_preservation", {})
    if cp.get("tested_terms"):
        print(f"Clinical Term Preservation: {cp.get('preservation_rate', 100.0)}% "
              f"({cp.get('preserved_terms')}/{cp.get('tested_terms')} medical terms kept)")

    fairness = report.get("fairness", {})
    if fairness:
        print(f"Demographic Equity: {fairness.get('equity_status', 'N/A')} "
              f"(Disparate Impact Ratio: {fairness.get('disparate_impact_ratio', 1.0)})\n")

    print(f"{'type':<14}{'caught':>7}{'of':>6}{'recall':>9}")
    for t, b in report["by_type"].items():
        print(f"{t:<14}{b['caught']:>7}{b['items']:>6}{b['recall']:>8}%")

    demographics = report.get("demographics", {})
    if demographics:
        print("\nDemographic Cohort Breakdown:")
        print(f"{'cohort':<18}{'caught':>7}{'of':>6}{'recall':>9}")
        for cname, cstat in sorted(demographics.items()):
            print(f"{cname:<18}{cstat['caught']:>7}{cstat['items']:>6}{cstat['recall']:>8}%")

    if report["missed"]:
        print("\nMissed (first few):")
        for m in report["missed"][:8]:
            print(f"  - [{m['type']}] {m['value']}  ({m.get('sample', '')} - {m.get('cohort', '')})")
    print(f"\nReport saved to {eval_mod.evaluation_file()}")


def _run_untoken(source: str | None, tokens_file: str | None) -> None:
    from chartcleaner import tokens as tok_mod

    if tokens_file:
        mapping = tok_mod.load_token_map(tokens_file)
    else:
        found = tok_mod.newest_token_map()
        if not found:
            print("No saved token maps found (they are created when tokenization "
                  "is enabled and a chart is cleaned).", file=sys.stderr)
            sys.exit(1)
        path, mapping = found
        print(f"Using newest token map: {path}")
    if source:  # a file path, or '-' for stdin
        text = sys.stdin.read() if source == "-" else Path(source).read_text(encoding="utf-8")
    else:
        text = pyperclip.paste()
    restored, n = tok_mod.untokenize(text, mapping)
    if source:
        out = Path(source).with_suffix(".untokened.txt")
        out.write_text(restored, encoding="utf-8")
        print(f"Restored {n} token(s) → {out}")
    else:
        pyperclip.copy(restored)
        print(f"Restored {n} token(s) — text copied back to clipboard.")


def main():
    parser = argparse.ArgumentParser(
        description="Clean and structure Epic EMR text for LLMs. "
                    "Run the desktop app with: python app.py"
    )
    parser.add_argument("-f", "--file", type=str, help="Path to a single text file to clean.")
    parser.add_argument("-d", "--dir", type=str, help="Path to a directory of text files to clean.")
    parser.add_argument(
        "-o", "--out", type=str, default="cleaned_charts",
        help="Output directory for batch processing.",
    )
    parser.add_argument(
        "--no-wrap", action="store_true",
        help="Omit the <patient_chart> wrapper (plain text only).",
    )
    parser.add_argument(
        "--audit", action="store_true",
        help="Also scan the cleaned output for leftovers and print findings.",
    )
    parser.add_argument("--version", action="version", version=f"chart-cleaner {__version__}")
    parser.add_argument("--watch", type=str, metavar="DIR",
                        help="Watch a folder and clean every new file automatically.")
    parser.add_argument("--evaluate", type=int, nargs="?", const=25, metavar="N",
                        help="Run the synthetic benchmark (N charts, default 25) and print a recall report card.")
    parser.add_argument("--untoken", type=str, nargs="?", const="__clipboard__", metavar="FILE",
                        help="Restore [[Tn]] tokens: FILE, '-' for stdin, or clipboard when omitted.")
    parser.add_argument("--tokens-file", type=str,
                        help="Explicit token map (data/tokens/*.json); default is the newest.")
    parser.add_argument(
        "--delta", action="store_true",
        help="Extract semantic copy-forward deltas between sequential daily progress notes.",
    )
    parser.add_argument(
        "--format", choices=["text", "markdown", "json", "xml"], default="text",
        help="Structured export format for LLMs (text, markdown, json, xml).",
    )
    args = parser.parse_args()

    if args.untoken is not None:
        src = None if args.untoken == "__clipboard__" else args.untoken
        _run_untoken(src, args.tokens_file)
        return
    if args.evaluate is not None:
        cleaner = MedicalCleaner(wrap_output=not args.no_wrap)
        _run_evaluate(args.evaluate, cleaner)
        return

    cleaner = MedicalCleaner(wrap_output=not args.no_wrap)

    if args.watch:
        watch_dir = Path(args.watch).expanduser()
        if not watch_dir.is_dir():
            print(f"Not a directory: {watch_dir}", file=sys.stderr)
            sys.exit(1)
        _run_watch(watch_dir, Path(args.out).expanduser() if args.out else None, cleaner)
        return

    if args.dir:
        input_dir = Path(args.dir)
        output_dir = Path(args.out)
        output_dir.mkdir(exist_ok=True)

        from chartcleaner.ingest import supported_extensions
        files = sorted(f for f in input_dir.iterdir()
                       if f.is_file() and f.suffix.lower() in (*{".txt"}, *supported_extensions()))
        if not files:
            print("No supported files (.txt/.md/.docx/.pdf) found in directory.")
            sys.exit(0)

        print(f"Batch processing {len(files)} files...")
        for file in tqdm(files, desc="Cleaning Charts"):
            process_file(file, cleaner, output_dir, audit=args.audit, delta=args.delta, out_format=args.format)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    elif args.file:
        file_path = Path(args.file)
        output_dir = Path(args.out)
        output_dir.mkdir(exist_ok=True)

        print(f"Processing {file_path.name}...")
        process_file(file_path, cleaner, output_dir, audit=args.audit, delta=args.delta, out_format=args.format)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    else:
        try:
            input_text = pyperclip.paste()
            if not input_text.strip():
                print("Clipboard is empty.")
                sys.exit(1)

            print("Processing clipboard text...")
            result = cleaner.clean_detailed(input_text)
            final_text = _format_output(result.text, args.delta, args.format)
            pyperclip.copy(final_text)

            _print_summary(result, "Clipboard")
            if args.audit:
                _print_audit(run_audit(result.text, cleaner.config))
            print("Cleaned text copied back to clipboard!")

        except Exception as e:
            print(f"Clipboard error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
