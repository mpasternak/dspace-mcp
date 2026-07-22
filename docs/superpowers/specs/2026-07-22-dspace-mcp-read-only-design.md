# dspace-mcp — serwer MCP tylko do odczytu dla DSpace 7–10

Data: 2026-07-22
Status: projekt zaakceptowany, przed planem implementacji

## Cel

Serwer MCP, dzięki któremu model językowy może rozmawiać z dowolną instancją
repozytorium DSpace w wersji 7–10: wyszukiwać rekordy, czytać metadane,
przeglądać strukturę społeczności i kolekcji, oglądać listę plików i wyciągać
tekst z PDF-ów.

Projekt jest generyczny — pakiet open source dla dowolnego użytkownika DSpace,
publikowany na GitHub i PyPI. Nie zawiera wiedzy o żadnym konkretnym systemie
zewnętrznym ani o żadnym konkretnym schemacie metadanych.

## Zakres

### W zakresie

- Wyłącznie odczyt (`GET`).
- Wyłącznie dostęp anonimowy — bez logowania, bez JWT, bez CSRF.
- Jedna instancja DSpace na proces serwera, wskazana w konfiguracji.
- Osiem narzędzi MCP (tabela niżej).
- Ekstrakcja tekstu z bitstreamów PDF, z twardymi limitami.

### Poza zakresem

- Jakiekolwiek operacje modyfikujące (deponowanie, edycja metadanych,
  wgrywanie plików).
- Uwierzytelnianie użytkowników i konta techniczne.
- OCR skanów.
- Statystyki wykorzystania (uzasadnienie w sekcji „Decyzje projektowe”).
- Obsługa wielu instancji DSpace w jednym procesie.

## Decyzje projektowe

### D1. Tylko dostęp anonimowy

Serwer nie posiada żadnych poświadczeń, więc jest **strukturalnie niezdolny**
do odczytania danych niepublicznych i do jakiejkolwiek modyfikacji. To
gwarancja wynikająca z konstrukcji, a nie z zachowania modelu — jedyny rodzaj,
który da się obronić przed działem IT właściciela repozytorium.

Konsekwencja: niewidoczne pozostają rekordy pod embargiem, w procesie
zatwierdzania (workflow) oraz kolekcje z ograniczonym dostępem. To jest
akceptowane.

Konsekwencja techniczna: token CSRF DSpace jest wymagany wyłącznie dla metod
mutujących i dla `/authn/login`, więc w tym projekcie nie występuje w ogóle.

### D2. Jedna instancja na proces

`DSPACE_BASE_URL` w konfiguracji, bez parametru `base_url` w narzędziach.
Eliminuje ryzyko SSRF i pomyłki modelu co do tego, które repozytorium jest
odpytywane. Obsługa wielu repozytoriów = wiele wpisów w konfiguracji klienta
MCP.

### D3. Warstwa MCP skraca, ale nie interpretuje

Odpowiedzi DSpace są spłaszczane do zwięzłych struktur (rekord w wynikach
wyszukiwania: ~200 B zamiast ~2–4 kB surowego HAL-a), ale nie są tłumaczone na
żaden inny model danych:

- autorzy zwracani są jako oryginalne stringi (`"Kowalski, Jan"`), bez
  rozbijania na imię i nazwisko — heurystyka „przecinek albo ostatnia spacja”
  myli się na nazwiskach wieloczłonowych i na nazwach instytucji wpisanych w
  pole autora;
- `type` zwracany jest surowo (`"Article"`, `"Rozprawa doktorska"`), bez
  mapowania na jakikolwiek zewnętrzny słownik typów.

Skracanie jest odwracalne — `get_item(full_metadata=True)` zwraca komplet
znormalizowanych pól metadanych. Interpretacja odwracalna nie jest, dlatego jej
tu nie ma. Logika domenowa (dopasowanie autorów, mapowanie typów) należy do
systemu konsumującego, który ma kontekst czyniący zgadywanie sensownym.

### D4. Ekonomia tokenów jako główne kryterium projektowe

Każde narzędzie, które może zwrócić dużo danych, zwraca też `total` (liczbę
wszystkich pasujących obiektów) i `truncated` (czy odpowiedź obcięto). Bez tego
model po cichu wyciąga wnioski z pierwszych 25 rekordów, sądząc, że widzi
całość.

`search_items` z `limit=0` zwraca **wyłącznie licznik** — odpowiedź na pytanie
„ile jest rekordów spełniających X” kosztuje jedno żądanie i ~30 tokenów.

### D5. Brak `get_statistics`

Endpoint `/api/statistics/usagereports/` w domyślnej konfiguracji DSpace 7+
jest dostępny wyłącznie dla administratorów, więc przy dostępie anonimowym
zwróci `403` na większości instancji. Narzędzie zawodne systemowo jest gorsze
niż jego brak: uczy model, że narzędzia tego serwera bywają zepsute, a to
zachowanie przenosi się na pozostałe. Do rozważenia ponownie, jeśli pojawi się
zapotrzebowanie przy repozytorium z publicznymi statystykami.

### D6. Opisy narzędzi po angielsku

Bez warstwy i18n. Modele poprawnie dobierają narzędzia z angielskim opisem przy
zapytaniu w innym języku, a dwa równoległe komplety opisów to dwa miejsca do
rozjechania się.

### D7. Przygotowanie pod ewentualny zapis, bez pisania go dzisiaj

Zapis w DSpace 7+ to osobna powierzchnia API (`/api/submission/workspaceitems`,
seria operacji JSON-Patch, `/api/workflow/workflowitems`) plus JWT i CSRF —
realnie drugi projekt tej samej wielkości. Nie realizujemy go, ale cztery
decyzje kształtu zdejmują przymus przepisywania, gdyby kiedyś był potrzebny:

1. Wszystkie żądania przechodzą przez jedną prywatną metodę `_get()` klasy
   `DSpaceClient`; dołożenie `_mutate()` nie dotyka narzędzi.
2. `httpx.AsyncClient` żyje w lifespanie serwera, więc cookie jar (wymagany
   przez rotujący token CSRF) ma gdzie mieszkać.
3. `Config` ma od początku pola `username`, `password`, `enable_write`
   (domyślnie `None`/`False`) — format konfiguracji się nie zmieni.
4. `server.py` rejestruje narzędzia warunkowo, więc gwarancja z D1 przetrwa
   dołożenie kodu zapisu: trzeba jawnie włączyć flagę **i** podać konto.

Świadomie **nie** wprowadzamy abstrakcyjnych interfejsów, warstwy „repository”
ani hierarchii `ReadOnlyClient`/`WriteClient`. Kosztowałyby dziś pliki, testy i
obciążenie poznawcze, a i tak zostałyby źle zgadnięte, bo API zapisu wygląda
zupełnie inaczej niż API odczytu.

## Architektura

```
dspace-mcp/
├── pyproject.toml          # uv + hatchling; deps: mcp[cli], httpx, pypdf
├── README.md               # angielski
├── LICENSE                 # MIT
├── .pre-commit-config.yaml # ruff
├── .github/workflows/ci.yml
├── src/dspace_mcp/
│   ├── config.py           # Config (dataclass) + parsowanie env i CLI
│   ├── client.py           # DSpaceClient: _get(), paginacja, błędy, wersja
│   ├── shaping.py          # HAL/DC → płaskie struktury (czyste funkcje)
│   ├── tools.py            # logika narzędzi, bez zależności od MCP
│   ├── pdf.py              # ekstrakcja tekstu z bitstreamu
│   └── server.py           # FastMCP: lifespan + rejestracja narzędzi
└── tests/
    ├── fixtures/           # realne odpowiedzi z DSpace 7.x i 8.x/9.x
    └── test_*.py
```

Granice odpowiedzialności:

- `shaping.py` — czyste funkcje, zero I/O. Najtańsze i najgęstsze testy w
  projekcie.
- `tools.py` — logika każdego narzędzia, przyjmuje `DSpaceClient`, nie wie nic
  o MCP. Testowalna bez uruchamiania serwera.
- `server.py` — wyłącznie adapter MCP: lifespan, rejestracja, przekazanie
  klienta. Bez logiki.

### Konfiguracja

| Zmienna | Domyślnie | Rola |
|---|---|---|
| `DSPACE_BASE_URL` | — (wymagane) | np. `https://demo.dspace.org/server` |
| `DSPACE_TIMEOUT` | `15` | sekundy na żądanie HTTP |
| `DSPACE_MAX_RESULTS` | `50` | twardy sufit na `limit` we wszystkich narzędziach |
| `DSPACE_PDF_MAX_MB` | `20` | próg odmowy pobrania pliku do ekstrakcji |

Każda nadpisywalna flagą CLI (`--base-url`, `--timeout`, …), żeby wpis w
konfiguracji klienta MCP dało się napisać bez zmiennych środowiskowych.

`base_url` przyjmowany jest z `/server` i bez — przy `404` na sondzie startowej
serwer dopisuje `/server` i próbuje ponownie. To najczęstsza pomyłka
konfiguracyjna i najtańsza do wybaczenia.

### Warstwa klienta

`DSpaceClient` odpowiada za cztery rzeczy, których nie powtarzamy w narzędziach:

**Paginacja HAL.** DSpace zwraca `{_embedded, page: {size, number, totalPages,
totalElements}, _links}`. Klient udostępnia `_get_page()` (jedna strona +
`total`) oraz `_get_all()` (podąża za `_links.next` z twardym sufitem
`MAX_RESULTS`, zwraca flagę `truncated`). Nigdy nie iterujemy bez ograniczenia.

**Normalizacja `_embedded`.** DSpace 7 i 8+ różnią się tym, co osadzają
domyślnie, a `?embed=` bywa ignorowany po przekroczeniu
`rest.embed.maxEmbedDepth`. Klient sprawdza, czy dostał to, o co prosił, i w
razie potrzeby dobija osobnym żądaniem, zamiast wysypywać się na `KeyError`.

**Mapowanie błędów na komunikaty dla modelu.** `404` → „nie ma takiego rekordu
(sprawdź UUID/handle)”; `401`/`403` → „rekord nie jest publicznie dostępny —
ten serwer działa anonimowo”; `ConnectError` → „repozytorium nieosiągalne pod
{base_url}”; `TimeoutException` → sugestia zawężenia zapytania. Model, który
dostaje wyjątek biblioteki HTTP, ponawia w kółko; model, który dostaje zdanie
w języku naturalnym, przestaje.

**Wykrycie wersji przy starcie.** `GET /api` zwraca `dspaceVersion`;
cache'ujemy w lifespanie i używamy do warunkowego zachowania oraz jako sondy
poprawności konfiguracji.

## Narzędzia

| Narzędzie | Endpoint | Kluczowe parametry |
|---|---|---|
| `search_items` | `/api/discover/search/objects` | `query`, `scope`, `year_from`, `year_to`, `author`, `type`, `sort`, `limit`, `offset` |
| `get_item` | `/api/core/items/{uuid}` lub `/api/pid/find` | `id` (UUID, handle albo DOI), `full_metadata` |
| `list_communities` | `/api/core/communities/search/top`, `/{uuid}/subcommunities` | `parent`, `depth` |
| `list_collections` | `/api/core/collections`, `/communities/{uuid}/collections` | `community` |
| `list_bitstreams` | `/items/{uuid}/bundles` → `/bundles/{uuid}/bitstreams` | `item`, `bundle` (domyślnie `ORIGINAL`) |
| `get_bitstream_text` | `/api/core/bitstreams/{uuid}/content` | `bitstream`, `max_pages`, `max_chars` |
| `list_facet_values` | `/api/discover/facets/{name}` | `facet`, `scope`, `query` |
| `get_repository_info` | `/api` + liczniki | — |

`get_item` rozgałęzia się po kształcie przekazanego `id` (UUID / handle / DOI),
bo model rzadko wie, który identyfikator trzyma w ręce; to pięć linii kodu,
które usuwają całą klasę nieudanych wywołań.

`list_facet_values` odpowiada na pytania typu „jakie typy prac są w kolekcji X”
lub „którzy autorzy publikują najwięcej”. Bez faset model musiałby ściągnąć
wszystkie rekordy i policzyć je sam — tysiące rekordów w kontekście zamiast
dwudziestu par `(wartość, licznik)`. DSpace liczy to po stronie Solr.

`get_repository_info` daje modelowi orientację (nazwa, wersja, liczba rekordów
i kolekcji) i służy jako sprawdzian dostępności przy diagnozie.

### Kształt odpowiedzi

Rekord w wynikach wyszukiwania (kompaktowy):

```json
{
  "uuid": "0f4a…",
  "handle": "123456789/4271",
  "url": "https://repo.example.org/handle/123456789/4271",
  "title": "…",
  "authors": ["Kowalski, Jan", "Nowak, Anna"],
  "year": 2025,
  "date_issued": "2025-03",
  "type": "Article",
  "doi": "10.1234/abcd",
  "collection": "Artykuły naukowe",
  "files": 2
}
```

`get_item` zwraca to samo plus `abstract`, `subjects`, `language`, `publisher`,
`ispartof`, `rights`, `sponsorship`; przy `full_metadata=True` dodatkowo pełny
znormalizowany słownik pól metadanych.

Odpowiedzi listowe mają kopertę: `{"total": 1234, "truncated": false,
"results": [...]}`.

`shaping.py` normalizuje oba formaty metadanych DSpace — słownik kluczy DC z
listami obiektów (`{key: [{value, language, authority, confidence, place}]}`)
oraz płaską listę `{key, value}`.

`year` wyprowadzamy z `dc.date.issued`, ale zwracamy też surowe `date_issued`,
bo wartości bywają w postaci `2025`, `2025-03` lub `2025-03-17`.

### Ekstrakcja tekstu z PDF

Przed pobraniem: `HEAD` na bitstream, sprawdzenie `Content-Length` względem
`DSPACE_PDF_MAX_MB` i `Content-Type`.

- Plik za duży → komunikat z rozmiarem i URL-em, żeby model podał link zamiast
  próbować dalej.
- Nie-PDF → komunikat z typem MIME i URL-em.
- PDF bez warstwy tekstowej (`pypdf` zwraca same puste stringi) → jawny
  komunikat „prawdopodobnie skan, OCR poza zakresem tego serwera”. Zwrócenie
  pustego stringa byłoby najgorszą opcją: model uznałby, że dokument jest
  pusty.

Tekst obcinany do `max_chars` z flagą `truncated` i podaniem liczby
przetworzonych stron.

## Testy

`pytest` + `respx`, `asyncio_mode = "auto"`.

- `tests/fixtures/` — realne odpowiedzi zrzucone z żywych instancji, po jednym
  komplecie na wersję (DSpace 7.x i 8.x/9.x). To jedyny sposób, żeby złapać
  różnice w `_embedded` przed wdrożeniem, a nie po.
- Testy per narzędzie: wynik poprawny, wynik pusty, `404`, `403`, timeout,
  obcięcie po `MAX_RESULTS` (asercja na `truncated: true`).
- Testy `shaping.py` osobno, na obu wariantach formatu metadanych.
- Testy `pdf.py`: mały PDF z warstwą tekstową, PDF bez warstwy tekstowej, plik
  przekraczający limit, plik nie-PDF.
- `@pytest.mark.live` — testy kontraktowe przeciwko `demo.dspace.org`,
  wyłączone domyślnie, uruchamiane ręcznie i z crona. Wyłapią zmianę API w
  nowej wersji DSpace, nie wywracając CI, gdy instancja demo leży.
- GitHub Actions: matryca Python 3.10–3.13, `ruff` + `pytest`.

## Kryteria ukończenia

1. `uvx dspace-mcp --base-url https://demo.dspace.org/server` startuje i
   odpowiada na `get_repository_info`.
2. Wszystkie osiem narzędzi działa przeciwko instancji demo (testy `live`).
3. Testy jednostkowe przechodzą na fixture'ach z DSpace 7.x i 8.x/9.x.
4. Serwer nie wykonuje żadnego żądania HTTP metodą inną niż `GET` i `HEAD` —
   zweryfikowane testem.
5. README z instrukcją konfiguracji dla klienta MCP i tabelą narzędzi.
