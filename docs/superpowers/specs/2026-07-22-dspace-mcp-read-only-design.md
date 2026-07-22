# dspace-mcp — serwer MCP tylko do odczytu dla DSpace 7+

Data: 2026-07-22
Wersja: 2 (po adwersaryjnym review i weryfikacji na żywych instancjach)
Status: projekt zaakceptowany, przed planem implementacji

## Cel

Serwer MCP, dzięki któremu model językowy może rozmawiać z dowolną instancją
repozytorium DSpace w wersji 7 lub nowszej: wyszukiwać rekordy, czytać
metadane, przeglądać strukturę społeczności i kolekcji, oglądać listę plików,
wyciągać tekst z PDF-ów i sprawdzać statystyki wykorzystania.

Projekt jest generyczny — pakiet open source dla dowolnego użytkownika DSpace,
publikowany na GitHub i PyPI. Nie zawiera wiedzy o żadnym konkretnym systemie
zewnętrznym ani o żadnym konkretnym schemacie metadanych.

Wszystkie twierdzenia o API w tym dokumencie zostały zweryfikowane wobec
`DSpace/RestContract` oraz empirycznie wobec żywych instancji w wersjach 7.2.1,
7.5, 7.6.5, 8.2, 8.4, 9.2, 10.1 i 11.0-SNAPSHOT (2026-07-22).

## Zakres

### W zakresie

- Wyłącznie odczyt (`GET`, plus `HEAD` jako fallback przy plikach).
- Wyłącznie dostęp anonimowy — bez logowania, bez JWT, bez CSRF.
- Jedna instancja DSpace na proces serwera, wskazana w konfiguracji.
- Dziewięć narzędzi MCP (tabela niżej).
- Ekstrakcja tekstu z bitstreamów PDF, z twardymi limitami.

### Poza zakresem

- Jakiekolwiek operacje modyfikujące (deponowanie, edycja metadanych,
  wgrywanie plików).
- Uwierzytelnianie użytkowników i konta techniczne.
- OCR skanów.
- Obsługa wielu instancji DSpace w jednym procesie.
- Legacy API DSpace 5/6 (`/rest`).
- Endpointy `/api/discover/browses` (indeksy przeglądania) — do rozważenia w
  kolejnej wersji.

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
mutujących i dla `/authn/login` (POST). Zweryfikowane empirycznie: GET bez
tokenu → 200, GET z celowo błędnym `X-XSRF-TOKEN` → 200, POST `/authn/login`
bez tokenu → 403. W tym projekcie CSRF nie występuje w ogóle.

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

Każde narzędzie, które może zwrócić dużo danych, zwraca `total` (liczbę
wszystkich pasujących obiektów) i `truncated` (czy odpowiedź obcięto). Bez tego
model po cichu wyciąga wnioski z pierwszych 25 rekordów, sądząc, że widzi
całość.

Wyjątek: `list_facet_values` zwraca wyłącznie `truncated`, ponieważ endpoint
faset nie podaje `totalElements` ani `totalPages` (zweryfikowane) — jedyną
drogą naprzód jest `_links.next`.

`search_items` z `limit=0` zwraca **wyłącznie licznik**. Implementacja wysyła
przy tym `size=1`, nie `size=0`: RestContract wymaga, by serwer odrzucał
`size=0` błędem 400 (instancja demo akurat je przyjmuje, ale to zachowanie
niekontraktowe, na które nie wolno projektować).

### D5. `get_item_statistics` jest w zakresie

Pierwsza wersja tego dokumentu wykluczała statystyki na podstawie fałszywej
przesłanki (rzekomej domyślnej ochrony administratorskiej). W rzeczywistości
domyślna konfiguracja we wszystkich gałęziach 7.x, 8.x i main to
`usage-statistics.authorization.admin.usage=false` — statystyki wyświetleń i
pobrań są **publiczne**. Zweryfikowane anonimowo na instancjach 7.6.5, 8.4, 9.2
i 10.1: wszędzie HTTP 200 z danymi. Admin-only są wyłącznie statystyki
wyszukiwań i workflow, których nie ruszamy.

Narzędzie musi mimo to poprawnie obsłużyć 401 (istnieje konfiguracja
zaostrzająca) — komunikatem „ta instancja nie udostępnia statystyk
anonimowo”, nie wyjątkiem.

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
ani hierarchii `ReadOnlyClient`/`WriteClient`.

### D8. Zdolności instancji wykrywamy, nie zakładamy

Zestaw filtrów wyszukiwania, faset i sortowań jest konfigurowalny per-instancja
(`discovery.xml`) i **różni się w praktyce**: waniliowy DSpace nie ma filtra po
typie dokumentu, a repozytorium MIT ma `itemtype`, `language`,
`dcDescriptionDegree` i inne. Użycie nieistniejącego filtra `f.<nazwa>` kończy
się błędem **422**.

Dlatego przy pierwszym użyciu (leniwie, wynik w cache lifespanu) pobieramy
`GET /api/discover/search`, który zwraca `filters[].filter` i
`sortOptions[].name`. Narzędzia:

- mapują parametry na filtry tylko wtedy, gdy dana instancja je ma;
- w przeciwnym razie zwracają komunikat „ta instancja nie udostępnia filtra po
  X; dostępne filtry: …”, zamiast pozwolić na 422;
- `get_repository_info` wymienia dostępne filtry, fasety i sortowania, dzięki
  czemu model wie, o co może pytać, zanim zapyta.

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
│   ├── client.py           # DSpaceClient: _get(), paginacja, błędy, sonda
│   ├── shaping.py          # HAL/DC → płaskie struktury (czyste funkcje)
│   ├── tools.py            # logika narzędzi, bez zależności od MCP
│   ├── pdf.py              # ekstrakcja tekstu z bitstreamu
│   └── server.py           # FastMCP: lifespan + rejestracja narzędzi
└── tests/
    ├── fixtures/           # realne odpowiedzi z DSpace 7.x, 8.x, 10.x, 11.x
    └── test_*.py
```

Granice odpowiedzialności:

- `shaping.py` — czyste funkcje, zero I/O. Najtańsze i najgęstsze testy.
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
serwer dopisuje `/server` i próbuje ponownie.

### Warstwa klienta

`DSpaceClient` odpowiada za rzeczy, których nie powtarzamy w narzędziach.

**Konfiguracja `httpx.AsyncClient`** — trzy ustawienia nie są opcjonalne:

- `follow_redirects=True` — bez tego `get_item` po handlu zwraca pustkę,
  ponieważ `/api/pid/find` odpowiada **302** z nagłówkiem `location`
  (zweryfikowane). Domyślna wartość w httpx to `False`.
- nagłówek `User-Agent: dspace-mcp/<wersja> (+https://github.com/mpasternak/dspace-mcp)`
  — serwer odpytuje cudze repozytoria w pętli sterowanej przez model; anonimowy
  ruch bez identyfikacji bywa powodem banów IP.
- **nigdy nie wysyłamy nagłówka `Origin`** — DSpace odrzuca wtedy nawet zwykłe
  GET-y błędem 403 (zweryfikowane empirycznie).

**Paginacja HAL.** Koperta: `{_embedded, page: {size, number, totalPages,
totalElements}, _links}`. Klient udostępnia `_get_page()` (jedna strona +
`total`) oraz `_get_all()` (podąża za `_links.next` z twardym sufitem
`MAX_RESULTS`, zwraca flagę `truncated`). Nigdy nie iterujemy bez ograniczenia.
Kolejność kluczy w `page` bywa różna między instancjami — parsujemy po nazwach,
nie po pozycji. Na pierwszej stronie `_links` bywa bez `first`/`prev`.

**Odczyt `_links`.** Pomocnik musi tolerować, że wartością relacji bywa
**lista**, nie obiekt (np. `workflowGroups` w kolekcji to lista trzech wpisów).
Naiwne `links[rel]["href"]` wywala się na kolekcjach.

**Walidacja UUID po naszej stronie.** Niepoprawny UUID w ścieżce zwraca z
DSpace **401 „Authentication is required”**, nie 400 — komunikat kompletnie
mylący dla modelu, który zacznie szukać sposobu na zalogowanie. Klient sprawdza
kształt UUID przed wysłaniem żądania.

**Rozpoznawanie typu obiektu** po polu `type`, nigdy po `uniqueType` — to
drugie występuje w 10.x/11.x i w DSpace-CRIS, ale nie w waniliowych 7.2.1,
7.5, 7.6.5, 8.4 ani 9.2.

**Mapowanie błędów na komunikaty dla modelu:**

| Sytuacja | Komunikat |
|---|---|
| `404` | „nie ma takiego obiektu (sprawdź UUID/handle)” |
| `401`/`403` | „obiekt nie jest publicznie dostępny — ten serwer działa anonimowo” |
| `422` | „ta instancja nie zna filtra X; dostępne filtry: …” |
| `501` (z `pid/find`) | „ta instancja nie rozwiązuje identyfikatorów tego typu” |
| `429`/`503` | „repozytorium ogranicza tempo zapytań — odczekaj” — **bez automatycznego ponawiania** |
| `ConnectError` | „repozytorium nieosiągalne pod {base_url}” |
| `TimeoutException` | sugestia zawężenia zapytania |

Treści `message` z błędów DSpace **nie przekazujemy** — Spring Boot zwraca tam
bezużyteczne „An exception has occurred”.

**Sonda startowa.** `GET /api` zwraca `dspaceName`, `dspaceUI` i
`dspaceVersion` w postaci opisowego stringa (`"DSpace 7.6.5"`,
`"DSpace 10.1-SNAPSHOT"`) — parsujemy regexem do majora i minora. Rola sondy:
walidacja konfiguracji (w tym dopisanie `/server`) oraz dane do
`get_repository_info`. Nie uzależniamy od wersji żadnego zachowania — zamiast
tego wykrywamy zdolności instancji (D8).

## Narzędzia

| Narzędzie | Endpoint | Kluczowe parametry |
|---|---|---|
| `search_items` | `/api/discover/search/objects` | `query`, `scope`, `year_from`, `year_to`, `author`, `sort`, `limit`, `offset` |
| `get_item` | `/api/core/items/{uuid}` lub `/api/pid/find` | `id` (UUID, handle albo DOI), `full_metadata` |
| `list_communities` | `/api/core/communities/search/top`, `/{uuid}/subcommunities` | `parent`, `depth` |
| `list_collections` | `/api/core/collections`, `/communities/{uuid}/collections` | `community` |
| `list_bitstreams` | `/items/{uuid}/bundles` → `/bundles/{uuid}/bitstreams` | `item`, `bundle` (domyślnie `ORIGINAL`) |
| `get_bitstream_text` | `/api/core/bitstreams/{uuid}/content` | `bitstream`, `max_chars` |
| `list_facet_values` | `/api/discover/facets/{name}` | `facet`, `scope`, `query`, `prefix`, `limit` |
| `get_item_statistics` | `/api/statistics/usagereports/{uuid}_TotalVisits` | `item` |
| `get_repository_info` | `/api` + `/api/discover/search` + liczniki | — |

### Szczegóły narzędzi

**`search_items`.** Mapowanie parametrów na API:

- `query` → `query`, `scope` → `scope` (UUID kolekcji lub społeczności),
  `dsoType=item` na stałe;
- `year_from`/`year_to` → `f.dateIssued=[YYYY TO YYYY],equals` (składnia
  zakresu Solr); brak jednej ze stron → `*`;
- `author` → `f.author=<wartość>,contains` — operator `contains`, nie `equals`,
  bo model rzadko zna dokładną formę zapisu nazwiska w repozytorium;
- `sort` → aliasy `relevance` / `newest` / `oldest` / `title`, mapowane na
  `score,DESC` / `dc.date.issued,DESC` / `dc.date.issued,ASC` / `dc.title,ASC`,
  z walidacją wobec `sortOptions` danej instancji (D8);
- `limit`/`offset` → `size`/`page`, z sufitem `MAX_RESULTS`;
- `?embed=owningCollection` — zweryfikowane, że działa na tym endpoincie i
  wypełnia pole `collection` w rekordzie kompaktowym bez zapytań N+1.

Nie ma parametru `type`: waniliowy DSpace nie definiuje filtra po typie
dokumentu. Jeśli instancja taki filtr ma (np. `itemtype`), `get_repository_info`
go pokaże, a model może użyć `list_facet_values`.

Odpowiedź wyszukiwania zawiera też fasety w `_embedded.facets` (top-level, nie
w `searchResult`) — odrzucamy je przy spłaszczaniu, ale warto wiedzieć, że
przychodzą w tym samym żądaniu.

**`get_item`.** Rozgałęzienie po kształcie `id`:

- UUID → `/api/core/items/{uuid}`;
- handle (`123456789/42` lub `hdl:…`) → `/api/pid/find?id=hdl:…` (302 →
  `follow_redirects`);
- DOI → najpierw `/api/pid/find?id=doi:…`, a przy 404/501 fallback na
  `search/objects?query="10.1234/abcd"`, bo na wielu instancjach DOI żyje
  wyłącznie w metadanych `dc.identifier.doi`.

**`list_communities`.** `depth` domyślnie `1`, twardy sufit `3`. Każdy poziom
to osobne żądanie na każdą społeczność poziomu wyżej, więc obowiązuje globalny
limit `MAX_RESULTS` na całe drzewo z flagą `truncated`.

**`list_facet_values`.** Parametry `prefix` (filtrowanie wartości po prefiksie,
bardzo przydatne przy dziesiątkach tysięcy autorów) i `limit`. Wartość fasety:
`{label, count, authorityKey}`. Bez `total` — patrz D4.

**`get_repository_info`.** Nazwa, URL, wersja, dostępne filtry/fasety/sorty
(D8) oraz liczniki. Liczniki **wyłącznie** przez
`discover/search/objects?size=1&dsoType=item|collection|community` →
`page.totalElements`. Endpoint `/api/core/items` jest zastrzeżony dla
administratorów i anonimowo zwraca 401 (zweryfikowane).

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
  "collection": "Artykuły naukowe"
}
```

`get_item` zwraca to samo plus `abstract`, `subjects`, `language`, `publisher`,
`ispartof`, `rights`, `sponsorship`, `files` (liczba bitstreamów w `ORIGINAL`);
przy `full_metadata=True` dodatkowo pełny słownik pól metadanych.

Odpowiedzi listowe mają kopertę: `{"total": 1234, "truncated": false,
"results": [...]}`.

**Format metadanych.** W REST API DSpace 7+ istnieje **jeden** format: mapa
kluczy DC z listami obiektów, `{"dc.title": [{"value": …, "language": …,
"authority": …, "confidence": …, "place": …}]}`. Płaska lista `{key, value}`
należy do zlikwidowanego API DSpace 5/6 i jest poza zakresem — nie normalizujemy
jej i nie testujemy.

**Rok wydania.** `date_issued` zwracamy surowo, `year` wyprowadzamy
defensywnie: pierwsza czterocyfrowa liczba w stringu, a gdy jej nie ma —
`null`. Wartości bywają kompletnie niesformatowane (spotkane w praktyce:
`"04/05/16"`), więc parser dat ISO by się wywrócił.

**`hitHighlights`** z wyszukiwania odrzucamy — zawierają znaczniki `<em>` i
encje HTML (`&#x2F;`), których model nie potrzebuje.

### Ekstrakcja tekstu z PDF

Kolejność sprawdzeń:

1. Metadane bitstreamu (`/api/core/bitstreams/{uuid}?embed=format`) dają
   `sizeBytes` oraz `format.mimetype`. Uwaga: bitstream **nie zawiera pola
   `mimetype`** bezpośrednio, a checksum nazywa się `checkSum` (wielkie S).
2. `sizeBytes` traktujemy jako wskazówkę, nie gwarancję — zaobserwowano
   rozjazd wobec faktycznego `Content-Length`. Dlatego pobieranie idzie
   **strumieniowo z twardym limitem bajtów**; po przekroczeniu przerywamy i
   zwracamy komunikat. `HEAD` zostaje jako tani fallback, gdy metadanych nie
   ma (przy `Transfer-Encoding: chunked` nagłówka `Content-Length` nie będzie).
3. Nie-PDF → komunikat z typem MIME i URL-em.
4. PDF zaszyfrowany (`pypdf` rzuca wyjątek) → komunikat „plik jest
   zabezpieczony hasłem”.
5. PDF bez warstwy tekstowej → jawny komunikat „prawdopodobnie skan, OCR poza
   zakresem tego serwera”. Zwrócenie pustego stringa byłoby najgorszą opcją:
   model uznałby, że dokument jest pusty.

`/content` bywa przekierowaniem do zewnętrznego magazynu (np. presigned URL do
S3) — stąd `follow_redirects=True` jest wymagane także tutaj.

Tekst obcinany do `max_chars`, w odpowiedzi `truncated` i liczba przetworzonych
stron. Nie ma osobnego `max_pages`: dwa nakładające się limity o niejasnym
pierwszeństwie to niepotrzebna komplikacja.

## Testy

`pytest` + `respx`, `asyncio_mode = "auto"`.

- `tests/fixtures/` — realne odpowiedzi zrzucone 2026-07-22 z żywych
  instancji: DSpace 7.6.5 (waikato), 8.x, 10.1 (demo) i 11.0-SNAPSHOT
  (sandbox), wraz z `README.md` opisującym pochodzenie. Pokrycie wersji jest
  **wyłącznie fixture'owe** — nie zakładamy dostępu do żywej instancji 7.x.
- Testy per narzędzie: wynik poprawny, wynik pusty, `404`, `401`, `422`,
  timeout, obcięcie po `MAX_RESULTS` (asercja na `truncated: true`).
- Testy `shaping.py` osobno — czyste funkcje, najgęstsze pokrycie.
- Testy `pdf.py`: PDF z warstwą tekstową, bez warstwy, zaszyfrowany,
  przekraczający limit (przerwanie strumienia), plik nie-PDF.
- Test architektoniczny: serwer nie wykonuje żadnego żądania metodą inną niż
  `GET`/`HEAD` (asercja na routerze `respx`).
- `@pytest.mark.live` — testy kontraktowe przeciwko `demo.dspace.org`,
  wyłączone domyślnie, uruchamiane ręcznie i z crona. **Nie mogą zależeć od
  konkretnych UUID-ów** — instancja demo jest cyklicznie resetowana, a jej
  wersja to ruchomy SNAPSHOT.
- GitHub Actions: matryca Python 3.10–3.13, `ruff` + `pytest`.

## Kryteria ukończenia

1. `uvx dspace-mcp --base-url https://demo.dspace.org/server` startuje i
   odpowiada na `get_repository_info`.
2. Wszystkie dziewięć narzędzi działa przeciwko instancji demo (testy `live`).
3. Testy jednostkowe przechodzą na fixture'ach z DSpace 7.x, 8.x, 10.x i 11.x.
4. Serwer nie wykonuje żadnego żądania metodą inną niż `GET` i `HEAD` —
   zweryfikowane testem.
5. README z instrukcją konfiguracji dla klienta MCP i tabelą narzędzi.

## Rejestr zmian względem wersji 1

Zmiany wynikły z adwersaryjnego review (model Fable) skonfrontowanego z
`RestContract` i plikami konfiguracyjnymi DSpace oraz z rekonesansu na ośmiu
żywych instancjach.

**Obalone twierdzenia wersji 1:**

1. „Statystyki są domyślnie admin-only” — fałsz, są publiczne. Narzędzie
   `get_item_statistics` wróciło do zakresu (D5).
2. „Istnieją dwa formaty metadanych do znormalizowania” — fałsz, płaska lista
   to legacy API DSpace 5/6. Usunięte razem z testami.
3. „Da się filtrować po `dc.type`” — fałsz, waniliowy DSpace nie ma takiego
   filtra, a nieznany filtr daje 422. Zastąpione wykrywaniem zdolności (D8).
4. „`rest.embed.maxEmbedDepth`” — taka właściwość nie istnieje; realne to
   `rest.projections.full.max` i `rest.projection.specificLevel.maxEmbed`.
   Cała wzmianka usunięta jako nieistotna dla zakresu.

**Uzupełnienia:** 302 z `pid/find` i wymóg `follow_redirects`; fallback DOI
przez wyszukiwanie; `size=1` zamiast `size=0`; mapowanie `year_from`/`author`
na składnię filtrów; `prefix` i `limit` dla faset; brak `total` dla faset;
`sizeBytes` + strumieniowy limit bajtów zamiast samego `HEAD`; PDF zaszyfrowany;
`User-Agent` i polityka 429/503 bez ponawiania; zakaz nagłówka `Origin`;
walidacja UUID po naszej stronie (401 zamiast 400); `type` zamiast `uniqueType`;
`_links` z wartością listową; liczniki przez `discover` (bo `/core/items` to
401); defensywne parsowanie roku; usunięty `max_pages`; `collection` przez
`embed=owningCollection` zamiast N+1.
