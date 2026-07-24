"""Testy ekstraktorów tekstu — czyste funkcje na bajtach.

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

from dspace_mcp.extractors import ExtractError, dispatch, extract_pdf
from dspace_mcp.extractors.base import normalize
from dspace_mcp.extractors.ooxml import extract_docx, extract_pptx, extract_xlsx
from dspace_mcp.extractors.opendocument import extract_odp, extract_ods, extract_odt
from office_samples import (
    docx_bytes,
    odp_bytes,
    ods_bytes,
    odt_bytes,
    pptx_bytes,
    xlsx_bytes,
)


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


# --- pozostałe budowniczowie PDF-ów (przeniesione z dawnego test_pdf.py) ----


def _text_pdf(pages: Sequence[Sequence[str]]) -> bytes:
    """Złóż wieloosobowy PDF z warstwą tekstową: lista stron, każda to lista linii.

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


def _scan_pdf(pages: int = 2) -> bytes:
    """PDF bez warstwy tekstowej - imitacja skanu (puste strony)."""
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=595, height=842)
    return _write(writer)


def _encrypted_pdf(
    user_password: str = "haslo", owner_password: str | None = None
) -> bytes:
    """PDF zaszyfrowany - domyślnie hasłem użytkownika, którego nie znamy."""
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(_text_pdf([["Sekret"]])))
    writer.encrypt(user_password=user_password, owner_password=owner_password)
    return _write(writer)


def _pdf_without_pages() -> bytes:
    """Formalnie poprawny PDF, ale z zerową liczbą stron."""
    return _write(pypdf.PdfWriter())


# --- budowniczowie działają (bez tego reszta testów niczego nie dowodzi) -----


def test_text_pdf_builder_is_readable_by_pypdf() -> None:
    reader = pypdf.PdfReader(io.BytesIO(_text_pdf([["Hello world"], ["Bye"]])))
    assert len(reader.pages) == 2
    assert "Hello world" in reader.pages[0].extract_text()
    assert "Bye" in reader.pages[1].extract_text()


def test_scan_pdf_builder_has_no_text() -> None:
    reader = pypdf.PdfReader(io.BytesIO(_scan_pdf(2)))
    assert [page.extract_text().strip() for page in reader.pages] == ["", ""]


# --- ścieżka szczęśliwa -----------------------------------------------------


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


def test_extract_pdf_result_shape() -> None:
    result = extract_pdf(_text_pdf([["Hello world"]]))
    assert set(result) == {
        "text",
        "truncated",
        "unit",
        "units_processed",
        "units_total",
    }
    assert isinstance(result["text"], str)
    assert isinstance(result["truncated"], bool)
    assert isinstance(result["units_processed"], int)
    assert isinstance(result["units_total"], int)


def test_extract_pdf_reads_all_pages_when_below_limit() -> None:
    result = extract_pdf(_text_pdf([["Alpha"], ["Beta"], ["Gamma"]]))
    assert result["unit"] == "pages"
    assert result["units_total"] == 3
    assert result["units_processed"] == 3
    assert result["truncated"] is False
    assert "Alpha" in result["text"]
    assert "Gamma" in result["text"]


def test_extract_pdf_does_not_consume_or_mutate_input_bytes() -> None:
    """Funkcja jest czysta: te same bajty można podać drugi raz."""
    data = _text_pdf([["Hello world"]])
    first = extract_pdf(data)
    assert extract_pdf(data) == first


# --- ekonomia tokenów: przerywamy czytanie po osiągnięciu max_chars ---------


def test_extract_pdf_stops_reading_pages_once_max_chars_reached() -> None:
    """Sedno modułu: nie parsujemy 40 stron, żeby oddać 200 znaków."""
    page = ["X" * 60] * 5  # ~300 znaków na stronę
    result = extract_pdf(_text_pdf([page] * 40), max_chars=200)
    assert result["unit"] == "pages"
    assert result["units_total"] == 40
    assert result["units_processed"] < result["units_total"]
    assert result["units_processed"] <= 2
    assert result["truncated"] is True
    assert len(result["text"]) == 200


def test_extract_pdf_truncated_when_stopped_early_even_if_text_shorter_than_limit() -> (
    None
):
    """Obcięcie to także "nie doszliśmy do końca", nie tylko cięcie stringa."""
    page = ["Y" * 50] * 2
    result = extract_pdf(_text_pdf([page] * 10), max_chars=110)
    assert result["units_processed"] < result["units_total"]
    assert result["truncated"] is True
    assert len(result["text"]) <= 110


def test_extract_pdf_default_max_chars_is_20000() -> None:
    data = _text_pdf([["Z" * 70] * 10] * 60)  # grubo ponad 20 000 znaków
    result = extract_pdf(data)
    assert len(result["text"]) == 20000
    assert result["truncated"] is True


def test_extract_pdf_max_chars_must_be_positive() -> None:
    data = _text_pdf([["Hello world"]])
    with pytest.raises(ValueError):
        extract_pdf(data, max_chars=0)
    with pytest.raises(ValueError):
        extract_pdf(data, max_chars=-1)


def test_extract_pdf_rejects_non_pdf():
    with pytest.raises(ExtractError) as exc:
        extract_pdf(b"this is not a pdf", max_chars=100)
    assert str(exc.value) == "This file is not a readable PDF."


# --- normalizacja tekstu (dspace_mcp.extractors.base.normalize) -------------


def test_extract_pdf_strips_surrounding_whitespace() -> None:
    result = extract_pdf(_text_pdf([["Hello world"]]))
    assert result["text"] == result["text"].strip()


def test_normalize_collapses_runs_of_blank_lines() -> None:
    """Wielokrotne puste linie zjadają tokeny; jedna pusta linia wystarcza."""
    assert normalize("a\n\n\n\n\nb") == "a\n\nb"
    assert normalize("  a\n\n\n b  \n\n") == "a\n\n b"


def test_normalize_does_not_touch_content() -> None:
    """Nie "poprawiamy" treści: pojedyncze przejścia do nowej linii i spacje zostają."""
    assert normalize("a\nb\n\nc  d") == "a\nb\n\nc  d"


# --- błędy ------------------------------------------------------------------


def test_extract_pdf_password_protected() -> None:
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(_encrypted_pdf())
    assert str(excinfo.value) == (
        "This PDF is password-protected, so its text cannot be extracted."
    )


def test_extract_pdf_encrypted_with_empty_user_password_is_still_readable() -> None:
    """Szyfrowanie "tylko właścicielskie" nie blokuje odczytu - nie zgłaszamy błędu."""
    data = _encrypted_pdf(user_password="", owner_password="wlasciciel")
    assert "Sekret" in extract_pdf(data)["text"]


def test_extract_pdf_empty_input() -> None:
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(b"")
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_extract_pdf_damaged() -> None:
    data = _text_pdf([["Hello world"]])
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(data[: len(data) // 2])
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_extract_pdf_without_pages() -> None:
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(_pdf_without_pages())
    assert str(excinfo.value) == "This file is not a readable PDF."


def test_extract_pdf_without_text_layer() -> None:
    """Pusty string byłby najgorszą odpowiedzią - model uznałby dokument za pusty."""
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(_scan_pdf(3))
    assert str(excinfo.value) == (
        "This PDF has no text layer - it is most likely a scan. "
        "OCR is out of scope for this server."
    )


def test_extract_error_carries_message_attribute() -> None:
    error = ExtractError("Boom.")
    assert error.message == "Boom."
    assert str(error) == "Boom."


@pytest.mark.parametrize(
    "data",
    [
        b"to nie jest pdf",
        pytest.param(None, id="encrypted"),
    ],
)
def test_extract_pdf_errors_do_not_leak_pypdf_traceback(data: bytes | None) -> None:
    """Model ma widzieć komunikat, nie ślad stosu pypdf."""
    payload = _encrypted_pdf() if data is None else data
    with pytest.raises(ExtractError) as excinfo:
        extract_pdf(payload)
    assert excinfo.value.__cause__ is None
    assert "pypdf" not in str(excinfo.value)


# --- dispatch: rejestr formatów, fallback po rozszerzeniu -------------------


def test_dispatch_selects_pdf_by_mimetype():
    result = dispatch(
        _one_page_pdf("Hello"),
        mimetype="application/pdf",
        filename="paper.pdf",
        max_chars=1000,
    )
    assert result["format"] == "pdf"
    assert "Hello" in result["text"]


def test_dispatch_falls_back_to_extension_for_generic_mimetype():
    result = dispatch(
        _one_page_pdf("Hello"),
        mimetype="application/octet-stream",
        filename="paper.PDF",
        max_chars=1000,
    )
    assert result["format"] == "pdf"


def test_dispatch_strips_mimetype_parameters():
    result = dispatch(
        _one_page_pdf("Hello"),
        mimetype="application/pdf; charset=binary",
        filename=None,
        max_chars=1000,
    )
    assert result["format"] == "pdf"


def test_dispatch_unknown_type_raises():
    with pytest.raises(ExtractError) as exc:
        dispatch(b"data", mimetype="image/png", filename="x.png", max_chars=100)
    assert "No text extractor" in str(exc.value)


# --- OOXML: docx, pptx, xlsx -------------------------------------------------


def test_extract_docx_joins_paragraphs():
    result = extract_docx(docx_bytes(["First para", "Second para"]), max_chars=1000)
    assert "First para" in result["text"]
    assert "Second para" in result["text"]
    assert result["unit"] == "paragraphs"
    assert result["units_total"] == 2
    assert result["truncated"] is False


def test_extract_docx_bad_zip_raises():
    with pytest.raises(ExtractError) as exc:
        extract_docx(b"not a zip", max_chars=100)
    assert "not a readable" in str(exc.value)


def test_extract_docx_empty_raises():
    with pytest.raises(ExtractError):
        extract_docx(docx_bytes([""]), max_chars=100)


def test_extract_pptx_reads_slides_in_order():
    data = pptx_bytes([["Slide one text"], ["Slide two text"]])
    result = extract_pptx(data, max_chars=1000)
    assert result["unit"] == "slides"
    assert result["units_total"] == 2
    assert result["text"].index("one") < result["text"].index("two")


def test_extract_pptx_reads_slides_in_numeric_not_lexicographic_order():
    """slide2.xml musi wypaść przed slide10.xml - lexico dałoby odwrotnie."""
    slides = [[f"Marker{i}"] for i in range(1, 12)]  # 11 slajdów: slide1..slide11
    data = pptx_bytes(slides)
    result = extract_pptx(data, max_chars=10000)
    assert result["units_total"] == 11
    text = result["text"]
    assert text.index("Marker2") < text.index("Marker10")
    assert text.index("Marker9") < text.index("Marker10")
    assert text.index("Marker10") < text.index("Marker11")


def test_extract_xlsx_flattens_cells_with_shared_strings():
    data = xlsx_bytes([[["Name", "City"], ["Ada", "London"]]])
    result = extract_xlsx(data, max_chars=1000)
    assert "Name" in result["text"] and "London" in result["text"]
    assert result["unit"] == "sheets"
    assert result["units_total"] == 1
    assert "\t" in result["text"]  # kolumny rozdzielone tabem


def test_extract_xlsx_reads_sheets_in_numeric_not_lexicographic_order():
    """sheet2.xml musi wypaść przed sheet10.xml - lexico dałoby odwrotnie."""
    sheets = [[[f"Marker{i}"]] for i in range(1, 12)]  # 11 arkuszy: sheet1..sheet11
    data = xlsx_bytes(sheets)
    result = extract_xlsx(data, max_chars=10000)
    assert result["units_total"] == 11
    text = result["text"]
    assert text.index("Marker2") < text.index("Marker10")
    assert text.index("Marker9") < text.index("Marker10")
    assert text.index("Marker10") < text.index("Marker11")


def test_extract_xlsx_reads_numeric_cell_without_type_attribute():
    """Komórka bez ``t`` (goły ``<v>``) to liczba - trzecia gałąź _cell_value."""
    data = xlsx_bytes([[[("n", "42"), "Label"]]])
    result = extract_xlsx(data, max_chars=1000)
    assert "42" in result["text"]
    assert "Label" in result["text"]


def test_extract_xlsx_reads_inline_string_cell():
    """``t="inlineStr"`` z zagnieżdżonym ``<is><t>`` - druga gałąź _cell_value."""
    data = xlsx_bytes([[[("inlineStr", "Direct text"), "Shared text"]]])
    result = extract_xlsx(data, max_chars=1000)
    assert "Direct text" in result["text"]
    assert "Shared text" in result["text"]


# --- ODF: odt, ods, odp -------------------------------------------------


def test_extract_odt_joins_paragraphs():
    result = extract_odt(odt_bytes(["Alpha line", "Beta line"]), max_chars=1000)
    assert "Alpha line" in result["text"] and "Beta line" in result["text"]
    assert result["unit"] == "paragraphs"
    assert result["units_total"] == 2


def test_extract_ods_flattens_cells():
    data = ods_bytes([[["Ada", "London"], ["Bob", "Paris"]]])
    result = extract_ods(data, max_chars=1000)
    assert "Ada" in result["text"] and "Paris" in result["text"]
    assert result["unit"] == "sheets"
    assert result["units_total"] == 1


def test_extract_odp_reads_pages():
    data = odp_bytes([["First slide"], ["Second slide"]])
    result = extract_odp(data, max_chars=1000)
    assert result["unit"] == "slides"
    assert result["units_total"] == 2
    assert "First slide" in result["text"]


def test_extract_odt_bad_zip_raises():
    with pytest.raises(ExtractError):
        extract_odt(b"not a zip", max_chars=100)
