# DSpace REST API — fixtures

Surowe, niemodyfikowane odpowiedzi z żywych instancji DSpace. Zebrane **2026-07-22**
(UTC ~15:13–15:20) przy pomocy `curl -sS --max-time 20/30`, **anonimowo** (bez
cookies, bez tokenów, bez nagłówka `X-XSRF-TOKEN`), wyłącznie metodą GET.

## Źródła

| Prefiks | Instancja | `dspaceVersion` | Uwagi |
|---|---|---|---|
| `dspace10_` | https://demo.dspace.org/server/api | `DSpace 10.1-SNAPSHOT` | główne źródło fixture'ów |
| `dspace11_` | https://sandbox.dspace.org/server/api | `DSpace 11.0-SNAPSHOT` | tylko root, do porównania |
| `dspace8_`  | https://dspace.mit.edu/server/api | `DSpace 8.2` (+ `crisVersion: cris-2024.02.04`) | tylko root — to DSpace-CRIS, nie waniliowy DSpace |
| `dspace7_`  | https://researchcommons.waikato.ac.nz/server/api | `DSpace 7.6.5` | waniliowy DSpace 7, do porównania kształtu |

UWAGA: `demo.dspace.org` jest **cyklicznie resetowana** (dane i UUID-y znikają).
Fixture'y traktować jako zamrożony kształt struktury, nie jako stabilne ID.

## Użyte identyfikatory (demo.dspace.org, stan 2026-07-22)

- item z bitstreamem: `4109f8db-ff30-4a46-9148-268b7fe18a17` („Test PhD Thesis”)
- item z wyszukiwania „cancer”: `5f116a15-d156-46ce-9eb8-d0c820eb6c05`, handle `123456789/443`
- bundle: `660faf2c-1038-43b0-82b5-400bdc5adce4` (ORIGINAL)
- bitstream: `45382064-a29a-402f-bb1b-5304f5031f30` (Test.pdf)
- community: `0958c910-2037-42a9-81c7-dca80e3892b4`

## Pliki i polecenia

Wszędzie poniżej `D=https://demo.dspace.org/server/api`.

| Plik | Polecenie |
|---|---|
| `dspace10_root.json`, `dspace10_root_headers.txt` | `curl -sS -D dspace10_root_headers.txt "$D" -o dspace10_root.json` |
| `dspace10_search_objects.json` | `curl -sS "$D/discover/search/objects?query=cancer&dsoType=item&size=2"` |
| `dspace10_search_objects_page1.json` | `curl -sS "$D/discover/search/objects?query=cancer&dsoType=item&size=2&page=1"` |
| `dspace10_item.json` | `curl -sS "$D/core/items/4109f8db-.../?embed=owningCollection,bundles"` |
| `dspace10_item_identifiers.json` | `curl -sS "$D/core/items/5f116a15-.../identifiers"` |
| `dspace10_communities_top.json` | `curl -sS "$D/core/communities/search/top?size=2"` |
| `dspace10_community_collections.json` | `curl -sS "$D/core/communities/0958c910-.../collections?size=2"` |
| `dspace10_bundles.json` | `curl -sS "$D/core/items/4109f8db-.../bundles"` |
| `dspace10_bitstreams.json` | `curl -sS "$D/core/bundles/660faf2c-.../bitstreams"` |
| `dspace10_bitstreamformat.json` | `curl -sS "$D/core/bitstreams/45382064-.../format"` |
| `dspace10_facets.json` | `curl -sS "$D/discover/facets"` |
| `dspace10_facets_author.json` | `curl -sS "$D/discover/facets/author?size=3"` |
| `dspace10_404.json` | `curl -sS "$D/core/items/00000000-0000-0000-0000-000000000000"` (HTTP 404) |
| `dspace10_401_malformed_uuid.json` | `curl -sS "$D/core/items/not-a-uuid"` (HTTP **401**, nie 400!) |
| `dspace10_usagereport_totalvisits.json` | `curl -sS "$D/statistics/usagereports/4109f8db-..._TotalVisits"` (HTTP **200** anonimowo) |
| `dspace10_pid_find_headers.txt` | `curl -sS -D - "$D/pid/find?id=hdl:123456789/443"` (HTTP **302** + `Location:`) |
| `dspace7_root.json` | `curl -sS https://researchcommons.waikato.ac.nz/server/api` |
| `dspace7_search_objects.json` | `curl -sS "https://researchcommons.waikato.ac.nz/server/api/discover/search/objects?query=cancer&dsoType=item&size=2"` |
| `dspace8_root.json` | `curl -sS https://dspace.mit.edu/server/api` |
| `dspace11_root.json` | `curl -sS https://sandbox.dspace.org/server/api` |

Pliki `*_headers.txt` zawierają surowe nagłówki odpowiedzi. Cookies `AWSALB*` /
`DSPACE-XSRF-COOKIE` to efemeryczne cookies publicznej instancji demo — nie są
sekretami.

## Fixtury uwierzytelniania (zebrane 2026-07-24)

Wyjątek od reguły „anonimowo, wyłącznie GET" z nagłówka tego pliku: tych trzech
nie da się zdobyć inaczej. Konto to **publiczne konto demonstracyjne** z
dokumentacji DSpace (`dspacedemo+admin@gmail.com` / `dspace`), nie czyjeś realne
poświadczenia.

| Plik | Polecenie |
|---|---|
| `dspace10_authn_status_anonymous.json`, `dspace10_authn_status_headers.txt` | `curl -sS -D dspace10_authn_status_headers.txt "$D/authn/status"` |
| `dspace10_authn_status_authenticated.json` | to samo, ale z `Authorization: Bearer <token>` po zalogowaniu |
| `dspace10_authn_login_401.json` | `curl -sS -X POST "$D/authn/login" -H "X-XSRF-TOKEN: …" -d 'user=…&password=<złe>'` (HTTP **401**) |

Odpowiedzi **udanego** logowania świadomie tu nie ma: niosłaby prawdziwy JWT, a
token w publicznym repozytorium to dokładnie ten wzorzec, który wyłapują skanery
sekretów. Testy budują ją inline (`tests/test_client.py::mock_login`).

Co z nich wynika dla kodu:

- `dspace10_authn_status_headers.txt` niesie `dspace-xsrf-token` (token CSRF,
  **rotuje** przy logowaniu) oraz `www-authenticate` z listą metod logowania
  obsługiwanych przez instancję (`password`, `orcid`) — stąd `shaping.auth_methods`.
- Body statusu **nie osadza** obiektu `eperson`, podaje tylko link. Dlatego nazwa
  zalogowanego konta w `get_repository_info` pochodzi z konfiguracji, a nie z
  dodatkowego żądania (decyzja A5).

## Najważniejsze cechy struktury (skrót)

- Rekordy wyszukiwania: `_embedded.searchResult._embedded.objects[]._embedded.indexableObject`
- Fasety wyszukiwania: `_embedded.facets` — **na najwyższym poziomie**, nie w `searchResult`
- Koperta stron: `page = {number, size, totalPages, totalElements}` (ale `discover/facets/*`
  ma tylko `{number, size}`)
- `metadata` to **dict** `{"dc.title": [{"value","language","authority","confidence","place"}]}`
- Bitstream **nie ma** pola `mimetype` — MIME jest w `/core/bitstreams/{uuid}/format`
  (`embed=format`)
- `uniqueType` występuje tylko w DSpace 10/11 i DSpace-CRIS — nie ma go w DSpace 7.6/8.4/9.2
