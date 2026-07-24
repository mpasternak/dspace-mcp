# dspace-mcp — opcjonalne uwierzytelnianie na potrzeby odczytu

Data: 2026-07-24
Status: projekt zaakceptowany, przed planem implementacji
Poprzednicy: `2026-07-22-dspace-mcp-read-only-design.md` (decyzje D1–D8),
`2026-07-23-multiformat-text-extraction-design.md` (decyzje E1–E6)

## Cel

Pozwolić użytkownikowi **opcjonalnie** podać login i hasło do repozytorium, żeby
serwer czytał także to, co widzi wyłącznie zalogowane konto: rekordy pod
embargiem, kolekcje z ograniczonym dostępem, pliki zamknięte dla anonimowego
gościa.

Wyłącznie odczyt. Nie powstaje żadne narzędzie zapisu ani żaden fragment kodu
zdolny zmodyfikować zawartość repozytorium.

## Zakres

### W zakresie

- Logowanie hasłem (`POST /api/authn/login`) na starcie procesu, gdy w
  konfiguracji podano konto.
- Doklejanie otrzymanego JWT do wszystkich żądań odczytu, łącznie z pobieraniem
  plików w `stream_bytes`.
- Odnawianie tokenu, zanim wygaśnie (decyzja A4).
- Zatrzymanie pracy i pytanie do użytkownika, gdy logowanie się nie powiedzie
  (decyzja A3).
- Raportowanie stanu uwierzytelnienia w `get_repository_info`.
- Narzędzie `compare_access`: porównanie widoczności rekordu i jego plików dla konta
  i dla anonimowej publiczności (decyzja A9).
- Nowe pola w `manifest.json` (paczka `.mcpb`), z hasłem oznaczonym jako
  `sensitive`.
- Aktualizacja tekstów, które dziś twierdzą, że serwer jest wyłącznie anonimowy:
  `README.md`, `CLAUDE.md`, opisy w `manifest.json` (`description`,
  `long_description`) oraz docstring modułu `client.py`.

### Poza zakresem

- **Jakikolwiek zapis.** Bez `/api/submission`, bez JSON-Patch, bez narzędzi
  mutujących. `Config.enable_write` pozostaje polem, którego nikt nie czyta.
- Metody logowania inne niż hasło: ORCID, Shibboleth, LDAP przez zewnętrzny IdP.
  Wszystkie wymagają przeglądarki i przekierowań OAuth/SAML, czego proces stdio
  nie obsłuży. Instancja bez logowania hasłem jest wykrywana i raportowana
  (decyzja A6), a nie obsługiwana.
- Wiele **kont** naraz. Jedno konto na proces, tak jak jedna instancja na proces
  (D2). Dwa **widoki** — konta i anonimowy — są natomiast w zakresie i opisuje je
  A9; to nie jest druga tożsamość, bo anonim nie wymaga żadnych poświadczeń.
- Trwałe przechowywanie tokenu między uruchomieniami. JWT żyje w pamięci
  procesu i ginie z nim.
- `POST /api/authn/logout`. Zwiększyłby powierzchnię metod mutujących o kolejny
  endpoint, żeby unieważnić token, który i tak wygasa sam.

## Ustalenia empiryczne

Zweryfikowane 2026-07-24 na `https://demo.dspace.org/server` (ta sama instancja,
którą biją testy `live`). Wszystkie liczby i nazwy nagłówków poniżej pochodzą z
rzeczywistych odpowiedzi, nie z dokumentacji.

| Obserwacja | Wynik |
|---|---|
| `GET /api/authn/status` | `200`, nagłówek `dspace-xsrf-token: <uuid>`, cookie `DSPACE-XSRF-COOKIE` z `Path=/server` |
| Nagłówek `www-authenticate` na `/authn/status` | `password realm="DSpace REST API", orcid realm="DSpace REST API", location="…"` — **lista metod obsługiwanych przez tę instancję** |
| `POST /api/authn/login` + `X-XSRF-TOKEN` + form `user`/`password` | `200`, nagłówek `authorization: Bearer <JWT>` |
| Token CSRF po zalogowaniu | **rotuje** — odpowiedź na login niesie nowy `dspace-xsrf-token` |
| Złe hasło | `401`, body `{"message":"Authentication failed!"}` |
| `POST /authn/login` bez tokenu CSRF | `403` (zgodne z tym, co D1 odnotował wcześniej) |
| Body `/authn/status` | `{"authenticated": bool, "authenticationMethod": "password"\|null, "_links": {"eperson": {…}}}` — obiekt `eperson` **nie jest osadzony**, jest tylko link |
| Payload JWT | `{"eid": "<uuid>", "sg": [], "exp": <unix>, "authenticationMethod": "password"}` |
| Czas życia tokenu | **~30 minut** (`exp` = logowanie + 1800 s, czyli domyślne `jwt.login.token.expiration`) |
| GET **publicznego** endpointu z nieważnym tokenem | **`200` i normalne dane anonimowe** — nie `401` |
| GET endpointu **wymagającego uprawnień** z nieważnym tokenem | `401`, identycznie jak anonimowo |
| Drugie i trzecie `GET /api/authn/status` w tej samej sesji | **brak** nagłówka `dspace-xsrf-token`; wartość żyje tylko w ciasteczku |
| `/authn/status` na **DSpace 9.1** (`repozytorium.wsb-nlu.edu.pl`) | **żadnego** tokenu CSRF: ani nagłówka, ani ciasteczka |
| POST logowania bez tokenu na tej instancji | `403` `"Access is denied. Invalid CSRF token."` — **i dopiero ta odpowiedź niesie token** |

Cztery z tych obserwacji obaliły założenia, na których opierałem wcześniejsze wersje
projektu:

1. Token CSRF **nie jest stały** — rotuje przy logowaniu, więc nie wolno go
   zapamiętać raz na zawsze.
2. Nazwy zalogowanego konta **nie da się** odczytać ze statusu bez dodatkowego
   żądania (patrz A5).
3. **Nieważny token nie powoduje błędu.** DSpace nie odrzuca żądania — po cichu
   traktuje je jak anonimowe i zwraca `200` z danymi publicznymi. To ustalenie
   przewraca całą pierwotną strategię odnawiania tokenu (patrz A4) i jest
   najważniejszym pomiarem w tym dokumencie.
4. **Nagłówek `DSPACE-XSRF-TOKEN` przychodzi tylko przy wystawianiu nowego
   tokenu.** Przy kolejnych żądaniach w tej samej sesji wartość jest wyłącznie w
   ciasteczku `DSPACE-XSRF-COOKIE` — klasyczny double-submit. Czytanie samego
   nagłówka wystarcza dokładnie raz, więc **drugie** logowanie (po wygaśnięciu
   tokenu, czyli po ~30 minutach pracy) poleciałoby bez `X-XSRF-TOKEN` i dostało
   `403`. Ciasteczko jest tu pełnoprawnym źródłem, a nie awaryjnym.

   To ustalenie kosztowało najwięcej: `curl` go **maskuje**, bo każde wywołanie w
   osobnym procesie startuje ze świeżym słoikiem ciasteczek i zawsze dostaje
   nagłówek. Ujawnił je dopiero test kontraktowy wobec żywej instancji, wykonany
   prawdziwym, długo żyjącym klientem HTTP. Wniosek na przyszłość: przy protokole
   zależnym od stanu sesji `curl` nie jest wiarygodnym modelem klienta.

5. **Nie każda instancja wydaje token CSRF, zanim o niego poprosisz.** Na DSpace
   9.1 `/authn/status` nie zwraca go w ogóle — ani w nagłówku, ani w ciasteczku —
   a token przychodzi dopiero razem z odmową `403` na POST logowania. Token jest
   tam generowany **leniwie**, przy pierwszym żądaniu, które faktycznie go
   potrzebuje.

   Konsekwencja dla A2: pojedyncze żądanie logowania nie wystarcza. Po `403`
   sprawdzamy, czy odpowiedź przyniosła token, i jeśli tak — ponawiamy **jeden**
   raz. Nie rozgałęziamy się przy tym po numerze wersji (D8): reagujemy na to, co
   instancja odpowiedziała, a nie na to, za jaką się podaje. Ta sama zasada, dla
   której filtry wyszukiwania wykrywamy, zamiast zakładać.

   Błąd wyszedł od użytkownika, nie z testów: na demo (10.1) ścieżka leniwa nie
   występuje, więc komplet testów offline i kontraktowych był zielony, a serwer
   i tak nie potrafił zalogować się do repozytorium uczelni.

Zastrzeżenie do punktu 3: mierzone tokenem o niepoprawnym **podpisie**, nie
tokenem wygasłym z podpisem poprawnym — sfabrykowanie tego drugiego wymagałoby
sekretu instancji, a poczekanie na naturalne wygaśnięcie ~30 minut sesji. W
implementacji DSpace obie sytuacje schodzą się w tej samej ścieżce („token nie
przechodzi weryfikacji → kontekst anonimowy"), a projekt przyjęty w A4 jest
odporny na wynik tego rozstrzygnięcia w obie strony, więc nie blokujemy się na
nim. Odnotowane jako znana granica wiedzy.

## Decyzje projektowe

### A1. Uwierzytelnianie poszerza zakres odczytu, nie zakres zapisu

Decyzja D1 („Tylko dostęp anonimowy") łączyła dwie różne gwarancje w jedno zdanie:
serwer nie widzi danych niepublicznych **i** nie potrafi niczego zmienić. Ta zmiana
rozdziela je i uchyla wyłącznie pierwszą:

- **Zakres odczytu** przestaje być stały. Domyślnie nadal anonimowy; po podaniu
  konta serwer widzi dokładnie to, co widzi to konto — nie więcej.
- **Niezdolność do modyfikacji zostaje strukturalna i bezwarunkowa.** Nie zależy
  od flagi, trybu ani od zachowania modelu: w kodzie nie istnieje ścieżka
  wysyłająca `PUT`, `PATCH`, `POST` (poza logowaniem) ani `DELETE`.

Konsekwencja dla właściciela repozytorium, którą trzeba umieć wypowiedzieć wprost:
konto podane w konfiguracji jest zwykłym kontem DSpace i podlega zwykłym
uprawnieniom. Podanie konta administratora daje modelowi wgląd we wszystko, co widzi
administrator. To wybór użytkownika, a nie coś, co serwer może za niego ograniczyć —
i dlatego README ma rekomendować konto o najmniejszych wystarczających uprawnieniach.

### A2. Dokładnie jeden nie-GET, z zaszytą ścieżką

Logowania nie da się zrobić GET-em, więc gwarancja „wyłącznie GET" musi zostać
świadomie i wąsko uchylona — inaczej niż przez ogólny klient z flagą trybu, gdzie
bezpieczeństwo sprowadza się do poprawności jednego `if`.

Kształt: prywatna metoda `DSpaceClient._login()` **bez parametru ścieżki**.
Adres powstaje wewnątrz jako `f"{self._api_url}/authn/login"`, więc żaden kod
wywołujący — ani dzisiejszy, ani przyszły, ani wygenerowany pod dyktando modelu —
nie ma jak skierować POST-a gdzie indziej. To jedyne miejsce w projekcie
wywołujące `self.http.post`.

Pole `self._api_url` jest wprawdzie **mutowalne**, ale nie otwiera to dziury:
nadpisuje je wyłącznie sonda startowa i wyłącznie na `{base_url}/server/api`
zbudowane z zamrożonej konfiguracji, lifespan woła sondę przed logowaniem, a żaden
argument narzędzia nigdy tam nie trafia. Zapisujemy to wprost, bo jest to element
uzasadnienia gwarancji, a nie przypadek.

**Logowanie nie podąża za przekierowaniami.** Klient jest zbudowany z
`follow_redirects=True` (bez tego `/pid/find` i pobieranie plików nie działają), a
`httpx` przy kodach **307/308 powtarza metodę razem z ciałem** — zweryfikowane w
źródle 0.28.1: `_redirect_method` degraduje POST do GET tylko dla 301/302/303, więc
307/308 przechodzą jako POST. Nagłówek `Authorization` jest przy zmianie origin
zdejmowany, ale pola formularza `user` i `password` **nie** — złośliwa albo przejęta
instancja mogłaby jednym przekierowaniem wyprowadzić hasło na obcy host (a
`normalize_base_url` dopuszcza jawne `http://`). Dlatego samo żądanie logowania
leci z `follow_redirects=False`, a przekierowanie w odpowiedzi na login jest
traktowane jako porażka logowania z własnym powodem.

Publiczne metody klienta (`get`, `get_page`, `get_all`, `stream_bytes`) nie zmieniają
sposobu wysyłania żądań: nadal GET.

Strażnik w testach asertuje **pełną równość** adresu (`url == f"{api_url}/authn/login"`),
nie `endswith` — inaczej `https://evil.example/authn/login` przeszedłby jako
poprawny cel, czyli test maskowałby dokładnie tę podatność, przed którą ma chronić.
Do tego test architektoniczny na źródłach: dokładnie jedno wystąpienie
`self.http.post`, zero `put`, `patch`, `delete` i `request`.

### A3. Nieudane logowanie zatrzymuje pracę i pyta użytkownika

Ciche zejście do trybu anonimowego jest najgorszym z możliwych zachowań: model
dostaje wtedy „Not found" albo „Not publicly available" na rekordy, które istnieją i
do których użytkownik ma dostęp, i w dobrej wierze melduje, że rekordów nie ma. Samo
ostrzeżenie na `stderr` też nie wystarcza — trafia do logu, którego nikt nie czyta.

Skoro proces stdio nie ma własnego kanału do użytkownika, pytanie idzie **przez
model**. Stan uwierzytelnienia to automat o czterech stanach:

| Stan | Kiedy | Zachowanie narzędzi |
|---|---|---|
| `ANONYMOUS` | nie podano konta | jak dziś, bez zmian |
| `AUTHENTICATED` | logowanie się powiodło | żądania niosą `Authorization` |
| `NEEDS_DECISION(powód)` | podano konto, logowanie padło | **zablokowane** |
| `ANONYMOUS_BY_CHOICE` | użytkownik świadomie zrezygnował | jak `ANONYMOUS` |

W stanie `NEEDS_DECISION` każde narzędzie odczytu wraca natychmiast, **bez żadnego
ruchu sieciowego**, stałą odpowiedzią:

```json
{"needs_user_decision": true,
 "error": "Login as <user> at <base-url> failed: <powód>. Ask the user how to
           proceed: either correct the username and password in this server's
           configuration and restart it, or — if they agree to work with public
           data only — call continue_anonymously."}
```

Bramka mieszka w istniejącym `_guard` w `server.py`, jedynym miejscu, przez które i
tak przechodzi każde narzędzie, i jedynym, które już dziś odpowiada za zamianę
sytuacji wyjątkowej w zdanie dla modelu. Wymaga to wyłuskania `ctx` z argumentów
narzędzia — dziś `_guard` klienta nie zna, bo `ctx` rozpakowuje dopiero ciało
narzędzia.

Bramka nie wystarcza sama, bo sprawdza stan **przed** wejściem do narzędzia, a do
`NEEDS_DECISION` można wpaść w środku już przepuszczonego wywołania (ponowne
logowanie z A4 padło). Żeby oba tory dawały modelowi **identyczną** odpowiedź,
klient sygnalizuje to osobnym wyjątkiem `NeedsDecision(reason)`, a `_guard` mapuje go
na tę samą stałą strukturę co bramka. Bez tego to samo zdarzenie wyglądałoby raz jak
pytanie, a raz jak zwykły błąd 401 — zależnie od tego, czy trafiło przed, czy po
wejściu do narzędzia.

Odblokowuje ją narzędzie `continue_anonymously`, rejestrowane **tylko wtedy, gdy w
konfiguracji podano konto** — instalacja anonimowa ma dokładnie dzisiejszy komplet
dziewięciu narzędzi, bez nowego pojęcia do zrozumienia. Jego opis dla modelu mówi
wprost, że wolno je wywołać wyłącznie po tym, jak użytkownik świadomie się zgodził.
Przejście działa tylko w jedną stronę i tylko z `NEEDS_DECISION`; wywołane w innym
stanie niczego nie zmienia i raportuje stan bieżący. Decyzja żyje do końca procesu i
nie jest nigdzie zapisywana — restart wraca do pytania.

### A4. Odnowienie tokenu proaktywne, z claimu `exp`; 401 tylko jako zabezpieczenie

Token żyje ~30 minut, a proces MCP tygodniami, więc wygaśnięcie jest sytuacją
normalną, nie wyjątkową.

Pierwotnie zaprojektowałem tu odnawianie **reaktywne** (czekaj na `401`, zaloguj
się ponownie, powtórz żądanie), odrzucając odczyt `exp` jako YAGNI. Pomiar to
obalił i decyzja jest odwrócona. Nieważny token **nie** daje `401` na endpointach
dostępnych publicznie — DSpace zwraca `200` i dane anonimowe. Reaktywna strategia
nigdy by się więc nie uruchomiła dla `discover/search`, `core/items` i całej reszty
odczytu: po ~30 minutach wyszukiwania zaczęłyby po cichu gubić rekordy zastrzeżone,
model meldowałby „nie ma", a użytkownik miałby do nich pełny dostęp. Byłoby to
ciche zejście do trybu anonimowego — czyli dokładnie to, czego A3 zabrania,
osiągnięte inną drogą.

**Proaktywnie, przed wysłaniem żądania.** Przy logowaniu odczytujemy z payloadu JWT
claim `exp` i odnawiamy token, zanim wygaśnie, z marginesem `max(5 min, 10% czasu
życia)`. Odczyt to rozdzielenie po kropkach, `base64url` i `json.loads` — bez
weryfikacji podpisu, bo nie jesteśmy stroną weryfikującą, tylko posiadaczem tokenu.
Zgodnie z regułą `shaping.py` funkcja **nigdy nie rzuca**: gdy payload jest
nieczytelny albo nie ma `exp`, zwraca `None`, a klient odnawia token po stałym
czasie (25 minut), zamiast się wywrócić.

**Reaktywnie, jako zabezpieczenie.** `401` na żądaniu wysłanym z tokenem nadal
wywołuje jedno ponowne logowanie i jedną powtórkę żądania (flaga lokalna dla
żądania, żadnej pętli). To łapie przypadki, których `exp` nie przewidzi:
rozjechane zegary, token unieważniony po stronie serwera, zmiana hasła.

Tylko `401`. Statusu `403` **nie** ponawiamy: DSpace odpowiada nim m.in. na
odrzucony token CSRF, więc ponawianie dokładałoby zbędne logowanie tam, gdzie i tak
nie pomoże.

Rozstrzygnięcie przypadku granicznego: gdy ponowne logowanie **się powiodło**, a
powtórzone żądanie znów zwraca `401`, nie ponawiamy dalej — stan zostaje
`AUTHENTICATED`. Komunikat dla modelu **nie** brzmi wtedy „your account has no
access", bo nie wiemy tego: nie zmierzyliśmy, czy DSpace odpowiada zalogowanemu bez
uprawnień kodem `401` czy `403`, a przy historii tego API (`401` na zepsuty UUID!)
zgadywanie jest tu zabronione. Komunikat mówi to, co wiemy: `the repository keeps
rejecting this session's token`.

### A5. Tożsamość raportujemy z konfiguracji, nie dodatkowym żądaniem

`/authn/status` podaje `eperson` wyłącznie jako link, więc wyświetlenie nazwy czy
adresu e-mail zalogowanego konta kosztowałoby osobny GET przy starcie. Nie warto:
użytkownik zna login, który sam wpisał.

`get_repository_info` zwraca blok `authentication` złożony z tego, co i tak mamy:

```json
{"mode": "authenticated",          // anonymous | authenticated | anonymous_by_choice
 "user": "<login z konfiguracji>", // tylko gdy podano konto
 "methods_offered": ["password", "orcid"]}  // z www-authenticate, jeśli już je znamy
```

Dwa doprecyzowania, żeby blok nie kosztował ani jednego dodatkowego żądania:

- W trybie `anonymous` blok zawiera **samo** `mode`. Wypełnienie `methods_offered`
  wymagałoby odpytania `/authn/status`, a bez podanego konta nie ma po co.
- Pola `method` (użyta metoda logowania) **nie** ma, choć `/authn/status` je zwraca:
  przed zalogowaniem jest tam `null`, więc odczytanie go znaczyłoby dodatkowe
  żądanie po zalogowaniu — dokładnie ten koszt, którego ta decyzja unika. Metoda i
  tak jest znana z góry, bo hasło to jedyna, jaką implementujemy.

Stan `needs_decision` nie występuje jako wartość `mode`, bo jest nieobserwowalny:
w tym stanie `get_repository_info` jest zablokowane jak każde inne narzędzie i model
dostaje pytanie z A3 zamiast raportu.

Dzięki temu model wie, czyimi oczami patrzy, i potrafi wyjaśnić użytkownikowi,
dlaczego czegoś nie widzi — zamiast twierdzić, że tego nie ma.

### A6. Metod logowania nie zakładamy, tylko pytamy instancję

Nagłówek `www-authenticate` na `/authn/status` wymienia metody skonfigurowane w tej
konkretnej instancji. To ta sama zasada, co D8 dla filtrów wyszukiwania: zestaw jest
konfigurowalny per-instancja, więc pytamy, zamiast zgadywać.

Zastosowanie: gdy `password` nie występuje na liście, wiemy **przed** wysłaniem
POST-a, że logowanie hasłem nie ma prawa się udać, i powód w `NEEDS_DECISION` jest
konkretny („this instance does not offer password login; it offers: orcid") zamiast
bezradnego „401".

Parsowanie trafia do `shaping.py` jako czysta funkcja `auth_methods(header) ->
list[str]`, podlegająca tamtejszej zasadzie: na śmieciowym wejściu zwraca pustą
listę i **nigdy nie rzuca**. Pusta lista znaczy „nie wiadomo" i nie blokuje próby
logowania.

### A7. Konfiguracja: oba pola albo żadne, pusty string to brak

`Config.username` i `Config.password` istnieją od wersji 1 jako przygotowanie pod
ten scenariusz (D7, punkt 3) i wreszcie zaczynają być czytane. Format konfiguracji
nie zmienia się — dokładnie tak, jak D7 zakładał.

Dwie reguły, obie wymuszone przy starcie:

1. **Oba albo żadne.** Sam login bez hasła (albo odwrotnie) to błąd konfiguracji z
   komunikatem, a nie ciche zignorowanie połowy ustawień. Reguła dotyczy wartości
   **po rozstrzygnięciu** pierwszeństwa, nie źródła — login z flagi `--username` i
   hasło ze zmiennej `DSPACE_PASSWORD` to poprawna konfiguracja.
2. **Pusty lub złożony z białych znaków = brak konta.** To nie kosmetyka, tylko
   zabezpieczenie przed scenariuszem, w którym host MCP podstawia za niewypełnione
   pole `user_config` pusty string zamiast pominąć zmienną: bez tej reguły anonimowa
   instalacja z paczki `.mcpb` wystartowałaby z `DSPACE_USERNAME=""` i próbowała
   logować się jako użytkownik o pustej nazwie. Czy MCPB tak robi — **nie zostało
   zweryfikowane**; reguła kosztuje jedno `strip()` i usuwa całą klasę ryzyka, więc
   wchodzi niezależnie od odpowiedzi. Do sprawdzenia empirycznie przy testach paczki
   (kryterium ukończenia 6).

### A8. Logowanie jest single-flight

Serwer MCP wykonuje narzędzia współbieżnie, więc bez zabezpieczenia dwa żądania
mogą jednocześnie stwierdzić, że token wymaga odnowienia, i wystartować dwa
logowania naraz. To nie jest teoretyczne: token CSRF **rotuje**, a cookie jar jest
wspólny, więc logowanie A pobrałoby token `c1`, logowanie B nadpisało cookie na
`c2`, a POST od A poleciałby z nagłówkiem `c1` i cookie `c2`. Niezgodność →
`403` → `NEEDS_DECISION` z powodem „CSRF token rejected" — czyli **serwer
zablokowany pytaniem o hasło, mimo że hasło jest poprawne**.

Logowanie obejmuje `asyncio.Lock`. Żądania, które zastały blokadę zajętą, po jej
zwolnieniu używają świeżo zdobytego tokenu, zamiast logować się po raz drugi.

### A9. Dwa widoki, jedno konto — przez osobne narzędzie, nie przez parametr

Uwierzytelniony serwer potrafi zadać to samo pytanie **także anonimowo**, żeby
odpowiedzieć na pytanie diagnostyczne: „użytkownik twierdzi, że brakuje plików —
co widzi zalogowany, a co widzi publiczność?".

Wystawiamy to jako **jedno dodatkowe narzędzie** `compare_access`, rejestrowane
wyłącznie wtedy, gdy logowanie się powiodło. Odrzucona alternatywa: parametr
`as_anonymous` na dziewięciu istniejących narzędziach. FastMCP buduje schemat
narzędzia ze stałej sygnatury funkcji, więc takiego parametru **nie da się** dodać
warunkowo — wszedłby do opisów także w instalacji czysto anonimowej, gdzie jest
bezużyteczny, i kosztował tokeny w każdej rozmowie (wbrew D4). Do tego model
musiałby sam pamiętać o dwóch wywołaniach i porównać na oko dwie długie listy
plików, co jest klasycznym miejscem na pomyłkę.

`compare_access` przyjmuje identyfikator rekordu (UUID, Handle lub DOI — tak jak
`get_item`) i zwraca **różnicę**, a nie dwa komplety danych: czy rekord jest widoczny
publicznie oraz które pliki widzi wyłącznie zalogowane konto. Zakres celowo zawężony
do rekordu i jego plików; porównanie na poziomie kolekcji („ile rekordów ukrytych
przed publicznością") jest tanie do dołożenia, ale nie zostało zamówione.

Dwie konsekwencje techniczne, obie obowiązkowe:

1. **Token nie może siedzieć na współdzielonym kliencie HTTP.** Trzymamy go w polu
   `DSpaceClient` i doklejamy per żądanie. Inaczej nagłówek jeździłby także na
   żądaniach „anonimowych" — i przy okazji na każdy absolutny URL spoza API, bo
   `_links.content` wskazuje host wybrany przez instancję, a mechanizm zdejmowania
   `Authorization` w `httpx` działa dopiero przy **przekierowaniu**, nie przy
   pierwszym żądaniu.
2. **Tor anonimowy ma własny `httpx.AsyncClient`, z własnym cookie jar.** Zmierzone:
   przy logowaniu instancja sadza do jara `AWSALB`/`AWSALBCORS` z `Path=/`. Żądanie
   „anonimowe" wysłane ze wspólnym jarem nie jest prawdziwie anonimowe, a instancja
   za proxy albo SSO mogłaby je po cichu uwierzytelnić — co czyniłoby całe
   porównanie bezwartościowym. Oba klienty są GET-only i oba obejmuje strażnik metod.

Pola `queried_as` w odpowiedziach pozostałych narzędzi **nie** wprowadzamy: skoro
tożsamość nie jest ich parametrem, wszystkie odpowiadają zawsze tożsamością konta, a
w `compare_access` podział wynika wprost ze struktury wyniku.

Odnotowanie uczciwe: użytkownik może już dziś osiągnąć porównanie bez tej funkcji —
wpinając w konfigurację klienta MCP drugi, anonimowy egzemplarz `dspace-mcp`.
`compare_access` musi to przebić wygodą (jedno wywołanie zamiast dwóch instalacji i
ręcznego diffu), a nie samą możliwością.

### A10. Widok anonimowy jako parametr — odwrócenie A9

A9 odrzuciła parametr `as_anonymous` na narzędziach odczytu, argumentując
kosztem tokenów: parametr wchodzi do schematu każdego narzędzia i płaci się za
niego w każdej rozmowie, także czysto anonimowej. Rachunek był **zły** i zmieniam
decyzję.

Dowód przyszedł z użycia. Agent pracujący na zalogowanym serwerze potrzebował
strony anonimowej porównania i — nie mając jak jej uzyskać — **ominął serwer w
całości** i poszedł `curl`-em. To jest najgorszy możliwy wynik: żądanie wysłane
poza serwerem nie ma wymuszonego GET-only, nie ma mapowania błędów na zdania dla
modelu, nie ma limitu rozmiaru pobrania ani spłaszczania odpowiedzi. Model
dostaje surowy HAL i interpretuje go na oko.

Kilkadziesiąt tokenów w opisie narzędzia jest tańsze niż utrata wszystkich
gwarancji, dla których ten serwer istnieje.

`continue_anonymously` nie było obejściem i nie miało nim być: jest sesyjne,
jednokierunkowe, a jego własny opis nakazuje wołać je dopiero na wyraźną prośbę
użytkownika.

Kształt: `as_anonymous: bool = False` na ośmiu narzędziach odczytu. Nie ma go w
`get_repository_info` (opisuje instancję, nie zbiór danych) ani w
`compare_access` (z definicji używa obu tożsamości). Parametr jest bezwarunkowy,
więc instalacja anonimowa i kontowa mają nadal **identyczne** schematy — test z
A9 tego pilnuje i pozostaje w mocy.

### A11. Filtry discovery muszą być użyteczne, nie tylko ogłaszane

`get_repository_info` ogłasza, jakie filtry ma instancja (D8), a `search_items`
pozwalało użyć czterech z nich. Model dostawał więc nazwę filtra i nie miał czym
jej zastosować. Zgłoszone z użycia: potrzebny był `access_status=restricted`,
nieosiągalny przez MCP.

Pułapka wykryta przy okazji: `query: "access_status:restricted"` **wygląda**, jakby
działało, i cicho zwraca `total: 0` — bo `query` idzie do wyszukiwania
pełnotekstowego, a nie w filtry Solr. Opis narzędzia mówi to teraz wprost.

`search_items` przyjmuje `filters: {nazwa: wartość}`, każdą nazwę sprawdzając
wobec tego, co instancja ogłasza. Wartość bez operatora dostaje domyślne
`,equals`; operator rozpoznajemy **po nazwie** z zamkniętej listy, nie po samej
obecności przecinka — inaczej „Kowalski, Jan" zostałby zinterpretowany jako
wartość „Kowalski" z operatorem „ Jan".

### A12. Poprawki zgłoszone z użycia

- **`compare_access` zwierało się w swoim własnym przypadku.** Wołało
  `list_bitstreams` z domyślnym bundlem `ORIGINAL`, a ten rzuca wyjątkiem, gdy
  bundla nie ma — czyli narzędzie odmawiało odpowiedzi dokładnie wtedy, gdy
  plików brakuje. Potwierdzone na żywej instancji: rekordy widoczne publicznie
  mają tam często wyłącznie `THUMBNAIL`. Porównanie obejmuje teraz wszystkie
  bundle, a nazwa bundla jedzie w każdym rekordzie.
- **`list_bundles`** — wcześniej listę bundli poznawało się wyłącznie z
  komunikatu błędu, czyli awaria pełniła funkcję interfejsu.
- **Flagi stanu rekordu** (`withdrawn`, `discoverable`, `in_archive`) w
  `shape_item`. Bez nich model nie odróżnia „wycofany" od „niedostępny dla
  ciebie". `None` znaczy „instancja tego nie podała", nie „fałsz".

## Architektura

Zmiany trzymają się dotychczasowego podziału warstw — nowy kod sieciowy trafia
wyłącznie do `client.py`.

### `config.py`

- Czytanie `DSPACE_USERNAME` i `DSPACE_PASSWORD` (puste = brak, reguła A7).
- Flagi `--username` i `--password`, z dotychczasowym pierwszeństwem
  flaga > zmienna > wartość domyślna. W helpie `--password` ostrzeżenie, że treść
  linii poleceń widać w `ps`, i wskazanie zmiennej środowiskowej jako zalecanej.
- Walidacja „oba albo żadne" we wspólnym helperze, wołanym z obu ścieżek budowy
  konfiguracji. Uwaga: `config_from_env` **nie** jest dziś używane w żadnej ścieżce
  uruchomieniowej (`server.main()` woła `parse_args()`, `mcpb/launch.py` woła
  `main()`) — jest publiczną funkcją modułu, używaną przez testy i ewentualnych
  konsumentów pakietu. To wystarczający powód, żeby zachowywała się identycznie;
  duplikowanie samej walidacji w dwóch miejscach nie jest.

### `client.py`

- `AuthState` — mały enum czterech stanów z A3, plus pole `reason` dla
  `NEEDS_DECISION`; stan trzymany w `DSpaceClient`.
- `_csrf_token()` — GET `/authn/status`, zwraca zawartość nagłówka
  `dspace-xsrf-token`. Wołane bezpośrednio przed każdym logowaniem, bo token
  rotuje. Przy okazji zapamiętuje `www-authenticate` (A6) i `authenticationMethod`.
- `_login()` — jedyny `self.http.post` w projekcie, ze ścieżką zaszytą i
  `follow_redirects=False` (A2), pod `asyncio.Lock` (A8). Zdobyty JWT ląduje w
  **polu klienta**, nie w nagłówkach współdzielonego `httpx.AsyncClient` (A9,
  punkt 1). Cookie `DSPACE-XSRF-COOKIE` obsługuje domyślny cookie jar `httpx`;
  klienta nie wolno budować z wyłączonymi ciasteczkami.
- `_token_expiry()` — czysty odczyt claimu `exp` z payloadu JWT (A4). Nie
  weryfikuje podpisu i **nigdy nie rzuca**: na nieczytelnym wejściu zwraca `None`,
  co przełącza klienta na stały interwał odnawiania.
- `authenticate()` — publiczna, wołana z lifespanu: sprawdza A6, loguje, ustawia
  stan na `AUTHENTICATED` albo `NEEDS_DECISION(powód)`. **Nie rzuca** — porażka
  logowania jest stanem do zaraportowania, nie wyjątkiem.
- `_auth_headers(anonymous: bool)` — jedyne miejsce doklejające `Authorization`.
  Przed wysłaniem sprawdza, czy token nie jest bliski wygaśnięcia, i w razie
  potrzeby odnawia go proaktywnie (A4).
- `_request_json` i `stream_bytes` — dostają tożsamość żądania (konto albo anonim)
  i obsługują `401` z A4 jako zabezpieczenie: jedno ponowne logowanie, jedna
  powtórka. Nadal wyłącznie GET.
- Drugi `httpx.AsyncClient` na tor anonimowy, z własnym cookie jar (A9, punkt 2).
  Budowany tym samym `build_http`, więc dziedziczy wszystkie empirycznie wymuszone
  ustawienia (brak `Origin`, `follow_redirects`, User-Agent).
- `_error_for_status` — komunikat dla `401`/`403` zależny od **tożsamości żądania**,
  nie od globalnego stanu (inaczej żądanie anonimowe z `compare_access`
  raportowałoby się jak żądanie konta). Dziś brzmi „this server queries DSpace
  anonymously", co zalogowanemu użytkownikowi każe się logować po raz drugi.

Mapowanie powodów porażki na zdania po angielsku (zasada: model ma dostać zdanie, z
którym może pójść do użytkownika):

| Sytuacja | Powód w `NEEDS_DECISION` |
|---|---|
| `password` spoza listy `www-authenticate` | `this instance does not offer password login; it offers: <lista>` |
| `401` z `/authn/login` | `the repository rejected that username or password` |
| `403` z `/authn/login` | `the repository refused the login request (CSRF token rejected)` |
| `200` bez nagłówka `authorization` | `the repository accepted the login but returned no token` |
| przekierowanie (3xx) w odpowiedzi na login | `the repository redirected the login request; refusing to send credentials to another address` |
| sieć / timeout | dotychczasowe komunikaty z `DSpaceError` |

Uwaga o przekierowaniach: `httpx` sam usuwa nagłówek `Authorization` przy
przekierowaniu na inne origin — poza podniesieniem HTTP na HTTPS pod tym samym
hostem (`_client.py`, `_redirect_headers`). Skutek jest pożądany: JWT nie wycieka
do S3, gdy `/content` przekierowuje do magazynu obiektów, a plik i tak się pobiera,
bo taki adres niesie własny podpis. Wymaga to jednak sprawdzenia na żywej instancji
z materiałem zastrzeżonym — jeśli któreś wdrożenie przekierowuje na adres
**nie**podpisany, pobranie pliku zastrzeżonego się nie uda i trzeba to opisać jako
znane ograniczenie, a nie naprawiać przepychaniem tokenu na obcy host.

### `shaping.py`

Jedna czysta funkcja `auth_methods(header: str | None) -> list[str]` (A6),
niepodnosząca wyjątków, testowana na surowym nagłówku z demo.

### `server.py`

- Bramka w `_guard` (A3): w stanie `NEEDS_DECISION` narzędzie wraca pytaniem, nie
  dotykając sieci; ten sam `_guard` mapuje wyjątek `NeedsDecision` na identyczną
  strukturę, żeby tor „w locie" nie różnił się od bramki.
- `continue_anonymously` — nowe narzędzie, rejestrowane tylko przy podanym koncie,
  poza bramką.
- `compare_access` (A9) — rejestrowane tylko po udanym logowaniu.
- Lifespan: `probe()` **przed** `authenticate()`. Kolejność jest istotna, bo sonda
  koryguje brakujące `/server` w adresie API, a logowanie musi trafić pod już
  poprawiony adres.
- `get_repository_info` wzbogacone o blok `authentication` (A5) — w `tools.py`,
  bo tam mieszka orkiestracja.

### `tools.py`

- `compare_access` — orkiestracja A9: pobranie rekordu i jego plików obiema
  tożsamościami, złożenie różnicy. Jak każde narzędzie: przyjmuje `DSpaceClient`,
  zwraca zwykły dict, nie wie nic o MCP.
- `get_item_statistics` ma **własne**, zaszyte mapowanie `401`/`403` na zdanie ze
  słowem „anonymously" (dziś: `This repository does not expose usage statistics
  anonymously.`). Po zalogowaniu jest ono nieprawdziwe, więc musi zależeć od
  tożsamości żądania — sama zmiana w `client._error_for_status` tego nie załatwia,
  bo `tools.py` ją nadpisuje.

## Testy

Wszystko na `respx`, offline; fixtury `authn/status` (anonimowa i zalogowana) oraz
odpowiedź na złe hasło łapane surowe z demo, zgodnie z zasadą repozytorium.

Strażnik trybu tylko-do-odczytu **nie znika** — zmienia się z „każde żądanie to GET"
na „każde żądanie to GET, a jedyny POST idzie pod `<api>/authn/login`":

```python
for call in respx.calls:
    if call.request.method != "GET":
        assert call.request.method == "POST"
        assert str(call.request.url) == f"{client.api_url}/authn/login"  # równość, nie endswith
```

Do tego test architektoniczny na źródłach pakietu: dokładnie jedno wystąpienie
`self.http.post` i zero `put`, `patch`, `delete`, `request` — strażnik, który
zadziała nawet dla ścieżki, o której napisaniu testu ktoś zapomni.

Poza tym:

- logowanie: token CSRF pobrany z `/authn/status` i wysłany jako `X-XSRF-TOKEN`,
  JWT z nagłówka odpowiedzi doklejony do kolejnych GET-ów,
- cookie `DSPACE-XSRF-COOKIE` faktycznie wysłane przy POST (double-submit działa),
- login leci pod adres **skorygowany** przez sondę (kolejność probe → authenticate),
- login z `follow_redirects=False`: odpowiedź 307 → porażka logowania, **żadnego**
  drugiego żądania (regresja na dziurę z A2),
- `stream_bytes` niesie `Authorization` (dostęp do plików zastrzeżonych),
- proaktywne odnowienie: token bliski wygaśnięcia jest wymieniany **przed**
  żądaniem; `_token_expiry()` na śmieciowym payloadzie zwraca `None` i nie rzuca,
- `401` na żądaniu → ponowne logowanie → powtórka; dokładnie jedna, bez pętli,
- drugi `401` po udanym ponownym logowaniu → stan zostaje `AUTHENTICATED`,
  komunikat nie twierdzi nic o uprawnieniach konta,
- `403` **nie** wywołuje ponownego logowania,
- współbieżność: dwa równoległe żądania z wygasłym tokenem → **jedno** logowanie
  (A8),
- porażka logowania → `NEEDS_DECISION`; każde z dziewięciu narzędzi wraca pytaniem
  i **nie wysyła żadnego żądania**,
- `NeedsDecision` rzucone w trakcie żądania daje tę samą strukturę co bramka,
- `continue_anonymously` odblokowuje; kolejne wywołania nie zmieniają stanu;
  wywołane w `AUTHENTICATED`/`ANONYMOUS` raportuje stan bieżący,
- po `ANONYMOUS_BY_CHOICE` żądania **nie niosą** `Authorization`,
- `continue_anonymously` nie jest zarejestrowane, gdy nie podano konta;
  `compare_access` odmawia pracy poza stanem `AUTHENTICATED` (inaczej porównywałoby
  anonima z anonimem i meldowało „nic nie jest ukryte"),
- `compare_access`: tor anonimowy nie niesie `Authorization` **w żadnym** żądaniu i
  używa osobnego cookie jar; różnica plików liczona po UUID,
- instancja bez `password` w `www-authenticate` → `NEEDS_DECISION` bez próby POST-a,
- `auth_methods()` na śmieciowym i pustym wejściu → pusta lista, bez wyjątku,
- konfiguracja: puste zmienne = tryb anonimowy; sam login bez hasła = błąd; login z
  flagi + hasło ze zmiennej = poprawne,
- `get_repository_info` raportuje każdy z obserwowalnych trybów,
- komunikat dla `403` różni się w torze anonimowym i koncie — także w
  `get_item_statistics`, które ma własne mapowanie.

Test `live` (poza domyślnym przebiegiem): logowanie do demo kontem publicznym z
dokumentacji DSpace, sterowane zmiennymi `DSPACE_TEST_USERNAME` /
`DSPACE_TEST_PASSWORD`; pomijany, gdy ich nie ma.

## Kryteria ukończenia

1. Bez podanego konta zachowanie jest identyczne z dzisiejszym — te same dziewięć
   narzędzi, te same żądania, ten sam wynik testów.
2. Z podanym kontem serwer czyta materiały widoczne wyłącznie dla tego konta,
   łącznie z zawartością plików.
3. Nieudane logowanie zatrzymuje pracę i stawia użytkownikowi wybór: poprawić
   konfigurację albo świadomie pracować anonimowo.
4. Wygaśnięcie tokenu jest niewidoczne dla użytkownika, a serwer **nigdy** nie
   zwraca po cichu wyników anonimowych, będąc w stanie `AUTHENTICATED`.
5. Strażnik metod HTTP przechodzi w nowej, węższej postaci (z asercją równości
   adresu); w kodzie nie ma ścieżki zdolnej wysłać `PUT`, `PATCH`, `DELETE` ani
   `POST` poza `/authn/login`, a poświadczenia nie mogą trafić pod inny adres niż
   ten jeden.
6. `compare_access` pokazuje, których plików nie widzi publiczność, a jego tor
   anonimowy nie niesie tokenu ani ciasteczek sesji konta.
7. Paczka `.mcpb` pozwala podać konto, z hasłem trzymanym jako `sensitive`, a
   pominięcie obu pól daje działającą instalację anonimową.
8. Żaden tekst nie twierdzi już, że serwer jest wyłącznie anonimowy: `README.md`,
   `CLAUDE.md`, `description` i `long_description` w `manifest.json` oraz docstring
   modułu `client.py`. README zaleca konto o najmniejszych wystarczających
   uprawnieniach.
9. `ruff check`, `ruff format --check` i `pytest -q` przechodzą na Pythonach
   3.10–3.13.

## Zmiany w decyzjach poprzedników

- **D1** — uchylona połowicznie, patrz A1. Zakres odczytu jest konfigurowalny;
  niezdolność do modyfikacji zostaje bezwarunkowa. Uwaga D1 o CSRF (wymagany tylko
  dla metod mutujących i dla `/authn/login`) staje się z teoretycznej praktyczna.
- **D2** — bez zmian co do instancji; rozciągnięta na **konto** (jedno na proces),
  ale **nie** na widok: A9 dokłada drugi, anonimowy tor. To zgodne z rozumowaniem
  D2, którego sednem było niedopuszczenie `base_url` jako parametru wywołania (bo
  otwarty URL to SSRF). Tożsamość jest zamkniętym, dwuwartościowym wyborem, nie
  otwartym adresem, więc ten argument jej nie dotyczy.
- **D5** (`get_item_statistics` w zakresie) — jej opis przepływu zakłada dostęp
  anonimowy, a samo narzędzie ma zaszyte słowo „anonymously" w komunikacie błędu.
  Po zalogowaniu przestaje ono być prawdą; patrz sekcja `tools.py`.
- **D7** — punkty 2 i 3 wykorzystane zgodnie z przeznaczeniem: `httpx.AsyncClient`
  żyjący w lifespanie ma gdzie trzymać cookie CSRF, a pola `username`/`password`
  czekały gotowe, więc format konfiguracji faktycznie nie musiał się zmienić.
  Punkt 4 (warunkowa rejestracja narzędzi) używany po raz pierwszy — tyle że dla
  `continue_anonymously` i `compare_access`, a nie dla zapisu. `enable_write`
  pozostaje polem, którego nikt nie czyta.
- **D8** — rozszerzona na metody logowania, patrz A6.
- **E5** („Read-only bez zmian") — jej teza, że inwariant „proces wysyła wyłącznie
  GET" trzyma się bez dotykania warstwy sieci, przestaje obowiązywać dosłownie.
  Zastępuje ją A2: jeden zaszyty POST pod `/authn/login` i nic poza tym. Zdanie o
  rozszerzaniu testu „tylko GET" o nowe wywołania zostaje w mocy — test dostaje
  teraz jeszcze jeden wymiar, metodę HTTP.
