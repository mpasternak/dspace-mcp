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
from .client import AuthState, DSpaceClient, DSpaceError, NeedsDecision
from .config import Config, parse_args


@dataclass
class AppContext:
    """Zawartość lifespan-context — jeden klient na cały proces."""

    client: DSpaceClient


def _client(ctx: Context) -> DSpaceClient:
    return ctx.request_context.lifespan_context.client


def _decision_prompt(client: DSpaceClient) -> dict[str, Any]:
    """Pytanie do użytkownika, zadane jego własnymi ustami — przez model (A3).

    Proces stdio nie ma własnego kanału do człowieka, więc jedyną drogą jest
    model. Struktura jest **jedna** dla obu torów: bramki (stan zastany przed
    wejściem do narzędzia) i wyjątku ``NeedsDecision`` (logowanie padło w
    trakcie już trwającego wywołania). Inaczej to samo zdarzenie raz wyglądałoby
    jak pytanie, a raz jak zwykły błąd.
    """
    return {"needs_user_decision": True, "error": client.decision_question()}


def _guard(fn: Callable) -> Callable:
    """Zamień wyjątki przeznaczone dla modelu na zwykłą odpowiedź.

    Model, który dostaje ślad stosu, ponawia w kółko; model, który dostaje
    zdanie po angielsku, zmienia zapytanie albo pyta użytkownika.

    Tu też mieszka bramka z A3: gdy podano konto, a logowanie padło, narzędzie
    nie rusza sieci — bo cichy odczyt anonimowy zwróciłby „nie ma takiego
    rekordu" na materiały, do których użytkownik ma pełny dostęp.
    """

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        ctx = kwargs.get("ctx") or (args[0] if args else None)
        client = _client(ctx) if ctx is not None else None
        if client is not None and client.auth_state is AuthState.NEEDS_DECISION:
            return _decision_prompt(client)
        try:
            return await fn(*args, **kwargs)
        except NeedsDecision:
            return (
                _decision_prompt(client)
                if client is not None
                else {"error": "login failed"}
            )
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
    filters: dict[str, str] | None = None,
    as_anonymous: bool = False,
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
        filters: any other discovery filter this instance supports, as
            {name: value} — call get_repository_info for the list. Append an
            operator if you need one other than the default `equals`, e.g.
            {"access_status": "restricted", "title": "cancer,contains"}. Note
            that `query` is full-text search and does NOT reach these filters.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.search_items(
        _client(ctx),
        query,
        scope,
        year_from,
        year_to,
        author,
        sort,
        limit,
        offset,
        filters,
        as_anonymous,
    )


@_guard
async def get_item(
    ctx: Context, id: str, full_metadata: bool = False, as_anonymous: bool = False
) -> dict[str, Any]:
    """Fetch one item by UUID, Handle or DOI.

    Accepts any of them, so pass whichever identifier you have: a UUID
    ("0f4a..."), a Handle ("123456789/4271" or "hdl:123456789/4271") or a DOI
    ("10.1234/abcd" or "https://doi.org/10.1234/abcd").

    Args:
        id: UUID, Handle or DOI of the item.
        full_metadata: also return every metadata field, unabridged.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.get_item(_client(ctx), id, full_metadata, anonymous=as_anonymous)


@_guard
async def list_communities(
    ctx: Context,
    parent: str | None = None,
    depth: int = 1,
    as_anonymous: bool = False,
) -> dict[str, Any]:
    """List communities — the top level of the repository's structure.

    Args:
        parent: UUID of a community whose sub-communities you want; omit for
            the top-level ones.
        depth: how many levels of sub-communities to include (1-3).
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.list_communities(
        _client(ctx), parent, depth, anonymous=as_anonymous
    )


@_guard
async def list_collections(
    ctx: Context,
    community: str | None = None,
    limit: int = 50,
    as_anonymous: bool = False,
) -> dict[str, Any]:
    """List collections, either of one community or of the whole repository.

    Collections are where items live; use a collection UUID as the `scope`
    argument of search_items to search inside it.

    Args:
        community: UUID of a community; omit to list every collection.
        limit: max collections to return.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.list_collections(
        _client(ctx), community, limit, anonymous=as_anonymous
    )


@_guard
async def list_bitstreams(
    ctx: Context, item: str, bundle: str = "ORIGINAL", as_anonymous: bool = False
) -> dict[str, Any]:
    """List the files attached to an item, with direct download URLs.

    This does not download anything: it returns names, sizes, MIME types,
    checksums and links you can hand to the user.

    Args:
        item: UUID of the item.
        bundle: which bundle to list; "ORIGINAL" holds the real files,
            "THUMBNAIL" and "LICENSE" hold generated ones. Pass an empty string
            to list every bundle, or call list_bundles to see which exist.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.list_bitstreams(
        _client(ctx), item, bundle, anonymous=as_anonymous
    )


@_guard
async def list_bundles(
    ctx: Context, item: str, as_anonymous: bool = False
) -> dict[str, Any]:
    """List an item's bundles — the named groups its files are filed under.

    Most repositories put the real files in "ORIGINAL" and generated previews in
    "THUMBNAIL", but the set differs per item. Call this when list_bitstreams
    reports the bundle you asked for does not exist, or when you want to know
    what an item actually holds before listing files.

    Args:
        item: UUID of the item.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.list_bundles(_client(ctx), item, anonymous=as_anonymous)


@_guard
async def get_bitstream_text(
    ctx: Context, bitstream: str, max_chars: int = 20000, as_anonymous: bool = False
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
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.get_bitstream_text(
        _client(ctx), bitstream, max_chars, anonymous=as_anonymous
    )


@_guard
async def list_facet_values(
    ctx: Context,
    facet: str,
    scope: str | None = None,
    query: str | None = None,
    prefix: str | None = None,
    limit: int = 25,
    as_anonymous: bool = False,
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
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.list_facet_values(
        _client(ctx), facet, scope, query, prefix, limit, anonymous=as_anonymous
    )


@_guard
async def get_item_statistics(
    ctx: Context, item: str, as_anonymous: bool = False
) -> dict[str, Any]:
    """Get the view count of an item.

    Most repositories expose this publicly; some switch it off, and then this
    returns a clear error instead of a number.

    Args:
        item: UUID of the item.
        as_anonymous: ask as the anonymous public instead of the logged-in
            account — use it to see what a visitor would get.
    """
    return await tools.get_item_statistics(_client(ctx), item, anonymous=as_anonymous)


@_guard
async def get_repository_info(ctx: Context) -> dict[str, Any]:
    """Describe the repository this server is connected to.

    Returns its name, URL, DSpace version, how many items, collections and
    communities it holds, and — importantly — which search filters, sort
    fields and facets it supports. Those are configurable per installation, so
    check here before using an exotic filter or facet name.
    """
    return await tools.get_repository_info(_client(ctx))


async def continue_anonymously(ctx: Context) -> dict[str, Any]:
    """Give up on logging in and work with publicly available data only.

    Call this ONLY after the user has explicitly said they want to continue
    without an account. If they would rather fix the credentials, they need to
    correct this server's configuration and restart it — nothing you can do
    from here.

    After this, restricted items and files stay invisible for the rest of the
    session, exactly as if no account had been configured.
    """
    client = _client(ctx)
    client.accept_anonymous()
    # Komunikat musi opisywać stan, w którym faktycznie jesteśmy. Przejście
    # działa tylko z NEEDS_DECISION, więc wywołane w innym stanie jest no-opem —
    # stała odpowiedź „pracuję na danych publicznych" przeczyłaby wtedy polu
    # `mode` w tym samym słowniku.
    messages = {
        AuthState.ANONYMOUS_BY_CHOICE: (
            "Working with publicly available data only. Restricted items and "
            "files will not appear in any result."
        ),
        AuthState.AUTHENTICATED: (
            "Nothing changed: this server is logged in and working normally."
        ),
        AuthState.ANONYMOUS: (
            "Nothing changed: no account is configured, so this server was "
            "already working with publicly available data only."
        ),
    }
    return {
        "mode": client.auth_state.value,
        "message": messages.get(client.auth_state, ""),
    }


@_guard
async def compare_access(ctx: Context, item: str) -> dict[str, Any]:
    """Compare what this account can see against what the public can see.

    Answers "the user says files are missing": it lists the item's files as the
    logged-in account, checks the same item anonymously, and returns the
    difference — telling you which files are not publicly available.

    Args:
        item: UUID, Handle or DOI of the item.
    """
    return await tools.compare_access(_client(ctx), item)


READ_TOOLS = (
    search_items,
    get_item,
    list_communities,
    list_collections,
    list_bundles,
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
        # Drugi klient to tor anonimowy (A9) — osobny, żeby miał własny cookie
        # jar: ciasteczka sesyjne z logowania uczyniłyby „anonima" nieanonimowym.
        anon_http = DSpaceClient.build_http(config)
        client = DSpaceClient(config, http, anon_http)
        async with http, anon_http:
            # Sonda przy starcie robi dwie rzeczy: mówi od razu, że adres jest
            # zły (zamiast pozwolić modelowi zderzyć się z tym w trakcie), i
            # koryguje brakujące „/server" zanim poleci pierwsze zapytanie.
            # Nie jest krytyczna — instancja z zablokowanym korzeniem API
            # nadal obsłuży wyszukiwanie, więc porażkę tylko sygnalizujemy.
            try:
                await client.probe()
            except DSpaceError as exc:
                print(f"dspace-mcp: startup check failed: {exc}", file=sys.stderr)
            # DOPIERO teraz logowanie: sonda mogła właśnie skorygować adres API
            # o brakujące „/server", a POST musi trafić pod poprawiony adres.
            await client.authenticate()
            if client.auth_state is AuthState.NEEDS_DECISION:
                print(
                    f"dspace-mcp: login as {config.username} failed: "
                    f"{client.auth_reason}",
                    file=sys.stderr,
                )
            yield AppContext(client=client)

    mcp = FastMCP("dspace-mcp", lifespan=lifespan)
    for fn in READ_TOOLS:
        mcp.tool()(fn)

    # Narzędzia zależne od stanu (D7 pkt 4): instalacja anonimowa nie widzi ani
    # jednego bajta ich opisów, więc nie płaci za nie tokenami w każdej rozmowie.
    if config.username and config.password:
        mcp.tool()(continue_anonymously)
        mcp.tool()(compare_access)

    # Narzędzia zapisu nie istnieją (decyzja D1/A1) i podanie konta ich nie
    # włącza: uwierzytelnianie poszerza zakres ODCZYTU, nic więcej.
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
