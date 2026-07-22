"""Testy ekstrakcji tekstu z PDF-a.

PDF-y testowe budujemy programowo, w pamięci — bez plików binarnych w repo i
bez dodatkowych zależności (żadnego reportlaba). Dokument z warstwą tekstową
składamy ręcznie z obiektów PDF (catalog/pages/page/font + strumień treści),
resztę przypadków (pusta strona, szyfrowanie) załatwia ``pypdf.PdfWriter``.
"""

from __future__ import annotations

import io
from collections.abc import Sequence

import pypdf
import pytest

from dspace_mcp.pdf import PdfError, extract_text

# --- budowniczowie PDF-ów ---------------------------------------------------


def build_text_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Złóż minimalny PDF z warstwą tekstową: lista stron, każda to linie tekstu.

    Ręczne składanie jest tu prostsze, niż wygląda: numeracja obiektów jest
    z góry znana, a jedyne miejsce, w którym łatwo o błąd, to tablica ``xref``
    — dlatego offsety liczymy z faktycznej pozycji w buforze, nie na piechotę.
    """
    catalog_num, pages_num, font_num = 1, 2, 3
    objects: list[bytes] = [
        b"",  # katalog - uzupełniany na końcu (potrzebuje numeru /Pages)
        b"",  # drzewo stron - uzupełniane, gdy znamy numery /Kids
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]

    def add(body: bytes) -> int:
        objects.append(body)
        return len(objects)

    kids: list[int] = []
    for lines in pages:
        ops = ["BT /F1 12 Tf 72 720 Td 14 TL"]
        ops += [f"({line}) Tj T*" for line in lines]
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        content_num = add(
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream)
        )
        kids.append(
            add(
                b"<< /Type /Page /Parent %d 0 R /MediaBox [0 0 612 792] "
                b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
                % (pages_num, font_num, content_num)
            )
        )

    objects[catalog_num - 1] = b"<< /Type /Catalog /Pages %d 0 R >>" % pages_num
    objects[pages_num - 1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % k for k in kids),
        len(kids),
    )

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: list[int] = []
    for num, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (num, body))
    xref_at = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, catalog_num, xref_at)
    )
    return out.getvalue()


def _write(writer: pypdf.PdfWriter) -> bytes:
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def build_scan_pdf(pages: int = 2) -> bytes:
    """PDF bez warstwy tekstowej - imitacja skanu (puste strony)."""
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    return _write(writer)


def build_encrypted_pdf(
    user_password: str = "haslo", owner_password: str | None = None
) -> bytes:
    """PDF zaszyfrowany - domyślnie hasłem użytkownika, którego nie znamy."""
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(build_text_pdf([["Sekret"]])))
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    return _write(writer)


def build_pdf_without_pages() -> bytes:
    """Formalnie poprawny PDF, ale z zerową liczbą stron."""
    return _write(pypdf.PdfWriter())


# --- budowniczowie działają (bez tego reszta testów niczego nie dowodzi) -----


def test_builder_produces_pdf_readable_by_pypdf() -> None:
    reader = pypdf.PdfReader(io.BytesIO(build_text_pdf([["Hello world"], ["Bye"]])))
    assert len(reader.pages) == 2
    assert "Hello world" in reader.pages[0].extract_text()
    assert "Bye" in reader.pages[1].extract_text()


def test_builder_scan_pdf_has_no_text() -> None:
    reader = pypdf.PdfReader(io.BytesIO(build_scan_pdf(2)))
    assert [page.extract_text().strip() for page in reader.pages] == ["", ""]


# --- ścieżka szczęśliwa -----------------------------------------------------


def test_extracts_text_from_single_page() -> None:
    result = extract_text(build_text_pdf([["Hello world", "Second line"]]))
    assert "Hello world" in result["text"]
    assert "Second line" in result["text"]


def test_result_shape() -> None:
    result = extract_text(build_text_pdf([["Hello world"]]))
    assert set(result) == {"text", "truncated", "pages_processed", "pages_total"}
    assert isinstance(result["text"], str)
    assert isinstance(result["truncated"], bool)
    assert isinstance(result["pages_processed"], int)
    assert isinstance(result["pages_total"], int)


def test_reads_all_pages_when_below_limit() -> None:
    result = extract_text(build_text_pdf([["Alpha"], ["Beta"], ["Gamma"]]))
    assert result["pages_total"] == 3
    assert result["pages_processed"] == 3
    assert result["truncated"] is False
    assert "Alpha" in result["text"]
    assert "Gamma" in result["text"]


def test_does_not_consume_or_mutate_input_bytes() -> None:
    """Funkcja jest czysta: te same bajty można podać drugi raz."""
    data = build_text_pdf([["Hello world"]])
    first = extract_text(data)
    assert extract_text(data) == first


# --- ekonomia tokenów: przerywamy czytanie po osiągnięciu max_chars ---------


def test_stops_reading_pages_once_max_chars_reached() -> None:
    """Sedno modułu: nie parsujemy 40 stron, żeby oddać 200 znaków."""
    page = ["X" * 60] * 5  # ~300 znaków na stronę
    result = extract_text(build_text_pdf([page] * 40), max_chars=200)
    assert result["pages_total"] == 40
    assert result["pages_processed"] < result["pages_total"]
    assert result["pages_processed"] <= 2
    assert result["truncated"] is True
    assert len(result["text"]) == 200


def test_truncated_when_stopped_early_even_if_text_shorter_than_limit() -> None:
    """Obcięcie to także "nie doszliśmy do końca", nie tylko cięcie stringa."""
    page = ["Y" * 50] * 2
    result = extract_text(build_text_pdf([page] * 10), max_chars=110)
    assert result["pages_processed"] < result["pages_total"]
    assert result["truncated"] is True
    assert len(result["text"]) <= 110


def test_default_max_chars_is_20000() -> None:
    data = build_text_pdf([["Z" * 70] * 10] * 60)  # grubo ponad 20 000 znaków
    result = extract_text(data)
    assert len(result["text"]) == 20000
    assert result["truncated"] is True


def test_max_chars_must_be_positive() -> None:
    data = build_text_pdf([["Hello world"]])
    with pytest.raises(ValueError):
        extract_text(data, max_chars=0)
    with pytest.raises(ValueError):
        extract_text(data, max_chars=-1)


# --- normalizacja tekstu ----------------------------------------------------


def test_strips_surrounding_whitespace() -> None:
    result = extract_text(build_text_pdf([["Hello world"]]))
    assert result["text"] == result["text"].strip()


def test_collapses_runs_of_blank_lines() -> None:
    """Wielokrotne puste linie zjadają tokeny; jedna pusta linia wystarcza."""
    from dspace_mcp.pdf import _normalize

    assert _normalize("a\n\n\n\n\nb") == "a\n\nb"
    assert _normalize("  a\n\n\n b  \n\n") == "a\n\n b"


def test_normalization_does_not_touch_content() -> None:
    """Nie "poprawiamy" treści: pojedyncze przejścia do nowej linii i spacje zostają."""
    from dspace_mcp.pdf import _normalize

    assert _normalize("a\nb\n\nc  d") == "a\nb\n\nc  d"


# --- błędy ------------------------------------------------------------------


def test_password_protected_pdf() -> None:
    with pytest.raises(PdfError) as excinfo:
        extract_text(build_encrypted_pdf())
    assert str(excinfo.value) == (
        "This PDF is password-protected, so its text cannot be extracted."
    )


def test_encrypted_with_empty_user_password_is_still_readable() -> None:
    """Szyfrowanie "tylko właścicielskie" nie blokuje odczytu - nie zgłaszamy błędu."""
    data = build_encrypted_pdf(user_password="", owner_password="wlasciciel")
    assert "Sekret" in extract_text(data)["text"]


def test_not_a_pdf() -> None:
    with pytest.raises(PdfError) as excinfo:
        extract_text(b"to nie jest pdf")
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_empty_input() -> None:
    with pytest.raises(PdfError) as excinfo:
        extract_text(b"")
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_damaged_pdf() -> None:
    data = build_text_pdf([["Hello world"]])
    with pytest.raises(PdfError) as excinfo:
        extract_text(data[: len(data) // 2])
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_pdf_without_pages() -> None:
    with pytest.raises(PdfError) as excinfo:
        extract_text(build_pdf_without_pages())
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_pdf_without_text_layer() -> None:
    """Pusty string byłby najgorszą odpowiedzią - model uznałby dokument za pusty."""
    with pytest.raises(PdfError) as excinfo:
        extract_text(build_scan_pdf(3))
    assert str(excinfo.value) == (
        "This PDF has no text layer - it is most likely a scan. "
        "OCR is out of scope for this server."
    )


def test_pdf_error_carries_message_attribute() -> None:
    error = PdfError("Boom.")
    assert error.message == "Boom."
    assert str(error) == "Boom."


@pytest.mark.parametrize(
    "data",
    [
        b"to nie jest pdf",
        pytest.param(None, id="encrypted"),
    ],
)
def test_errors_do_not_leak_pypdf_traceback(data: bytes | None) -> None:
    """Model ma widzieć komunikat, nie ślad stosu pypdf."""
    payload = build_encrypted_pdf() if data is None else data
    with pytest.raises(PdfError) as excinfo:
        extract_text(payload)
    assert excinfo.value.__cause__ is None
    assert "pypdf" not in str(excinfo.value)
