"""Testy ekstraktorów tekstu — czyste funkcje na bajtach.

PDF-y testowe budujemy programowo, w pamięci — bez plików binarnych w repo i
bez dodatkowych zależności (żadnego reportlaba). Dokument z warstwą tekstową
składamy ręcznie z obiektów PDF (catalog/pages/page/font + strumień treści),
resztę przypadków (pusta strona, szyfrowanie) załatwia ``pypdf.PdfWriter``.
"""

from __future__ import annotations

import io
import struct
import zipfile
from collections.abc import Sequence

import pypdf
import pytest

from dspace_mcp.extractors import ExtractError, dispatch, extract_pdf
from dspace_mcp.extractors.base import normalize, parse_xml
from dspace_mcp.extractors.msword import _scrape_text, extract_doc
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


# --- dispatch: routing dla wszystkich formatów ZIP+XML -----------------------

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_ODT_MIME = "application/vnd.oasis.opendocument.text"
_ODS_MIME = "application/vnd.oasis.opendocument.spreadsheet"
_ODP_MIME = "application/vnd.oasis.opendocument.presentation"

_ZIP_FORMATS = [
    pytest.param(_DOCX_MIME, "docx", lambda: docx_bytes(["Hello"]), id="docx"),
    pytest.param(_PPTX_MIME, "pptx", lambda: pptx_bytes([["Hello"]]), id="pptx"),
    pytest.param(_XLSX_MIME, "xlsx", lambda: xlsx_bytes([[["Hello"]]]), id="xlsx"),
    pytest.param(_ODT_MIME, "odt", lambda: odt_bytes(["Hello"]), id="odt"),
    pytest.param(_ODS_MIME, "ods", lambda: ods_bytes([[["Hello"]]]), id="ods"),
    pytest.param(_ODP_MIME, "odp", lambda: odp_bytes([["Hello"]]), id="odp"),
]


@pytest.mark.parametrize("mimetype, label, builder", _ZIP_FORMATS)
def test_dispatch_selects_zip_format_by_mimetype(mimetype, label, builder):
    result = dispatch(builder(), mimetype=mimetype, filename=None, max_chars=1000)
    assert result["format"] == label


@pytest.mark.parametrize("mimetype, label, builder", _ZIP_FORMATS)
def test_dispatch_selects_zip_format_by_extension_fallback(mimetype, label, builder):
    result = dispatch(
        builder(),
        mimetype="application/octet-stream",
        filename=f"x.{label}",
        max_chars=1000,
    )
    assert result["format"] == label


def test_dispatch_routes_msword_mimetype_to_doc_extractor():
    """.doc valid inline nie da się łatwo zbudować — sprawdzamy ROUTING: błąd
    musi pochodzić z ``extract_doc`` (komunikat o niepoprawnym Wordzie), a nie
    z generycznej ścieżki „No text extractor"."""
    with pytest.raises(ExtractError) as exc:
        dispatch(
            b"not an ole file",
            mimetype="application/msword",
            filename=None,
            max_chars=1000,
        )
    assert "not a readable Word document" in str(exc.value)


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


# --- legacy .doc (OLE) ---------------------------------------------------


def test_scrape_text_keeps_readable_lines():
    raw = b"\x00\x01Hello world\x00\x07\x0cthis is a document\x00"
    out = _scrape_text(raw)
    assert "Hello world" in out
    assert "this is a document" in out


def test_scrape_text_drops_control_noise():
    raw = bytes(range(0, 32)) + b"\x00\x00"
    assert _scrape_text(raw) == ""


def test_extract_doc_rejects_non_ole():
    with pytest.raises(ExtractError) as exc:
        extract_doc(b"plain bytes, not OLE", max_chars=100)
    assert "not a readable" in str(exc.value)


def _ole_dir_entry(
    name: str, obj_type: int, child: int, start_sector: int, size: int
) -> bytes:
    """Zbuduj 128-bajtowy wpis katalogu OLE (MS-CFB 2.6.1)."""
    entry = bytearray(128)
    name_utf16 = name.encode("utf-16-le")
    entry[: len(name_utf16)] = name_utf16
    struct.pack_into("<H", entry, 64, len(name_utf16) + 2)  # dł. nazwy + NUL
    entry[66] = obj_type
    entry[67] = 1  # color flag: black
    struct.pack_into("<I", entry, 68, 0xFFFFFFFF)  # left sibling: brak
    struct.pack_into("<I", entry, 72, 0xFFFFFFFF)  # right sibling: brak
    struct.pack_into("<I", entry, 76, child)
    struct.pack_into("<I", entry, 116, start_sector)
    struct.pack_into("<Q", entry, 120, size)
    return bytes(entry)


def _ole_bytes_with_worddocument_as_storage() -> bytes:
    """Poprawny nagłówkowo plik OLE, w którym ``WordDocument`` jest STORAGE
    (katalogiem), a nie STREAM.

    To odtwarza usterkę z audytu: konstrukcja ``olefile.OleFileIO`` przechodzi
    bez zarzutu (wpis katalogu jest formalnie poprawny), ``ole.exists(...)``
    też zwraca ``True`` — błąd ujawnia się dopiero przy ``ole.openstream(...)``,
    czyli w DRUGIM bloku ``try`` w ``extract_doc``, którego przed poprawką nic
    nie osłaniało. ``olefile`` rzuca wtedy gołym ``OSError``, a nie
    ``ExtractError``.
    """
    endofchain = 0xFFFFFFFE
    freesect = 0xFFFFFFFF
    fatsect = 0xFFFFFFFD

    header = bytearray(512)
    header[0:8] = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    struct.pack_into("<H", header, 24, 0x003E)  # minor version
    struct.pack_into("<H", header, 26, 0x0003)  # major version 3 (sektory 512 B)
    struct.pack_into("<H", header, 28, 0xFFFE)  # byte order
    struct.pack_into("<H", header, 30, 9)  # sector shift -> 512
    struct.pack_into("<H", header, 32, 6)  # mini sector shift -> 64
    struct.pack_into("<I", header, 40, 0)  # liczba sektorów katalogu (0 dla v3)
    struct.pack_into("<I", header, 44, 1)  # liczba sektorów FAT
    struct.pack_into("<I", header, 48, 1)  # pierwszy sektor katalogu
    struct.pack_into("<I", header, 56, 0x1000)  # próg mini-strumienia
    struct.pack_into("<I", header, 60, endofchain)  # brak MiniFAT
    struct.pack_into("<I", header, 68, endofchain)  # brak DIFAT
    for i in range(109):
        struct.pack_into("<I", header, 76 + i * 4, 0 if i == 0 else freesect)

    fat = [freesect] * 128
    fat[0] = fatsect  # sektor 0 to sam FAT
    fat[1] = endofchain  # sektor 1 (katalog) to jednosektorowy łańcuch
    fat_bytes = b"".join(struct.pack("<I", v) for v in fat)

    dirsec = bytearray(512)
    dirsec[0:128] = _ole_dir_entry("Root Entry", 5, 1, endofchain, 0)
    dirsec[128:256] = _ole_dir_entry("WordDocument", 1, 0xFFFFFFFF, endofchain, 0)
    # pozostałe dwa wpisy w sektorze zostają puste (object type 0)

    return bytes(header) + fat_bytes + bytes(dirsec)


def test_extract_doc_truncated_after_ole_magic_raises():
    """Bajty zaczynają się od magii OLE, ale dalej to śmieci — trafia to w
    konstrukcję ``OleFileIO`` (pierwszy blok ``try``), już wcześniej osłonięty."""
    magic = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    garbage = magic + b"\x00garbage not a real ole structure\xff" * 20
    with pytest.raises(ExtractError):
        extract_doc(garbage, max_chars=100)


def test_extract_doc_internal_corruption_raises_extract_error():
    """Regresja: uszkodzenie ujawniające się dopiero w drugim bloku ``try``
    (``ole.exists`` / ``ole.openstream(...).read()``) musi też stać się
    ``ExtractError`` — nie gołym wyjątkiem olefile."""
    data = _ole_bytes_with_worddocument_as_storage()
    with pytest.raises(ExtractError) as exc:
        extract_doc(data, max_chars=100)
    assert "not a readable" in str(exc.value)


# --- read_member: uszkodzone ciało części ZIP (finalny przegląd gałęzi) ----


def _corrupt_zip_member(data: bytes, member: str) -> bytes:
    """Uszkodź skompresowane ciało ``member``, zostawiając katalog centralny
    nietknięty — ``zipfile.ZipFile(...)`` (i ``getinfo``) nadal przechodzą,
    awaria ujawnia się dopiero przy ``zf.read(member)``.
    """
    buf = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        info = zf.getinfo(member)
    offset = info.header_offset
    name_len, extra_len = struct.unpack_from("<HH", buf, offset + 26)
    data_start = offset + 30 + name_len + extra_len
    data_end = data_start + info.compress_size
    # Zamiana bitów w środku strumienia deflate psuje kody Huffmana —
    # zlib zgłasza to jako zlib.error, a nie jako uszkodzenie ZIP-a.
    mid = (data_start + data_end) // 2
    for i in range(mid, min(mid + 8, data_end)):
        buf[i] ^= 0xFF
    return bytes(buf)


def test_corrupt_zip_member_read_raises_extract_error():
    """Regresja: ZIP z poprawnym katalogiem centralnym (``open_zip`` i
    ``getinfo`` przechodzą), ale uszkodzonym strumieniem danych członka —
    awaria ujawnia się dopiero w ``zf.read(name)`` wewnątrz ``read_member``.

    Przed poprawką ten odczyt nie był niczym osłonięty: przeciekał goły
    ``zlib.error`` (dekompresja) prosto przez ``dispatch`` do modelu —
    dokładnie ta usterka, którą łata ten commit.
    """
    data = docx_bytes(["hello world"])
    corrupted = _corrupt_zip_member(data, "word/document.xml")
    with pytest.raises(ExtractError):
        dispatch(
            corrupted,
            mimetype=(
                "application/vnd.openxmlformats-officedocument"
                ".wordprocessingml.document"
            ),
            filename=None,
            max_chars=1000,
        )


def test_parse_xml_billion_laughs_raises_extract_error():
    """Regresja: parsowanie z rozwinięciem encji (\"billion laughs\") musi
    zostać odrzucone jako ``ExtractError``, nie rozwinięte/nie crashnąć —
    to gwarancja defusedxml, którą tu pinujemy."""
    payload = (
        b'<?xml version="1.0"?>\n'
        b"<!DOCTYPE lolz [\n"
        b' <!ENTITY lol "lol">\n'
        b' <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">\n'
        b' <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;'
        b'&lol2;&lol2;">\n'
        b"]>\n"
        b"<lolz>&lol3;</lolz>"
    )
    with pytest.raises(ExtractError):
        parse_xml(payload, "test")
