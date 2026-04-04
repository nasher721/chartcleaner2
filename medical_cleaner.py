import re
import sys
import json
import argparse
import pyperclip
from pathlib import Path
from tqdm import tqdm
from thefuzz import fuzz
from presidio_analyzer import AnalyzerEngine
from presidio_analyzer.nlp_engine import SpacyNlpEngine
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

_SCRIPT_DIR = Path(__file__).resolve().parent

_REQUIRED_CONFIG_KEYS = (
    "emr_line_metadata",
    "boilerplate",
    "epic_phi_patterns",
    "literal_replacements",
    "clinical_headers",
)


def _compile_or_exit(pattern: str, flags: int, context: str):
    try:
        return re.compile(pattern, flags=flags)
    except re.error as e:
        print(f"Invalid regular expression ({context}): {e}")
        sys.exit(1)


class MedicalCleaner:
    def __init__(self, config_path=None, wrap_output: bool = True):
        self.wrap_output = wrap_output
        path = (_SCRIPT_DIR / "config.json") if config_path is None else Path(config_path)

        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = f.read()
        except FileNotFoundError:
            print(f"Error: {path} not found. Place config.json next to medical_cleaner.py or pass a path.")
            sys.exit(1)

        try:
            self.config = json.loads(raw)
        except json.JSONDecodeError as e:
            print(f"Invalid JSON in {path}: {e}")
            sys.exit(1)

        missing = [k for k in _REQUIRED_CONFIG_KEYS if k not in self.config]
        if missing:
            print(f"config.json missing required key(s): {', '.join(missing)}")
            sys.exit(1)

        cfg = self.config
        self.emr_meta_regex = [
            _compile_or_exit(p, re.IGNORECASE | re.MULTILINE, f"emr_line_metadata[{i}]")
            for i, p in enumerate(cfg["emr_line_metadata"])
        ]
        self.boiler_regex = [
            _compile_or_exit(p, re.IGNORECASE | re.DOTALL | re.MULTILINE, f"boilerplate[{i}]")
            for i, p in enumerate(cfg["boilerplate"])
        ]
        self.phi_regex = []
        for i, pair in enumerate(cfg["epic_phi_patterns"]):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                print(f"epic_phi_patterns[{i}] must be a two-element [pattern, replacement] list.")
                sys.exit(1)
            r = _compile_or_exit(pair[0], re.IGNORECASE, f"epic_phi_patterns[{i}]")
            self.phi_regex.append((r, pair[1]))
        self.literal_regex = []
        for i, pair in enumerate(cfg["literal_replacements"]):
            if not isinstance(pair, (list, tuple)) or len(pair) != 2:
                print(f"literal_replacements[{i}] must be a two-element [pattern, replacement] list.")
                sys.exit(1)
            r = _compile_or_exit(pair[0], re.IGNORECASE, f"literal_replacements[{i}]")
            self.literal_regex.append((r, pair[1]))
        self.header_regex = []
        for i, h in enumerate(cfg["clinical_headers"]):
            pat = rf"^\s*({h})\s*:?\s*$"
            self.header_regex.append(
                _compile_or_exit(pat, re.IGNORECASE | re.MULTILINE, f"clinical_headers[{i}] ({h!r})")
            )

        self._dup_note_cfg = self.config.get("duplicate_note_detection") or {}
        self._dup_note_splitter = None
        if self._dup_note_cfg.get("enabled") is not False:
            sp = self._dup_note_cfg.get(
                "split_pattern",
                r"(?=^(?:Progress Notes by .+|Attestation signed by .+)$)",
            )
            self._dup_note_splitter = _compile_or_exit(
                sp, re.MULTILINE | re.IGNORECASE, "duplicate_note_detection.split_pattern"
            )

        nlp_engine = SpacyNlpEngine(
            models=[{"lang_code": "en", "model_name": "en_core_web_sm"}]
        )
        self.analyzer = AnalyzerEngine(nlp_engine=nlp_engine)
        self.anonymizer = AnonymizerEngine()
        self.allow_list = set(term.lower() for term in self.config.get("nlp_allow_list", []))

    def _body_after_first_line(self, segment: str) -> str:
        s = segment.strip()
        if not s or "\n" not in s:
            return ""
        return s.split("\n", 1)[1].strip()

    def _apply_duplicate_note_dedup(self, text: str) -> str:
        """Drop near-duplicate Epic note blocks (e.g. same progress note pasted twice) by body text."""
        if self._dup_note_splitter is None:
            return text
        cfg = self._dup_note_cfg
        min_body = int(cfg.get("min_body_chars", 400))
        threshold = int(cfg.get("similarity_threshold", 90))
        parts = self._dup_note_splitter.split(text)
        if len(parts) <= 1:
            return text

        kept: list[str] = [parts[0]]
        seen_bodies: list[str] = []

        for seg in parts[1:]:
            body = self._body_after_first_line(seg)
            if len(body) < min_body:
                kept.append(seg)
                continue
            b_low = body.lower()
            if any(fuzz.ratio(b_low, p.lower()) >= threshold for p in seen_bodies):
                continue
            seen_bodies.append(body)
            kept.append(seg)

        return "".join(kept)

    def _apply_fuzzy_deduplication(self, text: str, threshold: int = 95) -> str:
        """Removes note bloat by matching paragraphs that are statistically similar (copy-forwarded)."""
        paragraphs = text.split("\n\n")
        deduped = []

        for p in paragraphs:
            p_clean = p.strip()
            if len(p_clean) < 100:
                deduped.append(p)
                continue

            is_duplicate = False
            for saved_p in deduped:
                if len(saved_p.strip()) > 100:
                    similarity = fuzz.ratio(p_clean.lower(), saved_p.strip().lower())
                    if similarity >= threshold:
                        is_duplicate = True
                        break

            if not is_duplicate:
                deduped.append(p)

        return "\n\n".join(deduped)

    def _apply_nlp_redaction(self, text: str) -> str:
        """Uses Presidio to redact entities while respecting the custom medical allow-list."""
        results = self.analyzer.analyze(
            text=text, entities=["PERSON", "PHONE_NUMBER", "EMAIL_ADDRESS"], language="en"
        )

        filtered_results = [
            res
            for res in results
            if text[res.start : res.end].lower() not in self.allow_list
        ]

        operators = {
            "PERSON": OperatorConfig("replace", {"new_value": "[REDACTED_NAME]"}),
            "PHONE_NUMBER": OperatorConfig("replace", {"new_value": "[REDACTED_PHONE]"}),
            "EMAIL_ADDRESS": OperatorConfig("replace", {"new_value": "[REDACTED_EMAIL]"}),
        }

        return self.anonymizer.anonymize(
            text=text, analyzer_results=filtered_results, operators=operators
        ).text

    def clean(self, text: str) -> str:
        t = text

        for r in self.emr_meta_regex:
            t = r.sub("", t)
        for r in self.boiler_regex:
            t = r.sub("", t)

        for r, replacement in self.phi_regex:
            t = r.sub(replacement, t)

        t = self._apply_nlp_redaction(t)

        for r, replacement in self.literal_regex:
            t = r.sub(replacement, t)

        t = re.sub(r"[ \t]+$", "", t, flags=re.MULTILINE)
        t = re.sub(r"\n{3,}", "\n\n", t).strip()

        t = self._apply_duplicate_note_dedup(t)

        t = self._apply_fuzzy_deduplication(t)

        for r in self.header_regex:
            t = r.sub(r"## \1", t)
        t = re.sub(r"^\s*[•\-*]\s+", "- ", t, flags=re.MULTILINE)

        if self.wrap_output:
            return f"<patient_chart>\n{t}\n</patient_chart>"
        return t


def process_file(file_path: Path, cleaner: MedicalCleaner, output_dir: Path):
    """Processes a single text file and saves the output."""
    try:
        raw_text = file_path.read_text(encoding="utf-8")
        clean_text = cleaner.clean(raw_text)

        out_path = output_dir / f"{file_path.stem}_cleaned{file_path.suffix}"
        out_path.write_text(clean_text, encoding="utf-8")
    except Exception as e:
        print(f"Failed to process {file_path.name}: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Clean and structure Epic EMR text for LLMs.")
    parser.add_argument("-f", "--file", type=str, help="Path to a single text file to clean.")
    parser.add_argument("-d", "--dir", type=str, help="Path to a directory of text files to clean.")
    parser.add_argument(
        "-o", "--out", type=str, default="cleaned_charts", help="Output directory for batch processing."
    )
    parser.add_argument(
        "--no-wrap",
        action="store_true",
        help="Omit <patient_chart> wrapper (plain text only).",
    )

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
            process_file(file, cleaner, output_dir)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    elif args.file:
        file_path = Path(args.file)
        output_dir = Path(args.out)
        output_dir.mkdir(exist_ok=True)

        print(f"Processing {file_path.name}...")
        process_file(file_path, cleaner, output_dir)
        print(f"Done! Outputs written to: {output_dir.resolve()}")

    else:
        try:
            input_text = pyperclip.paste()
            if not input_text.strip():
                print("Clipboard is empty.")
                sys.exit(1)

            print("Processing clipboard text...")
            cleaned = cleaner.clean(input_text)
            pyperclip.copy(cleaned)

            print(f"Reduced from {len(input_text):,} to {len(cleaned):,} characters.")
            print("Cleaned text copied back to clipboard!")

        except Exception as e:
            print(f"Clipboard error: {e}", file=sys.stderr)
            sys.exit(1)
