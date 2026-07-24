"""Asynchroniczny klient REST API DSpace 7+ — wyłącznie odczyt.

Klient robi te rzeczy, których nie chcemy powtarzać w każdym narzędziu:
sklejanie URL-i, mapowanie błędów HTTP na komunikaty zrozumiałe dla modelu,
paginację HAL, sondę startową, wykrywanie zdolności instancji oraz — opcjonalnie
— logowanie na konto, żeby czytać materiały niepubliczne.

Do **odczytu danych** wysyła wyłącznie żądania GET. Jedynym wyjątkiem w całym
projekcie jest ``POST`` pod ``/authn/login`` w :meth:`DSpaceClient._login`:
ścieżka jest tam zaszyta, więc nie da się nią sięgnąć nigdzie indziej, a samo
żądanie nie podąża za przekierowaniami (decyzja A2). Żadna metoda mutująca dane
nie istnieje — niezdolność do modyfikacji repozytorium jest bezwarunkowa i
wynika z konstrukcji, nie z trybu ani z dobrej woli modelu.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import enum
import json
import re
import time
from typing import TYPE_CHECKING, Any

import httpx

from . import __version__
from .shaping import auth_methods, parse_version

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

#: Z jakim wyprzedzeniem wymieniamy token (decyzja A4). Nieważny token nie daje
#: 401 na endpointach publicznych — DSpace po cichu odpowiada danymi anonimowymi
#: — więc wygaśnięciu trzeba zapobiegać, a nie na nie reagować.
TOKEN_REFRESH_MARGIN = 300.0
TOKEN_REFRESH_FRACTION = 0.1

#: Interwał odnawiania, gdy z payloadu tokenu nie da się odczytać ``exp``.
TOKEN_BLIND_REFRESH = 1500.0

#: Najkrótszy odstęp między logowaniami na ścieżce proaktywnej. Bezpiecznik na
#: wypadek tokenu, który zaraz po wystawieniu wygląda na przeterminowany —
#: najczęściej przez rozjechany zegar. Bez tego serwer logowałby się przed każdym
#: żądaniem, a natrętny klient kończy z banem IP.
TOKEN_MIN_LOGIN_INTERVAL = 60.0


class DSpaceError(Exception):
    """Błąd przeznaczony do pokazania modelowi. ``message`` jest po angielsku."""

    #: Kod HTTP, jeśli błąd powstał z odpowiedzi serwera (używane wewnętrznie
    #: przez sondę, która na 404 ponawia z dopisanym „/server").
    status: int | None = None

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NeedsDecision(DSpaceError):
    """Logowanie zawiodło — dalsza praca wymaga decyzji użytkownika (A3).

    Osobny typ, bo to nie jest zwykły błąd żądania: serwer nie ma sensownej
    ścieżki dalej, dopóki użytkownik nie poprawi konfiguracji albo świadomie nie
    zgodzi się na dostęp anonimowy. Dziedziczy po ``DSpaceError``, więc kod,
    który tamten łapie, nie przestaje działać.
    """


class AuthState(enum.Enum):
    """Stan uwierzytelnienia procesu (decyzja A3)."""

    #: Nie podano konta — dotychczasowe zachowanie serwera.
    ANONYMOUS = "anonymous"
    #: Zalogowano; żądania konta niosą token.
    AUTHENTICATED = "authenticated"
    #: Podano konto, logowanie padło — narzędzia czekają na decyzję użytkownika.
    NEEDS_DECISION = "needs_decision"
    #: Użytkownik świadomie wybrał pracę na danych publicznych.
    ANONYMOUS_BY_CHOICE = "anonymous_by_choice"


def token_expiry(token: str) -> float | None:
    """Czas wygaśnięcia (``exp``) z payloadu JWT albo ``None``.

    **Nie weryfikujemy podpisu** — nie jesteśmy stroną weryfikującą, tylko
    posiadaczem tokenu; podpis sprawdza serwer, który go wystawił.

    Jak wszystko, co czyta cudze dane: **nigdy nie rzuca**. Nieczytelny payload
    daje ``None``, co przełącza klienta na odnawianie po stałym czasie zamiast
    wysadzać żądanie.
    """
    try:
        payload = token.split(".")[1]
    except (AttributeError, IndexError):
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        decoded = json.loads(base64.urlsafe_b64decode(padded))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(decoded, dict):
        return None
    exp = decoded.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def is_uuid(value: str) -> bool:
    """Czy ``value`` ma kształt UUID-a (bez wnikania w wersję i wariant)?"""
    return bool(_UUID_RE.match(value)) if isinstance(value, str) else False


def require_uuid(value: str, what: str = "") -> str:
    """Zwróć ``value``, jeśli to UUID; w przeciwnym razie rzuć ``DSpaceError``.

    Sprawdzamy po swojej stronie, bo DSpace na niepoprawny UUID w ścieżce
    odpowiada **401 „Authentication is required"** (nie 400) — komunikat, po
    którym model zaczyna szukać sposobu na zalogowanie się, zamiast poprawić
    identyfikator. Patrz ``tests/fixtures/dspace10_401_malformed_uuid.json``.

    ``what`` doprecyzowuje, o który identyfikator chodzi (``"scope"``,
    ``"item"``…), żeby model wiedział, który argument poprawić.
    """
    if not is_uuid(value):
        label = f"{what} UUID" if what else "UUID"
        raise DSpaceError(f"'{value}' is not a valid {label}.")
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

    def __init__(
        self,
        config: Config,
        http: httpx.AsyncClient,
        anon_http: httpx.AsyncClient | None = None,
    ) -> None:
        self.config = config
        self.http = http
        # Tor anonimowy ma WŁASNEGO klienta, więc i własny cookie jar (A9):
        # przy logowaniu instancja sadza ciasteczka sesyjne z Path=/, a żądanie
        # „anonimowe" wysłane z nimi nie byłoby prawdziwie anonimowe — całe
        # porównanie w compare_access straciłoby wtedy sens.
        self._anon_http = (
            anon_http if anon_http is not None else self.build_http(config)
        )
        self._api_url = config.api_url
        self._probe: dict | None = None
        self._capabilities: dict | None = None

        self.auth_state = (
            AuthState.ANONYMOUS
            if not (config.username and config.password)
            else AuthState.NEEDS_DECISION
        )
        #: Powód, dla którego nie ma sesji — pokazywany użytkownikowi przez model.
        #: Stan początkowy przy podanym koncie jest celowo „zamknięty": dopóki
        #: logowanie się nie powiedzie, nie wolno po cichu odpytywać anonimowo,
        #: bo użytkownik jest przekonany, że widzi materiały swojego konta.
        self.auth_reason: str = (
            ""
            if self.auth_state is AuthState.ANONYMOUS
            else "the login has not run yet"
        )
        self._token: str | None = None
        self._refresh_at: float = 0.0
        self._last_login_at: float = 0.0
        self._offered_methods: list[str] = []
        # Logowanie jest single-flight (A8): token CSRF rotuje, a cookie jar jest
        # wspólny, więc dwa równoległe logowania unieważniałyby sobie żądania.
        self._login_lock = asyncio.Lock()

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

    def _error_for_status(
        self, status: int, where: str, *, anonymous: bool = False
    ) -> DSpaceError:
        """Zamień kod HTTP na komunikat, z którym model może coś zrobić.

        Treści ``message`` z ciała błędu DSpace świadomie nie przekazujemy —
        Spring Boot wpisuje tam bezużyteczne „An exception has occurred".

        Komunikat o braku dostępu zależy od **tożsamości tego żądania**, nie od
        globalnego stanu: inaczej anonimowy tor ``compare_access`` raportowałby
        się tak, jakby pytało konto.
        """
        as_account = not anonymous and self.auth_state is AuthState.AUTHENTICATED
        if status == 404:
            message = f"Not found: no such object at {where}. Check the UUID or handle."
        elif status in (401, 403) and as_account:
            message = (
                f"The repository refused access to that object for the account "
                f"this server is logged in as ({self.config.username})."
            )
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
        self,
        url: str,
        params: dict | None = None,
        *,
        where: str,
        anonymous: bool = False,
        _retried: bool = False,
    ) -> dict:
        """Jedno żądanie GET pod bezwzględny URL, z mapowaniem błędów."""
        headers = await self._auth_headers(anonymous)
        http = self._anon_http if anonymous else self.http
        try:
            response = await http.get(url, params=params, headers=headers)
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

        if response.status_code == 401 and self._may_retry(anonymous, _retried):
            await self._login(stale=self._token)
            return await self._request_json(
                url, params, where=where, anonymous=anonymous, _retried=True
            )

        if response.status_code == 401 and _retried:
            # Świeżo zalogowani, a wciąż 401: to anomalia sesji, nie zwykły brak
            # uprawnień — i nie udajemy, że wiemy, który to przypadek.
            raise DSpaceError(
                f"The repository keeps rejecting this session's token for {where}."
            )

        if response.status_code >= 400:
            raise self._error_for_status(
                response.status_code, where, anonymous=anonymous
            )

        # Błędom „odpowiedź przyszła, ale to nie nasze API" nadajemy status
        # odpowiedzi HTTP (2xx/3xx). Sonda odróżnia po nim sytuację „pod tym
        # adresem siedzi coś innego” (np. interfejs Angulara, który na /api
        # oddaje HTML) od zerwanego połączenia, gdzie status zostaje None.
        try:
            payload = response.json()
        except (json.JSONDecodeError, ValueError) as exc:
            error = DSpaceError(
                f"The repository returned a response that is not valid JSON "
                f"for {where}."
            )
            error.status = response.status_code
            raise error from exc
        if not isinstance(payload, dict):
            error = DSpaceError(
                f"The repository returned unexpected JSON (not an object) for {where}."
            )
            error.status = response.status_code
            raise error
        return payload

    def _may_retry(self, anonymous: bool, retried: bool) -> bool:
        """Czy wolno na 401 zalogować się ponownie i powtórzyć żądanie? (A4)

        Dokładnie raz, tylko dla żądań konta i tylko gdy sesja miała działać.
        Drugiego 401 po **udanym** ponownym logowaniu nie tłumaczymy brakiem
        uprawnień konta — nie zmierzyliśmy, czy DSpace odpowiada wtedy 401 czy
        403, a to API ma historię zaskakujących kodów (401 na zepsuty UUID).

        Świeżo zdobyty token wyklucza się jako przyczyna 401, więc wtedy nie
        logujemy się ponownie — to samo ograniczenie czasowe, co na ścieżce
        proaktywnej. Bez niego model iterujący po rekordach, na które instancja
        odpowiada 401 zamiast 403, wywoływałby pełne logowanie przy KAŻDYM z
        nich: dokładnie ten natrętny ruch, który kończy się banem IP.
        """
        return (
            not anonymous
            and not retried
            and self.auth_state is AuthState.AUTHENTICATED
            and time.time() - self._last_login_at >= TOKEN_MIN_LOGIN_INTERVAL
        )

    async def get(
        self, path: str, params: dict | None = None, *, anonymous: bool = False
    ) -> dict:
        """GET pod ścieżkę **względną wobec /api**, np. ``/core/items/{uuid}``."""
        return await self._request_json(
            self._url(path), params, where=path, anonymous=anonymous
        )

    # --- uwierzytelnianie ---------------------------------------------------

    async def authenticate(self) -> None:
        """Zaloguj się, jeśli podano konto. **Nie rzuca** (decyzja A3).

        Porażka logowania jest stanem do zaraportowania użytkownikowi, a nie
        wyjątkiem wywracającym start serwera: publiczne zasoby nadal są
        osiągalne, gdy tylko użytkownik świadomie się na to zgodzi.
        """
        if self.auth_state is AuthState.ANONYMOUS:
            return
        try:
            await self._login()
        except NeedsDecision:
            pass  # stan i powód ustawił już _login(); tu tylko nie wybuchamy
        except DSpaceError as exc:
            self._needs_decision(str(exc))

    def accept_anonymous(self) -> None:
        """Użytkownik świadomie zgodził się pracować na danych publicznych.

        Przejście działa wyłącznie z ``NEEDS_DECISION`` i tylko w jedną stronę;
        decyzja żyje do końca procesu i nie jest nigdzie zapisywana.
        """
        if self.auth_state is AuthState.NEEDS_DECISION:
            self.auth_state = AuthState.ANONYMOUS_BY_CHOICE
            self._token = None

    @property
    def offered_methods(self) -> list[str]:
        """Metody logowania ogłoszone przez instancję (puste = nie pytaliśmy)."""
        return list(self._offered_methods)

    def decision_question(self) -> str:
        """Pytanie do użytkownika, które model ma mu zadać (decyzja A3).

        Jedno źródło tej treści: ta sama funkcja obsługuje bramkę w
        ``server.py`` (stan zastany przed wejściem do narzędzia) i wyjątek
        ``NeedsDecision`` (logowanie padło w trakcie wywołania). Gdyby tekst żył
        w dwóch miejscach, to samo zdarzenie z czasem zaczęłoby brzmieć różnie
        zależnie od tego, którym torem trafiło do modelu.
        """
        return (
            f"Login as {self.config.username} at {self.config.base_url} failed: "
            f"{self.auth_reason}. Ask the user how to proceed: either correct the "
            f"username and password in this server's configuration and restart it, "
            f"or — if they agree to work with public data only — call "
            f"continue_anonymously."
        )

    def _needs_decision(self, reason: str) -> NeedsDecision:
        self.auth_state = AuthState.NEEDS_DECISION
        self.auth_reason = reason
        self._token = None
        return NeedsDecision(self.decision_question())

    async def _csrf_token(self) -> str | None:
        """Token CSRF z ``/authn/status``; przy okazji spisuje metody logowania.

        Pobierany bezpośrednio przed każdym logowaniem, bo **rotuje** — DSpace
        wystawia nowy w odpowiedzi na login.

        Nagłówek ``DSPACE-XSRF-TOKEN`` przychodzi **tylko wtedy, gdy serwer
        wystawia nowy token**; przy kolejnych żądaniach wartość żyje wyłącznie w
        ciasteczku (wzorzec double-submit). Dlatego ciasteczko jest tu pełnoprawnym
        źródłem, a nie awaryjnym: bez tego każde logowanie po pierwszym żądaniu
        leciałoby bez ``X-XSRF-TOKEN`` i dostawało 403. Zweryfikowane na żywej
        instancji — trzy kolejne ``/authn/status`` dały nagłówek tylko za pierwszym
        razem.
        """
        try:
            response = await self.http.get(f"{self._api_url}/authn/status")
        except httpx.HTTPError as exc:
            raise DSpaceError(
                f"Repository unreachable at {self.config.base_url}."
            ) from exc
        self._offered_methods = auth_methods(response.headers.get("www-authenticate"))
        return response.headers.get("dspace-xsrf-token") or self.http.cookies.get(
            "DSPACE-XSRF-COOKIE"
        )

    async def _login(self, *, stale: str | None = None) -> None:
        """Zdobądź token sesji. Jedyne miejsce w projekcie wysyłające POST.

        ``stale`` to token, który zdaniem wywołującego przestał działać (ścieżka
        reaktywna po 401). Gdy w międzyczasie ktoś inny zdobył nowy, kończymy bez
        drugiego logowania — o to chodzi w single-flight z A8.
        """
        async with self._login_lock:
            if (
                self._token is not None
                and self._token != stale
                and time.time() < self._refresh_at
            ):
                return

            csrf = await self._csrf_token()
            # Pytamy instancję, zamiast zakładać (A6): gdy nie ogłasza logowania
            # hasłem, wysyłanie go donikąd nie prowadzi, a powód jest konkretny.
            if self._offered_methods and "password" not in self._offered_methods:
                raise self._needs_decision(
                    "this instance does not offer password login; it offers: "
                    + ", ".join(self._offered_methods)
                )

            try:
                response = await self.http.post(
                    f"{self._api_url}/authn/login",
                    data={
                        "user": self.config.username,
                        "password": self.config.password,
                    },
                    headers={"X-XSRF-TOKEN": csrf} if csrf else None,
                    # Klient globalnie podąża za przekierowaniami (bez tego nie
                    # działa /pid/find ani pobieranie plików), ale httpx przy
                    # 307/308 powtarza metodę RAZEM Z CIAŁEM — hasło wyjechałoby
                    # wtedy pod adres wskazany przez serwer. Patrz decyzja A2.
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                raise DSpaceError(
                    f"Repository unreachable at {self.config.base_url}."
                ) from exc

            self._adopt_token(response)

    def _adopt_token(self, response: httpx.Response) -> None:
        """Wyciągnij token z odpowiedzi logowania albo powiedz, co poszło nie tak."""
        if response.is_redirect:
            raise self._needs_decision(
                "the repository redirected the login request; refusing to send "
                "credentials to another address"
            )
        if response.status_code == 401:
            raise self._needs_decision(
                "the repository rejected that username or password"
            )
        if response.status_code == 403:
            raise self._needs_decision(
                "the repository refused the login request (CSRF token rejected)"
            )
        if response.status_code >= 400:
            raise self._needs_decision(
                f"the repository returned HTTP {response.status_code} "
                "to the login request"
            )

        header = response.headers.get("authorization", "")
        token = header[len("Bearer ") :].strip() if header.startswith("Bearer ") else ""
        if not token:
            raise self._needs_decision(
                "the repository accepted the login but returned no token"
            )

        self._token = token
        self._last_login_at = time.time()
        expiry = token_expiry(token)
        if expiry is None:
            self._refresh_at = time.time() + TOKEN_BLIND_REFRESH
        else:
            lifetime = max(0.0, expiry - time.time())
            margin = max(TOKEN_REFRESH_MARGIN, lifetime * TOKEN_REFRESH_FRACTION)
            self._refresh_at = expiry - margin
        self.auth_state = AuthState.AUTHENTICATED
        self.auth_reason = ""

    async def _auth_headers(self, anonymous: bool) -> dict[str, str] | None:
        """Nagłówek ``Authorization`` dla tego żądania — jedyne takie miejsce.

        Token siedzi w polu klienta, a nie w nagłówkach współdzielonego
        ``httpx.AsyncClient`` (A9): inaczej jeździłby także na żądania toru
        anonimowego i na każdy absolutny URL spoza API.
        """
        if anonymous or self.auth_state is not AuthState.AUTHENTICATED:
            return None
        now = time.time()
        if (
            now >= self._refresh_at
            and now - self._last_login_at >= TOKEN_MIN_LOGIN_INTERVAL
        ):
            await self._login(stale=self._token)
        return {"Authorization": f"Bearer {self._token}"}

    # --- sonda startowa -----------------------------------------------------

    async def probe(self) -> dict:
        """Odpytaj korzeń API: nazwa, adresy i wersja instancji (z cache'em)."""
        if self._probe is not None:
            return self._probe

        try:
            payload = await self._request_json(self._api_url, where="/")
        except DSpaceError as exc:
            # Najczęstsza pomyłka konfiguracyjna: base_url bez „/server".
            # Ponawiamy, gdy serwer w ogóle odpowiedział, ale nie tym, czego
            # oczekujemy: 404, albo 2xx/3xx z treścią, która nie jest naszym
            # API (na gołym hoście DSpace serwuje interfejs Angulara, który
            # na /api oddaje HTML ze statusem 200/202 — sam 404 by tego nie
            # złapał). Zerwane połączenie ma status None i nie jest ponawiane,
            # bo drugie żądanie pod ten sam host tylko podwoi czas oczekiwania.
            if exc.status is None or (exc.status != 404 and exc.status >= 400):
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
        self,
        path: str,
        params: dict | None = None,
        *,
        key: str,
        anonymous: bool = False,
    ) -> tuple[list[dict], dict]:
        """Jedna strona: ``(elementy spod _embedded[key], koperta page)``.

        Kolejność kluczy w ``page`` bywa różna między instancjami, a endpoint
        faset nie podaje ``totalElements`` — czytamy po nazwach, nie po pozycji.
        """
        payload = await self.get(path, params, anonymous=anonymous)
        items, page, _links = self._envelope(payload, key)
        return items, page

    async def get_all(
        self,
        path: str,
        params: dict | None = None,
        *,
        key: str,
        limit: int,
        anonymous: bool = False,
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
            payload = await self._request_json(
                url, request_params, where=path, anonymous=anonymous
            )
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

    async def stream_bytes(
        self,
        url: str,
        *,
        max_bytes: int,
        anonymous: bool = False,
        _retried: bool = False,
    ) -> bytes:
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
        headers = await self._auth_headers(anonymous)
        http = self._anon_http if anonymous else self.http
        try:
            async with http.stream("GET", url, headers=headers) as response:
                if response.status_code == 401 and self._may_retry(anonymous, _retried):
                    await response.aread()
                    await self._login(stale=self._token)
                    return await self.stream_bytes(
                        url, max_bytes=max_bytes, anonymous=anonymous, _retried=True
                    )
                if response.status_code == 401 and _retried:
                    # Ta sama powściągliwość co w _request_json: po udanym
                    # ponownym logowaniu 401 jest anomalią sesji, a o
                    # uprawnieniach konta nic nie wiemy.
                    await response.aread()
                    raise DSpaceError(
                        f"The repository keeps rejecting this session's token "
                        f"for {url}."
                    )
                if response.status_code >= 400:
                    await response.aread()
                    raise self._error_for_status(
                        response.status_code, url, anonymous=anonymous
                    )
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
