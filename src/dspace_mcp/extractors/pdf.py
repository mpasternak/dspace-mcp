"""Ekstrakcja tekstu z PDF przez pypdf — czysta funkcja na bajtach.

Pobieraniem (ze strumieniowym limitem bajtów) zajmuje się ``client.py``; tu
dostajemy gotowe bajty. Czytamy strony po kolei i przerywamy po ``max_chars``.
"""

from __future__ import annotations

import io

import pypdf
from pypdf.errors import DependencyError, FileNotDecryptedError

from .base import ExtractError, assemble

__all__ = ["extract_pdf"]

_NOT_A_PDF = "This file is not a readable PDF."
_ENCRYPTED = "This PDF is password-protected, so its text cannot be extracted."
_NO_TEXT_LAYER = (
    "This PDF has no text layer - it is most likely a scan. "
    "OCR is out of scope for this server."
)


def extract_pdf(data: bytes, *, max_chars: int = 20000) -> dict:
    """Wyciągnij tekst z PDF-a; jednostką są strony."""
    reader, pages_total = _open(data)
    if pages_total == 0:
        raise ExtractError(_NOT_A_PDF)
    return assemble(
        (_page_text(page) for page in reader.pages),
        total=pages_total,
        unit="pages",
        max_chars=max_chars,
        empty_message=_NO_TEXT_LAYER,
    )


def _open(data: bytes) -> tuple[pypdf.PdfReader, int]:
    """Otwórz dokument i policz strony, tłumacząc wyjątki pypdf na ``ExtractError``."""
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return reader, len(reader.pages)
    except (FileNotDecryptedError, DependencyError):
        raise ExtractError(_ENCRYPTED) from None
    except Exception:
        raise ExtractError(_NOT_A_PDF) from None


def _page_text(page: pypdf.PageObject) -> str:
    """Tekst jednej strony; strona nie do odczytania liczy się jako pusta."""
    try:
        return page.extract_text()
    except Exception:
        return ""
