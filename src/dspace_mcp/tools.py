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

from .client import DSpaceClient, DSpaceError, is_uuid
from .pdf import PdfError, extract_text
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

DOI_RE = re.compile(r"10\.\d{4,9}/\S+")


def _envelope(
    results: list[dict], total: int | None, truncated: bool
) -> dict[str, Any]:
    """Wspólna koperta odpowiedzi listowych (decyzja D4 — model musi wiedzieć,
    że nie widzi całości)."""
    return {"total": total, "truncated": truncated, "results": results}


def _require_uuid(value: str, what: str) -> str:
    """DSpace na niepoprawny UUID w ścieżce odpowiada 401 „Authentication is
    required” zamiast 400, co wysyła model na manowce logowania. Odsiewamy to
    zanim cokolwiek wyślemy."""
    if not is_uuid(value):
        raise DSpaceError(f"'{value}' is not a valid {what} UUID.")
    return value


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
    limit = max(0, min(limit, client.config.max_results))
    params: dict[str, Any] = {"dsoType": "item", "embed": "owningCollection"}

    if query:
        params["query"] = query
    if scope:
        params["scope"] = _require_uuid(scope, "scope")

    if year_from is not None or year_to is not None:
        lo = year_from if year_from is not None else "*"
        hi = year_to if year_to is not None else "*"
        params["f.dateIssued"] = f"[{lo} TO {hi}],equals"

    if author:
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

    ui_url = (await client.probe()).get("ui_url", "")
    results = [shape_item(hit, ui_url=ui_url) for hit in hits]
    truncated = total is not None and total > offset + len(results)
    return _envelope(results, total, truncated)


async def get_item(
    client: DSpaceClient, id: str, full_metadata: bool = False
) -> dict[str, Any]:
    """Pojedynczy rekord po UUID, handlu albo DOI."""
    identifier = id.strip()
    embed = "owningCollection,bundles/bitstreams"

    if is_uuid(identifier):
        raw = await client.get(f"/core/items/{identifier}", {"embed": embed})
    else:
        raw = await _resolve_identifier(client, identifier, embed)

    ui_url = (await client.probe()).get("ui_url", "")
    shaped = shape_item(raw, ui_url=ui_url, full=True if full_metadata else True)
    if not full_metadata:
        shaped.pop("metadata", None)
    shaped["files"] = _count_original_bitstreams(raw)
    return shaped


async def _resolve_identifier(
    client: DSpaceClient, identifier: str, embed: str
) -> dict[str, Any]:
    """Handle i DOI rozwiązujemy przez /pid/find (302 → item). DOI bywa jednak
    zapisane wyłącznie w metadanych, bez zarejestrowanego providera — wtedy
    zostaje wyszukiwanie."""
    lowered = identifier.lower()
    is_doi = lowered.startswith(("doi:", "10.", "https://doi.org/"))

    if is_doi:
        doi = identifier.split("doi.org/")[-1].removeprefix("doi:")
        try:
            return await client.get("/pid/find", {"id": f"doi:{doi}"})
        except DSpaceError:
            return await _find_by_doi_in_metadata(client, doi, embed)

    handle = identifier.removeprefix("hdl:")
    return await client.get("/pid/find", {"id": f"hdl:{handle}"})


async def _find_by_doi_in_metadata(
    client: DSpaceClient, doi: str, embed: str
) -> dict[str, Any]:
    payload = await client.get(
        "/discover/search/objects",
        {"query": f'"{doi}"', "dsoType": "item", "size": 5, "embed": embed},
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
    """Liczba plików w pakiecie ORIGINAL — o ile embed je przyniósł."""
    bundles = raw.get("_embedded", {}).get("bundles", {})
    entries = bundles.get("_embedded", {}).get("bundles") if bundles else None
    if entries is None:
        return None
    for bundle in entries:
        if bundle.get("name") == "ORIGINAL":
            inner = bundle.get("_embedded", {}).get("bitstreams", {})
            listed = inner.get("_embedded", {}).get("bitstreams")
            if listed is not None:
                return len(listed)
            page = inner.get("page", {})
            return page.get("totalElements")
    return 0


async def list_communities(
    client: DSpaceClient, parent: str | None = None, depth: int = 1
) -> dict[str, Any]:
    """Drzewo społeczności. Każdy poziom to osobne żądanie na każdą społeczność
    poziomu wyżej, więc `depth` ma twardy sufit."""
    depth = max(1, min(depth, 3))
    budget = client.config.max_results

    if parent:
        path = f"/core/communities/{_require_uuid(parent, 'community')}/subcommunities"
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
            child = await list_communities(client, node["uuid"], depth - 1)
            node["subcommunities"] = child["results"]
            budget -= len(child["results"])
            truncated = truncated or child["truncated"]

    return _envelope(results, total, truncated)


async def list_collections(
    client: DSpaceClient, community: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """Kolekcje — całego repozytorium albo jednej społeczności."""
    limit = min(limit, client.config.max_results)
    if community:
        uuid = _require_uuid(community, "community")
        path = f"/core/communities/{uuid}/collections"
    else:
        path = "/core/collections"

    items, total, truncated = await client.get_all(path, key="collections", limit=limit)
    return _envelope([shape_collection(c) for c in items], total, truncated)


async def list_bitstreams(
    client: DSpaceClient, item: str, bundle: str = "ORIGINAL"
) -> dict[str, Any]:
    """Pliki rekordu. Typ MIME leży w osobnym zasobie `format`, więc dociągamy
    go zagnieżdżonym embedem zamiast żądaniem na każdy plik."""
    uuid = _require_uuid(item, "item")
    bundles, _ = await client.get_page(
        f"/core/items/{uuid}/bundles",
        {"embed": "bitstreams/format"},
        key="bundles",
    )

    results: list[dict] = []
    for entry in bundles:
        if bundle and entry.get("name") != bundle:
            continue
        inner = entry.get("_embedded", {}).get("bitstreams", {})
        for raw in inner.get("_embedded", {}).get("bitstreams", []):
            fmt = raw.get("_embedded", {}).get("format", {})
            results.append(shape_bitstream(raw, mimetype=fmt.get("mimetype")))

    if not results and bundle:
        names = sorted({e.get("name", "?") for e in bundles})
        if names and bundle not in names:
            raise DSpaceError(
                f"This item has no '{bundle}' bundle. Available bundles: "
                f"{', '.join(names)}."
            )

    return _envelope(results, len(results), False)


async def get_bitstream_text(
    client: DSpaceClient, bitstream: str, max_chars: int = 20000
) -> dict[str, Any]:
    """Tekst z PDF-a. Rozmiar i typ bierzemy z metadanych, ale limit egzekwuje
    strumień — `sizeBytes` bywa niezgodne z rzeczywistością."""
    uuid = _require_uuid(bitstream, "bitstream")
    raw = await client.get(f"/core/bitstreams/{uuid}", {"embed": "format"})
    fmt = raw.get("_embedded", {}).get("format", {})
    mimetype = fmt.get("mimetype")
    url = link_href(raw, "content")
    size = raw.get("sizeBytes")
    limit_mb = client.config.pdf_max_mb

    if not url:
        raise DSpaceError("This bitstream has no downloadable content.")

    if mimetype and "pdf" not in mimetype.lower():
        raise DSpaceError(
            f"This file is {mimetype}, not a PDF, so no text can be extracted. "
            f"Give the user this link instead: {url}"
        )

    if size and size > client.config.pdf_max_bytes:
        mb = size / (1024 * 1024)
        raise DSpaceError(
            f"This file is {mb:.1f} MB, above the {limit_mb} MB limit. "
            f"Give the user this link instead: {url}"
        )

    data = await client.stream_bytes(url, max_bytes=client.config.pdf_max_bytes)
    try:
        extracted = extract_text(data, max_chars=max_chars)
    except PdfError as exc:
        raise DSpaceError(f"{exc} Link to the file: {url}") from exc

    return {
        "bitstream": uuid,
        "name": raw.get("name"),
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
    limit = min(limit, client.config.max_results)
    params: dict[str, Any] = {"size": limit}
    if scope:
        params["scope"] = _require_uuid(scope, "scope")
    if query:
        params["query"] = query
    if prefix:
        params["prefix"] = prefix

    try:
        payload = await client.get(f"/discover/facets/{facet}", params)
    except DSpaceError as exc:
        available = await _available_facets(client)
        if available:
            raise DSpaceError(
                f"This repository has no '{facet}' facet. Available facets: "
                f"{', '.join(available)}."
            ) from exc
        raise

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
    uuid = _require_uuid(item, "item")
    try:
        payload = await client.get(f"/statistics/usagereports/{uuid}_TotalVisits")
    except DSpaceError as exc:
        if "not publicly available" in str(exc).lower():
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
    }
