"""Testy kontraktowe wobec żywej instancji DSpace.

Wyłączone domyślnie (``addopts = -m 'not live'``), uruchamiane świadomie::

    uv run pytest -m live

Po co one są, skoro reszta testów działa na fixture'ach: fixture zamraża API z
dnia, w którym go pobrano, a te testy wychwycą zmianę kontraktu w nowej wersji
DSpace. Dlatego **nie mogą zależeć od konkretnych UUID-ów ani tytułów** —
instancja demo jest cyklicznie resetowana, a jej wersja to ruchomy SNAPSHOT.
Sprawdzamy kształt odpowiedzi, nie jej treść.
"""

from __future__ import annotations

import os

import pytest

from dspace_mcp import tools
from dspace_mcp.client import DSpaceClient
from dspace_mcp.config import Config

pytestmark = pytest.mark.live

BASE_URL = os.environ.get("DSPACE_TEST_URL", "https://demo.dspace.org/server")


@pytest.fixture
async def client():
    config = Config(base_url=BASE_URL, timeout=30)
    http = DSpaceClient.build_http(config)
    async with http:
        yield DSpaceClient(config, http)


async def test_base_url_without_server_suffix_is_corrected(client):
    """README obiecuje, że brak „/server" w adresie jest wykrywany. Gołe
    demo.dspace.org serwuje na /api interfejs Angulara (HTML, status 2xx),
    więc sam warunek na 404 tego nie łapał."""
    bare = BASE_URL.removesuffix("/server")
    if bare == BASE_URL:
        pytest.skip("DSPACE_TEST_URL nie kończy się na /server")

    config = Config(base_url=bare, timeout=30)
    http = DSpaceClient.build_http(config)
    async with http:
        corrected = DSpaceClient(config, http)
        info = await tools.get_repository_info(corrected)
        assert info["counts"]["items"] is not None
        assert corrected.api_url.endswith("/server/api")


async def test_handle_of_a_community_is_reported_not_faked(client):
    """/pid/find rozwiązuje każdy obiekt — społeczność nie może wrócić
    przebrana za publikację."""
    from dspace_mcp.client import DSpaceError

    communities = await tools.list_communities(client)
    handles = [c["handle"] for c in communities["results"] if c.get("handle")]
    if not handles:
        pytest.skip("brak społeczności z handlem na tej instancji")

    with pytest.raises(DSpaceError) as exc:
        await tools.get_item(client, handles[0])
    assert "community" in str(exc.value)


async def test_repository_info(client):
    info = await tools.get_repository_info(client)
    assert info["name"]
    assert info["version"].lower().startswith("dspace")
    assert info["counts"]["items"] is not None
    # D8: instancja musi umieć powiedzieć, o co wolno ją pytać.
    assert "author" in info["search_filters"]
    assert info["sort_fields"]


async def test_count_only_search_is_cheap(client):
    result = await tools.search_items(client, limit=0)
    assert result["results"] == []
    assert isinstance(result["total"], int)


async def test_search_and_fetch_roundtrip(client):
    found = await tools.search_items(client, limit=3, sort="newest")
    assert found["results"], "instancja demo powinna mieć jakiekolwiek rekordy"

    record = found["results"][0]
    assert set(record) == {
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

    by_uuid = await tools.get_item(client, record["uuid"])
    assert by_uuid["uuid"] == record["uuid"]

    if record["handle"]:
        # Ścieżka przez /pid/find, czyli 302 → wymaga follow_redirects.
        by_handle = await tools.get_item(client, record["handle"])
        assert by_handle["uuid"] == record["uuid"]
        # Kształt musi być identyczny niezależnie od użytego identyfikatora:
        # przekierowanie z /pid/find gubi `?embed=`, więc bez ponownego
        # pobrania po UUID `collection` i `files` byłyby tu puste.
        assert by_handle == by_uuid


async def test_full_metadata_is_lossless(client):
    found = await tools.search_items(client, limit=1)
    uuid = found["results"][0]["uuid"]
    full = await tools.get_item(client, uuid, full_metadata=True)
    assert full["metadata"], "tryb pełny musi zwrócić surowe pola DC"
    assert any(key.startswith("dc.") for key in full["metadata"])


async def test_structure_endpoints(client):
    communities = await tools.list_communities(client)
    assert communities["results"]
    collections = await tools.list_collections(client, limit=5)
    assert collections["results"]


async def test_facets_have_counts(client):
    values = await tools.list_facet_values(client, "author", limit=5)
    assert values["total"] is None  # endpoint faset nie podaje totalElements
    assert all(isinstance(v["count"], int) for v in values["results"])


async def test_statistics_are_public(client):
    """Domyślna konfiguracja DSpace 7+ udostępnia je anonimowo."""
    found = await tools.search_items(client, limit=1)
    stats = await tools.get_item_statistics(client, found["results"][0]["uuid"])
    assert stats["report_type"] == "TotalVisits"


async def test_unknown_facet_is_explained_not_crashed(client):
    from dspace_mcp.client import DSpaceError

    with pytest.raises(DSpaceError) as exc:
        await tools.list_facet_values(client, "definitely_not_a_facet")
    assert "available facets" in str(exc.value).lower()
