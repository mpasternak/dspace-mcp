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
from dspace_mcp.client import DSpaceError
from dspace_mcp.config import Config

ITEM_UUID = "11111111-2222-3333-4444-555555555555"
OTHER_UUID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


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
    ) -> None:
        self.routes = routes or {}
        self.pages = pages or {}
        self.config = config or Config(base_url="https://repo.test/server")
        self.api_url = self.config.api_url
        self.calls: list[tuple[str, dict]] = []
        self.streamed: list[str] = []
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

    async def get(self, path: str, params: dict | None = None) -> dict:
        self.calls.append((path, params or {}))
        if path not in self.routes:
            raise DSpaceError(f"Not found: no such object at {path}.")
        value = self.routes[path]
        if isinstance(value, Exception):
            raise value
        return value

    async def get_page(
        self, path: str, params: dict | None = None, *, key: str
    ) -> tuple[list[dict], dict]:
        self.calls.append((path, params or {}))
        items, total = self.pages.get(path, ([], 0))
        return items, {"totalElements": total}

    async def get_all(
        self, path: str, params: dict | None = None, *, key: str, limit: int
    ) -> tuple[list[dict], int | None, bool]:
        self.calls.append((path, params or {}))
        items, total = self.pages.get(path, ([], 0))
        cut = items[:limit]
        return cut, total, len(cut) < (total or 0)

    async def stream_bytes(self, url: str, *, max_bytes: int) -> bytes:
        self.streamed.append(url)
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


def bitstream_raw(name: str = "paper.pdf", mimetype: str = "application/pdf") -> dict:
    return {
        "uuid": OTHER_UUID,
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


async def test_get_bitstream_text_refuses_non_pdf():
    raw = bitstream_raw("data.csv", "text/csv")
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": raw})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID)
    assert "text/csv" in str(exc.value)
    assert "content" in str(exc.value)  # link musi trafić do modelu


async def test_get_bitstream_text_refuses_oversized_file():
    raw = bitstream_raw()
    raw["sizeBytes"] = 999 * 1024 * 1024
    client = FakeClient({f"/core/bitstreams/{ITEM_UUID}": raw})
    with pytest.raises(DSpaceError) as exc:
        await tools.get_bitstream_text(client, ITEM_UUID)
    assert "MB" in str(exc.value)
    assert client.streamed == []  # nie pobieramy ani bajta


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
