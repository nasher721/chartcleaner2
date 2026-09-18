"""File ingestion: turn .txt / .md / .docx / .pdf into chart text.

Core path is dependency-light: plain reads for text, a stdlib zipfile +
XML extractor for .docx (docx2md-style), and PyMuPDF for .pdf text.
When optional libraries are installed they are used automatically and can
also be forced via config ``ingest.engine``:

- ``markitdown`` — Microsoft's universal document→Markdown converter
- ``docling``    — layout-aware document parser (heavier install)
- ``ocrmypdf``   — import or CLI; adds a text layer to scanned PDFs that
                   yield no text on their own

PDF output additionally runs through :func:`strip_pdf_furniture`, which
removes running page headers/footers and normalizes markdown ``#`` headings
(pdfmd-inspired) before the cleaning pipeline sees the text.

Security notes: .docx XML is untrusted input — DTD/entity declarations are
rejected before parsing (stdlib ElementTree has no expansion hardening), and
the OCR bridge always invokes ocrmypdf as an argument list (``shell=False``),
never through a shell.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from importlib.util import find_spec
from pathlib import Path
from xml.etree import ElementTree

__all__ = [
    "IngestError",
    "IngestResult",
    "load_file",
    "supported_extensions",
    "converters_status",
    "strip_pdf_furniture",
]

TEXT_EXTS = {".txt", ".md", ".markdown", ".text", ".log"}
OCR_MIN_CHARS_PER_PAGE = 40  # fewer extracted chars than this ⇒ treat as scanned
_DOCX_XML_MAX_BYTES = 64 * 1024 * 1024  # a legit document.xml is far below this


class IngestError(Exception):
    """Raised when a file cannot be turned into text."""


@dataclass
class IngestResult:
    text: str
    engine: str  # "text" | "docx-stdlib" | "markitdown" | "docling" | "pymupdf" | "pymupdf+ocr" | ...
    warnings: list[str] = field(default_factory=list)


def supported_extensions() -> list[str]:
    return sorted(TEXT_EXTS | {".docx", ".pdf"})


def _find(mod: str) -> bool:
    try:
        return find_spec(mod) is not None
    except (ImportError, ValueError):
        return False


def _ocrmypdf_cli() -> str | None:
    return shutil.which("ocrmypdf")


def _ocr_available() -> bool:
    return _find("ocrmypdf") or bool(_ocrmypdf_cli())


def converters_status() -> dict:
    """Capabilities for the Settings status card."""
    return {
        "pymupdf": _find("fitz"),
        "markitdown": _find("markitdown"),
        "docling": _find("docling"),
        "ocrmypdf": _ocr_available(),
        "medspacy": _find("medspacy"),
        "watchdog": _find("watchdog"),
        "ex4nicegui": _find("ex4nicegui"),
    }


# ---------------------------------------------------------------------------
# .docx — zero-dependency extractor (docx2md-style: unzip + parse document.xml)
# ---------------------------------------------------------------------------

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx_stdlib(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as z:
            xml_bytes = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise IngestError(f"Could not read .docx ({e}). Is it a real Word file?") from e
    if len(xml_bytes) > _DOCX_XML_MAX_BYTES:
        raise IngestError("document.xml is implausibly large — refusing to parse.")
    # stdlib ElementTree has no entity-expansion hardening; a DTD with internal
    # entities (billion laughs) would exhaust memory. document.xml never
    # legitimately carries a DTD, so reject one outright.
    head = xml_bytes[:4096].lstrip()
    if head.startswith(b"<?xml"):
        nl = head.find(b"\n")
        head = head[nl + 1:] if nl >= 0 else head
    if head[:len(b"<!DOCTYPE")] == b"<!DOCTYPE" or b"<!ENTITY" in xml_bytes[:65536]:
        raise IngestError("document.xml contains DTD/entity declarations — refusing to parse.")
    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as e:
        raise IngestError(f"Corrupt .docx XML: {e}") from e

    lines: list[str] = []
    for para in root.iter(f"{_W_NS}p"):
        parts: list[str] = []
        for node in para.iter():
            if node.tag == f"{_W_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag == f"{_W_NS}tab":
                parts.append("\t")
            elif node.tag == f"{_W_NS}br":
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# .pdf — PyMuPDF text, OCR fallback, optional advanced engines
# ---------------------------------------------------------------------------

def _pdf_text_pymupdf(path: Path) -> tuple[str, int]:
    try:
        import pymupdf  # PyMuPDF ≥ 1.24 preferred name
    except ImportError:  # pragma: no cover - older wheels
        import fitz as pymupdf

    doc = pymupdf.open(path)
    try:
        pages = [page.get_text("text").strip() for page in doc]
    finally:
        doc.close()
    return "\n\n".join(f"--- page {i + 1} ---\n{p}" for i, p in enumerate(pages) if p), len(pages)


def _pdf_needs_ocr(text: str, pages: int) -> bool:
    return pages > 0 and len(re.sub(r"\s", "", text)) < OCR_MIN_CHARS_PER_PAGE * pages


def _pdf_ocr(path: Path, engine_note: str) -> tuple[str, str]:
    """Add a text layer with OCRmyPDF and re-extract. Returns (text, engine)."""
    out_path = Path(tempfile.mkstemp(suffix="_ocr.pdf")[1])
    tmp_out = str(out_path)
    try:
        cli = _ocrmypdf_cli()
        if cli:
            # Argument-list invocation, no shell — file paths are never
            # interpreted by a shell, so there is nothing to inject into.
            proc = subprocess.run(
                [cli, "--quiet", "--force-ocr", str(path), tmp_out],
                capture_output=True, timeout=600, shell=False,
            )
            if proc.returncode != 0:
                raise IngestError(f"ocrmypdf failed: {proc.stderr.decode(errors='replace')[:300]}")
        elif _find("ocrmypdf"):
            import ocrmypdf

            ocrmypdf.ocr(str(path), tmp_out, force_ocr=True, quiet=True)
        else:
            raise IngestError("No OCR available (install ocrmypdf, or the ocrmypdf CLI).")
        text, pages = _pdf_text_pymupdf(out_path)
        return text, f"{engine_note}+ocr"
    finally:
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass


def _advanced_convert(path: Path, engine: str) -> str:
    if engine == "markitdown":
        from markitdown import MarkItDown

        return MarkItDown(enable_plugins=False).convert(str(path)).text_content or ""
    if engine == "docling":
        from docling.document_converter import DocumentConverter

        return DocumentConverter().convert(str(path)).document.export_to_markdown()
    raise IngestError(f"Unknown ingest engine '{engine}'")


def _pdf_ingest(path: Path, cfg: dict, res: IngestResult) -> str:
    engine_pref = (cfg.get("ingest") or {}).get("engine", "auto")
    if engine_pref in ("markitdown", "docling") and _find(engine_pref):
        try:
            res.engine = engine_pref
            return _advanced_convert(path, engine_pref)
        except Exception as e:
            res.warnings.append(f"{engine_pref} failed ({e}); falling back to built-in PDF text.")
    if not _find("fitz"):
        raise IngestError("PDF support needs PyMuPDF (pip install pymupdf) — or install markitdown.")

    text, pages = _pdf_text_pymupdf(path)
    res.engine = "pymupdf"
    if _pdf_needs_ocr(text, pages):
        if _ocr_available():
            try:
                text, res.engine = _pdf_ocr(path, res.engine)
                res.warnings.append("PDF had no text layer — ran OCR (scanned document).")
            except Exception as e:
                res.warnings.append(f"OCR failed: {e}")
        else:
            res.warnings.append(
                "PDF looks scanned (almost no extractable text). Install ocrmypdf to enable OCR.")
    return text


# ---------------------------------------------------------------------------
# pdf furniture removal (pdfmd-inspired): running headers/footers + # headings
# ---------------------------------------------------------------------------

# bare page numbers and "Page 2 of 10" style markers (pdfmd-style removal)
_PAGE_MARKER_RE = re.compile(
    r"(?i)(?:page\s*)?\d{1,4}\s*(?:/|of)\s*\d{1,4}"
    r"|(?:page\s*\d{1,4})"
    r"|[-–—]?\s*\d{1,4}\s*[-–—]"
)


def strip_pdf_furniture(text: str, min_repeat: int = 3) -> tuple[str, int]:
    """Remove lines repeated across page boundaries and normalize '#' headings.

    Returns (cleaned_text, lines_removed). A line is furniture when the same
    stripped text appears on at least ``min_repeat`` distinct pages and is not
    plausibly chart content (short, or a bare page number).
    """
    page_blocks = re.split(r"(?m)^--- page \d+ ---\s*$", text)
    if len(page_blocks) < min_repeat:
        blocks = text.split("\f")
        if len(blocks) < min_repeat:
            # No page markers: still normalize headings, but nothing to dedupe.
            cleaned, n = re.subn(r"(?m)^#{1,6}\s+(.*)$", r"\1", text)
            return cleaned, n
        page_blocks = blocks

    edge_lines: dict[str, set[int]] = {}
    page_marker_pages: set[int] = set()
    pages = [b.strip("\n") for b in page_blocks if b.strip()]
    for idx, block in enumerate(pages):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if not lines:
            continue
        # running headers/footers live in the first/last couple of lines
        edges = {lines[0], lines[-1]}
        if len(lines) > 1:
            edges.add(lines[1])
        if len(lines) > 2:
            edges.add(lines[-2])
        for edge in edges:
            edge_lines.setdefault(edge, set()).add(idx)
            if _PAGE_MARKER_RE.fullmatch(edge):
                page_marker_pages.add(idx)

    furniture = {s for s, pgs in edge_lines.items()
                 if len(pgs) >= min_repeat
                 and (len(s) <= 80
                      or bool(_PAGE_MARKER_RE.fullmatch(s)))}
    strip_markers = len(page_marker_pages) >= min_repeat

    removed = 0
    out_pages: list[str] = []
    for block in pages:
        lines = [ln for ln in block.splitlines() if ln.strip()]
        kept = []
        for ln in lines:
            s = ln.strip()
            if s in furniture or (strip_markers and _PAGE_MARKER_RE.fullmatch(s)):
                removed += 1
            else:
                kept.append(ln)
        out_pages.append("\n".join(kept))
    cleaned = "\n\n".join(out_pages)
    cleaned, n = re.subn(r"(?m)^#{1,6}\s+(.*)$", r"\1", cleaned)
    return cleaned, removed + n


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def load_file(path: str | Path, config: dict | None = None) -> IngestResult:
    """Read any supported file into chart text. Raises IngestError on failure."""
    path = Path(path)
    if not path.exists() or not path.is_file():
        raise IngestError(f"File not found: {path}")
    cfg = config or {}
    res = IngestResult(text="", engine="text")

    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        res.text = path.read_text(encoding="utf-8", errors="replace")
        res.engine = "text"
        return res

    if ext == ".docx":
        # Word documents are always zip archives; markitdown reads arbitrary
        # bytes as text, so a corrupt file would silently become "text".
        if not zipfile.is_zipfile(path):
            raise IngestError(f"'{path.name}' is not a valid .docx file (not a zip archive).")
        if _find("markitdown") and (cfg.get("ingest") or {}).get("engine", "auto") in ("auto", "markitdown"):
            try:
                res.text, res.engine = _advanced_convert(path, "markitdown"), "markitdown"
            except Exception as e:
                res.warnings.append(f"markitdown failed ({e}); using built-in .docx reader.")
        if not res.text:
            res.text, res.engine = _docx_stdlib(path), "docx-stdlib"
        return res

    if ext == ".pdf":
        res.text = _pdf_ingest(path, cfg, res)
        cleaned, n = strip_pdf_furniture(res.text)
        if n:
            res.warnings.append(f"Removed {n} repeated page header/footer / heading marker line(s).")
        res.text = cleaned
        return res

    raise IngestError(f"Unsupported file type '{ext}' (supported: {', '.join(supported_extensions())})")


def _main(argv: list[str]) -> int:  # pragma: no cover - manual smoke helper
    for arg in argv[1:]:
        try:
            r = load_file(arg)
            print(f"== {arg} [{r.engine}] ==")
            print(r.text[:400])
            for w in r.warnings:
                print(f"  ! {w}", file=sys.stderr)
        except IngestError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main(sys.argv))
