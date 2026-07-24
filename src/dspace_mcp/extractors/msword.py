"""Best-effort ekstrakcja tekstu ze starego binarnego .doc (OLE Compound File).

Stary format Worda nie ma opcji stdlib, więc czytamy strumień ``WordDocument``
przez ``olefile`` i wyłuskujemy z niego czytelny tekst. To świadomie proste i
stratne (bez czytania FIB): gdy tekst się wyłuska — oddajemy go; gdy nie —
``ExtractError`` z komunikatem, nigdy pusty string ani stack trace.
"""

from __future__ import annotations

import io
import re

import olefile

from .base import ExtractError, normalize

__all__ = ["extract_doc"]

_NOT_A_DOC = "This file is not a readable Word document."
_NO_TEXT = "This Word document has no extractable text; it may be a scan or empty."

#: Ciągi 2+ spacji zwijamy do jednej.
_SPACES = re.compile(r"[ ]{2,}")


def extract_doc(data: bytes, *, max_chars: int = 20000) -> dict:
    """Wyciągnij tekst z .doc best-effort; brak naturalnej jednostki."""
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    stream = io.BytesIO(data)
    if not olefile.isOleFile(stream):
        raise ExtractError(_NOT_A_DOC)
    try:
        ole = olefile.OleFileIO(stream)
    except Exception:
        raise ExtractError(_NOT_A_DOC) from None
    try:
        if not ole.exists("WordDocument"):
            raise ExtractError(_NOT_A_DOC)
        raw = ole.openstream("WordDocument").read()
    finally:
        ole.close()

    text = normalize(_scrape_text(raw))
    if not text:
        raise ExtractError(_NO_TEXT)
    return {
        "text": text[:max_chars],
        "truncated": len(text) > max_chars,
        "unit": None,
        "units_processed": None,
        "units_total": None,
    }


def _scrape_text(raw: bytes) -> str:
    """Best-effort: dekoduj cp1252, wytnij bajty sterujące, zostaw sensowne linie.

    Zostawiamy tylko linie z co najmniej trzema literami — to odsiewa szum
    formatowania (tablice offsetów, nazwy stylów), a zostawia zdania.
    """
    decoded = raw.decode("cp1252", errors="ignore")
    cleaned = "".join(
        ch if (ch.isprintable() or ch in "\n\t") else " " for ch in decoded
    )
    cleaned = _SPACES.sub(" ", cleaned)
    lines = [line.strip() for line in cleaned.splitlines()]
    kept = [line for line in lines if sum(c.isalpha() for c in line) >= 3]
    return "\n".join(kept)
