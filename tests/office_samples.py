"""Buildery minimalnych, poprawnych kontenerów ZIP+XML do testów ekstraktorów.

To nie są pliki z żywej instancji (jak fixture'y HTTP), tylko najmniejsze
poprawne kontenery, które ćwiczą nasz parser stdlib: deterministyczne i bez
zewnętrznych narzędzi. Namespace'y są prawdziwe, żeby ``localname`` działał
tak jak na plikach z Worda/LibreOffice.
"""

from __future__ import annotations

import io
import zipfile

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P = "http://schemas.openxmlformats.org/presentationml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

_TEXT = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
_TABLE = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
_DRAW = "urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
_OFFICE = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in members.items():
            zf.writestr(name, content)
    return buf.getvalue()


def docx_bytes(paragraphs: list[str]) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    doc = (
        f'<?xml version="1.0"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    return _zip({"word/document.xml": doc})


def pptx_bytes(slides: list[list[str]]) -> bytes:
    members: dict[str, str] = {}
    for i, texts in enumerate(slides, 1):
        runs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
        members[f"ppt/slides/slide{i}.xml"] = (
            f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}"><a:txBody>{runs}</a:txBody></p:sld>'
        )
    return _zip(members)


def xlsx_bytes(sheets: list[list[list[str]]]) -> bytes:
    strings: list[str] = []
    index: dict[str, int] = {}

    def sid(value: str) -> int:
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return index[value]

    members: dict[str, str] = {}
    for i, rows in enumerate(sheets, 1):
        rows_xml = ""
        for r, row in enumerate(rows, 1):
            cells = ""
            for c, value in enumerate(row):
                ref = f"{chr(ord('A') + c)}{r}"
                cells += f'<c r="{ref}" t="s"><v>{sid(value)}</v></c>'
            rows_xml += f'<row r="{r}">{cells}</row>'
        members[f"xl/worksheets/sheet{i}.xml"] = (
            f'<worksheet xmlns="{_S}"><sheetData>{rows_xml}</sheetData></worksheet>'
        )
    si = "".join(f"<si><t>{s}</t></si>" for s in strings)
    members["xl/sharedStrings.xml"] = (
        f'<sst xmlns="{_S}" count="{len(strings)}" '
        f'uniqueCount="{len(strings)}">{si}</sst>'
    )
    return _zip(members)


def odt_bytes(paragraphs: list[str]) -> bytes:
    ps = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    content = (
        f'<office:document-content xmlns:office="{_OFFICE}" xmlns:text="{_TEXT}">'
        f"<office:body><office:text>{ps}</office:text></office:body>"
        f"</office:document-content>"
    )
    return _zip({"content.xml": content})


def ods_bytes(sheets: list[list[list[str]]]) -> bytes:
    tables = ""
    for rows in sheets:
        rows_xml = ""
        for row in rows:
            cells = "".join(
                f"<table:table-cell><text:p>{v}</text:p></table:table-cell>"
                for v in row
            )
            rows_xml += f"<table:table-row>{cells}</table:table-row>"
        tables += f"<table:table>{rows_xml}</table:table>"
    content = (
        f'<office:document-content xmlns:office="{_OFFICE}" '
        f'xmlns:table="{_TABLE}" xmlns:text="{_TEXT}">'
        f"<office:body><office:spreadsheet>{tables}</office:spreadsheet>"
        f"</office:body></office:document-content>"
    )
    return _zip({"content.xml": content})


def odp_bytes(slides: list[list[str]]) -> bytes:
    pages = ""
    for texts in slides:
        frames = "".join(f"<text:p>{t}</text:p>" for t in texts)
        pages += f"<draw:page>{frames}</draw:page>"
    content = (
        f'<office:document-content xmlns:office="{_OFFICE}" '
        f'xmlns:draw="{_DRAW}" xmlns:text="{_TEXT}">'
        f"<office:body><office:presentation>{pages}</office:presentation>"
        f"</office:body></office:document-content>"
    )
    return _zip({"content.xml": content})
