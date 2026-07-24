"""Adapter MCP: lifespan ze współdzielonym klientem HTTP i rejestracja narzędzi.

Cała logika mieszka w :mod:`dspace_mcp.tools`; tutaj są wyłącznie opisy
narzędzi (to, co czyta model, wybierając narzędzie) i sprowadzenie błędów do
zwięzłego ``{"error": ...}``.
"""

from __future__ import annotations

import functools
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from . import tools
from .client import DSpaceClient, DSpaceError
from .config import Config, parse_args


@dataclass
class AppContext:
    """Zawartość lifespan-context — jeden klient na cały proces."""

    client: DSpaceClient


def _client(ctx: Context) -> DSpaceClient:
    return ctx.request_context.lifespan_context.client


def _guard(fn: Callable) -> Callable:
    """Zamień wyjątki przeznaczone dla modelu na zwykłą odpowiedź.

    Model, który dostaje ślad stosu, ponawia w kółko; model, który dostaje
    zdanie po angielsku, zmienia zapytanie albo pyta użytkownika.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except DSpaceError as exc:
            return {"error": str(exc)}

    return wrapper


@_guard
async def search_items(
    ctx: Context,
    query: str | None = None,
    scope: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    author: str | None = None,
    sort: str | None = None,
    limit: int = 25,
    offset: int = 0,
) -> dict[str, Any]:
    """Search the repository for items (publications, theses, datasets...).

    Returns compact records plus `total`, the number of matching items in the
    whole repository. Set `limit=0` to get only that count, which is the cheap
    way to answer "how many items match X".

    Args:
        query: free-text query; omit to match everything.
        scope: UUID of a collection or community to search within.
        year_from: earliest year of issue (inclusive).
        year_to: latest year of issue (inclusive).
        author: author name; matched as a substring.
        sort: one of "relevance", "newest", "oldest", "title".
        limit: max records to return (0 = count only).
        offset: number of records to skip; use multiples of `limit`.
    """
    return await tools.search_items(
        _client(ctx), query, scope, year_from, year_to, author, sort, limit, offset
    )


@_guard
async def get_item(
    ctx: Context, id: str, full_metadata: bool = False
) -> dict[str, Any]:
    """Fetch one item by UUID, Handle or DOI.

    Accepts any of them, so pass whichever identifier you have: a UUID
    ("0f4a..."), a Handle ("123456789/4271" or "hdl:123456789/4271") or a DOI
    ("10.1234/abcd" or "https://doi.org/10.1234/abcd").

    Args:
        id: UUID, Handle or DOI of the item.
        full_metadata: also return every metadata field, unabridged.
    """
    return await tools.get_item(_client(ctx), id, full_metadata)


@_guard
async def list_communities(
    ctx: Context, parent: str | None = None, depth: int = 1
) -> dict[str, Any]:
    """List communities — the top level of the repository's structure.

    Args:
        parent: UUID of a community whose sub-communities you want; omit for
            the top-level ones.
        depth: how many levels of sub-communities to include (1-3).
    """
    return await tools.list_communities(_client(ctx), parent, depth)


@_guard
async def list_collections(
    ctx: Context, community: str | None = None, limit: int = 50
) -> dict[str, Any]:
    """List collections, either of one community or of the whole repository.

    Collections are where items live; use a collection UUID as the `scope`
    argument of search_items to search inside it.

    Args:
        community: UUID of a community; omit to list every collection.
        limit: max collections to return.
    """
    return await tools.list_collections(_client(ctx), community, limit)


@_guard
async def list_bitstreams(
    ctx: Context, item: str, bundle: str = "ORIGINAL"
) -> dict[str, Any]:
    """List the files attached to an item, with direct download URLs.

    This does not download anything: it returns names, sizes, MIME types,
    checksums and links you can hand to the user.

    Args:
        item: UUID of the item.
        bundle: which bundle to list; "ORIGINAL" holds the real files,
            "THUMBNAIL" and "LICENSE" hold generated ones.
    """
    return await tools.list_bitstreams(_client(ctx), item, bundle)


@_guard
async def get_bitstream_text(
    ctx: Context, bitstream: str, max_chars: int = 20000
) -> dict[str, Any]:
    """Extract the text of a document so you can read or summarise it.

    Supports PDF, Word (.docx, legacy .doc), OpenDocument (.odt, .ods, .odp)
    and Office Open XML (.pptx, .xlsx). The result reports which `format` was
    read and, where meaningful, how many pages, slides or sheets it processed.
    Scans without OCR, encrypted files, unsupported types and oversized files
    come back as a clear error with a download link.

    Args:
        bitstream: UUID of the bitstream (get it from list_bitstreams).
        max_chars: stop after this many characters.
    """
    return await tools.get_bitstream_text(_client(ctx), bitstream, max_chars)


@_guard
async def list_facet_values(
    ctx: Context,
    facet: str,
    scope: str | None = None,
    query: str | None = None,
    prefix: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """List the values of a search facet together with their counts.

    This is the cheap way to answer "which authors publish most here?" or
    "what subjects does this collection cover?" — the repository counts for
    you, so you never have to download the records. Call get_repository_info
    first to see which facets this instance offers; they are configurable and
    differ between repositories.

    Args:
        facet: facet name, e.g. "author", "subject", "dateIssued".
        scope: UUID of a collection or community to count within.
        query: restrict counting to items matching this query.
        prefix: only values starting with this prefix.
        limit: max values to return.
    """
    return await tools.list_facet_values(
        _client(ctx), facet, scope, query, prefix, limit
    )


@_guard
async def get_item_statistics(ctx: Context, item: str) -> dict[str, Any]:
    """Get the view count of an item.

    Most repositories expose this publicly; some switch it off, and then this
    returns a clear error instead of a number.

    Args:
        item: UUID of the item.
    """
    return await tools.get_item_statistics(_client(ctx), item)


@_guard
async def get_repository_info(ctx: Context) -> dict[str, Any]:
    """Describe the repository this server is connected to.

    Returns its name, URL, DSpace version, how many items, collections and
    communities it holds, and — importantly — which search filters, sort
    fields and facets it supports. Those are configurable per installation, so
    check here before using an exotic filter or facet name.
    """
    return await tools.get_repository_info(_client(ctx))


READ_TOOLS = (
    search_items,
    get_item,
    list_communities,
    list_collections,
    list_bitstreams,
    get_bitstream_text,
    list_facet_values,
    get_item_statistics,
    get_repository_info,
)


def build_server(config: Config) -> FastMCP:
    """Złóż serwer MCP dla podanej konfiguracji."""

    @asynccontextmanager
    async def lifespan(_: FastMCP) -> AsyncIterator[AppContext]:
        http = DSpaceClient.build_http(config)
        client = DSpaceClient(config, http)
        async with http:
            # Sonda przy starcie robi dwie rzeczy: mówi od razu, że adres jest
            # zły (zamiast pozwolić modelowi zderzyć się z tym w trakcie), i
            # koryguje brakujące „/server" zanim poleci pierwsze zapytanie.
            # Nie jest krytyczna — instancja z zablokowanym korzeniem API
            # nadal obsłuży wyszukiwanie, więc porażkę tylko sygnalizujemy.
            try:
                await client.probe()
            except DSpaceError as exc:
                print(f"dspace-mcp: startup check failed: {exc}", file=sys.stderr)
            yield AppContext(client=client)

    mcp = FastMCP("dspace-mcp", lifespan=lifespan)
    for fn in READ_TOOLS:
        mcp.tool()(fn)

    # Narzędzia zapisu nie istnieją (decyzja D1). Gdyby kiedyś powstały,
    # rejestrujemy je wyłącznie przy jawnym `config.enable_write` i podanym
    # koncie — sam kod na dysku nie ma wtedy prawa niczego zmienić.
    return mcp


def main(argv: list[str] | None = None) -> int:
    """Punkt wejścia CLI (``dspace-mcp``)."""
    try:
        config = parse_args(argv)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    build_server(config).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
