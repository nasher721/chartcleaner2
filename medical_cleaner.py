#!/usr/bin/env python3
"""Clean and structure Epic-style EMR text for LLMs (CLI).

This is the command-line entry point. It builds on the same engine as the
local desktop app (``app.py``): config.json rules + custom_rules/*.py script
stages, with per-stage statistics printed after each run.
"""

import argparse
import sys
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


def process_file(file_path: Path, cleaner: MedicalCleaner, output_dir: Path,
                 audit: bool = False) -> None:
    """Processes a single text file and saves the output."""
    try:
        raw_text = file_path.read_text(encoding="utf-8")
        result = cleaner.clean_detailed(raw_text)
        out_path = output_dir / f"{file_path.stem}_cleaned{file_path.suffix}"
        out_path.write_text(result.text, encoding="utf-8")
        _print_summary(result, file_path.name)
        if audit:
            _print_audit(run_audit(result.text, cleaner.config))
    except Exception as e:
        print(f"Failed to process {file_path.name}: {e}", file=sys.stderr)


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
    args = parser.parse_args()

    cleaner = MedicalCleaner(wrap_output=not args.no_wrap)

    if args.dir:
        input_dir = Path(args.dir)
        output_dir = Path(args.out)
        output_dir.mkdir(exist_ok=True)

        files = list(input_dir.glob("*.txt"))
        if not files:
            print("No .txt files found in directory.")
            sys.exit(0)

        print(f"Batch processing {len(files)} files...")
        for file in tqdm(files, desc="Cleaning Charts"):
            process_file(file, cleaner, output_dir, audit=args.audit)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    elif args.file:
        file_path = Path(args.file)
        output_dir = Path(args.out)
        output_dir.mkdir(exist_ok=True)

        print(f"Processing {file_path.name}...")
        process_file(file_path, cleaner, output_dir, audit=args.audit)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    else:
        try:
            input_text = pyperclip.paste()
            if not input_text.strip():
                print("Clipboard is empty.")
                sys.exit(1)

            print("Processing clipboard text...")
            result = cleaner.clean_detailed(input_text)
            pyperclip.copy(result.text)

            _print_summary(result, "Clipboard")
            if args.audit:
                _print_audit(run_audit(result.text, cleaner.config))
            print("Cleaned text copied back to clipboard!")

        except Exception as e:
            print(f"Clipboard error: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
