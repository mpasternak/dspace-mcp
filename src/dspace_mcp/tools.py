"""Logika narzędzi MCP.

Ten moduł nie wie nic o MCP — każda funkcja przyjmuje :class:`DSpaceClient`
i zwraca zwykły słownik. Dzięki temu testuje się je bez uruchamiania serwera,
a `server.py` zostaje cienkim adapterem.

Wszystkie stringi zwracane na zewnątrz są po angielsku: to pakiet
międzynarodowy, a odbiorcą tekstu jest model językowy.
"""

from __future__ import annotations

import re
from typing import Any

from .client import AuthState, DSpaceClient, DSpaceError, is_uuid, require_uuid
from .extractors import ExtractError, dispatch
from .shaping import (
    link_href,
    metadata_value,
    search_hits,
    shape_bitstream,
    shape_collection,
    shape_community,
    shape_facet_value,
    shape_item,
)

# Aliasy sortowania: model dostaje słowa, DSpace chce nazw pól indeksu.
SORT_ALIASES = {
    "relevance": "score,DESC",
    "newest": "dc.date.issued,DESC",
    "oldest": "dc.date.issued,ASC",
    "title": "dc.title,ASC",
}

# Pełny rekord: kolekcja właścicielska i pliki jednym żądaniem.
ITEM_EMBED = "owningCollection,bundles/bitstreams"

# Maksymalne zagłębienie drzewa społeczności (każdy poziom to N żądań).
MAX_COMMUNITY_DEPTH = 3

_DOI_PREFIX_RE = re.compile(r"(?i)^\s*(?:https?://)?(?:dx\.)?doi\.org/|^\s*doi:\s*")


def _envelope(
    results: list[dict], total: int | None, truncated: bool
) -> dict[str, Any]:
    """Wspólna koperta odpowiedzi listowych (decyzja D4 — model musi wiedzieć,
    że nie widzi całości)."""
    return {"total": total, "truncated": truncated, "results": results}


async def _ui_url(client: DSpaceClient) -> str:
    """Adres interfejsu WWW do budowania linków dla użytkownika.

    Sonda bywa jedyną rzeczą, która na danej instancji nie działa (bywa, że
    korzeń ``/api`` jest zablokowany, a ``/discover`` nie) — brak linku nie
    jest powodem, żeby wywrócić całe wyszukiwanie.
    """
    try:
        return (await client.probe()).get("ui_url") or ""
    except DSpaceError:
        return ""


async def _require_filter(client: DSpaceClient, name: str, argument: str) -> None:
    """Sprawdź, czy instancja zna dany filtr wyszukiwania (decyzja D8).

    Zestaw filtrów pochodzi z ``discovery.xml`` i różni się między
    instalacjami; użycie nieznanego filtru kończy się surowym 422. Lepiej
    powiedzieć modelowi wprost, czego ta instancja nie potrafi.
    """
    filters = (await client.capabilities()).get("filters", [])
    if filters and name not in filters:
        raise DSpaceError(
            f"This repository has no '{name}' search filter, so the "
            f"'{argument}' argument cannot be used here. Available filters: "
            f"{', '.join(sorted(filters))}."
        )


async def search_items(
    client: DSpaceClient,
    query: str | None = None,
    scope: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    author: str | None = None,
    sort: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Wyszukiwanie rekordów przez /discover/search/objects."""
    if limit < 0:
        raise DSpaceError("limit must be zero or greater.")
    if offset < 0:
        raise DSpaceError("offset must be zero or greater.")
    limit = min(limit, client.config.max_results)

    # DSpace stronicuje numerami stron, nie przesunięciem. Offset niebędący
    # wielokrotnością limitu dałby po cichu inne okno wyników niż zamówione,
    # więc odmawiamy zamiast zwrócić coś prawie dobrego.
    if limit and offset % limit:
        raise DSpaceError(
            f"offset must be a multiple of limit ({limit}); got {offset}. "
            f"Page through results with offset 0, {limit}, {limit * 2}, …"
        )

    params: dict[str, Any] = {"dsoType": "item", "embed": "owningCollection"}

    if query:
        params["query"] = query
    if scope:
        params["scope"] = require_uuid(scope, "scope")

    if year_from is not None or year_to is not None:
        await _require_filter(client, "dateIssued", "year_from/year_to")
        lo = year_from if year_from is not None else "*"
        hi = year_to if year_to is not None else "*"
        params["f.dateIssued"] = f"[{lo} TO {hi}],equals"

    if author:
        await _require_filter(client, "author", "author")
        # `contains`, nie `equals` — model rzadko zna dokładną formę zapisu
        # nazwiska przyjętą w danym repozytorium.
        params["f.author"] = f"{author},contains"

    if sort:
        resolved = SORT_ALIASES.get(sort.lower(), sort)
        field = resolved.split(",")[0]
        available = (await client.capabilities()).get("sorts", [])
        if available and field not in available:
            raise DSpaceError(
                f"This repository cannot sort by '{sort}'. "
                f"Available sort fields: {', '.join(sorted(available))}. "
                f"You can also use the aliases: {', '.join(SORT_ALIASES)}."
            )
        params["sort"] = resolved

    # `limit=0` to interfejs „policz, nie pokazuj”. Wysyłamy size=1, bo
    # RestContract każe serwerom odrzucać size=0 błędem 400.
    params["size"] = 1 if limit == 0 else limit
    if limit:
        params["page"] = offset // limit

    payload = await client.get("/discover/search/objects", params)
    hits, page = search_hits(payload)
    total = page.get("totalElements")

    if limit == 0:
        return _envelope([], total, bool(total))

    ui_url = await _ui_url(client)
    results = [shape_item(hit, ui_url=ui_url) for hit in hits]
    truncated = total is not None and total > offset + len(results)
    return _envelope(results, total, truncated)


async def get_item(
    client: DSpaceClient, id: str, full_metadata: bool = False
) -> dict[str, Any]:
    """Pojedynczy rekord po UUID, handlu albo DOI."""
    identifier = id.strip()
    uuid = (
        identifier
        if is_uuid(identifier)
        else await _resolve_to_uuid(client, identifier)
    )

    # Zawsze pobieramy rekord po UUID, nawet gdy handle/DOI już go rozwiązały:
    # /pid/find odpowiada przekierowaniem, a przekierowanie gubi `?embed=`,
    # więc bez tego kroku ta sama publikacja miałaby inny kształt zależnie od
    # tego, jakim identyfikatorem o nią zapytano.
    raw = await client.get(f"/core/items/{uuid}", {"embed": ITEM_EMBED})

    shaped = shape_item(raw, ui_url=await _ui_url(client), full=True)
    if not full_metadata:
        # Tryb domyślny zwraca komplet pól opisowych, ale bez surowych
        # metadanych — te są dostępne na żądanie (decyzja D3).
        shaped.pop("metadata", None)
    shaped["files"] = _count_original_bitstreams(raw)
    return shaped


async def _resolve_to_uuid(client: DSpaceClient, identifier: str) -> str:
    """Zamień handle albo DOI na UUID rekordu."""
    lowered = identifier.lower()
    if lowered.startswith("10.") or lowered.startswith("doi:") or "doi.org/" in lowered:
        raw = await _resolve_doi(client, _DOI_PREFIX_RE.sub("", identifier).strip())
    else:
        handle = re.sub(r"(?i)^hdl:", "", identifier)
        raw = await client.get("/pid/find", {"id": f"hdl:{handle}"})

    kind = raw.get("type")
    if kind and kind != "item":
        raise DSpaceError(
            f"'{identifier}' points to a {kind}, not an item. "
            f"Use list_collections or list_communities to explore it, or "
            f"search_items with scope set to its UUID."
        )

    uuid = raw.get("uuid") or raw.get("id")
    if not uuid:
        raise DSpaceError(f"Could not resolve '{identifier}' to an item.")
    return str(uuid)


async def _resolve_doi(client: DSpaceClient, doi: str) -> dict[str, Any]:
    """DOI rozwiązujemy przez /pid/find, a gdy instancja go nie zna — przez
    wyszukiwanie: na wielu repozytoriach DOI żyje wyłącznie w metadanych,
    bez zarejestrowanego providera."""
    try:
        return await client.get("/pid/find", {"id": f"doi:{doi}"})
    except DSpaceError as exc:
        # Tylko „nie znaleziono" i „nie umiem takich identyfikatorów"
        # uzasadniają drugie podejście. Przy 429 czy timeoucie kolejne
        # żądanie tylko pogorszy sprawę i zamaskuje prawdziwą przyczynę.
        if exc.status not in (404, 501):
            raise

    payload = await client.get(
        "/discover/search/objects",
        {"query": f'"{doi}"', "dsoType": "item", "size": 5},
    )
    hits, _ = search_hits(payload)
    for hit in hits:
        found = metadata_value(hit.get("metadata", {}), "dc.identifier.doi")
        if found and found.lower().endswith(doi.lower()):
            return hit
    raise DSpaceError(
        f"No item found for DOI {doi}. This repository may not register DOIs, "
        f"or the DOI may belong to another repository."
    )


def _count_original_bitstreams(raw: dict) -> int | None:
    """Liczba plików w pakiecie ORIGINAL — o ile embed je przyniósł.

    Bierzemy ``page.totalElements`` osadzonej koperty, a nie długość listy:
    osadzone kolekcje są stronicowane (domyślnie 20 pozycji), więc rekord z
    45 plikami pokazałby ich 20.
    """
    bundles = raw.get("_embedded", {}).get("bundles", {})
    entries = bundles.get("_embedded", {}).get("bundles") if bundles else None
    if entries is None:
        return None
    for bundle in entries:
        if bundle.get("name") == "ORIGINAL":
            inner = bundle.get("_embedded", {}).get("bitstreams", {})
            total = inner.get("page", {}).get("totalElements")
            if total is not None:
                return total
            listed = inner.get("_embedded", {}).get("bitstreams")
            return len(listed) if listed is not None else None
    return 0


async def list_communities(
    client: DSpaceClient,
    parent: str | None = None,
    depth: int = 1,
    _budget: int | None = None,
) -> dict[str, Any]:
    """Drzewo społeczności. Każdy poziom to osobne żądanie na każdą społeczność
    poziomu wyżej, więc `depth` ma twardy sufit, a limit obowiązuje globalnie
    dla całego drzewa, nie osobno dla każdego poziomu."""
    depth = max(1, min(depth, MAX_COMMUNITY_DEPTH))
    budget = client.config.max_results if _budget is None else _budget
    if budget <= 0:
        return _envelope([], None, True)

    if parent:
        path = f"/core/communities/{require_uuid(parent, 'community')}/subcommunities"
    else:
        path = "/core/communities/search/top"

    items, total, truncated = await client.get_all(
        path, key="communities", limit=budget
    )
    results = [shape_community(c) for c in items]
    budget -= len(results)

    if depth > 1:
        for node in results:
            if budget <= 0:
                truncated = True
                break
            child = await list_communities(client, node["uuid"], depth - 1, budget)
            node["subcommunities"] = child["results"]
            budget -= len(child["results"])
            truncated = truncated or child["truncated"]

    return _envelope(results, total, truncated)


async def list_collections(
    client: DSpaceClient, community: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Kolekcje — całego repozytorium albo jednej społeczności."""
    limit = max(0, min(limit, client.config.max_results))
    if community:
        uuid = require_uuid(community, "community")
        path = f"/core/communities/{uuid}/collections"
    else:
        path = "/core/collections"

    items, total, truncated = await client.get_all(path, key="collections", limit=limit)
    return _envelope([shape_collection(c) for c in items], total, truncated)


async def list_bitstreams(
    client: DSpaceClient,
    item: str,
    bundle: str = "ORIGINAL",
    *,
    anonymous: bool = False,
) -> dict[str, Any]:
    """Pliki rekordu.

    Listę bitstreamów pobieramy osobnym, stronicowanym żądaniem zamiast czytać
    ją z osadzonej koperty: osadzone kolekcje ucinają się na 20 pozycjach i nie
    dałoby się uczciwie ustawić `truncated` (decyzja D4). Typ MIME leży w
    osobnym zasobie `format`, więc dociągamy go embedem.
    """
    uuid = require_uuid(item, "item")
    bundles, _ = await client.get_page(
        f"/core/items/{uuid}/bundles", key="bundles", anonymous=anonymous
    )

    names = sorted({entry.get("name", "?") for entry in bundles})
    selected = [e for e in bundles if not bundle or e.get("name") == bundle]
    if bundle and not selected:
        raise DSpaceError(
            f"This item has no '{bundle}' bundle. Available bundles: "
            f"{', '.join(names) or 'none'}."
        )

    results: list[dict] = []
    total = 0
    truncated = False
    budget = client.config.max_results

    for entry in selected:
        bundle_uuid = entry.get("uuid") or entry.get("id")
        if not bundle_uuid:
            continue
        items, bundle_total, bundle_truncated = await client.get_all(
            f"/core/bundles/{bundle_uuid}/bitstreams",
            {"embed": "format"},
            key="bitstreams",
            limit=max(budget, 0),
            anonymous=anonymous,
        )
        for raw in items:
            fmt = raw.get("_embedded", {}).get("format", {})
            results.append(shape_bitstream(raw, mimetype=fmt.get("mimetype")))
        total += bundle_total if bundle_total is not None else len(items)
        truncated = truncated or bundle_truncated
        budget -= len(items)
        if budget <= 0:
            truncated = truncated or total > len(results)
            break

    return _envelope(results, total, truncated)


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


async def list_facet_values(
    client: DSpaceClient,
    facet: str,
    scope: str | None = None,
    query: str | None = None,
    prefix: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Wartości fasety wraz z licznikami — tanie odpowiedzi na pytania „ile
    czego jest”, liczone po stronie Solr."""
    limit = max(1, min(limit, client.config.max_results))
    params: dict[str, Any] = {"size": limit}
    if scope:
        params["scope"] = require_uuid(scope, "scope")
    if query:
        params["query"] = query
    if prefix:
        params["prefix"] = prefix

    try:
        payload = await client.get(f"/discover/facets/{facet}", params)
    except DSpaceError as exc:
        # Tylko odpowiedzi znaczące „nie ma takiej fasety" zamieniamy na
        # podpowiedź. Przy 429 czy 500 faseta może istnieć, a wmówienie
        # modelowi, że jej nie ma, trwale wyłączy poprawne narzędzie.
        if exc.status not in (400, 404, 422):
            raise
        available = await _available_facets(client)
        if not available:
            raise
        raise DSpaceError(
            f"This repository has no '{facet}' facet. Available facets: "
            f"{', '.join(available)}."
        ) from exc

    values = payload.get("_embedded", {}).get("values", [])
    results = [shape_facet_value(v) for v in values]
    # Endpoint faset nie podaje totalElements — jedyny sygnał to obecność `next`.
    truncated = bool(link_href(payload, "next"))
    return _envelope(results, None, truncated)


async def _available_facets(client: DSpaceClient) -> list[str]:
    try:
        payload = await client.get("/discover/facets")
    except DSpaceError:
        return []
    facets = payload.get("_embedded", {}).get("facets", [])
    return [f.get("name", "?") for f in facets]


async def get_item_statistics(client: DSpaceClient, item: str) -> dict[str, Any]:
    """Statystyki wyświetleń. Domyślnie publiczne we wszystkich wersjach 7+,
    ale instancja może je zamknąć — wtedy mówimy to wprost."""
    uuid = require_uuid(item, "item")
    try:
        payload = await client.get(f"/statistics/usagereports/{uuid}_TotalVisits")
    except DSpaceError as exc:
        if exc.status in (401, 403):
            # Komunikat musi zależeć od tożsamości: po zalogowaniu słowo
            # „anonymously" jest po prostu nieprawdziwe i wysyła model (a przez
            # niego użytkownika) w stronę logowania, które już nastąpiło.
            if getattr(client, "auth_state", None) is AuthState.AUTHENTICATED:
                raise DSpaceError(
                    "This repository does not expose usage statistics to the "
                    f"account this server is logged in as ({client.config.username})."
                ) from exc
            raise DSpaceError(
                "This repository does not expose usage statistics anonymously."
            ) from exc
        raise

    points = payload.get("points", [])
    views = None
    if points:
        views = points[0].get("values", {}).get("views")
    return {
        "item": uuid,
        "views": views,
        "report_type": payload.get("report-type"),
    }


async def compare_access(client: DSpaceClient, item: str) -> dict[str, Any]:
    """Co z tego rekordu widzi konto, a czego nie widzi publiczność (decyzja A9).

    Zwraca **różnicę**, a nie dwa komplety danych: model dostaje gotową
    odpowiedź na pytanie „użytkownik twierdzi, że brakuje plików", zamiast
    porównywać na oko dwie długie listy.

    Widok anonimowy leci osobnym klientem HTTP, bez tokenu i bez ciasteczek
    sesji — inaczej „anonim" bywałby po cichu uwierzytelniony i całe porównanie
    nie znaczyłoby nic.
    """
    identifier = item.strip()
    uuid = (
        identifier
        if is_uuid(identifier)
        else await _resolve_to_uuid(client, identifier)
    )

    account = await list_bitstreams(client, uuid)

    visible_to_anonymous = True
    anonymous_files: list[dict] = []
    try:
        await client.get(f"/core/items/{uuid}", anonymous=True)
    except DSpaceError:
        visible_to_anonymous = False
    else:
        try:
            anonymous_files = (await list_bitstreams(client, uuid, anonymous=True))[
                "results"
            ]
        except DSpaceError:
            # Rekord publiczny, ale jego pliki już nie — to normalny wynik
            # porównania (embargo na pełny tekst), nie awaria.
            anonymous_files = []

    public_uuids = {entry.get("uuid") for entry in anonymous_files}
    both = [f for f in account["results"] if f.get("uuid") in public_uuids]
    restricted = [f for f in account["results"] if f.get("uuid") not in public_uuids]

    return {
        "item": uuid,
        "visible_to_anonymous": visible_to_anonymous,
        "files": {"both": both, "authenticated_only": restricted},
        "summary": _access_summary(
            len(account["results"]), len(restricted), visible_to_anonymous
        ),
    }


def _access_summary(total: int, restricted: int, item_public: bool) -> str:
    """Jedno zdanie, które model może powtórzyć użytkownikowi."""
    if not item_public:
        return (
            "The item itself is not visible to the public, so none of its "
            f"{total} file(s) can be reached anonymously."
        )
    if restricted == 0:
        return (
            f"All {total} file(s) are public: no files are hidden from anonymous users."
        )
    return f"{restricted} of {total} file(s) are not available to anonymous users."


def _authentication_report(client: DSpaceClient) -> dict[str, Any]:
    """Czyimi oczami patrzy serwer (decyzja A5).

    Buduje się wyłącznie z tego, co i tak mamy: nazwa konta pochodzi z
    konfiguracji, bo ``/authn/status`` podaje ``eperson`` samym linkiem, a
    dociąganie go kosztowałoby dodatkowe żądanie przy każdym starcie.
    """
    state = getattr(client, "auth_state", None)
    if state is None or state is AuthState.ANONYMOUS:
        return {"mode": "anonymous"}
    if state is AuthState.ANONYMOUS_BY_CHOICE:
        return {"mode": "anonymous_by_choice", "user": client.config.username}
    report: dict[str, Any] = {"mode": "authenticated", "user": client.config.username}
    offered = getattr(client, "offered_methods", [])
    if offered:
        report["methods_offered"] = offered
    return report


async def get_repository_info(client: DSpaceClient) -> dict[str, Any]:
    """Wizytówka instancji: czym jest, w jakiej wersji, ile ma rekordów i —
    najważniejsze — o co wolno ją pytać (decyzja D8)."""
    info = await client.probe()
    caps = await client.capabilities()

    counts: dict[str, int | None] = {}
    for dso, label in (
        ("item", "items"),
        ("collection", "collections"),
        ("community", "communities"),
    ):
        try:
            payload = await client.get(
                "/discover/search/objects", {"dsoType": dso, "size": 1}
            )
            _, page = search_hits(payload)
            counts[label] = page.get("totalElements")
        except DSpaceError:
            counts[label] = None

    return {
        "name": info.get("name"),
        "url": info.get("ui_url"),
        "api": client.api_url,
        "version": info.get("version"),
        "counts": counts,
        "search_filters": caps.get("filters", []),
        "sort_fields": caps.get("sorts", []),
        "sort_aliases": list(SORT_ALIASES),
        "facets": await _available_facets(client),
        "authentication": _authentication_report(client),
    }
