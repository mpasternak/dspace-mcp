"""Asynchroniczny klient REST API DSpace 7+ — wyłącznie odczyt.

Klient robi te rzeczy, których nie chcemy powtarzać w każdym narzędziu:
sklejanie URL-i, mapowanie błędów HTTP na komunikaty zrozumiałe dla modelu,
paginację HAL, sondę startową i wykrywanie zdolności instancji.

Wysyła **wyłącznie** żądania GET — to gwarancja bezpieczeństwa całego projektu,
wynikająca z konstrukcji, a nie z dobrej woli modelu.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any

import httpx

from . import __version__
from .shaping import parse_version

if TYPE_CHECKING:  # pragma: no cover - tylko dla typów
    from .config import Config

USER_AGENT = f"dspace-mcp/{__version__} (+https://github.com/mpasternak/dspace-mcp)"

#: Twardy sufit liczby żądań w jednej pętli paginacyjnej. Bezpiecznik na wypadek
#: instancji, która w nieskończoność podaje `_links.next`.
MAX_PAGE_REQUESTS = 20

_UUID_RE = re.compile(
    r"\A[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\Z"
)


class DSpaceError(Exception):
    """Błąd przeznaczony do pokazania modelowi. ``message`` jest po angielsku."""

    #: Kod HTTP, jeśli błąd powstał z odpowiedzi serwera (używane wewnętrznie
    #: przez sondę, która na 404 ponawia z dopisanym „/server").
    status: int | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def is_uuid(value: str) -> bool:
    """Czy ``value`` ma kształt UUID-a (bez wnikania w wersję i wariant)?"""
    return bool(_UUID_RE.match(value)) if isinstance(value, str) else False


def require_uuid(value: str) -> str:
    """Zwróć ``value``, jeśli to UUID; w przeciwnym razie rzuć ``DSpaceError``.

    Sprawdzamy po swojej stronie, bo DSpace na niepoprawny UUID w ścieżce
    odpowiada **401 „Authentication is required"** (nie 400) — komunikat, po
    którym model zaczyna szukać sposobu na zalogowanie się, zamiast poprawić
    identyfikator. Patrz ``tests/fixtures/dspace10_401_malformed_uuid.json``.
    """
    if not is_uuid(value):
        raise DSpaceError(f"'{value}' is not a valid UUID.")
    return value


def _href(link: Any) -> str | None:
    """Wyciągnij ``href`` z relacji HAL, tolerując listę zamiast obiektu.

    Wartością relacji bywa lista (np. ``workflowGroups`` w kolekcji), więc
    naiwne ``links[rel]["href"]`` wywala się na realnych odpowiedziach.
    """
    if isinstance(link, list):
        link = link[0] if link else None
    if isinstance(link, dict):
        href = link.get("href")
        if isinstance(href, str) and href:
            return href
    return None


def _mb(num_bytes: int) -> str:
    """Rozmiar w megabajtach do komunikatu (bez zbędnych zer po przecinku)."""
    value = num_bytes / (1024 * 1024)
    return f"{value:.0f}" if value >= 1 else f"{value:.2f}"


class DSpaceClient:
    """Cienka warstwa nad ``httpx.AsyncClient``, mówiąca w języku DSpace'a."""

    def __init__(self, config: Config, http: httpx.AsyncClient) -> None:
        self.config = config
        self.http = http
        self._api_url = config.api_url
        self._probe: dict | None = None
        self._capabilities: dict | None = None

    @property
    def api_url(self) -> str:
        """Aktualny korzeń API — sonda może go skorygować o „/server"."""
        return self._api_url

    @classmethod
    def build_http(cls, config: Config) -> httpx.AsyncClient:
        """Zbuduj klienta HTTP z ustawieniami, które nie są opcjonalne."""
        return httpx.AsyncClient(
            # /api/pid/find odpowiada 302 z nagłówkiem Location, a httpx domyślnie
            # NIE podąża za przekierowaniami — bez tego wyszukanie po handlu (i
            # pobranie /content przekierowanego do S3) zwraca pustkę.
            follow_redirects=True,
            timeout=config.timeout,
            headers={
                # Odpytujemy cudze repozytoria w pętli sterowanej przez model;
                # anonimowy ruch bez identyfikacji bywa powodem banów IP.
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                # Nagłówka Origin NIE ustawiamy nigdy: z nim DSpace odrzuca nawet
                # zwykłe GET-y błędem 403 (zweryfikowane empirycznie).
            },
        )

    # --- warstwa żądań ------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self._api_url}/{path.lstrip('/')}"

    def _error_for_status(self, status: int, where: str) -> DSpaceError:
        """Zamień kod HTTP na komunikat, z którym model może coś zrobić.

        Treści ``message`` z ciała błędu DSpace świadomie nie przekazujemy —
        Spring Boot wpisuje tam bezużyteczne „An exception has occurred".
        """
        if status == 404:
            message = f"Not found: no such object at {where}. Check the UUID or handle."
        elif status in (401, 403):
            message = (
                "Not publicly available: this server queries DSpace anonymously "
                "and has no access to that object."
            )
        elif status == 422:
            message = (
                "The repository rejected this query (422). It usually means an "
                "unknown search filter; call get_repository_info to see which "
                "filters this instance supports."
            )
        elif status == 501:
            message = "This repository cannot resolve identifiers of that type."
        elif status in (429, 503):
            # Świadomie BEZ automatycznego ponawiania — to my jesteśmy natrętnym
            # klientem, a ponawianie pod limitem tempa kończy się banem.
            message = "The repository is rate-limiting requests. Wait before retrying."
        else:
            message = (
                f"The repository returned an unexpected error (HTTP {status}) "
                f"for {where}."
            )
        error = DSpaceError(message)
        error.status = status
        return error

    async def _request_json(
        self, url: str, params: dict | None = None, *, where: str
    ) -> dict:
        """Jedno żądanie GET pod bezwzględny URL, z mapowaniem błędów."""
        try:
            response = await self.http.get(url, params=params)
        except httpx.ConnectError as exc:
            raise DSpaceError(
                f"Repository unreachable at {self.config.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise DSpaceError(
                "The repository did not respond in time; try narrowing the query."
            ) from exc
        except httpx.HTTPError as exc:  # np. błąd pętli przekierowań, TLS
            raise DSpaceError(
                f"Repository unreachable at {self.config.base_url}."
            ) from exc

        if response.status_code >= 400:
            raise self._error_for_status(response.status_code, where)

        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            raise DSpaceError(
                f"The repository returned a response that is not valid JSON "
                f"for {where}."
            ) from exc
        if not isinstance(payload, dict):
            raise DSpaceError(
                f"The repository returned unexpected JSON (not an object) for {where}."
            )
        return payload

    async def get(self, path: str, params: dict | None = None) -> dict:
        """GET pod ścieżkę **względną wobec /api**, np. ``/core/items/{uuid}``."""
        return await self._request_json(self._url(path), params, where=path)

    # --- sonda startowa -----------------------------------------------------

    async def probe(self) -> dict:
        """Odpytaj korzeń API: nazwa, adresy i wersja instancji (z cache'em)."""
        if self._probe is not None:
            return self._probe

        try:
            payload = await self._request_json(self._api_url, where="/")
        except DSpaceError as exc:
            # Najczęstsza pomyłka konfiguracyjna: base_url bez „/server".
            # Próbujemy raz, a gdy się uda — zapamiętujemy poprawiony korzeń.
            if exc.status != 404:
                raise
            retry_url = f"{self.config.base_url.rstrip('/')}/server/api"
            if retry_url == self._api_url:
                raise
            payload = await self._request_json(retry_url, where="/")
            self._api_url = retry_url

        version = payload.get("dspaceVersion")
        self._probe = {
            "name": payload.get("dspaceName"),
            "ui_url": payload.get("dspaceUI"),
            "server_url": payload.get("dspaceServer"),
            "version": version,
            "version_tuple": parse_version(version),
        }
        return self._probe

    # --- paginacja HAL ------------------------------------------------------

    @staticmethod
    def _envelope(payload: dict, key: str) -> tuple[list[dict], dict, dict]:
        """Rozpakuj kopertę HAL do ``(elementy, page, _links)``.

        Dwa kształty w praktyce: płaski (``_embedded.communities``) oraz
        zagnieżdżony w /discover/search/objects, gdzie ``page`` i ``_links.next``
        siedzą w ``_embedded.searchResult``, a nie na wierzchu.
        """
        embedded = payload.get("_embedded")
        if not isinstance(embedded, dict):
            return [], {}, {}

        items = embedded.get(key)
        if isinstance(items, list):
            page = payload.get("page")
            links = payload.get("_links")
            return (
                list(items),
                page if isinstance(page, dict) else {},
                links if isinstance(links, dict) else {},
            )

        for value in embedded.values():
            if not isinstance(value, dict):
                continue
            inner = value.get("_embedded")
            if isinstance(inner, dict) and isinstance(inner.get(key), list):
                page = value.get("page")
                links = value.get("_links")
                return (
                    list(inner[key]),
                    page if isinstance(page, dict) else {},
                    links if isinstance(links, dict) else {},
                )
        return [], {}, {}

    async def get_page(
        self, path: str, params: dict | None = None, *, key: str
    ) -> tuple[list[dict], dict]:
        """Jedna strona: ``(elementy spod _embedded[key], koperta page)``.

        Kolejność kluczy w ``page`` bywa różna między instancjami, a endpoint
        faset nie podaje ``totalElements`` — czytamy po nazwach, nie po pozycji.
        """
        payload = await self.get(path, params)
        items, page, _links = self._envelope(payload, key)
        return items, page

    async def get_all(
        self, path: str, params: dict | None = None, *, key: str, limit: int
    ) -> tuple[list[dict], int | None, bool]:
        """Podążaj za ``_links.next``, zbierając najwyżej ``limit`` elementów.

        Zwraca ``(elementy, total, truncated)``. ``total`` bierzemy z pierwszej
        odpowiedzi i bywa ``None`` (endpoint faset go nie zwraca). ``truncated``
        oznacza „jest tego więcej, niż pokazujemy".
        """
        cap = max(0, min(limit, self.config.max_results))
        items: list[dict] = []
        total: int | None = None
        truncated = False

        url = self._url(path)
        request_params = dict(params) if params else None

        for attempt in range(MAX_PAGE_REQUESTS):
            payload = await self._request_json(url, request_params, where=path)
            page_items, page, links = self._envelope(payload, key)
            if attempt == 0:
                raw_total = page.get("totalElements")
                total = raw_total if isinstance(raw_total, int) else None
            items.extend(page_items)
            next_href = _href(links.get("next"))

            if len(items) >= cap:
                truncated = len(items) > cap or bool(next_href)
                items = items[:cap]
                break
            # Pusta strona z linkiem `next` to prosta droga do pętli bez końca.
            if not next_href or not page_items:
                break
            # Href strony następnej niesie już własne query — nie doklejamy params.
            url, request_params = next_href, None
        else:
            truncated = True

        if total is not None and total > len(items):
            truncated = True
        return items, total, truncated

    # --- zdolności instancji ------------------------------------------------

    async def capabilities(self) -> dict:
        """Filtry i sortowania obsługiwane przez TĘ instancję (leniwie, z cache).

        Zestaw jest konfigurowalny per-instancja (``discovery.xml``), a użycie
        nieistniejącego filtra kończy się błędem 422 — dlatego pytamy zamiast
        zakładać. Gdy endpoint zawiedzie, zwracamy puste listy: brak wiedzy o
        filtrach nie może wysadzić narzędzia, które akurat ich nie potrzebuje.
        """
        if self._capabilities is not None:
            return self._capabilities

        try:
            payload = await self.get("/discover/search")
        except DSpaceError:
            capabilities = {"filters": [], "sorts": []}
        else:
            capabilities = {
                "filters": _names(payload.get("filters"), "filter"),
                "sorts": _names(payload.get("sortOptions"), "name"),
            }
        self._capabilities = capabilities
        return capabilities

    # --- pobieranie plików --------------------------------------------------

    async def stream_bytes(self, url: str, *, max_bytes: int) -> bytes:
        """Pobierz zawartość spod BEZWZGLĘDNEGO URL-a, twardo limitując rozmiar.

        ``sizeBytes`` z metadanych bitstreamu bywa nieaktualne, a przy
        ``Transfer-Encoding: chunked`` nie ma nawet ``Content-Length`` — więc
        liczymy bajty w locie i przerywamy, gdy przekroczą limit.
        """
        too_big = DSpaceError(
            f"File is larger than the {_mb(max_bytes)} MB limit; "
            f"give the user this link instead: {url}"
        )
        chunks: list[bytes] = []
        size = 0
        try:
            async with self.http.stream("GET", url) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._error_for_status(response.status_code, url)
                declared = response.headers.get("content-length")
                if declared and declared.isdigit() and int(declared) > max_bytes:
                    raise too_big
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > max_bytes:
                        raise too_big
                    chunks.append(chunk)
        except httpx.ConnectError as exc:
            raise DSpaceError(
                f"Repository unreachable at {self.config.base_url}."
            ) from exc
        except httpx.TimeoutException as exc:
            raise DSpaceError(
                "The repository did not respond in time; try narrowing the query."
            ) from exc
        except httpx.HTTPError as exc:
            raise DSpaceError(f"Could not download the file at {url}.") from exc
        return b"".join(chunks)


def _names(entries: Any, key: str) -> list[str]:
    """Wyciągnij listę nazw z listy słowników, ignorując śmieci."""
    if not isinstance(entries, list):
        return []
    names = []
    for entry in entries:
        if isinstance(entry, dict) and isinstance(entry.get(key), str):
            names.append(entry[key])
    return names
