"""Testy logiki narzędzi na atrapie klienta.

`tools.py` odpowiada za to, *o co* pytamy DSpace i *jak* składamy odpowiedź —
nie za transport. Atrapa pozwala sprawdzić dokładnie te decyzje: jakie
parametry poleciały, co się stało przy braku danych, kiedy zapala się
`truncated`.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import fixture_json
from dspace_mcp import tools
from dspace_mcp.client import AuthState, DSpaceError
from dspace_mcp.config import Config

ITEM_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
RESTRICTED_UUID = "99999999-8888-7777-6666-555555555555"


class FakeClient:
    """Minimalna atrapa :class:`DSpaceClient` sterowana tablicą tras.

    Zapamiętuje każde wywołanie, żeby test mógł sprawdzić nie tylko wynik, ale
    i to, o co faktycznie zapytaliśmy — przy DSpace połowa błędów bierze się z
    parametrów zapytania, nie z parsowania odpowiedzi.
    """

    def __init__(
        self,
        routes: dict[str, Any] | None = None,
        *,
        pages: dict[str, tuple] | None = None,
        config: Config | None = None,
        anon_routes: dict[str, Any] | None = None,
        anon_pages: dict[str, tuple] | None = None,
        auth_state: AuthState = AuthState.ANONYMOUS,
    ) -> None:
        self.routes = routes or {}
        self.pages = pages or {}
        # Widok anonimowy (A9): domyślnie taki sam jak widok konta, bo większość
        # testów nie dotyczy różnicy w widoczności.
        self.anon_routes = self.routes if anon_routes is None else anon_routes
        self.anon_pages = self.pages if anon_pages is None else anon_pages
        self.auth_state = auth_state
        self.auth_reason = ""
        self.offered_methods: list[str] = []
        self.config = config or Config(base_url="https://repo.test/server")
        self.api_url = self.config.api_url
        self.calls: list[tuple[str, dict]] = []
        self.anon_calls: list[tuple[str, dict]] = []
        self.streamed: list[str] = []
        self.anon_streamed: list[str] = []
        self.stream_payload = b""
        self.caps = {"filters": ["author", "dateIssued"], "sorts": ["dc.title"]}

    async def probe(self) -> dict:
        return {
            "name": "Test Repo",
            "ui_url": "https://repo.test",
            "server_url": "https://repo.test/server",
            "version": "DSpace 10.1",
            "version_tuple": (10, 1),
        }

    async def capabilities(self) -> dict:
        return self.caps

    def _record(self, path: str, params: dict | None, anonymous: bool) -> None:
        (self.anon_calls if anonymous else self.calls).append((path, params or {}))

    async def get(
        self, path: str, params: dict | None = None, *, anonymous: bool = False
    ) -> dict:
        self._record(path, params, anonymous)
        routes = self.anon_routes if anonymous else self.routes
        if path not in routes:
            # Prawdziwy klient zawsze nadaje `status` (patrz _error_for_status),
            # a kod wywołujący na nim polega, żeby odróżnić brak dostępu od awarii.
            missing = DSpaceError(f"Not found: no such object at {path}.")
            missing.status = 404
            raise missing
        value = routes[path]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_page(
        self,
        path: str,
        params: dict | None = None,
        *,
        key: str,
        anonymous: bool = False,
    ) -> tuple[list[dict], dict]:
        self._record(path, params, anonymous)
        pages = self.anon_pages if anonymous else self.pages
        items, total = pages.get(path, ([], 0))
        return items, {"totalElements": total}

    async def get_all(
        self,
        path: str,
        params: dict | None = None,
        *,
        key: str,
        limit: int,
        anonymous: bool = False,
    ) -> tuple[list[dict], int | None, bool]:
        self._record(path, params, anonymous)
        pages = self.anon_pages if anonymous else self.pages
        items, total = pages.get(path, ([], 0))
        cut = items[:limit]
        return cut, total, len(cut) < (total or 0)

    async def stream_bytes(
        self, url: str, *, max_bytes: int, anonymous: bool = False
    ) -> bytes:
        (self.anon_streamed if anonymous else self.streamed).append(url)
        return self.stream_payload

    def params_for(self, path: str) -> dict:
        for called, params in self.calls:
            if called == path:
                return params
        raise AssertionError(f"Nie było wywołania {path}; były: {self.calls}")


def search_payload(hits: list[dict], total: int) -> dict:
    """Koperta wyszukiwania w kształcie, jaki naprawdę zwraca DSpace."""
    return {
        "_embedded": {
            "searchResult": {
                "_embedded": {
                    "objects": [{"_embedded": {"indexableObject": h}} for h in hits]
                },
                "page": {"size": len(hits), "totalElements": total, "number": 0},
            }
        }
    }


def error(message: str, *, status: int | None = None) -> DSpaceError:
    """Błąd taki, jaki zbudowałby klient — z kodem HTTP, po którym `tools.py`
    rozróżnia sytuacje (404 to co innego niż 429)."""
    exc = DSpaceError(message)
    exc.status = status
    return exc


def item_raw(uuid: str = ITEM_UUID, **extra: Any) -> dict:
    raw = {
        "uuid": uuid,
        "handle": "123456789/42",
        "type": "item",
        "metadata": {
            "dc.title": [{"value": "A study of things", "place": 0}],
            "dc.contributor.author": [{"value": "Kowalski, Jan", "place": 0}],
            "dc.date.issued": [{"value": "2025-03", "place": 0}],
        },
    }
    raw.update(extra)
    return raw


# --- search_items ---------------------------------------------------------


async def test_search_builds_year_range_filter():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    await tools.search_items(client, query="cancer", year_from=2020, year_to=2024)
    params = client.params_for("/discover/search/objects")
    assert params["f.dateIssued"] == "[2020 TO 2024],equals"
    assert params["query"] == "cancer"
    assert params["dsoType"] == "item"


async def test_search_open_ended_year_range():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    await tools.search_items(client, year_from=2020)
    assert client.params_for("/discover/search/objects")["f.dateIssued"] == (
        "[2020 TO *],equals"
    )


async def test_search_author_uses_contains_not_equals():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    await tools.search_items(client, author="Kowalski")
    assert client.params_for("/discover/search/objects")["f.author"] == (
        "Kowalski,contains"
    )


async def test_search_embeds_owning_collection_to_avoid_n_plus_one():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    await tools.search_items(client)
    assert client.params_for("/discover/search/objects")["embed"] == "owningCollection"


async def test_limit_zero_counts_without_returning_records():
    client = FakeClient({"/discover/search/objects": search_payload([item_raw()], 137)})
    result = await tools.search_items(client, query="x", limit=0)
    assert result["total"] == 137
    assert result["results"] == []
    # size=0 jest niekontraktowe (RestContract każe je odrzucać błędem 400).
    assert client.params_for("/discover/search/objects")["size"] == 1


async def test_search_limit_is_capped_by_config():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    client.config = Config(base_url="https://repo.test/server", max_results=10)
    await tools.search_items(client, limit=999)
    assert client.params_for("/discover/search/objects")["size"] == 10


async def test_search_reports_truncation():
    hits = [item_raw(), item_raw(OTHER_UUID)]
    client = FakeClient({"/discover/search/objects": search_payload(hits, 500)})
    result = await tools.search_items(client, limit=2)
    assert result["truncated"] is True
    assert len(result["results"]) == 2


async def test_search_sort_alias_is_translated():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    client.caps = {"filters": [], "sorts": ["dc.date.issued", "score"]}
    await tools.search_items(client, sort="newest")
    assert (
        client.params_for("/discover/search/objects")["sort"] == "dc.date.issued,DESC"
    )


async def test_search_rejects_sort_the_instance_lacks():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    client.caps = {"filters": [], "sorts": ["score"]}
    with pytest.raises(DSpaceError) as exc:
        await tools.search_items(client, sort="title")
    assert "score" in str(exc.value)


async def test_search_rejects_malformed_scope_without_calling_api():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    with pytest.raises(DSpaceError):
        await tools.search_items(client, scope="not-a-uuid")
    assert client.calls == []


async def test_search_shapes_records_from_real_fixture():
    payload = fixture_json("dspace10_search_objects.json")
    client = FakeClient({"/discover/search/objects": payload})
    result = await tools.search_items(client, query="cancer")
    assert result["results"], "fixture powinien zawierać rekordy"
    first = result["results"][0]
    assert set(first) == {
        "uuid",
        "handle",
        "url",
        "title",
        "authors",
        "year",
        "withdrawn",
        "discoverable",
        "in_archive",
        "date_issued",
        "type",
        "doi",
        "collection",
    }


# --- get_item -------------------------------------------------------------


async def test_get_item_by_uuid():
    client = FakeClient({f"/core/items/{ITEM_UUID}": item_raw()})
    result = await tools.get_item(client, ITEM_UUID)
    assert result["uuid"] == ITEM_UUID
    assert result["title"] == "A study of things"
    assert "metadata" not in result


async def test_get_item_full_metadata_includes_everything():
    client = FakeClient({f"/core/items/{ITEM_UUID}": item_raw()})
    result = await tools.get_item(client, ITEM_UUID, full_metadata=True)
    assert "metadata" in result
    assert result["metadata"]["dc.title"] == ["A study of things"]


def resolved_via_pid(routes: dict | None = None) -> FakeClient:
    """Klient, u którego /pid/find rozwiązuje handle/DOI na nasz rekord.

    Rozwiązanie identyfikatora to dopiero pierwszy krok — po nim narzędzie
    pobiera rekord po UUID, żeby dostać embed, którego przekierowanie nie
    przenosi. Stąd obie trasy w atrapie.
    """
    base = {
        "/pid/find": item_raw(),
        f"/core/items/{ITEM_UUID}": item_raw(),
    }
    base.update(routes or {})
    return FakeClient(base)


async def test_get_item_by_handle_uses_pid_find():
    client = resolved_via_pid()
    await tools.get_item(client, "123456789/42")
    assert client.params_for("/pid/find") == {"id": "hdl:123456789/42"}


async def test_get_item_strips_hdl_prefix():
    client = resolved_via_pid()
    await tools.get_item(client, "hdl:123456789/42")
    assert client.params_for("/pid/find") == {"id": "hdl:123456789/42"}


async def test_get_item_by_handle_refetches_with_embed():
    """Bez tego kroku ta sama publikacja miałaby inny kształt zależnie od
    tego, jakim identyfikatorem o nią zapytano (brak `collection` i `files`)."""
    client = resolved_via_pid()
    await tools.get_item(client, "123456789/42")
    assert client.params_for(f"/core/items/{ITEM_UUID}") == {"embed": tools.ITEM_EMBED}


@pytest.mark.parametrize(
    "identifier",
    [
        "10.1234/abcd",
        "doi:10.1234/abcd",
        "https://doi.org/10.1234/abcd",
        "http://doi.org/10.1234/abcd",
        "https://dx.doi.org/10.1234/abcd",
    ],
)
async def test_get_item_recognises_doi_forms(identifier):
    client = resolved_via_pid()
    await tools.get_item(client, identifier)
    assert client.params_for("/pid/find") == {"id": "doi:10.1234/abcd"}


async def test_get_item_rejects_handle_of_a_community():
    """/pid/find rozwiązuje każdy obiekt, więc bez kontroli typu model
    dostałby społeczność przebraną za publikację."""
    community = {"uuid": OTHER_UUID, "type": "community", "name": "Faculty of X"}
    client = FakeClient({"/pid/find": community})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_item(client, "10673/1251")
    assert "community" in str(exc.value)
    assert "list_communities" in str(exc.value)


async def test_get_item_by_doi_falls_back_to_search():
    """Na wielu instancjach DOI żyje wyłącznie w metadanych."""
    hit = item_raw()
    hit["metadata"]["dc.identifier.doi"] = [{"value": "10.1234/abcd", "place": 0}]
    client = FakeClient(
        {
            "/pid/find": error("Not found", status=404),
            "/discover/search/objects": search_payload([hit], 1),
            f"/core/items/{ITEM_UUID}": item_raw(),
        }
    )
    result = await tools.get_item(client, "https://doi.org/10.1234/abcd")
    assert result["uuid"] == ITEM_UUID


async def test_doi_fallback_only_on_not_found():
    """Przy 429 faseta… to znaczy DOI może istnieć — drugie żądanie tylko
    dorzuciłoby ruchu pod limiter i zamaskowało prawdziwą przyczynę."""
    client = FakeClient({"/pid/find": error("Rate limited", status=429)})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_item(client, "10.1234/abcd")
    assert "rate" in str(exc.value).lower()
    assert not any(call == "/discover/search/objects" for call, _ in client.calls)


async def test_get_item_by_doi_reports_when_nothing_matches():
    client = FakeClient(
        {
            "/pid/find": error("Not found", status=404),
            "/discover/search/objects": search_payload([], 0),
        }
    )
    with pytest.raises(DSpaceError) as exc:
        await tools.get_item(client, "10.9999/nope")
    assert "10.9999/nope" in str(exc.value)


async def test_get_item_counts_files_from_embedded_bundles():
    raw = item_raw(
        _embedded={
            "bundles": {
                "_embedded": {
                    "bundles": [
                        {
                            "name": "ORIGINAL",
                            "_embedded": {
                                "bitstreams": {
                                    "_embedded": {"bitstreams": [{}, {}, {}]}
                                }
                            },
                        }
                    ]
                }
            }
        }
    )
    client = FakeClient({f"/core/items/{ITEM_UUID}": raw})
    result = await tools.get_item(client, ITEM_UUID)
    assert result["files"] == 3


# --- list_communities / list_collections ----------------------------------


async def test_list_communities_top_level():
    client = FakeClient(
        pages={
            "/core/communities/search/top": (
                [{"uuid": OTHER_UUID, "name": "Faculty of X", "handle": "1/1"}],
                1,
            )
        }
    )
    result = await tools.list_communities(client)
    assert result["results"][0]["name"] == "Faculty of X"


async def test_list_communities_depth_is_capped():
    client = FakeClient(pages={"/core/communities/search/top": ([], 0)})
    await tools.list_communities(client, depth=99)
    # Sufit 3 poziomów: przy pustym wyniku i tak nie schodzimy głębiej,
    # ale wywołanie nie może wybuchnąć ani zapętlić się.
    assert client.calls


async def test_list_collections_of_community():
    client = FakeClient(
        pages={
            f"/core/communities/{OTHER_UUID}/collections": (
                [{"uuid": ITEM_UUID, "name": "Articles", "archivedItemsCount": 12}],
                1,
            )
        }
    )
    result = await tools.list_collections(client, community=OTHER_UUID)
    assert result["results"][0]["items_count"] == 12


async def test_list_collections_rejects_bad_uuid():
    client = FakeClient()
    with pytest.raises(DSpaceError):
        await tools.list_collections(client, community="nope")


# --- list_bitstreams ------------------------------------------------------


ORIGINAL_UUID = "bbbbbbbb-cccc-dddd-eeee-ffffffffffff"
THUMBNAIL_UUID = "cccccccc-dddd-eeee-ffff-111111111111"


def bundle_ref(name: str, uuid: str) -> dict:
    """Bundle tak, jak przychodzi z /core/items/{uuid}/bundles — bez `id`."""
    return {"uuid": uuid, "name": name, "type": "bundle"}


def bitstream_raw(
    name: str = "paper.pdf",
    mimetype: str = "application/pdf",
    uuid: str = OTHER_UUID,
) -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "sizeBytes": 14884,
        "checkSum": {"checkSumAlgorithm": "MD5", "value": "abc123"},
        "sequenceId": 2,
        "bundleName": "ORIGINAL",
        "_embedded": {"format": {"mimetype": mimetype}},
        "_links": {"content": {"href": "https://repo.test/server/api/x/content"}},
    }


async def test_list_bitstreams_returns_only_the_requested_bundle():
    client = FakeClient(
        pages={
            f"/core/items/{ITEM_UUID}/bundles": (
                [
                    bundle_ref("THUMBNAIL", THUMBNAIL_UUID),
                    bundle_ref("ORIGINAL", ORIGINAL_UUID),
                ],
                2,
            ),
            f"/core/bundles/{ORIGINAL_UUID}/bitstreams": ([bitstream_raw()], 1),
            f"/core/bundles/{THUMBNAIL_UUID}/bitstreams": (
                [bitstream_raw("thumb.jpg", "image/jpeg")],
                1,
            ),
        }
    )
    result = await tools.list_bitstreams(client, ITEM_UUID)
    assert len(result["results"]) == 1
    assert result["results"][0]["name"] == "paper.pdf"
    assert result["results"][0]["mimetype"] == "application/pdf"
    assert result["truncated"] is False


async def test_list_bitstreams_pages_instead_of_reading_embedded_list():
    """Osadzona lista bitstreamów ucina się na 20 pozycjach i nie niesie
    sygnału o obcięciu — dlatego pobieramy ją osobnym, stronicowanym
    żądaniem, a nie z embeda."""
    many = [bitstream_raw(f"file{i}.pdf") for i in range(30)]
    client = FakeClient(
        pages={
            f"/core/items/{ITEM_UUID}/bundles": (
                [bundle_ref("ORIGINAL", ORIGINAL_UUID)],
                1,
            ),
            f"/core/bundles/{ORIGINAL_UUID}/bitstreams": (many, 45),
        }
    )
    client.config = Config(base_url="https://repo.test/server", max_results=10)
    result = await tools.list_bitstreams(client, ITEM_UUID)
    assert len(result["results"]) == 10
    assert result["total"] == 45
    assert result["truncated"] is True


async def test_list_bitstreams_reports_missing_bundle():
    client = FakeClient(
        pages={
            f"/core/items/{ITEM_UUID}/bundles": (
                [bundle_ref("ORIGINAL", ORIGINAL_UUID)],
                1,
            )
        }
    )
    with pytest.raises(DSpaceError) as exc:
        await tools.list_bitstreams(client, ITEM_UUID, bundle="NOPE")
    assert "ORIGINAL" in str(exc.value)


# --- get_bitstream_text ---------------------------------------------------


async def test_get_bitstream_text_refuses_unmapped_mimetype():
    """Mimetype, dla którego nie ma ekstraktora: komunikat dispatchu, nie „not
    a PDF" — a link do pliku i tak ma trafić do modelu."""
    raw = bitstream_raw("data.csv", "text/csv")
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": raw})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID)
    assert "No text extractor" in str(exc.value)
    assert "text/csv" in str(exc.value)
    assert "content" in str(exc.value)  # link musi trafić do modelu


async def test_get_bitstream_text_falls_back_to_extension_for_generic_mimetype():
    """Instancja oddaje `application/octet-stream`, ale nazwa zdradza PDF."""
    from test_extractors import _one_page_pdf

    raw = bitstream_raw("report.pdf", "application/octet-stream")
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": raw})
    client.stream_payload = _one_page_pdf("Hello")
    result = await tools.get_bitstream_text(client, ITEM_UUID)
    assert result["format"] == "pdf"
    assert "Hello" in result["text"]


async def test_get_bitstream_text_refuses_oversized_file():
    raw = bitstream_raw()
    raw["sizeBytes"] = 999 * 1024 * 1024
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": raw})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID)
    assert "MB" in str(exc.value)
    assert client.streamed == []  # nie pobieramy ani bajta


async def test_get_bitstream_text_wraps_extract_error_with_download_link():
    """Uszkodzony/nie-PDF strumień: ExtractError z ekstraktora ma się zamienić
    w DSpaceError z linkiem do pliku, nie przeciekać jako surowy wyjątek."""
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": bitstream_raw()})
    client.stream_payload = b"not a pdf"
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID)
    assert "Link to the file" in str(exc.value)
    assert "https://repo.test/server/api/x/content" in str(exc.value)


# --- list_facet_values ----------------------------------------------------


async def test_facet_values_shape_and_truncation():
    client = FakeClient(
        {
            "/discover/facets/author": {
                "_embedded": {
                    "values": [
                        {"label": "Simmons, Cameron", "count": 190},
                        {"label": "Nowak, Anna", "count": 12, "authorityKey": "abc"},
                    ]
                },
                "_links": {"next": {"href": "https://repo.test/next"}},
            }
        }
    )
    result = await tools.list_facet_values(client, "author")
    assert result["total"] is None  # endpoint faset nie podaje totalElements
    assert result["truncated"] is True
    assert result["results"][0] == {
        "label": "Simmons, Cameron",
        "count": 190,
        "authority_key": None,
    }


async def test_unknown_facet_lists_available_ones():
    client = FakeClient(
        {
            "/discover/facets/itemtype": error("Bad request", status=400),
            "/discover/facets": {
                "_embedded": {"facets": [{"name": "author"}, {"name": "subject"}]}
            },
        }
    )
    with pytest.raises(DSpaceError) as exc:
        await tools.list_facet_values(client, "itemtype")
    assert "author" in str(exc.value) and "subject" in str(exc.value)


async def test_facet_transport_error_is_not_reported_as_missing_facet():
    """Wmowienie modelowi, ze istniejaca faseta nie istnieje, trwale wylacza
    poprawne narzedzie - a 429 nic o istnieniu fasety nie mowi."""
    client = FakeClient(
        {
            "/discover/facets/author": error("Rate limited", status=429),
            "/discover/facets": {"_embedded": {"facets": [{"name": "author"}]}},
        }
    )
    with pytest.raises(DSpaceError) as exc:
        await tools.list_facet_values(client, "author")
    assert "rate" in str(exc.value).lower()


# --- statistics / repository info -----------------------------------------


async def test_item_statistics_extracts_views():
    client = FakeClient(
        {
            f"/statistics/usagereports/{ITEM_UUID}_TotalVisits": {
                "points": [{"values": {"views": 3}}],
                "report-type": "TotalVisits",
            }
        }
    )
    result = await tools.get_item_statistics(client, ITEM_UUID)
    assert result["views"] == 3


async def test_item_statistics_explains_closed_instance():
    client = FakeClient(
        {
            f"/statistics/usagereports/{ITEM_UUID}_TotalVisits": error(
                "Not publicly available.", status=403
            )
        }
    )
    with pytest.raises(DSpaceError) as exc:
        await tools.get_item_statistics(client, ITEM_UUID)
    assert "statistics" in str(exc.value).lower()


async def test_repository_info_lists_capabilities():
    client = FakeClient(
        {
            "/discover/search/objects": search_payload([], 21),
            "/discover/facets": {"_embedded": {"facets": [{"name": "author"}]}},
        }
    )
    result = await tools.get_repository_info(client)
    assert result["name"] == "Test Repo"
    assert result["counts"]["items"] == 21
    assert result["search_filters"] == ["author", "dateIssued"]
    assert result["facets"] == ["author"]
    assert "newest" in result["sort_aliases"]


# --- regresje z koncowego review ------------------------------------------


async def test_search_rejects_offset_that_is_not_a_multiple_of_limit():
    """DSpace stronicuje numerami stron: offset=4 przy limit=3 po cichu dalby
    te sama strone co offset=3, czyli inne okno niz zamowione."""
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    with pytest.raises(DSpaceError) as exc:
        await tools.search_items(client, limit=3, offset=4)
    assert "multiple of limit" in str(exc.value)
    assert client.calls == []


async def test_search_offset_maps_to_page_number():
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    await tools.search_items(client, limit=10, offset=30)
    assert client.params_for("/discover/search/objects")["page"] == 3


async def test_search_rejects_author_filter_the_instance_lacks():
    """Decyzja D8: filtr spoza discovery.xml daje surowe 422, wiec mowimy
    modelowi wprost, czego ta instancja nie potrafi."""
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    client.caps = {"filters": ["title", "subject"], "sorts": []}
    with pytest.raises(DSpaceError) as exc:
        await tools.search_items(client, author="Kowalski")
    assert "author" in str(exc.value)
    assert "title" in str(exc.value)
    assert client.calls == []


async def test_search_allows_filters_when_capabilities_unknown():
    """Gdy instancja nie odpowiedziala na /discover/search, nie blokujemy
    zapytania - brak wiedzy to nie to samo co brak filtru."""
    client = FakeClient({"/discover/search/objects": search_payload([], 0)})
    client.caps = {"filters": [], "sorts": []}
    await tools.search_items(client, author="Kowalski", year_from=2020)
    params = client.params_for("/discover/search/objects")
    assert params["f.author"] == "Kowalski,contains"


async def test_get_bitstream_text_rejects_zero_max_chars():
    """max_chars pochodzi od modelu, wiec zly zakres to komunikat, a nie
    ValueError - i na pewno nie po pobraniu 20 MB."""
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": bitstream_raw()})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID, max_chars=0)
    assert "max_chars" in str(exc.value)
    assert client.streamed == []


async def test_count_of_files_uses_total_not_page_length():
    """Osadzona lista bitstreamow konczy sie na 20 pozycjach; prawda o liczbie
    plikow siedzi w page.totalElements."""
    raw = item_raw(
        _embedded={
            "bundles": {
                "_embedded": {
                    "bundles": [
                        {
                            "name": "ORIGINAL",
                            "_embedded": {
                                "bitstreams": {
                                    "_embedded": {"bitstreams": [{}] * 20},
                                    "page": {"size": 20, "totalElements": 45},
                                }
                            },
                        }
                    ]
                }
            }
        }
    )
    client = FakeClient({f"/core/items/{ITEM_UUID}": raw})
    result = await tools.get_item(client, ITEM_UUID)
    assert result["files"] == 45


async def test_community_tree_shares_one_global_budget():
    """Limit obowiazuje dla calego drzewa, nie osobno na kazdym poziomie."""
    top = [
        {"uuid": f"0000000{i}-0000-0000-0000-00000000000{i}", "name": f"C{i}"}
        for i in range(1, 4)
    ]
    children = [
        {"uuid": f"1000000{i}-0000-0000-0000-00000000000{i}", "name": f"S{i}"}
        for i in range(1, 4)
    ]
    pages = {"/core/communities/search/top": (top, 3)}
    for node in top:
        pages[f"/core/communities/{node['uuid']}/subcommunities"] = (children, 3)

    client = FakeClient(pages=pages)
    client.config = Config(base_url="https://repo.test/server", max_results=5)
    result = await tools.list_communities(client, depth=2)

    seen = len(result["results"]) + sum(
        len(node.get("subcommunities", [])) for node in result["results"]
    )
    assert seen <= 5
    assert result["truncated"] is True


# --- compare_access i raport stanu (A5, A9) ---------------------------------


def _two_bundles_with(files: list[dict]) -> dict[str, tuple]:
    return {
        f"/core/items/{ITEM_UUID}/bundles": (
            [bundle_ref("ORIGINAL", ORIGINAL_UUID)],
            1,
        ),
        f"/core/bundles/{ORIGINAL_UUID}/bitstreams": (files, len(files)),
    }


async def test_compare_access_names_the_files_only_the_account_can_see():
    """Sedno funkcji: „użytkownik twierdzi, że brakuje plików" — których?"""
    public = bitstream_raw("abstract.pdf", uuid=OTHER_UUID)
    restricted = bitstream_raw("full-text.pdf", uuid=RESTRICTED_UUID)
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=_two_bundles_with([public, restricted]),
        anon_routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        anon_pages=_two_bundles_with([public]),
        auth_state=AuthState.AUTHENTICATED,
    )

    result = await tools.compare_access(client, ITEM_UUID)

    assert result["visible_to_anonymous"] is True
    assert [f["name"] for f in result["files"]["authenticated_only"]] == [
        "full-text.pdf"
    ]
    assert [f["name"] for f in result["files"]["both"]] == ["abstract.pdf"]


async def test_compare_access_reports_an_item_the_public_cannot_see_at_all():
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=_two_bundles_with([bitstream_raw("thesis.pdf")]),
        anon_routes={},
        anon_pages={},
        auth_state=AuthState.AUTHENTICATED,
    )

    result = await tools.compare_access(client, ITEM_UUID)

    assert result["visible_to_anonymous"] is False
    assert [f["name"] for f in result["files"]["authenticated_only"]] == ["thesis.pdf"]
    assert result["files"]["both"] == []


async def test_compare_access_says_so_when_nothing_is_hidden():
    """„Wszystko widać publicznie" jest tak samo użyteczne jak lista braków."""
    same = _two_bundles_with([bitstream_raw("open.pdf")])
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=same,
        anon_pages=same,
        auth_state=AuthState.AUTHENTICATED,
    )

    result = await tools.compare_access(client, ITEM_UUID)

    assert result["files"]["authenticated_only"] == []
    assert "no files" in result["summary"].lower()


async def test_compare_access_asks_both_identities():
    """Bez pytania obiema tożsamościami porównanie nie ma o co się oprzeć."""
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=_two_bundles_with([bitstream_raw("a.pdf")]),
        auth_state=AuthState.AUTHENTICATED,
    )

    await tools.compare_access(client, ITEM_UUID)

    assert client.calls, "widok konta nie został odpytany"
    assert client.anon_calls, "widok anonimowy nie został odpytany"


async def test_get_repository_info_reports_anonymous_mode():
    client = FakeClient(routes={"/discover/search/objects": {}})
    info = await tools.get_repository_info(client)
    assert info["authentication"] == {"mode": "anonymous"}


async def test_get_repository_info_names_the_account_when_logged_in():
    config = Config(
        base_url="https://repo.test/server",
        username="reader@repo.test",
        password="s3kret",
    )
    client = FakeClient(
        routes={"/discover/search/objects": {}},
        config=config,
        auth_state=AuthState.AUTHENTICATED,
    )
    client.offered_methods = ["password", "orcid"]

    info = await tools.get_repository_info(client)

    assert info["authentication"]["mode"] == "authenticated"
    assert info["authentication"]["user"] == "reader@repo.test"
    assert info["authentication"]["methods_offered"] == ["password", "orcid"]


async def test_statistics_error_does_not_say_anonymously_when_logged_in():
    """D5 zaszyło tu słowo „anonymously"; po zalogowaniu jest ono nieprawdą."""
    denied = DSpaceError("nope")
    denied.status = 403
    config = Config(
        base_url="https://repo.test/server",
        username="reader@repo.test",
        password="s3kret",
    )
    client = FakeClient(
        routes={f"/statistics/usagereports/{ITEM_UUID}_TotalVisits": denied},
        config=config,
        auth_state=AuthState.AUTHENTICATED,
    )

    with pytest.raises(DSpaceError) as exc:
        await tools.get_item_statistics(client, ITEM_UUID)

    assert "anonymously" not in str(exc.value)
    assert "reader@repo.test" in str(exc.value)


async def test_statistics_error_still_says_anonymously_without_an_account():
    denied = DSpaceError("nope")
    denied.status = 403
    client = FakeClient(
        routes={f"/statistics/usagereports/{ITEM_UUID}_TotalVisits": denied}
    )

    with pytest.raises(DSpaceError) as exc:
        await tools.get_item_statistics(client, ITEM_UUID)

    assert "anonymously" in str(exc.value)


async def test_compare_access_refuses_when_not_actually_logged_in():
    """Po `continue_anonymously` „widok konta" jest w rzeczywistości anonimowy.

    Bramka w `_guard` blokuje tylko NEEDS_DECISION, a `_auth_headers` dokleja
    token wyłącznie w stanie AUTHENTICATED — więc bez tej odmowy narzędzie
    porównałoby anonima z anonimem i z pełnym przekonaniem zameldowało „nic nie
    jest ukryte". Fałszywe zapewnienie w narzędziu stworzonym dokładnie po to,
    żeby odpowiadać na pytanie „czy czegoś brakuje".
    """
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=_two_bundles_with([bitstream_raw("secret.pdf")]),
        auth_state=AuthState.ANONYMOUS_BY_CHOICE,
    )

    with pytest.raises(DSpaceError) as exc:
        await tools.compare_access(client, ITEM_UUID)

    assert "not logged in" in str(exc.value).lower()


async def test_compare_access_does_not_turn_a_timeout_into_a_permissions_verdict():
    """Timeout po stronie anonimowej to awaria, a nie dowód, że rekord jest ukryty."""
    timeout = DSpaceError("The repository did not respond in time; try narrowing.")
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=_two_bundles_with([bitstream_raw("a.pdf")]),
        anon_routes={f"/core/items/{ITEM_UUID}": timeout},
        auth_state=AuthState.AUTHENTICATED,
    )

    with pytest.raises(DSpaceError) as exc:
        await tools.compare_access(client, ITEM_UUID)

    assert "did not respond in time" in str(exc.value)


async def test_authentication_report_does_not_claim_a_failed_login_succeeded():
    """`tools.py` ma być poprawny bez warstwy MCP — nie może polegać na bramce."""
    config = Config(
        base_url="https://repo.test/server",
        username="reader@repo.test",
        password="s3kret",
    )
    client = FakeClient(
        routes={"/discover/search/objects": {}},
        config=config,
        auth_state=AuthState.NEEDS_DECISION,
    )
    client.auth_reason = "the repository rejected that username or password"

    info = await tools.get_repository_info(client)

    assert info["authentication"]["mode"] != "authenticated"
    assert "rejected" in info["authentication"]["reason"]


async def test_compare_access_works_when_the_item_has_no_original_bundle():
    """Zgłoszone z użycia: narzędzie zwierało się dokładnie w swoim przypadku.

    Rekord, którego pliki są niedostępne, często nie ma widocznego bundla
    ORIGINAL w ogóle. `list_bitstreams` rzuca wtedy „This item has no 'ORIGINAL'
    bundle", więc `compare_access` odmawiało odpowiedzi właśnie wtedy, gdy
    plików faktycznie brakuje — czyli w jedynej sytuacji, do której powstało.
    """
    thumb = bitstream_raw("cover.jpg", "image/jpeg", uuid=OTHER_UUID)
    thumb["bundleName"] = "THUMBNAIL"
    only_thumbnails = {
        f"/core/items/{ITEM_UUID}/bundles": (
            [bundle_ref("THUMBNAIL", THUMBNAIL_UUID)],
            1,
        ),
        f"/core/bundles/{THUMBNAIL_UUID}/bitstreams": ([thumb], 1),
    }
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages=only_thumbnails,
        anon_pages=only_thumbnails,
        auth_state=AuthState.AUTHENTICATED,
    )

    result = await tools.compare_access(client, ITEM_UUID)

    assert result["files"]["authenticated_only"] == []
    assert [f["name"] for f in result["files"]["both"]] == ["cover.jpg"]


async def test_compare_access_sees_past_the_original_bundle():
    """Anonim widzi tylko miniaturę, konto — prawdziwy plik. To jest ten wynik."""
    pdf = bitstream_raw("full-text.pdf", uuid=RESTRICTED_UUID)
    thumb = bitstream_raw("cover.jpg", "image/jpeg", uuid=OTHER_UUID)
    thumb["bundleName"] = "THUMBNAIL"
    bundles = [
        bundle_ref("ORIGINAL", ORIGINAL_UUID),
        bundle_ref("THUMBNAIL", THUMBNAIL_UUID),
    ]
    client = FakeClient(
        routes={f"/core/items/{ITEM_UUID}": {"uuid": ITEM_UUID}},
        pages={
            f"/core/items/{ITEM_UUID}/bundles": (bundles, 2),
            f"/core/bundles/{ORIGINAL_UUID}/bitstreams": ([pdf], 1),
            f"/core/bundles/{THUMBNAIL_UUID}/bitstreams": ([thumb], 1),
        },
        anon_pages={
            f"/core/items/{ITEM_UUID}/bundles": (
                [bundle_ref("THUMBNAIL", THUMBNAIL_UUID)],
                1,
            ),
            f"/core/bundles/{THUMBNAIL_UUID}/bitstreams": ([thumb], 1),
        },
        auth_state=AuthState.AUTHENTICATED,
    )

    result = await tools.compare_access(client, ITEM_UUID)

    assert [f["name"] for f in result["files"]["authenticated_only"]] == [
        "full-text.pdf"
    ]
    assert [f["name"] for f in result["files"]["both"]] == ["cover.jpg"]


# --- filtry discovery, bundle, widok anonimowy ------------------------------


async def test_search_items_passes_a_declared_filter_through():
    """D8 mówi, jakie filtry ma instancja — musi też dać się ich użyć."""
    client = FakeClient(routes={"/discover/search/objects": {}})
    client.caps = {"filters": ["author", "access_status"], "sorts": []}

    await tools.search_items(client, filters={"access_status": "restricted"})

    _, params = client.calls[-1]
    assert params["f.access_status"] == "restricted,equals"


async def test_search_items_keeps_an_explicit_operator():
    client = FakeClient(routes={"/discover/search/objects": {}})
    client.caps = {"filters": ["title"], "sorts": []}

    await tools.search_items(client, filters={"title": "cancer,contains"})

    _, params = client.calls[-1]
    assert params["f.title"] == "cancer,contains"


async def test_search_items_does_not_mistake_a_comma_in_the_value_for_an_operator():
    """„Kowalski, Jan" ma przecinek, ale to nie jest operator."""
    client = FakeClient(routes={"/discover/search/objects": {}})
    client.caps = {"filters": ["author"], "sorts": []}

    await tools.search_items(client, filters={"author": "Kowalski, Jan"})

    _, params = client.calls[-1]
    assert params["f.author"] == "Kowalski, Jan,equals"


async def test_search_items_rejects_a_filter_the_instance_does_not_have():
    """Nieznany filtr kończy się surowym 422 — lepiej powiedzieć to wprost."""
    client = FakeClient(routes={"/discover/search/objects": {}})
    client.caps = {"filters": ["author"], "sorts": []}

    with pytest.raises(DSpaceError) as exc:
        await tools.search_items(client, filters={"nosuchfilter": "x"})

    assert "nosuchfilter" in str(exc.value)
    assert "author" in str(exc.value)


async def test_list_bundles_names_what_the_item_actually_has():
    """Bez tego listę bundli poznaje się wyłącznie z komunikatu błędu."""
    client = FakeClient(
        pages={
            f"/core/items/{ITEM_UUID}/bundles": (
                [
                    bundle_ref("ORIGINAL", ORIGINAL_UUID),
                    bundle_ref("THUMBNAIL", THUMBNAIL_UUID),
                ],
                2,
            )
        }
    )

    result = await tools.list_bundles(client, ITEM_UUID)

    assert [b["name"] for b in result["results"]] == ["ORIGINAL", "THUMBNAIL"]
    assert result["results"][0]["uuid"] == ORIGINAL_UUID


async def test_read_tools_can_ask_anonymously_while_logged_in():
    """Bez tego jedyną drogą do widoku publicznego jest ominięcie serwera."""
    client = FakeClient(
        routes={"/discover/search/objects": {}},
        anon_routes={"/discover/search/objects": {}},
        auth_state=AuthState.AUTHENTICATED,
    )

    await tools.search_items(client, query="cancer", anonymous=True)

    assert client.anon_calls, "zapytanie nie poszło torem anonimowym"
    assert not client.calls, "zapytanie poszło tożsamością konta"
