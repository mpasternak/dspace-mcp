"""Testy ekstraktorów tekstu — czyste funkcje na bajtach."""

from __future__ import annotations

import pytest

from dspace_mcp.extractors import ExtractError, extract_pdf


def _one_page_pdf(text: str) -> bytes:
    """Minimalny, poprawny 1-stronicowy PDF z warstwą tekstową."""
    content = f"BT /F1 24 Tf 72 700 Td ({text}) Tj ET".encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    pdf = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % i + obj + b"\nendobj\n"
    xref_pos = len(pdf)
    pdf += b"xref\n0 %d\n" % (len(objs) + 1)
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += b"%010d 00000 n \n" % off
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (
        len(objs) + 1,
        xref_pos,
    )
    return pdf


def test_extract_pdf_returns_text_and_page_units():
    result = extract_pdf(_one_page_pdf("Hello world"), max_chars=1000)
    assert "Hello world" in result["text"]
    assert result["unit"] == "pages"
    assert result["units_total"] == 1
    assert result["units_processed"] == 1
    assert result["truncated"] is False


def test_extract_pdf_truncates_and_reports():
    result = extract_pdf(_one_page_pdf("Hello world"), max_chars=3)
    assert len(result["text"]) <= 3
    assert result["truncated"] is True


def test_extract_pdf_rejects_non_pdf():
    with pytest.raises(ExtractError) as exc:
        extract_pdf(b"this is not a pdf", max_chars=100)
    assert "not a readable PDF" in str(exc.value)
