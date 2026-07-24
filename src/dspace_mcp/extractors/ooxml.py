"""Ekstrakcja tekstu z OOXML (docx, pptx, xlsx) — czysty stdlib zip+xml.

Wszystkie trzy to kontenery ZIP z XML-em. Tekst zbieramy po nazwie lokalnej
elementu (``t``, ``row``, ``c``), bo prefiksy namespace bywają różne między
generatorami. Czytamy jednostki po kolei i przerywamy po ``max_chars``.
"""

from __future__ import annotations

from .base import (
    ExtractError,
    assemble,
    localname,
    open_zip,
    parse_xml,
    read_member,
)

__all__ = ["extract_docx", "extract_pptx", "extract_xlsx"]

_DOCX = "Word document"
_PPTX = "PowerPoint presentation"
_XLSX = "Excel workbook"


def _trailing_number(name: str) -> int:
    """Końcowa liczba z nazwy części archiwum, do sortowania numerycznego.

    ``ppt/slides/slide12.xml`` → 12, ``xl/worksheets/sheet2.xml`` → 2. Zwykły
    ``sorted()`` posortowałby leksykograficznie (``sheet10`` przed ``sheet2``),
    a kolejność slajdów/arkuszy musi odpowiadać kolejności w prezentacji/
    skoroszycie, nie kolejności znaków w nazwie pliku.
    """
    digits = "".join(ch for ch in name.rsplit("/", 1)[-1] if ch.isdigit())
    return int(digits) if digits else 0


def extract_docx(data: bytes, *, max_chars: int = 20000) -> dict:
    """docx: tekst akapitów (``w:p`` → linia, ``w:t`` → treść runa)."""
    zf = open_zip(data, _DOCX)
    try:
        xml = read_member(zf, "word/document.xml", _DOCX)
    finally:
        zf.close()

    root = parse_xml(xml, _DOCX)
    paragraphs = [e for e in root.iter() if localname(e.tag) == "p"]
    return assemble(
        (_runs_text(p) for p in paragraphs),
        total=len(paragraphs),
        unit="paragraphs",
        max_chars=max_chars,
        empty_message=f"This {_DOCX} contains no extractable text.",
    )


def extract_pptx(data: bytes, *, max_chars: int = 20000) -> dict:
    """pptx: tekst slajdów po kolei (``a:t`` w ``ppt/slides/slideN.xml``).

    Nazwy slajdów sortujemy z góry (jedno przejście po ``namelist()``), ale
    samo czytanie+parsowanie każdej części odkładamy do leniwego generatora,
    żeby ``assemble`` mogło przerwać po ``max_chars`` bez czytania reszty
    archiwum (patrz ``pdf.py`` — ten sam wzorzec).
    """
    zf = open_zip(data, _PPTX)
    try:
        slide_names = sorted(
            (
                n
                for n in zf.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            ),
            key=_trailing_number,
        )
        if not slide_names:
            raise ExtractError(f"This file is not a readable {_PPTX}.")
        return assemble(
            (
                _slide_text(parse_xml(read_member(zf, n, _PPTX), _PPTX))
                for n in slide_names
            ),
            total=len(slide_names),
            unit="slides",
            max_chars=max_chars,
            empty_message=f"This {_PPTX} contains no extractable text.",
        )
    finally:
        zf.close()


def extract_xlsx(data: bytes, *, max_chars: int = 20000) -> dict:
    """xlsx: arkusze spłaszczone do wierszy (tab między kolumnami).

    ``sharedStrings.xml`` to jedna, mała część — czytamy ją zachłannie z góry.
    Same arkusze (potencjalnie dużo i duże) czytamy+parsujemy leniwie, jeden
    generator na ``assemble``, żeby wczesne zatrzymanie po ``max_chars``
    faktycznie oszczędzało pracę (patrz ``pdf.py``).
    """
    zf = open_zip(data, _XLSX)
    try:
        shared = _shared_strings(zf)
        sheet_names = sorted(
            (
                n
                for n in zf.namelist()
                if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
            ),
            key=_trailing_number,
        )
        if not sheet_names:
            raise ExtractError(f"This file is not a readable {_XLSX}.")
        return assemble(
            (
                _sheet_text(parse_xml(read_member(zf, n, _XLSX), _XLSX), shared)
                for n in sheet_names
            ),
            total=len(sheet_names),
            unit="sheets",
            max_chars=max_chars,
            empty_message=f"This {_XLSX} contains no extractable text.",
        )
    finally:
        zf.close()


def _runs_text(paragraph) -> str:
    """Sklej tekst wszystkich runów (``t``) akapitu."""
    return "".join(e.text or "" for e in paragraph.iter() if localname(e.tag) == "t")


def _slide_text(root) -> str:
    """Tekst slajdu: wszystkie ``a:t`` w kolejności, po jednym na linię."""
    parts = [e.text or "" for e in root.iter() if localname(e.tag) == "t"]
    return "\n".join(p for p in parts if p)


def _shared_strings(zf) -> list[str]:
    """Tabela stringów współdzielonych (``xl/sharedStrings.xml``)."""
    xml = read_member(zf, "xl/sharedStrings.xml", _XLSX, optional=True)
    if not xml:
        return []
    root = parse_xml(xml, _XLSX)
    strings: list[str] = []
    for si in (e for e in root if localname(e.tag) == "si"):
        strings.append(
            "".join(t.text or "" for t in si.iter() if localname(t.tag) == "t")
        )
    return strings


def _sheet_text(root, shared: list[str]) -> str:
    """Arkusz → wiersze (tab między kolumnami, nowa linia między wierszami)."""
    rows: list[str] = []
    for row in (e for e in root.iter() if localname(e.tag) == "row"):
        cells = [_cell_value(c, shared) for c in row if localname(c.tag) == "c"]
        rows.append("\t".join(cells))
    return "\n".join(rows)


def _cell_value(cell, shared: list[str]) -> str:
    """Wartość komórki: shared string po indeksie, inline, albo liczba z ``v``."""
    cell_type = cell.get("t")
    value = next((e for e in cell if localname(e.tag) == "v"), None)
    if cell_type == "s":
        if value is not None and value.text and value.text.isdigit():
            idx = int(value.text)
            if 0 <= idx < len(shared):
                return shared[idx]
        return ""
    if cell_type == "inlineStr":
        return "".join(t.text or "" for t in cell.iter() if localname(t.tag) == "t")
    return value.text if value is not None and value.text else ""
