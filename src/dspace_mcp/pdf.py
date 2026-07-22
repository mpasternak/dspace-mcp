"""Ekstrakcja tekstu z PDF-a — czysta funkcja na bajtach.

Nic tu nie pobiera z sieci i nic nie zapisuje na dysk: pobieraniem (ze
strumieniowym limitem bajtów) zajmuje się ``client.py``, tu dostajemy gotowe
bajty. Dzięki temu moduł testuje się bez HTTP i bez plików.

Dwie rzeczy są ważniejsze niż wierność oryginałowi (D4, ekonomia tokenów):

1. Czytamy strony po kolei i **przerywamy**, gdy uzbierany tekst osiągnie
   ``max_chars`` — parsowanie 400 stron po to, by oddać 20 000 znaków, to
   zmarnowany czas i pamięć.
2. Każda porażka kończy się ``PdfError`` z komunikatem po angielsku,
   przeznaczonym dla modelu. Zwłaszcza skan bez warstwy tekstowej: oddanie
   pustego stringa byłoby najgorszą opcją, bo model uznałby, że dokument jest
   pusty.
"""

from __future__ import annotations

import io
import re

import pypdf
from pypdf.errors import DependencyError, FileNotDecryptedError

__all__ = ["PdfError", "extract_text"]

_NOT_A_PDF = "This file is not a readable PDF."
_ENCRYPTED = "This PDF is password-protected, so its text cannot be extracted."
_NO_TEXT_LAYER = (
    "This PDF has no text layer - it is most likely a scan. "
    "OCR is out of scope for this server."
)

#: Ciąg 3+ znaków nowej linii (czyli 2+ pustych linii) → jedna pusta linia.
_BLANK_LINES = re.compile(r"\n{3,}")


class PdfError(Exception):
    """Błąd przeznaczony do pokazania modelowi. ``message`` jest po angielsku."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def _normalize(text: str) -> str:
    """Minimalna normalizacja: zwijamy nadmiar pustych linii i obcinamy końce.

    Świadomie nic więcej — nie „poprawiamy” treści dokumentu, bo każda taka
    poprawka to zgadywanie, a model dostaje wtedy tekst, którego w PDF-ie nie
    ma.
    """
    return _BLANK_LINES.sub("\n\n", text).strip()


def extract_text(data: bytes, *, max_chars: int = 20000) -> dict:
    """Wyciągnij tekst z PDF-a podanego jako bajty.

    Zwraca ``{"text", "truncated", "pages_processed", "pages_total"}``, gdzie
    ``pages_processed`` to liczba stron faktycznie tkniętych (może być mniejsza
    niż ``pages_total``, jeśli limit znaków wypadł wcześniej).

    Rzuca ``PdfError`` (plik nie jest PDF-em, jest zaszyfrowany albo nie ma
    warstwy tekstowej) lub ``ValueError`` przy ``max_chars <= 0``.
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    reader, pages_total = _open(data)
    if pages_total == 0:
        raise PdfError(_NOT_A_PDF)

    chunks: list[str] = []
    collected = 0
    pages_processed = 0
    for page in reader.pages:
        pages_processed += 1
        chunks.append(_page_text(page))
        collected += len(chunks[-1]) + 1  # +1 za łącznik między stronami
        if collected >= max_chars:
            break

    text = _normalize("\n".join(chunks))
    if not text:
        # Rozróżnienie kluczowe dla modelu: „nie umiem odczytać” to co innego
        # niż „dokument jest pusty”.
        raise PdfError(_NO_TEXT_LAYER)

    # Obcięcie to nie tylko przycięty string — także niedoczytane strony.
    truncated = len(text) > max_chars or pages_processed < pages_total
    return {
        "text": text[:max_chars],
        "truncated": truncated,
        "pages_processed": pages_processed,
        "pages_total": pages_total,
    }


def _open(data: bytes) -> tuple[pypdf.PdfReader, int]:
    """Otwórz dokument i policz strony, tłumacząc wyjątki pypdf na ``PdfError``.

    ``pypdf`` jest leniwy: wiele błędów (w tym brak hasła) wychodzi dopiero przy
    sięgnięciu po strony, dlatego liczba stron jest częścią otwierania.
    Nie testujemy ``reader.is_encrypted``, bo PDF-y zaszyfrowane pustym hasłem
    użytkownika (typowe „zabezpieczenie przed edycją”) czytają się normalnie i
    odmowa byłaby fałszywym alarmem.

    ``from None`` jest tu celowe — model dostaje komunikat, nie ślad stosu.
    """
    try:
        reader = pypdf.PdfReader(io.BytesIO(data))
        return reader, len(reader.pages)
    except (FileNotDecryptedError, DependencyError):
        # DependencyError = szyfrowanie, którego pypdf nie umie rozpiąć;
        # z punktu widzenia użytkownika to ten sam problem co brak hasła.
        raise PdfError(_ENCRYPTED) from None
    except Exception:
        # Uszkodzony PDF potrafi wyrzucić dowolny wyjątek z głębi parsera —
        # łapiemy szeroko, bo alternatywą jest przeciek stack trace'a do modelu.
        raise PdfError(_NOT_A_PDF) from None


def _page_text(page: pypdf.PageObject) -> str:
    """Tekst pojedynczej strony; strona nie do odczytania liczy się jako pusta.

    Pojedyncza felerna strona nie powinna unieważniać całego dokumentu — jeśli
    żadna się nie odczyta, i tak skończy się to komunikatem o braku warstwy
    tekstowej.
    """
    try:
        return page.extract_text()
    except Exception:
        return ""
