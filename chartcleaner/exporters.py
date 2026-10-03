"""Export a cleaned chart for other apps.

* :func:`to_docx` — a Word document (standard-library zip/XML, no extra
  packages). Markdown headings and clinical section headers become bold
  headings; everything else is one paragraph per line.
* :func:`to_markdown` — Markdown with YAML front matter (date, source, note
  type), ready for Obsidian or any notes app; :func:`save_to_vault` writes it
  into a folder.
* :func:`to_smartphrase` — text that pastes cleanly into Epic: plain ASCII
  (curly quotes, dashes, degree signs, bullets replaced), no tabs, lines
  wrapped at 80 characters keeping indentation, ``***`` blanks kept.
"""

from __future__ import annotations

import io
import re
import textwrap
import unicodedata
import zipfile
from datetime import date as _date
from pathlib import Path
from xml.sax.saxutils import escape

__all__ = ["to_docx", "to_markdown", "save_to_vault", "to_smartphrase"]

_HEADING = re.compile(r"^(?:#{1,6}\s+(?P<md>.+)|(?P<hdr>[A-Z][A-Za-z /&()-]{2,40}):\s*)$")
_WRAPPER = re.compile(r"^</?[A-Za-z_][\w-]*>$")


def _docx_paragraph(text: str, heading: bool) -> str:
    if not text:
        return "<w:p/>"
    props = '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr>' if heading else ""
    return (f'<w:p><w:r>{props}<w:t xml:space="preserve">{escape(text)}</w:t></w:r></w:p>')


def to_docx(text: str, title: str = "Cleaned chart") -> bytes:
    """A minimal .docx (opens in Word, Pages, Google Docs)."""
    body = [_docx_paragraph(title, True)]
    for line in text.splitlines():
        if _WRAPPER.match(line.strip()):
            continue
        m = _HEADING.match(line.strip())
        body.append(_docx_paragraph((m.group("md") or m.group("hdr")) if m else line.rstrip(), bool(m)))
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                f'<w:body>{"".join(body)}</w:body></w:document>')
    files = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>'),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/'
            'relationships/officeDocument" Target="word/document.xml"/></Relationships>'),
        "word/document.xml": document,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


def to_markdown(text: str, *, source: str = "Chart Cleaner", note_type: str = "",
                today: _date | None = None) -> str:
    from .service import format_output

    body = format_output(text, "markdown")[0]
    meta = [f"date: {(today or _date.today()).isoformat()}", f"source: {source}"]
    if note_type:
        meta.append(f"note_type: {note_type}")
    meta.append("tags: [chart]")
    return "---\n" + "\n".join(meta) + "\n---\n\n" + body.strip() + "\n"


def save_to_vault(markdown: str, vault: str | Path, name: str = "") -> Path:
    """Write a note into an Obsidian (or any) folder; never overwrites."""
    folder = Path(vault).expanduser()
    if not folder.is_dir():
        raise FileNotFoundError(f"Not a folder: {folder}")
    stem = re.sub(r"[^\w -]+", "", name).strip() or f"Chart {_date.today().isoformat()}"
    path = folder / f"{stem}.md"
    n = 2
    while path.exists():
        path = folder / f"{stem} ({n}).md"
        n += 1
    path.write_text(markdown, encoding="utf-8")
    return path


_ASCII = {"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
          "−": "-", "…": "...", " ": " ", "•": "-", "·": "-", "●": "-",
          "°": " deg ", "→": "->", "←": "<-", "↑": "up", "↓": "down",
          "µ": "mc", "μ": "mc", "≥": ">=", "≤": "<=", "×": "x"}


def to_smartphrase(text: str, width: int = 80) -> str:
    out_lines = []
    for line in text.splitlines():
        if _WRAPPER.match(line.strip()):
            continue
        line = "".join(_ASCII.get(ch, ch) for ch in line.replace("\t", "    "))
        line = unicodedata.normalize("NFKD", line).encode("ascii", "ignore").decode("ascii")
        line = re.sub(r" {2,}deg ", " deg ", line).replace(" deg C", " C").replace(" deg F", " F")
        line = line.rstrip()
        if len(line) <= width:
            out_lines.append(line)
            continue
        indent = re.match(r"^\s*(?:[-*]\s+)?", line).group(0)
        out_lines.extend(textwrap.wrap(line, width=width, subsequent_indent=" " * len(indent),
                                       break_long_words=False, break_on_hyphens=False))
    return "\n".join(out_lines).strip("\n") + "\n"
