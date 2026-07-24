"""Ekstrakcja tekstu z bitstreamów: rejestr formatów i dispatch po mimetypie.

Cała wiedza „który format" siedzi tutaj; ``tools.py`` woła wyłącznie
:func:`dispatch`. Każdy ekstraktor to czysta funkcja
``(data: bytes, *, max_chars) -> dict``.
"""

from __future__ import annotations

from collections.abc import Callable

from .base import ExtractError
from .pdf import extract_pdf

__all__ = ["ExtractError", "dispatch"]

#: mimetype → (funkcja ekstraktora, etykieta formatu w wyniku).
_BY_MIMETYPE: dict[str, tuple[Callable[..., dict], str]] = {
    "application/pdf": (extract_pdf, "pdf"),
}

#: rozszerzenie nazwy pliku → mimetype (fallback, gdy mimetype pusty/ogólny).
_BY_EXTENSION: dict[str, str] = {
    "pdf": "application/pdf",
}

#: mimetypy „nic konkretnego" — wtedy ufamy rozszerzeniu nazwy pliku.
_GENERIC = {"", "application/octet-stream", "binary/octet-stream"}


def _normalize_mimetype(mimetype: str | None) -> str:
    if not isinstance(mimetype, str):
        return ""
    return mimetype.split(";", 1)[0].strip().lower()


def _extension(filename: str | None) -> str:
    if not isinstance(filename, str) or "." not in filename:
        return ""
    return filename.rsplit(".", 1)[1].strip().lower()


def dispatch(
    data: bytes,
    *,
    mimetype: str | None,
    filename: str | None,
    max_chars: int,
) -> dict:
    """Wybierz ekstraktor po mimetypie (z fallbackiem na rozszerzenie) i uruchom.

    Fallback na rozszerzenie stosujemy tylko, gdy mimetype jest pusty albo
    ogólny — instancje potrafią oddać ``application/octet-stream`` dla plików,
    które nazwą zdradzają format.
    """
    normalized = _normalize_mimetype(mimetype)
    entry = _BY_MIMETYPE.get(normalized)
    if entry is None and normalized in _GENERIC:
        mapped = _BY_EXTENSION.get(_extension(filename))
        if mapped:
            entry = _BY_MIMETYPE.get(mapped)
    if entry is None:
        raise ExtractError(f"No text extractor for {mimetype or 'this file type'}.")
    func, label = entry
    result = func(data, max_chars=max_chars)
    result["format"] = label
    return result
