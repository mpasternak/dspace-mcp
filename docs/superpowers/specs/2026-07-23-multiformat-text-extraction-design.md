# dspace-mcp — ekstrakcja tekstu z wielu formatów plików

Data: 2026-07-23
Status: projekt zaakceptowany, przed planem implementacji
Poprzednik: `2026-07-22-dspace-mcp-read-only-design.md` (decyzje D1–D8)

## Cel

Rozszerzyć `get_bitstream_text` z „tylko PDF" na najpopularniejsze formaty
dokumentów, tak by model mógł czytać i streszczać nie tylko PDF-y, ale też
pliki Word, OpenDocument, prezentacje i arkusze pobrane z repozytorium DSpace.

## Zakres

### W zakresie

Osiem formatów, po mimetypie (z fallbackiem na rozszerzenie nazwy pliku):

| Format | Rozszerzenia | Ekstraktor | Nowa zależność |
|---|---|---|---|
| PDF | `.pdf` | pypdf (istnieje) | — |
| Word OOXML | `.docx` | własny, `zipfile`+`xml.etree` | — |
| Word legacy | `.doc` | olefile, best-effort | **olefile (~424 KB)** |
| OpenDocument tekst | `.odt` | własny, `zipfile`+`xml.etree` | — |
| OpenDocument arkusz | `.ods` | własny, `zipfile`+`xml.etree` | — |
| OpenDocument prezentacja | `.odp` | własny, `zipfile`+`xml.etree` | — |
| PowerPoint OOXML | `.pptx` | własny, `zipfile`+`xml.etree` | — |
| Excel OOXML | `.xlsx` | własny, `zipfile`+`xml.etree` | — |

### Poza zakresem

- OCR skanów (bez zmian względem poprzedniej decyzji).
- RTF, HTML, czysty tekst — świadomie pominięte w tej wersji.
- Ciężkie biblioteki (`python-docx` 22 MB, `python-pptx` 35 MB) — ciągną `lxml`
  i `Pillow`, niepotrzebne do samego tekstu.
- Extras / opcjonalne zależności — patrz decyzja E1.

## Decyzje projektowe

### E1. Bez extras — wszystko w bazie

Zmierzone rozmiary (izolowany `uv pip install --target`, z zależnościami
tranzytywnymi): wszystkie lekkie ekstraktory razem to ~2,7 MB, a przy wariancie
stdlib+olefile realnie ~424 KB nowej zależności. Wobec 72 MB obecnego `.venv`
dzielenie tego na extras (`[pdf]`, `[msword]`, …) to biurokracja bez zysku:
komplikuje `pyproject.toml`, dokłada w kodzie gałąź „brak biblioteki → doinstaluj
extra" i pogarsza UX (`uvx dspace-mcp` przestałby czytać cokolwiek bez extrasa).

Decyzja: ekstraktory są zwykłymi zależnościami. `uvx dspace-mcp` czyta wszystkie
osiem formatów od razu.

### E2. Stdlib dla rodziny ZIP+XML, biblioteka tylko gdy nie ma wyboru

docx, pptx, xlsx (OOXML) oraz odt, ods, odp (ODF) to kontenery ZIP zawierające
XML. Tekst wyciągamy z nich `zipfile` + `xml.etree.ElementTree` ze standardowej
biblioteki — zero zależności, jeden spójny zestaw błędów, pełna kontrola nad
komunikatem dla modelu. To pasuje do DNA projektu: `extractors/pdf.py` już jest
czystą funkcją na bajtach testowaną na surowych fixture'ach.

Stary binarny `.doc` (OLE Compound File) nie ma opcji stdlib, więc dla niego —
i tylko dla niego — sięgamy po `olefile` i robimy **best-effort** ekstrakcję
tekstu ze strumienia `WordDocument`. Best-effort znaczy: gdy się uda, zwracamy
tekst; gdy nie — `ExtractError` z linkiem do pliku, nigdy pusty string ani
wyjątek techniczny.

### E3. Kształt odpowiedzi uogólniony na jednostki

Dzisiejszy PDF zwraca `pages_processed`/`pages_total`. Uogólniamy na jednostki
wspólne dla formatów: `unit` ∈ {`pages`, `slides`, `sheets`, `paragraphs`,
`null`} plus `units_processed`/`units_total`. To drobna zmiana kształtu wyjścia
narzędzia; akceptowalna, bo projekt jest w 0.1.x/Beta, a model czyta JSON
dynamicznie.

### E4. Limit rozmiaru przemianowany, z aliasem wstecznym

`pdf_max_mb` → `extract_max_mb` (nazwa neutralna, bo to już nie tylko PDF).
Zgodność wsteczna: `DSPACE_PDF_MAX_MB` i `--pdf-max-mb` pozostają działającymi
aliasami. Nowe: `DSPACE_EXTRACT_MAX_MB` i `--extract-max-mb`. Gdy podane oba,
wygrywa nowa nazwa; alias emituje ostrzeżenie na stderr o przestarzałości.

### E5. Read-only bez zmian

Ekstrakcja działa na bajtach pobranych przez GET-only `client.stream_bytes`.
Inwariant „proces wysyła wyłącznie GET" (D1) trzyma się bez dotykania warstwy
sieci. Test „tylko GET" zostaje rozszerzony o wywołania nowych formatów.

### E6. Bezpieczne parsowanie niezaufanych plików

Pliki pochodzą z dowolnych repozytoriów wskazanych przez użytkownika — to
niezaufane wejście. Goły `xml.etree` jest podatny na ataki „billion laughs"
(wykładnicza ekspansja encji) i XXE, a rozpakowywanie ZIP — na „zip bomby".
Dlatego:

- XML rodziny ZIP+XML parsujemy przez **`defusedxml`** (mała, czysto-pythonowa
  paczka, `defusedxml>=0.7`), nie przez goły `xml.etree`. Dokument z bombą
  encji kończy się `ExtractError`, nie DoS-em procesu.
- Każdą część archiwum czytamy przez helper `read_member`, który sprawdza
  **rozpakowany** rozmiar (`ZipInfo.file_size`) wobec twardego sufitu i odmawia
  ponad limit.

To dokłada jedną zależność (`defusedxml`) ponad pierwotne założenie „tylko
`olefile`". Uzasadnione modelem zagrożeń: koszt ~50 KB, korzyść to odporność na
złośliwy plik.

## Architektura

Obecny `dspace_mcp/pdf.py` zostaje zastąpiony pakietem:

```
dspace_mcp/extractors/
  __init__.py      # ExtractError, rejestr mimetype→ekstraktor, dispatch()
  pdf.py           # extract_pdf   (przeniesiony obecny kod pypdf)
  ooxml.py         # extract_docx / extract_pptx / extract_xlsx (wspólny helper)
  opendocument.py  # extract_odt / extract_ods / extract_odp   (wspólny helper)
  msword.py        # extract_doc   (olefile, best-effort)
```

### Kontrakt ekstraktora

Każdy ekstraktor to **czysta funkcja**, bez I/O i bez sieci:

```python
def extract_<fmt>(data: bytes, *, max_chars: int = 20000) -> dict
```

Zwraca:

```python
{
    "text": str,              # znormalizowany, przycięty do max_chars
    "truncated": bool,        # tekst dłuższy niż max_chars LUB nie wszystkie jednostki przetworzone
    "unit": str | None,       # "pages" | "slides" | "sheets" | "paragraphs" | None
    "units_processed": int | None,
    "units_total": int | None,
}
```

Rzuca `ExtractError` (komunikat po angielsku dla modelu) przy: pliku nie w danym
formacie, zaszyfrowanym, uszkodzonym, oraz braku warstwy tekstowej / pustym
wyniku. `ExtractError` zastępuje dzisiejszy `PdfError`.

Reguła z `shaping.py` obowiązuje analogicznie: brakujący element XML czy
nietypowy kształt kontenera to normalne wejście z cudzej instancji — kończy się
`ExtractError` z sensownym komunikatem, nigdy przeciekiem stack trace do modelu.

### Rejestr i dispatch

`extractors/__init__.py` trzyma mapę mimetype → `(funkcja, format_label)` oraz
mapę rozszerzenie → mimetype (fallback). Funkcja:

```python
def dispatch(data: bytes, *, mimetype: str | None,
             filename: str | None, max_chars: int) -> dict
```

1. Normalizuje mimetype (obcina parametry `; charset=…`, lowercase).
2. Wybiera ekstraktor po mimetypie; gdy mimetype pusty lub ogólny
   (`application/octet-stream`) — po rozszerzeniu z `filename`.
3. Brak dopasowania → `ExtractError`: „No text extractor for {mimetype}."
   (link dokłada `tools.py`, który zna URL).
4. Wywołuje ekstraktor i dokłada `"format": format_label` do wyniku.

Cała wiedza „który format" siedzi w pakiecie `extractors/`, nie w `tools.py`.

### Zmiany poza `extractors/`

- `server.py::_guard` — łapie `ExtractError` zamiast `PdfError`.
- `tools.py::get_bitstream_text` — chudnie: pobiera metadane bitstreamu jak dziś
  (`embed=format`: mimetype, `name`, `sizeBytes`, link `content`), woła
  `dispatch(...)`, dokleja link przy `ExtractError`. Znika twarde sprawdzenie
  „pdf in mimetype".
- `config.py` — `extract_max_mb` z aliasem `pdf_max_mb` (E4); `pdf_max_bytes`
  property staje się `extract_max_bytes` (alias zachowany, jeśli używany).
- Docstring narzędzia `get_bitstream_text` (czyta go model) — lista obsługiwanych
  formatów zamiast „tylko PDF".
- `pyproject.toml` — dochodzą `olefile>=0.47` i `defusedxml>=0.7` do `dependencies` (E6).

## Szczegóły ekstrakcji per rodzina

### OOXML (docx, pptx, xlsx) — `ooxml.py`

Wspólny helper otwiera ZIP, iteruje po właściwych częściach, zbiera tekst z
elementów tekstowych danego namespace, **przerywa po osiągnięciu `max_chars`**
(jak pypdf czyta strony po kolei):

- **docx**: `word/document.xml`; tekst z `<w:t>`, akapit `<w:p>` → nowa linia.
  Jednostka: `paragraphs`.
- **pptx**: `ppt/slides/slide{N}.xml` po kolei; tekst z `<a:t>`. Jednostka:
  `slides`. `units_total` = liczba plików slajdów.
- **xlsx**: `xl/sharedStrings.xml` (tabela stringów) + `xl/worksheets/sheet{N}.xml`;
  komórki `<c>` z `t="s"` odsyłają do shared strings po indeksie, inne mają
  `<v>` inline. Wartości arkusza spłaszczamy do wierszy (tab między kolumnami,
  nowa linia między wierszami). Jednostka: `sheets`.

Namespace'y bierzemy z faktycznych tagów (parsujemy z prefiksem `{ns}local`),
nie zakładamy stałego prefiksu — różne generatory piszą różnie.

### ODF (odt, ods, odp) — `opendocument.py`

Wszystkie trzy trzymają treść w `content.xml`. Tekst z elementów
`text:p`/`text:span`/`text:h` (namespace ODF `text:`). Dla `ods` komórki
`table:table-cell` → wiersze jak w xlsx. Jednostki: `paragraphs` (odt),
`sheets` (ods, po `table:table`), `slides` (odp, po `draw:page`).

### Legacy .doc — `msword.py`

`olefile` otwiera kontener OLE; czytamy strumień `WordDocument`, wyłuskujemy
tekst best-effort (odfiltrowanie bajtów sterujących, dekodowanie cp1252/utf-16
z tolerancją błędów). Gdy nie ma czytelnego tekstu → `ExtractError` (skan/plik
binarny bez warstwy tekstowej). `unit`/`units_*` = `null` (brak naturalnej
jednostki). Uszkodzony/nieolejowy plik → `ExtractError`.

### PDF — `pdf.py`

Przeniesiony obecny kod. Zmiana tylko w kształcie wyniku: `pages_processed`/
`pages_total` → `unit="pages"` + `units_processed`/`units_total`. `PdfError`
→ `ExtractError` (jeden typ dla całego pakietu).

## Obsługa błędów

Wszystkie komunikaty po angielsku (odbiorcą jest model). `dispatch` i ekstraktory
rzucają `ExtractError`; `tools.py` dokłada link do pliku i przepuszcza jako
`{"error": …}` przez `_guard`. Klasy porażek:

- Nieobsługiwany typ → „No text extractor for {mimetype}; here's the link: {url}".
- Zły/uszkodzony plik danego formatu → „This file is not a readable {fmt}."
- Zaszyfrowany (docx/pdf) → „This {fmt} is password-protected…".
- Brak warstwy tekstowej (skan PDF, .doc bez tekstu) → jak dziś dla PDF.
- Za duży plik → limit `extract_max_bytes`, link zamiast treści (bez zmian).

## Testy

- **Fixture'y** (`tests/fixtures/`): po jednym małym, prawdziwym pliku każdego
  formatu (docx, doc, odt, ods, odp, pptx, xlsx) + brzegowe: zaszyfrowany docx,
  xlsx z shared strings i wieloma arkuszami, uszkodzony ZIP, .doc będący skanem.
  Trzymane jak reszta — nietknięte, surowe.
- **`tests/test_extractors.py`**: czyste testy każdej funkcji na bajtach,
  parametryzowane po formatach; przycinanie do `max_chars`, `truncated`,
  liczby jednostek, ścieżki błędów.
- **`tests/test_tools.py`**: dispatch po mimetypie, fallback po rozszerzeniu,
  format bez ekstraktora, limit rozmiaru, doklejanie linku do błędu.
- **`tests/test_config.py`**: `extract_max_mb`, alias `pdf_max_mb`, pierwszeństwo
  nowej nazwy, ostrzeżenie o przestarzałości.
- **`tests/test_client.py`**: istniejący `test_client_only_ever_sends_get`
  rozszerzony o pobranie i ekstrakcję nowych formatów — asercja `{"GET"}` musi
  obejmować też je (E5).

## Migracja / zgodność

- Wyjście `get_bitstream_text`: `pages_*` → `unit`+`units_*` (E3) — drobna,
  akceptowalna zmiana w 0.1.x.
- Konfiguracja: `--pdf-max-mb` / `DSPACE_PDF_MAX_MB` dalej działają jako aliasy
  (E4).
- Import: `dspace_mcp.pdf` znika na rzecz `dspace_mcp.extractors`; to API
  wewnętrzne, bez publicznych konsumentów.
- `CLAUDE.md` — wzmianki o `pdf.py` zaktualizować do `extractors/`.
