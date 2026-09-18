"""Tests for chartcleaner.ingest: docx/pdf/txt loaders + PDF furniture strip."""

import zipfile
from xml.sax.saxutils import escape

import pytest

from chartcleaner import ingest
from chartcleaner.ingest import IngestError, load_file, strip_pdf_furniture

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _make_docx(path, paragraphs, inject_prefix=""):
    body = "".join(
        f'<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>' for p in paragraphs)
    xml = ('<?xml version="1.0"?>' + inject_prefix +
           f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>')
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", xml)


def _make_pdf(path, pages):
    import pymupdf

    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        page.insert_text((72, 72), "\n".join(lines))
    doc.save(path)
    doc.close()


# -- txt -----------------------------------------------------------------------

def test_txt_load(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("MRN: 123456\nAssessment: ok", encoding="utf-8")
    res = load_file(f)
    assert res.engine == "text"
    assert "MRN" in res.text


def test_missing_file(tmp_path):
    with pytest.raises(IngestError):
        load_file(tmp_path / "nope.txt")


def test_unsupported_extension(tmp_path):
    f = tmp_path / "x.exe"
    f.write_bytes(b"\x00")
    with pytest.raises(IngestError, match="Unsupported"):
        load_file(f)


# -- docx ----------------------------------------------------------------------

def test_docx_stdlib_roundtrip(tmp_path):
    _make_docx(tmp_path / "ok.docx",
               ["Patient Jane Roe", "MRN: 7654321", "Assessment: stable"])
    res = load_file(tmp_path / "ok.docx")
    assert "Jane Roe" in res.text
    assert "7654321" in res.text
    assert res.text.count("\n") >= 2


def test_docx_rejects_dtd_entities(tmp_path):
    _make_docx(tmp_path / "evil.docx", ["x"],
               inject_prefix='<!DOCTYPE w [<!ENTITY a "aaaa">]>')
    with pytest.raises(IngestError, match="DTD"):
        load_file(tmp_path / "evil.docx")


def test_docx_rejects_garbage(tmp_path):
    (tmp_path / "bad.docx").write_bytes(b"not a zip")
    with pytest.raises(IngestError):
        load_file(tmp_path / "bad.docx")


# -- pdf -----------------------------------------------------------------------

def test_pdf_text_and_furniture(tmp_path):
    # only the header/footer repeat; content varies per page like a real export
    pages = [
        ["Confidential - Mercy General", f"Patient John Doe, visit {i}",
         f"Page {i} of 4", f"Assessment: stable day {i}", ""]
        for i in range(1, 5)
    ]
    _make_pdf(tmp_path / "c.pdf", pages)
    res = load_file(tmp_path / "c.pdf")
    assert res.engine.startswith("pymupdf")
    assert "John Doe" in res.text
    assert "stable day 2" in res.text           # varied content survives
    assert "Mercy General" not in res.text      # identical running header gone
    assert "Page 2 of 4" not in res.text        # per-page numbers gone
    assert "--- page" not in res.text
    assert any("header/footer" in w for w in res.warnings)


def test_pdf_scanned_without_ocr_warns(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page()
    page.draw_line((0, 0), (100, 100))  # vector art only, no text
    doc.save(tmp_path / "scan.pdf")
    doc.close()
    res = load_file(tmp_path / "scan.pdf")
    assert any("scanned" in w.lower() or "ocr" in w.lower() for w in res.warnings)


# -- furniture stripper (unit level) --------------------------------------------

def test_strip_furniture_no_pages_keeps_text():
    cleaned, removed = strip_pdf_furniture("Assessment: fine\n# Heading\nPlan: home")
    assert "Assessment: fine" in cleaned
    assert "# Heading" not in cleaned and "Heading" in cleaned
    assert removed >= 1


def test_strip_furniture_dedupe():
    text = "\n".join(
        f"--- page {i} ---\nRUNNING HEADER {i % 1}\ncontent {i}\n2 / 9"
        for i in range(1, 5))
    cleaned, removed = strip_pdf_furniture(text)
    assert "content 3" in cleaned
    assert "RUNNING HEADER" not in cleaned
    assert "2 / 9" not in cleaned


# -- status --------------------------------------------------------------------

def test_converters_status_shape():
    status = ingest.converters_status()
    for key in ("pymupdf", "markitdown", "docling", "ocrmypdf",
                "medspacy", "watchdog", "ex4nicegui"):
        assert key in status and isinstance(status[key], bool)
