"""Ekstrakcja tekstu z ODF (odt, ods, odp) — czysty stdlib zip+xml.

Wszystkie trzy trzymają treść w ``content.xml``. Tekst bierzemy przez
``itertext()`` właściwych elementów: w ODF treść siedzi wprost w węzłach
tekstowych, więc to daje pełny tekst bez interpretacji.
"""

from __future__ import annotations

from .base import assemble, localname, open_zip, parse_xml, read_member

__all__ = ["extract_odt", "extract_ods", "extract_odp"]

_ODT = "OpenDocument text"
_ODS = "OpenDocument spreadsheet"
_ODP = "OpenDocument presentation"


def _content_root(data: bytes, fmt: str):
    zf = open_zip(data, fmt)
    try:
        xml = read_member(zf, "content.xml", fmt)
    finally:
        zf.close()
    return parse_xml(xml, fmt)


def extract_odt(data: bytes, *, max_chars: int = 20000) -> dict:
    """odt: akapity i nagłówki (``text:p``, ``text:h``)."""
    root = _content_root(data, _ODT)
    paragraphs = [e for e in root.iter() if localname(e.tag) in ("p", "h")]
    return assemble(
        ("".join(p.itertext()) for p in paragraphs),
        total=len(paragraphs),
        unit="paragraphs",
        max_chars=max_chars,
        empty_message=f"This {_ODT} contains no extractable text.",
    )


def extract_ods(data: bytes, *, max_chars: int = 20000) -> dict:
    """ods: arkusze (``table:table``) → wiersze/komórki (tab między kolumnami)."""
    root = _content_root(data, _ODS)
    tables = [e for e in root.iter() if localname(e.tag) == "table"]
    return assemble(
        (_table_text(t) for t in tables),
        total=len(tables),
        unit="sheets",
        max_chars=max_chars,
        empty_message=f"This {_ODS} contains no extractable text.",
    )


def extract_odp(data: bytes, *, max_chars: int = 20000) -> dict:
    """odp: slajdy (``draw:page``)."""
    root = _content_root(data, _ODP)
    pages = [e for e in root.iter() if localname(e.tag) == "page"]
    return assemble(
        ("".join(p.itertext()) for p in pages),
        total=len(pages),
        unit="slides",
        max_chars=max_chars,
        empty_message=f"This {_ODP} contains no extractable text.",
    )


def _table_text(table) -> str:
    rows: list[str] = []
    for row in (e for e in table.iter() if localname(e.tag) == "table-row"):
        cells = ["".join(c.itertext()) for c in row if localname(c.tag) == "table-cell"]
        rows.append("\t".join(cells))
    return "\n".join(rows)
