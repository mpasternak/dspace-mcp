"""Ekstrakcja tekstu z bitstreamów: wspólny kontrakt i (od Tasku 2) dispatch.

Każdy ekstraktor to czysta funkcja ``(data: bytes, *, max_chars) -> dict``
zwracająca ``{"text","truncated","unit","units_processed","units_total"}``.
"""

from __future__ import annotations

from .base import ExtractError
from .pdf import extract_pdf

__all__ = ["ExtractError", "extract_pdf"]
