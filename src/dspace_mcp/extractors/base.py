"""Wspólny fundament ekstraktorów: wyjątek, normalizacja, kontenery ZIP+XML.

Nic tu nie robi I/O poza czytaniem bajtów podanych na wejściu. Reguła jak w
``shaping.py``: cudzy plik to nieufne wejście — brak elementu czy zły kształt
kończy się ``ExtractError`` z komunikatem po angielsku, nigdy przeciekiem
stack trace'a do modelu.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from collections.abc import Iterable
from xml.etree.ElementTree import Element, ParseError

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring as _safe_fromstring

#: 3+ znaki nowej linii → jedna pusta linia (jak w dawnym pdf.py).
_BLANK_LINES = re.compile(r"\n{3,}")

#: Twardy sufit rozpakowanego rozmiaru pojedynczej części kontenera — ochrona
#: przed „zip bombą" (małe archiwum, gigabajty po dekompresji).
_MAX_PART_BYTES = 100 * 1024 * 1024


class ExtractError(Exception):
    """Błąd przeznaczony do pokazania modelowi. ``message`` jest po angielsku."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def normalize(text: str) -> str:
    """Minimalna normalizacja: zwijamy nadmiar pustych linii, obcinamy końce."""
    return _BLANK_LINES.sub("\n\n", text).strip()


def open_zip(data: bytes, fmt: str) -> zipfile.ZipFile:
    """Otwórz kontener ZIP albo rzuć ``ExtractError`` z nazwą formatu."""
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ExtractError(f"This file is not a readable {fmt}.") from None


def localname(tag: str) -> str:
    """Nazwa lokalna elementu bez namespace'u: ``{ns}p`` → ``p``.

    Parsujemy po nazwie lokalnej, bo różne generatory piszą różne prefiksy,
    a nazwa lokalna (``t``, ``p``, ``row``) jest stała w danym formacie.
    """
    return tag.rsplit("}", 1)[-1]


def read_member(
    zf: zipfile.ZipFile, name: str, fmt: str, *, optional: bool = False
) -> bytes:
    """Odczytaj część archiwum, pilnując rozpakowanego rozmiaru (anty-zip-bomba).

    ``optional`` → brak części zwraca puste bajty (np. ``sharedStrings`` bywa
    nieobecne); w przeciwnym razie brak części znaczy „to nie ten format".
    """
    try:
        info = zf.getinfo(name)
    except KeyError:
        if optional:
            return b""
        raise ExtractError(f"This file is not a readable {fmt}.") from None
    if info.file_size > _MAX_PART_BYTES:
        raise ExtractError(f"This {fmt} is too large to process safely.")
    try:
        return zf.read(name)
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError):
        raise ExtractError(f"This file is not a readable {fmt}.") from None


def parse_xml(xml: bytes, fmt: str) -> Element:
    """Sparsuj część XML bezpiecznie (defusedxml) albo rzuć ``ExtractError``.

    Pliki pochodzą z dowolnych, niezaufanych repozytoriów, więc nie używamy
    gołego ``xml.etree`` — jest podatny na ataki „billion laughs" i XXE.
    defusedxml odrzuca takie dokumenty, a my zamieniamy to na ``ExtractError``.
    """
    try:
        return _safe_fromstring(xml)
    except (ParseError, DefusedXmlException):
        raise ExtractError(f"This file is not a readable {fmt}.") from None


def assemble(
    unit_texts: Iterable[str],
    *,
    total: int,
    unit: str | None,
    max_chars: int,
    empty_message: str,
) -> dict:
    """Złóż wynik z tekstów kolejnych jednostek, przerywając po ``max_chars``.

    ``unit_texts`` bywa leniwym generatorem (strony PDF, slajdy) — czytamy po
    kolei i przestajemy, gdy uzbierany tekst osiągnie limit; ``units_processed``
    bywa wtedy mniejsze niż ``total``. Pusty wynik → ``ExtractError`` (rozróżnienie
    „nie umiem odczytać" vs „dokument pusty" jest kluczowe dla modelu).
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be a positive integer")

    chunks: list[str] = []
    collected = 0
    processed = 0
    for chunk in unit_texts:
        processed += 1
        chunks.append(chunk)
        collected += len(chunk) + 1  # +1 za łącznik między jednostkami
        if collected >= max_chars:
            break

    text = normalize("\n".join(chunks))
    if not text:
        raise ExtractError(empty_message)
    truncated = len(text) > max_chars or processed < total
    return {
        "text": text[:max_chars],
        "truncated": truncated,
        "unit": unit,
        "units_processed": processed,
        "units_total": total,
    }
