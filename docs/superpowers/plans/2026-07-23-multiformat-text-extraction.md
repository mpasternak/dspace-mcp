# Multiformat Text Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rozszerzyć narzędzie `get_bitstream_text` z „tylko PDF" na osiem formatów (pdf, docx, doc, odt, ods, odp, pptx, xlsx), z rejestrem ekstraktorów i dispatchem po mimetypie.

**Architecture:** `dspace_mcp/pdf.py` zostaje zastąpiony pakietem `dspace_mcp/extractors/`. Każdy ekstraktor to czysta funkcja `(bytes, *, max_chars) -> dict`. Rodzina ZIP+XML (OOXML, ODF) obsługiwana `zipfile`+`xml.etree` ze stdlib; legacy `.doc` przez `olefile` (jedyna nowa zależność). `tools.get_bitstream_text` woła `extractors.dispatch(...)` i doklejaja link do błędu.

**Tech Stack:** Python ≥3.10, `httpx`, `mcp[cli]`, `pypdf` (istnieje), `olefile` (nowa), stdlib `zipfile`/`xml.etree`, `pytest`+`pytest-asyncio`+`respx`, `ruff`, `uv`.

## Global Constraints

- Python floor: `>=3.10`. Ruff `line-length = 88`, `target-version = py310`.
- **Żadnych ciężkich zależności**: nie `lxml`, nie `Pillow`, nie `python-docx`/`python-pptx`. Nowe zależności to tylko `olefile>=0.47` (legacy `.doc`) i `defusedxml>=0.7` (bezpieczne parsowanie XML z niezaufanych plików) — obie małe i czysto-pythonowe.
- **Bezpieczeństwo parsowania**: pliki pochodzą z niezaufanych repozytoriów. XML zawsze przez `defusedxml` (nie goły `xml.etree` — podatny na „billion laughs"/XXE). Rozpakowywanie części ZIP limitowane rozmiarem (anty-zip-bomba).
- **Ekstraktory są czyste**: żadnego I/O, sieci, dostępu do dysku — tylko bajty na wejściu.
- **Nic w warstwie shaping/ekstrakcji nie przecieka wyjątkiem technicznym do modelu**: cudze pliki to normalne, nieufne wejście; porażka → `ExtractError` z komunikatem po **angielsku**.
- **Docstringi i komentarze po polsku; wszystkie stringi widoczne dla modelu/użytkownika po angielsku.**
- **Read-only inwariant (D1)**: proces wysyła wyłącznie GET. Ekstrakcja działa na już pobranych bajtach; nie wolno dodać żadnego żądania nie-GET.
- Każdy commit kończy się standardowymi trailerami repo:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01FtURxSFMFXU5udUkMKqRPq
  ```
- Uruchamianie testów: `uv run pytest`. Lint: `uv run ruff check .`. Format: `uv run ruff format .`.
- Praca na gałęzi `feat/multiformat-text-extraction` (już istnieje).

## File Structure

**Tworzone:**
- `src/dspace_mcp/extractors/__init__.py` — `ExtractError`, rejestr, `dispatch()`.
- `src/dspace_mcp/extractors/base.py` — `ExtractError`, `normalize`, `open_zip`, `localname`, `parse_xml`, `assemble` (wspólne).
- `src/dspace_mcp/extractors/pdf.py` — `extract_pdf` (przeniesiony pypdf, nowy kształt wyniku).
- `src/dspace_mcp/extractors/ooxml.py` — `extract_docx`, `extract_pptx`, `extract_xlsx`.
- `src/dspace_mcp/extractors/opendocument.py` — `extract_odt`, `extract_ods`, `extract_odp`.
- `src/dspace_mcp/extractors/msword.py` — `extract_doc` (olefile).
- `tests/office_samples.py` — buildery minimalnych, poprawnych kontenerów ZIP+XML dla testów.
- `tests/test_extractors.py` — testy wszystkich ekstraktorów i dispatchu.

**Modyfikowane:**
- `src/dspace_mcp/tools.py` — `get_bitstream_text` woła `dispatch`, usuwa twardy warunek „pdf in mimetype".
- `src/dspace_mcp/server.py` — `_guard` przestaje importować/łapać `PdfError`.
- `src/dspace_mcp/config.py` — `extract_max_mb` + aliasy wsteczne `pdf_max_mb`/`pdf_max_bytes`.
- `pyproject.toml` — dodaje `olefile>=0.47` i `defusedxml>=0.7`.
- `tests/test_tools.py`, `tests/test_config.py`, `tests/test_client.py` — nowe i dostosowane testy.
- `CLAUDE.md` — wzmianki o `pdf.py` → `extractors/`.

**Usuwane:**
- `src/dspace_mcp/pdf.py` (treść przenosi się do `extractors/pdf.py` + `base.py`).
- `tests/test_pdf.py` (treść przenosi się do `tests/test_extractors.py`).

---

### Task 1: Pakiet `extractors/` — `base.py`, `ExtractError`, przeniesiony PDF

Przenosi logikę PDF do nowego pakietu, wyodrębnia wspólne pomocniki i uogólnia kształt wyniku z `pages_*` na `unit`+`units_*`. Po tym tasku PDF działa jak dawniej, tylko z nowego miejsca i w nowym kształcie. `dispatch` powstaje w Tasku 2.

**Files:**
- Create: `src/dspace_mcp/extractors/__init__.py`
- Create: `src/dspace_mcp/extractors/base.py`
- Create: `src/dspace_mcp/extractors/pdf.py`
- Create: `tests/test_extractors.py`
- Delete: `src/dspace_mcp/pdf.py`, `tests/test_pdf.py`
- Modify: `src/dspace_mcp/tools.py` (import + `get_bitstream_text` tymczasowo woła `extract_pdf`)
- Modify: `src/dspace_mcp/server.py` (`_guard`)

**Interfaces:**
- Produces:
  - `ExtractError(message: str)` z atrybutem `.message` (w `base.py`, re-eksport z `__init__.py`).
  - `base.normalize(text: str) -> str`
  - `base.open_zip(data: bytes, fmt: str) -> zipfile.ZipFile` (raises `ExtractError`)
  - `base.localname(tag: str) -> str`
  - `base.parse_xml(xml: bytes, fmt: str) -> xml.etree.ElementTree.Element` (raises `ExtractError`; parsuje przez defusedxml)
  - `base.read_member(zf, name: str, fmt: str, *, optional: bool = False) -> bytes` (limit rozpakowanego rozmiaru; brak części → `ExtractError` lub `b""`)
  - `base.assemble(unit_texts: Iterable[str], *, total: int, unit: str | None, max_chars: int, empty_message: str) -> dict` — zwraca `{"text","truncated","unit","units_processed","units_total"}`.
  - `pdf.extract_pdf(data: bytes, *, max_chars: int = 20000) -> dict`

- [ ] **Step 0: Dodaj zależność `defusedxml`**

In `pyproject.toml`, w `dependencies` dodaj po `pypdf>=4.0`:

```python
    "defusedxml>=0.7",
```

Run: `uv sync`
Expected: `defusedxml` zainstalowane (mała, czysto-pythonowa paczka; bezpieczne parsowanie XML z niezaufanych plików).

- [ ] **Step 1: Utwórz `base.py` z wyjątkiem i pomocnikami**

Create `src/dspace_mcp/extractors/base.py`:

```python
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
from collections.abc import Iterable
from xml.etree.ElementTree import Element, ParseError

from defusedxml.ElementTree import fromstring as _safe_fromstring
from defusedxml.common import DefusedXmlException

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
    return zf.read(name)


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
```

- [ ] **Step 2: Utwórz `extractors/pdf.py` (przeniesiony pypdf, nowy kształt)**

Create `src/dspace_mcp/extractors/pdf.py`:

```python
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
```

- [ ] **Step 3: Utwórz `extractors/__init__.py` (na razie re-eksport)**

Create `src/dspace_mcp/extractors/__init__.py`:

```python
"""Ekstrakcja tekstu z bitstreamów: wspólny kontrakt i (od Tasku 2) dispatch.

Każdy ekstraktor to czysta funkcja ``(data: bytes, *, max_chars) -> dict``
zwracająca ``{"text","truncated","unit","units_processed","units_total"}``.
"""

from __future__ import annotations

from .base import ExtractError
from .pdf import extract_pdf

__all__ = ["ExtractError", "extract_pdf"]
```

- [ ] **Step 4: Przełącz `tools.py` i `server.py` na nowy pakiet**

In `src/dspace_mcp/tools.py`, replace the import line:

```python
from .pdf import PdfError, extract_text
```

with:

```python
from .extractors import ExtractError, extract_pdf
```

In `src/dspace_mcp/tools.py::get_bitstream_text`, replace the extraction block:

```python
    data = await client.stream_bytes(url, max_bytes=client.config.pdf_max_bytes)
    try:
        extracted = extract_text(data, max_chars=max_chars)
    except PdfError as exc:
        raise DSpaceError(f"{exc} Link to the file: {url}") from exc
```

with (still PDF-only until Task 2):

```python
    data = await client.stream_bytes(url, max_bytes=client.config.pdf_max_bytes)
    try:
        extracted = extract_pdf(data, max_chars=max_chars)
    except ExtractError as exc:
        raise DSpaceError(f"{exc} Link to the file: {url}") from exc
```

In `src/dspace_mcp/server.py`, remove the import `from .pdf import PdfError` and change:

```python
        except (DSpaceError, PdfError) as exc:
```

to:

```python
        except DSpaceError as exc:
```

(`ExtractError` is always wrapped into `DSpaceError` inside `get_bitstream_text`, so `_guard` only needs `DSpaceError`.)

- [ ] **Step 5: Usuń stare pliki**

```bash
git rm src/dspace_mcp/pdf.py tests/test_pdf.py
```

- [ ] **Step 6: Napisz testy PDF w nowym pliku (najpierw czerwone)**

Create `tests/test_extractors.py`:

```python
"""Testy ekstraktorów tekstu — czyste funkcje na bajtach."""

from __future__ import annotations

import pytest

from dspace_mcp.extractors import ExtractError, extract_pdf


def _make_pdf(pages: list[str]) -> bytes:
    """Zbuduj mały, prawdziwy PDF z jedną linią tekstu na stronę."""
    from pypdf import PdfWriter
    from reportlab.pdfgen import canvas  # type: ignore

    import io

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    for text in pages:
        c.drawString(72, 720, text)
        c.showPage()
    c.save()
    buf.seek(0)
    return buf.read()
```

Note: unikamy dodatkowej zależności `reportlab`. Zamiast generować PDF, użyj małego, poprawnego PDF-a wpisanego bajtami. Zastąp `_make_pdf` powyżej poniższą wersją bez zależności:

```python
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
    pdf += (
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF"
        % (len(objs) + 1, xref_pos)
    )
    return pdf


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


def test_extract_pdf_rejects_non_pdf():
    with pytest.raises(ExtractError) as exc:
        extract_pdf(b"this is not a pdf", max_chars=100)
    assert "not a readable PDF" in str(exc.value)
```

- [ ] **Step 7: Uruchom testy — najpierw sam nowy plik**

Run: `uv run pytest tests/test_extractors.py -v`
Expected: PASS (3 testy). Jeśli minimalny PDF nie parsuje w pypdf, poprawiaj `_one_page_pdf` aż `test_extract_pdf_returns_text_and_page_units` przejdzie — to bootstrap fixture, nie kod produkcyjny.

- [ ] **Step 8: Uruchom całość i lint**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS. Jeśli jakiś test w `test_tools.py`/`test_server.py` odwołuje się do `pages_processed`/`pages_total` albo do `dspace_mcp.pdf`, zaktualizuj go na nowy kształt (`units_processed`/`units_total`, `unit`) i nowy import — to część tego refaktora.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "refactor: pakiet extractors/ + uogolniony ksztalt wyniku (unit/units_*)"
```
(+ standardowe trailery)

---

### Task 2: Rejestr i `dispatch()`, rewire `get_bitstream_text`

**Files:**
- Modify: `src/dspace_mcp/extractors/__init__.py`
- Modify: `src/dspace_mcp/tools.py:get_bitstream_text`
- Modify: `tests/test_extractors.py`, `tests/test_tools.py`

**Interfaces:**
- Consumes: `extract_pdf` (Task 1).
- Produces:
  - `dispatch(data: bytes, *, mimetype: str | None, filename: str | None, max_chars: int) -> dict` — zwraca wynik ekstraktora z doklejonym `"format": <label>`; raises `ExtractError` gdy brak ekstraktora.
  - moduł-poziomowe rejestry `_BY_MIMETYPE`, `_BY_EXTENSION` (rozszerzane w kolejnych taskach).

- [ ] **Step 1: Napisz testy dispatchu (czerwone)**

Append to `tests/test_extractors.py`:

```python
from dspace_mcp.extractors import dispatch


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
```

- [ ] **Step 2: Uruchom — verify fail**

Run: `uv run pytest tests/test_extractors.py -k dispatch -v`
Expected: FAIL — `cannot import name 'dispatch'`.

- [ ] **Step 3: Zaimplementuj `dispatch` w `__init__.py`**

Replace `src/dspace_mcp/extractors/__init__.py` with:

```python
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
        raise ExtractError(
            f"No text extractor for {mimetype or 'this file type'}."
        )
    func, label = entry
    result = func(data, max_chars=max_chars)
    result["format"] = label
    return result
```

- [ ] **Step 4: Uruchom testy dispatchu — verify pass**

Run: `uv run pytest tests/test_extractors.py -k dispatch -v`
Expected: PASS (4 testy).

- [ ] **Step 5: Przełącz `get_bitstream_text` na `dispatch`**

In `src/dspace_mcp/tools.py`, change the import:

```python
from .extractors import ExtractError, extract_pdf
```

to:

```python
from .extractors import ExtractError, dispatch
```

Replace the whole body of `get_bitstream_text` with (usuwa twardy warunek „pdf in mimetype" — o nieobsługiwanym typie decyduje teraz `dispatch`):

```python
async def get_bitstream_text(
    client: DSpaceClient, bitstream: str, max_chars: int = 20000
) -> dict[str, Any]:
    """Tekst z pliku. Rozmiar i typ bierzemy z metadanych, ale limit egzekwuje
    strumień — `sizeBytes` bywa niezgodne z rzeczywistością. O tym, który
    ekstraktor zadziała, decyduje `extractors.dispatch` po mimetypie."""
    if max_chars <= 0:
        raise DSpaceError("max_chars must be greater than zero.")

    uuid = require_uuid(bitstream, "bitstream")
    raw = await client.get(f"/core/bitstreams/{uuid}", {"embed": "format"})
    fmt = raw.get("_embedded", {}).get("format", {})
    mimetype = fmt.get("mimetype")
    name = raw.get("name")
    url = link_href(raw, "content")
    size = raw.get("sizeBytes")
    limit_mb = client.config.extract_max_mb

    if not url:
        raise DSpaceError("This bitstream has no downloadable content.")

    if size and size > client.config.extract_max_bytes:
        mb = size / (1024 * 1024)
        raise DSpaceError(
            f"This file is {mb:.1f} MB, above the {limit_mb} MB limit. "
            f"Give the user this link instead: {url}"
        )

    data = await client.stream_bytes(url, max_bytes=client.config.extract_max_bytes)
    try:
        extracted = dispatch(
            data, mimetype=mimetype, filename=name, max_chars=max_chars
        )
    except ExtractError as exc:
        raise DSpaceError(f"{exc} Link to the file: {url}") from exc

    return {
        "bitstream": uuid,
        "name": name,
        "mimetype": mimetype,
        "size_bytes": size,
        "download_url": url,
        **extracted,
    }
```

Note: `client.config.extract_max_mb`/`extract_max_bytes` powstają w Tasku 3. Do tego czasu istnieją jako aliasy? Nie — dlatego **Task 3 wykonaj przed uruchomieniem całego zestawu**, albo tymczasowo zostaw `pdf_max_mb`/`pdf_max_bytes` w tym kroku i podmień w Tasku 3. Zalecane: podmień na `extract_*` tutaj i od razu zrób Task 3 (obie zmiany dotyczą tej samej ścieżki).

- [ ] **Step 6: Zaktualizuj testy `test_tools.py` dla dispatchu**

In `tests/test_tools.py`, przy teście `get_bitstream_text` dla nie-PDF: dziś oczekuje komunikatu „not a PDF". Zmień asercję na komunikat dispatchu. Znajdź test mockujący bitstream z mimetypem nie-PDF i ustaw oczekiwanie:

```python
    assert "No text extractor" in result["error"]
```

Dodaj test fallbacku po rozszerzeniu (mimetype `application/octet-stream`, nazwa `report.pdf`) — mockując `/core/bitstreams/{uuid}` z `format.mimetype = "application/octet-stream"`, `name = "report.pdf"` i `content` wskazującym na mały PDF (użyj `_one_page_pdf` zaimportowanego z `tests.test_extractors` lub zduplikuj builder w conftest). Oczekiwanie: `result["format"] == "pdf"`.

- [ ] **Step 7: Uruchom całość + lint**

Run: `uv run pytest -q && uv run ruff check .`
Expected: PASS (po wykonaniu Tasku 3, jeśli podmieniłeś na `extract_*`).

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: rejestr ekstraktorow i dispatch po mimetypie z fallbackiem na rozszerzenie"
```

---

### Task 3: Konfiguracja — `extract_max_mb` z aliasem wstecznym

**Files:**
- Modify: `src/dspace_mcp/config.py`
- Modify: `tests/test_config.py`

**Interfaces:**
- Produces (na `Config`):
  - pole `extract_max_mb: int` (kanoniczne).
  - property `extract_max_bytes: int`.
  - property `pdf_max_mb: int` i `pdf_max_bytes: int` (aliasy wsteczne, zwracają wartości `extract_*`).
  - env `DSPACE_EXTRACT_MAX_MB` (kanoniczne) z aliasem `DSPACE_PDF_MAX_MB`.
  - flaga `--extract-max-mb` z aliasem `--pdf-max-mb`.

- [ ] **Step 1: Napisz testy konfiguracji (czerwone)**

Append to `tests/test_config.py`:

```python
def test_extract_max_mb_from_new_env():
    cfg = config_from_env(
        {"DSPACE_BASE_URL": "https://x/server", "DSPACE_EXTRACT_MAX_MB": "5"}
    )
    assert cfg.extract_max_mb == 5
    assert cfg.extract_max_bytes == 5 * 1024 * 1024


def test_pdf_max_mb_env_is_backward_compatible_alias():
    cfg = config_from_env(
        {"DSPACE_BASE_URL": "https://x/server", "DSPACE_PDF_MAX_MB": "7"}
    )
    assert cfg.extract_max_mb == 7
    # aliasy nadal odczytywalne
    assert cfg.pdf_max_mb == 7
    assert cfg.pdf_max_bytes == 7 * 1024 * 1024


def test_new_env_wins_over_alias():
    cfg = config_from_env(
        {
            "DSPACE_BASE_URL": "https://x/server",
            "DSPACE_EXTRACT_MAX_MB": "5",
            "DSPACE_PDF_MAX_MB": "7",
        }
    )
    assert cfg.extract_max_mb == 5


def test_extract_max_mb_cli_flag():
    cfg = parse_args(["--base-url", "https://x/server", "--extract-max-mb", "9"])
    assert cfg.extract_max_mb == 9


def test_pdf_max_mb_cli_flag_still_works():
    cfg = parse_args(["--base-url", "https://x/server", "--pdf-max-mb", "3"])
    assert cfg.extract_max_mb == 3
```

- [ ] **Step 2: Uruchom — verify fail**

Run: `uv run pytest tests/test_config.py -k "extract or alias or new_env" -v`
Expected: FAIL — `Config` nie ma `extract_max_mb`.

- [ ] **Step 3: Zmień `config.py`**

In `src/dspace_mcp/config.py`:

Dodaj stałe obok istniejących:

```python
ENV_EXTRACT_MAX_MB = "DSPACE_EXTRACT_MAX_MB"
DEFAULT_EXTRACT_MAX_MB = 20
```

Zamień pole `pdf_max_mb` w dataclass `Config` na `extract_max_mb` i dołóż aliasy (zachowaj kolejność pól — `frozen=True`):

```python
    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    max_results: int = DEFAULT_MAX_RESULTS
    extract_max_mb: int = DEFAULT_EXTRACT_MAX_MB
    username: str | None = None
    password: str | None = None
    enable_write: bool = False

    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api"

    @property
    def extract_max_bytes(self) -> int:
        """Limit ekstrakcji w bajtach (strumień liczymy w bajtach)."""
        return self.extract_max_mb * 1024 * 1024

    # Aliasy wsteczne: do 0.1.x limit nazywał się „pdf". Zostają, bo obce
    # konfiguracje MCP mogą ich używać, a to już nie tylko PDF.
    @property
    def pdf_max_mb(self) -> int:
        return self.extract_max_mb

    @property
    def pdf_max_bytes(self) -> int:
        return self.extract_max_bytes
```

Usuń stare stałe `ENV_PDF_MAX_MB = "DSPACE_PDF_MAX_MB"` **nie usuwaj** — zostaje jako alias. W `config_from_env` rozwiąż z pierwszeństwem nowej nazwy:

```python
    extract_mb = _number_from_env(
        env,
        ENV_EXTRACT_MAX_MB,
        converter=int,
        kind="integer",
        default=_number_from_env(
            env,
            ENV_PDF_MAX_MB,
            converter=int,
            kind="integer",
            default=DEFAULT_EXTRACT_MAX_MB,
        ),
    )
```

i przekaż `extract_max_mb=extract_mb` do konstruktora `Config` (usuwając dawny argument `pdf_max_mb=...`).

W `_build_parser` dodaj flagę kanoniczną i przemianuj pomoc; **zachowaj** `--pdf-max-mb` jako alias tej samej wartości docelowej. Najprościej: obie flagi zapisują do tego samego `dest`:

```python
    parser.add_argument(
        "--extract-max-mb",
        "--pdf-max-mb",
        dest="extract_max_mb",
        metavar="MB",
        type=int,
        default=None,
        help=(
            f"Refuse to download bitstreams larger than this for text extraction "
            f"(default: {DEFAULT_EXTRACT_MAX_MB}, or ${ENV_EXTRACT_MAX_MB}; "
            f"alias: --pdf-max-mb / ${ENV_PDF_MAX_MB})."
        ),
    )
```

W `parse_args` podmień rozwiązywanie: użyj `args.extract_max_mb` i env z pierwszeństwem nowej nazwy:

```python
        extract_max_mb=_resolve_number(
            parser,
            args.extract_max_mb,
            "--extract-max-mb",
            env,
            ENV_EXTRACT_MAX_MB,
            converter=int,
            kind="integer",
            default=(
                config_from_env_number_alias(env)  # patrz niżej
            ),
        ),
```

Aby nie komplikować `_resolve_number`, zastąp powyższe prostszym: policz domyślną z aliasu ręcznie przed wywołaniem:

```python
    alias_default = _number_from_env(
        env, ENV_PDF_MAX_MB, converter=int, kind="integer",
        default=DEFAULT_EXTRACT_MAX_MB,
    )
```

i przekaż `default=alias_default` do `_resolve_number(...)` dla `extract_max_mb`. Usuń dawny blok `pdf_max_mb=_resolve_number(...)`.

- [ ] **Step 4: Uruchom testy config — verify pass**

Run: `uv run pytest tests/test_config.py -v`
Expected: PASS. Jeśli istniejące testy odwoływały się do `pdf_max_mb` jako pola/argumentu konstruktora, zaktualizuj je na `extract_max_mb` (property `pdf_max_mb` nadal działa do odczytu).

- [ ] **Step 5: Uruchom całość + lint + format**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: limit extract_max_mb z aliasem wstecznym pdf_max_mb"
```

---

### Task 4: OOXML — docx, pptx, xlsx

**Files:**
- Create: `src/dspace_mcp/extractors/ooxml.py`
- Create: `tests/office_samples.py`
- Modify: `src/dspace_mcp/extractors/__init__.py` (rejestracja)
- Modify: `tests/test_extractors.py`

**Interfaces:**
- Consumes: `base.open_zip`, `base.parse_xml`, `base.localname`, `base.assemble`, `base.ExtractError`.
- Produces:
  - `extract_docx(data, *, max_chars=20000) -> dict` (unit=`paragraphs`)
  - `extract_pptx(data, *, max_chars=20000) -> dict` (unit=`slides`)
  - `extract_xlsx(data, *, max_chars=20000) -> dict` (unit=`sheets`)
  - buildery w `tests/office_samples.py`: `docx_bytes(paragraphs)`, `pptx_bytes(slides)`, `xlsx_bytes(sheets)`, plus `_zip(members)`.

- [ ] **Step 1: Utwórz buildery testowe**

Create `tests/office_samples.py`:

```python
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
    body = "".join(
        f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs
    )
    doc = (
        f'<?xml version="1.0"?>'
        f'<w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    )
    return _zip({"word/document.xml": doc})


def pptx_bytes(slides: list[list[str]]) -> bytes:
    members: dict[str, str] = {}
    for i, texts in enumerate(slides, 1):
        runs = "".join(
            f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts
        )
        members[f"ppt/slides/slide{i}.xml"] = (
            f'<p:sld xmlns:p="{_P}" xmlns:a="{_A}">'
            f"<a:txBody>{runs}</a:txBody></p:sld>"
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
```

- [ ] **Step 2: Napisz testy OOXML (czerwone)**

Append to `tests/test_extractors.py`:

```python
from tests.office_samples import docx_bytes, pptx_bytes, xlsx_bytes
from dspace_mcp.extractors.ooxml import extract_docx, extract_pptx, extract_xlsx


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


def test_extract_xlsx_flattens_cells_with_shared_strings():
    data = xlsx_bytes([[["Name", "City"], ["Ada", "London"]]])
    result = extract_xlsx(data, max_chars=1000)
    assert "Name" in result["text"] and "London" in result["text"]
    assert result["unit"] == "sheets"
    assert result["units_total"] == 1
    assert "\t" in result["text"]  # kolumny rozdzielone tabem
```

- [ ] **Step 3: Uruchom — verify fail**

Run: `uv run pytest tests/test_extractors.py -k "docx or pptx or xlsx" -v`
Expected: FAIL — `No module named 'dspace_mcp.extractors.ooxml'`.

- [ ] **Step 4: Zaimplementuj `ooxml.py`**

Create `src/dspace_mcp/extractors/ooxml.py`:

```python
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


def _slide_number(name: str) -> int:
    """Numer slajdu z ``ppt/slides/slide12.xml`` → 12 (do sortowania)."""
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
    """pptx: tekst slajdów po kolei (``a:t`` w ``ppt/slides/slideN.xml``)."""
    zf = open_zip(data, _PPTX)
    try:
        slide_names = sorted(
            (
                n
                for n in zf.namelist()
                if n.startswith("ppt/slides/slide") and n.endswith(".xml")
            ),
            key=_slide_number,
        )
        if not slide_names:
            raise ExtractError(f"This file is not a readable {_PPTX}.")
        slides = [
            _slide_text(parse_xml(read_member(zf, n, _PPTX), _PPTX))
            for n in slide_names
        ]
    finally:
        zf.close()

    return assemble(
        iter(slides),
        total=len(slides),
        unit="slides",
        max_chars=max_chars,
        empty_message=f"This {_PPTX} contains no extractable text.",
    )


def extract_xlsx(data: bytes, *, max_chars: int = 20000) -> dict:
    """xlsx: arkusze spłaszczone do wierszy (tab między kolumnami)."""
    zf = open_zip(data, _XLSX)
    try:
        shared = _shared_strings(zf)
        sheet_names = sorted(
            n
            for n in zf.namelist()
            if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")
        )
        if not sheet_names:
            raise ExtractError(f"This file is not a readable {_XLSX}.")
        sheets = [
            _sheet_text(parse_xml(read_member(zf, n, _XLSX), _XLSX), shared)
            for n in sheet_names
        ]
    finally:
        zf.close()

    return assemble(
        iter(sheets),
        total=len(sheets),
        unit="sheets",
        max_chars=max_chars,
        empty_message=f"This {_XLSX} contains no extractable text.",
    )


def _runs_text(paragraph) -> str:
    """Sklej tekst wszystkich runów (``t``) akapitu."""
    return "".join(
        e.text or "" for e in paragraph.iter() if localname(e.tag) == "t"
    )


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
        cells = [
            _cell_value(c, shared)
            for c in row
            if localname(c.tag) == "c"
        ]
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
        return "".join(
            t.text or "" for t in cell.iter() if localname(t.tag) == "t"
        )
    return value.text if value is not None and value.text else ""
```

- [ ] **Step 5: Zarejestruj formaty w `__init__.py`**

In `src/dspace_mcp/extractors/__init__.py`, add import and extend registries:

```python
from .ooxml import extract_docx, extract_pptx, extract_xlsx
```

Rozszerz `_BY_MIMETYPE`:

```python
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": (
        extract_docx,
        "docx",
    ),
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": (
        extract_pptx,
        "pptx",
    ),
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": (
        extract_xlsx,
        "xlsx",
    ),
```

Rozszerz `_BY_EXTENSION`:

```python
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
```

- [ ] **Step 6: Uruchom testy OOXML — verify pass**

Run: `uv run pytest tests/test_extractors.py -k "docx or pptx or xlsx" -v`
Expected: PASS (6 testów).

- [ ] **Step 7: Uruchom całość + lint + format**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: ekstraktory OOXML (docx, pptx, xlsx) na stdlib zip+xml"
```

---

### Task 5: ODF — odt, ods, odp

**Files:**
- Create: `src/dspace_mcp/extractors/opendocument.py`
- Modify: `src/dspace_mcp/extractors/__init__.py` (rejestracja)
- Modify: `tests/test_extractors.py`

**Interfaces:**
- Consumes: `base.open_zip`, `base.parse_xml`, `base.localname`, `base.assemble`, `base.ExtractError`; buildery `odt_bytes`, `ods_bytes`, `odp_bytes` z `tests/office_samples.py` (Task 4).
- Produces:
  - `extract_odt(data, *, max_chars=20000) -> dict` (unit=`paragraphs`)
  - `extract_ods(data, *, max_chars=20000) -> dict` (unit=`sheets`)
  - `extract_odp(data, *, max_chars=20000) -> dict` (unit=`slides`)

- [ ] **Step 1: Napisz testy ODF (czerwone)**

Append to `tests/test_extractors.py`:

```python
from tests.office_samples import odt_bytes, ods_bytes, odp_bytes
from dspace_mcp.extractors.opendocument import (
    extract_odp,
    extract_ods,
    extract_odt,
)


def test_extract_odt_joins_paragraphs():
    result = extract_odt(odt_bytes(["Alpha line", "Beta line"]), max_chars=1000)
    assert "Alpha line" in result["text"] and "Beta line" in result["text"]
    assert result["unit"] == "paragraphs"
    assert result["units_total"] == 2


def test_extract_ods_flattens_cells():
    result = extract_ods(ods_bytes([[["Ada", "London"], ["Bob", "Paris"]]]), 1000)
    assert "Ada" in result["text"] and "Paris" in result["text"]
    assert result["unit"] == "sheets"
    assert result["units_total"] == 1


def test_extract_odp_reads_pages():
    result = extract_odp(odp_bytes([["First slide"], ["Second slide"]]), 1000)
    assert result["unit"] == "slides"
    assert result["units_total"] == 2
    assert "First slide" in result["text"]


def test_extract_odt_bad_zip_raises():
    with pytest.raises(ExtractError):
        extract_odt(b"not a zip", max_chars=100)
```

- [ ] **Step 2: Uruchom — verify fail**

Run: `uv run pytest tests/test_extractors.py -k "odt or ods or odp" -v`
Expected: FAIL — `No module named 'dspace_mcp.extractors.opendocument'`.

- [ ] **Step 3: Zaimplementuj `opendocument.py`**

Create `src/dspace_mcp/extractors/opendocument.py`:

```python
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
        cells = [
            "".join(c.itertext())
            for c in row
            if localname(c.tag) == "table-cell"
        ]
        rows.append("\t".join(cells))
    return "\n".join(rows)
```

- [ ] **Step 4: Zarejestruj formaty w `__init__.py`**

In `src/dspace_mcp/extractors/__init__.py`, add import:

```python
from .opendocument import extract_odp, extract_ods, extract_odt
```

Rozszerz `_BY_MIMETYPE`:

```python
    "application/vnd.oasis.opendocument.text": (extract_odt, "odt"),
    "application/vnd.oasis.opendocument.spreadsheet": (extract_ods, "ods"),
    "application/vnd.oasis.opendocument.presentation": (extract_odp, "odp"),
```

Rozszerz `_BY_EXTENSION`:

```python
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "odp": "application/vnd.oasis.opendocument.presentation",
```

- [ ] **Step 5: Uruchom testy ODF — verify pass**

Run: `uv run pytest tests/test_extractors.py -k "odt or ods or odp" -v`
Expected: PASS (4 testy).

- [ ] **Step 6: Uruchom całość + lint + format**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat: ekstraktory ODF (odt, ods, odp) na stdlib zip+xml"
```

---

### Task 6: Legacy `.doc` przez `olefile`

Best-effort ekstrakcja tekstu ze strumienia `WordDocument` starego binarnego formatu. Świadomie prosta i stratna — gdy nie ma czytelnego tekstu, `ExtractError` z linkiem, nigdy pusty string.

**Files:**
- Modify: `pyproject.toml` (dodaj `olefile>=0.47`)
- Create: `src/dspace_mcp/extractors/msword.py`
- Modify: `src/dspace_mcp/extractors/__init__.py` (rejestracja)
- Modify: `tests/test_extractors.py`

**Interfaces:**
- Produces:
  - `extract_doc(data, *, max_chars=20000) -> dict` (unit=`None`, units=`None`)
  - `msword._scrape_text(raw: bytes) -> str` (czysta, testowalna osobno)

- [ ] **Step 1: Dodaj zależność**

In `pyproject.toml`, w `dependencies` dodaj po `pypdf>=4.0`:

```python
    "olefile>=0.47",
```

Run: `uv sync`
Expected: `olefile` zainstalowane, `uv.lock` zaktualizowany.

- [ ] **Step 2: Napisz testy (czerwone) — najpierw czysty `_scrape_text`**

Append to `tests/test_extractors.py`:

```python
from dspace_mcp.extractors.msword import _scrape_text, extract_doc


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
```

- [ ] **Step 3: Uruchom — verify fail**

Run: `uv run pytest tests/test_extractors.py -k "scrape or extract_doc" -v`
Expected: FAIL — `No module named 'dspace_mcp.extractors.msword'`.

- [ ] **Step 4: Zaimplementuj `msword.py`**

Create `src/dspace_mcp/extractors/msword.py`:

```python
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
_NO_TEXT = (
    "This Word document has no extractable text; it may be a scan or empty."
)

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
```

- [ ] **Step 5: Zarejestruj format w `__init__.py`**

In `src/dspace_mcp/extractors/__init__.py`, add import:

```python
from .msword import extract_doc
```

Rozszerz `_BY_MIMETYPE`:

```python
    "application/msword": (extract_doc, "doc"),
```

Rozszerz `_BY_EXTENSION`:

```python
    "doc": "application/msword",
```

- [ ] **Step 6: Uruchom testy .doc — verify pass**

Run: `uv run pytest tests/test_extractors.py -k "scrape or extract_doc" -v`
Expected: PASS (3 testy).

- [ ] **Step 7: Uruchom całość + lint + format**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: best-effort ekstrakcja legacy .doc przez olefile"
```

---

### Task 7: Read-only test, docstring narzędzia, CLAUDE.md

Domyka inwariant „tylko GET" o nowe formaty, aktualizuje opis narzędzia widziany przez model i dokumentację projektu.

**Files:**
- Modify: `tests/test_client.py:test_client_only_ever_sends_get`
- Modify: `src/dspace_mcp/server.py:get_bitstream_text` (docstring narzędzia)
- Modify: `CLAUDE.md`

**Interfaces:**
- Consumes: `dispatch` i wszystkie ekstraktory (Taski 1–6).

- [ ] **Step 1: Rozszerz test „tylko GET" o pobranie i ekstrakcję nie-PDF**

In `tests/test_client.py::test_client_only_ever_sends_get`, po istniejących wywołaniach dodaj pobranie treści docx przez ten sam klient (ekstrakcja to czysta funkcja, więc wystarczy udowodnić, że pobieranie bajtów dowolnego formatu też jest GET). Dodaj mock i wywołanie:

```python
    docx_url = f"{API}/core/bitstreams/{VALID_UUID}/content"
    respx.get(docx_url).mock(
        return_value=httpx.Response(200, content=b"PK\x03\x04docx-bytes")
    )
    await client.stream_bytes(docx_url, max_bytes=ONE_MB)
```

Asercja `{"GET"}` na końcu testu już obejmuje nowe wywołanie — potwierdź, że nadal przechodzi.

- [ ] **Step 2: Uruchom test read-only — verify pass**

Run: `uv run pytest tests/test_client.py::test_client_only_ever_sends_get -v`
Expected: PASS.

- [ ] **Step 3: Zaktualizuj docstring narzędzia `get_bitstream_text`**

In `src/dspace_mcp/server.py`, zamień docstring funkcji `get_bitstream_text` na (to czyta model, wybierając narzędzie):

```python
    """Extract the text of a document so you can read or summarise it.

    Supports PDF, Word (.docx, legacy .doc), OpenDocument (.odt, .ods, .odp)
    and Office Open XML (.pptx, .xlsx). The result reports which `format` was
    read and, where meaningful, how many `units` (pages, slides or sheets)
    were processed. Scans without OCR, encrypted files, unsupported types and
    oversized files come back as a clear error with a download link.

    Args:
        bitstream: UUID of the bitstream (get it from list_bitstreams).
        max_chars: stop after this many characters.
    """
```

- [ ] **Step 4: Zaktualizuj `CLAUDE.md`**

In `CLAUDE.md`, w opisie architektury zamień odwołania do `pdf.py` na `extractors/`. Zamień punkt o `pdf.py`:

```markdown
- **`config.py`** — frozen `Config` dataclass, built from env vars or CLI flags (flag >
  env > default). `pdf.py` — pure PDF-bytes → text extraction, raises `PdfError`.
```

na:

```markdown
- **`config.py`** — frozen `Config` dataclass, built from env vars or CLI flags (flag >
  env > default). **`extractors/`** — a package of pure `bytes → text` extractors
  (`pdf`, `ooxml` for docx/pptx/xlsx, `opendocument` for odt/ods/odp, `msword` for
  legacy `.doc`) behind a mimetype→extractor `dispatch()`; all raise `ExtractError`.
  Non-stdlib deps here are only `olefile` (legacy `.doc`) and `defusedxml`
  (safe XML parsing of untrusted files); the ZIP+XML formats use `zipfile` +
  `defusedxml.ElementTree`.
```

W sekcji „Conventions" zamień wzmiankę `PdfError` w zdaniu o dwóch typach błędów na `ExtractError`:

```markdown
- **Two error types cross the boundary to the model**: `DSpaceError` and `ExtractError`.
```

- [ ] **Step 5: Uruchom pełny zestaw + lint + format**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: PASS (cały zestaw, wszystkie formaty).

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs: read-only test i opis narzedzia o wielu formatach; CLAUDE.md"
```

---

## Self-Review

**Spec coverage:**
- E1 (bez extras) → cały plan: zależności w `dependencies`, brak `optional-dependencies`. ✓
- E2 (stdlib zip+xml, olefile tylko dla .doc) → Task 4, 5 (stdlib zip; XML przez defusedxml — wymóg bezpieczeństwa), Task 6 (olefile). ✓
- E3 (kształt `unit`+`units_*`) → Task 1 `base.assemble`, PDF; potwierdzone testami we wszystkich taskach. ✓
- E4 (`extract_max_mb` + alias) → Task 3. ✓
- E5 (read-only bez zmian, test rozszerzony) → Task 7 Step 1. ✓
- Formaty pdf/docx/doc/odt/ods/odp/pptx/xlsx → Taski 1, 4, 5, 6. ✓
- Dispatch po mimetypie + fallback po rozszerzeniu → Task 2. ✓
- Docstring narzędzia + CLAUDE.md → Task 7. ✓
- Fixture'y/testy per format → `tests/office_samples.py` + `tests/test_extractors.py` w Taskach 4–6. Uwaga: zamiast plików z żywej instancji użyto minimalnych, poprawnych kontenerów budowanych deterministycznie — świadome odstępstwo od litery specu (sekcja Testy), zachowujące jej cel (realny ZIP+XML z prawdziwymi namespace'ami) bez commitowania binariów, których plan nie może wygenerować inline. Zaszyfrowany docx pominięty (nie da się zbudować minimalnie) — pokryte ścieżki: zły ZIP, brak części, pusty tekst.

**Placeholder scan:** brak „TBD/TODO"; każdy krok kodowy ma pełny kod; komendy z oczekiwanym wynikiem. ✓

**Type consistency:** `extract_<fmt>(data, *, max_chars) -> dict` jednolite; `dispatch(data, *, mimetype, filename, max_chars)` spójne w Tasku 2 i 7; `base.assemble(...)` z tym samym zestawem kluczy wyniku wszędzie; `ExtractError` jeden typ w całym pakiecie; `Config.extract_max_mb`/`extract_max_bytes` używane w `tools.py` (Task 2) zgodnie z definicją (Task 3). ✓

Uwaga wykonawcza: **Task 2 i Task 3 dotykają tej samej ścieżki `get_bitstream_text`/config** — wykonaj je parami (Task 2 zostawia `extract_*`, Task 3 je definiuje), a pełny `pytest` uruchamiaj dopiero po Tasku 3.
