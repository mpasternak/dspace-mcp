"""Testy asynchronicznego klienta HTTP do REST API DSpace.

Fixture'y w ``tests/fixtures/`` to surowe odpowiedzi z żywych instancji — tam,
gdzie test dotyczy KSZTAŁTU odpowiedzi, korzystamy z nich zamiast wymyślać
JSON-a. Tam, gdzie test dotyczy zachowania klienta (paginacja, limity), payload
budujemy syntetycznie, bo fixture'y linkują do prawdziwych hostów.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from conftest import fixture_json
from dspace_mcp import __version__
from dspace_mcp.client import DSpaceClient, DSpaceError, is_uuid, require_uuid
from dspace_mcp.config import Config

BASE = "https://repo.test/server"
API = "https://repo.test/server/api"

VALID_UUID = "4109f8db-ff30-4a46-9148-268b7fe18a17"


def make_config(**kwargs: Any) -> Config:
    params: dict[str, Any] = {"base_url": BASE}
    params.update(kwargs)
    return Config(**params)


def make_client(**kwargs: Any) -> DSpaceClient:
    config = make_config(**kwargs)
    return DSpaceClient(config, DSpaceClient.build_http(config))


def hal_page(
    key: str,
    items: list[dict],
    *,
    next_href: str | None = None,
    total: int | None = None,
    number: int = 0,
    size: int = 2,
) -> dict:
    """Syntetyczna koperta HAL — tylko te klucze, które klient ma czytać."""
    page: dict[str, Any] = {"size": size, "number": number}
    if total is not None:
        page["totalElements"] = total
        page["totalPages"] = -(-total // size)
    links: dict[str, Any] = {"self": {"href": f"{API}/x"}}
    if next_href:
        links["next"] = {"href": next_href}
    return {"_embedded": {key: items}, "page": page, "_links": links}


# --- is_uuid / require_uuid -------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "4109f8db-ff30-4a46-9148-268b7fe18a17",
        "5F116A15-D156-46CE-9EB8-D0C820EB6C05",
        "00000000-0000-0000-0000-000000000000",
    ],
)
def test_is_uuid_accepts_real_uuids(value: str) -> None:
    assert is_uuid(value) is True


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-a-uuid",
        "123456789/443",
        "4109f8db-ff30-4a46-9148",
        "4109f8db-ff30-4a46-9148-268b7fe18a17x",
        "4109f8dbff304a469148268b7fe18a17",
        "zzzzzzzz-ff30-4a46-9148-268b7fe18a17",
        " 4109f8db-ff30-4a46-9148-268b7fe18a17 ",
    ],
)
def test_is_uuid_rejects_junk(value: str) -> None:
    assert is_uuid(value) is False


def test_require_uuid_returns_value_when_valid() -> None:
    assert require_uuid(VALID_UUID) == VALID_UUID


@respx.mock
async def test_require_uuid_raises_without_sending_a_request() -> None:
    """DSpace na zły UUID w ścieżce odpowiada 401 (patrz fixture) — mylące dla
    modelu, więc odrzucamy taki identyfikator jeszcze przed wysłaniem."""
    route = respx.get(url__startswith=API).mock(return_value=httpx.Response(200))
    with pytest.raises(DSpaceError) as exc:
        require_uuid("not-a-uuid")
    assert str(exc.value) == "'not-a-uuid' is not a valid UUID."
    assert route.call_count == 0
    assert len(respx.calls) == 0


def test_401_fixture_documents_why_uuid_validation_exists() -> None:
    payload = fixture_json("dspace10_401_malformed_uuid")
    assert payload["status"] == 401
    assert payload["path"].endswith("/core/items/not-a-uuid")


# --- build_http -------------------------------------------------------------


def test_build_http_follows_redirects() -> None:
    """/api/pid/find odpowiada 302 — bez tego get_item po handlu zwraca pustkę."""
    http = DSpaceClient.build_http(make_config())
    assert http.follow_redirects is True


def test_build_http_uses_configured_timeout() -> None:
    http = DSpaceClient.build_http(make_config(timeout=3.5))
    assert http.timeout.connect == 3.5
    assert http.timeout.read == 3.5


def test_build_http_sets_identifying_user_agent() -> None:
    http = DSpaceClient.build_http(make_config())
    assert http.headers["User-Agent"] == (
        f"dspace-mcp/{__version__} (+https://github.com/mpasternak/dspace-mcp)"
    )
    assert http.headers["Accept"] == "application/json"


def test_build_http_never_sets_origin_header() -> None:
    """Z nagłówkiem Origin DSpace odrzuca nawet zwykłe GET-y błędem 403."""
    http = DSpaceClient.build_http(make_config())
    assert "origin" not in {name.lower() for name in http.headers}


@respx.mock
async def test_requests_carry_no_origin_header() -> None:
    route = respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_item"))
    )
    await make_client().get(f"/core/items/{VALID_UUID}")
    sent = route.calls[0].request
    assert "origin" not in {name.lower() for name in sent.headers}
    assert sent.headers["user-agent"].startswith("dspace-mcp/")


@respx.mock
async def test_client_follows_302_from_pid_find() -> None:
    """Zweryfikowane empirycznie: /pid/find zwraca 302 + Location na /core/items."""
    target = f"{API}/core/items/5f116a15-d156-46ce-9eb8-d0c820eb6c05"
    respx.get(f"{API}/pid/find").mock(
        return_value=httpx.Response(302, headers={"location": target})
    )
    respx.get(target).mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_item"))
    )
    payload = await make_client().get("/pid/find", {"id": "hdl:123456789/443"})
    assert payload["uuid"] == VALID_UUID
    assert len(respx.calls) == 2


# --- get(): sklejanie ścieżki i parametry -----------------------------------


@respx.mock
async def test_get_joins_path_with_api_url_and_passes_params() -> None:
    route = respx.get(f"{API}/discover/search/objects").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_search_objects"))
    )
    client = make_client()
    payload = await client.get(
        "/discover/search/objects", {"query": "cancer", "dsoType": "item"}
    )
    assert payload["query"] == "cancer"
    request = route.calls[0].request
    assert request.url.params["query"] == "cancer"
    assert request.url.params["dsoType"] == "item"


@respx.mock
async def test_get_accepts_path_without_leading_slash() -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(200, json={"uuid": VALID_UUID})
    )
    payload = await make_client().get(f"core/items/{VALID_UUID}")
    assert payload["uuid"] == VALID_UUID


# --- get(): mapowanie błędów ------------------------------------------------


@respx.mock
async def test_get_404_mentions_path_and_suggests_checking_the_id() -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(404, json=fixture_json("dspace10_404"))
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert str(exc.value) == (
        f"Not found: no such object at /core/items/{VALID_UUID}. "
        "Check the UUID or handle."
    )


@respx.mock
async def test_get_404_does_not_leak_spring_boot_message() -> None:
    """Spring zwraca bezużyteczne „An exception has occurred" — nie pokazujemy go."""
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(404, json=fixture_json("dspace10_404"))
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert "exception has occurred" not in str(exc.value)


@pytest.mark.parametrize("status", [401, 403])
@respx.mock
async def test_get_401_and_403_explain_anonymous_access(status: int) -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(status, json={"status": status})
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert str(exc.value) == (
        "Not publicly available: this server queries DSpace anonymously "
        "and has no access to that object."
    )


@respx.mock
async def test_get_422_points_at_repository_info() -> None:
    respx.get(f"{API}/discover/search/objects").mock(
        return_value=httpx.Response(422, json={"status": 422})
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get("/discover/search/objects", {"f.nosuch": "x,equals"})
    assert str(exc.value) == (
        "The repository rejected this query (422). It usually means an unknown "
        "search filter; call get_repository_info to see which filters this "
        "instance supports."
    )


@respx.mock
async def test_get_501_means_unsupported_identifier_type() -> None:
    respx.get(f"{API}/pid/find").mock(return_value=httpx.Response(501))
    with pytest.raises(DSpaceError) as exc:
        await make_client().get("/pid/find", {"id": "doi:10.1234/abcd"})
    assert str(exc.value) == "This repository cannot resolve identifiers of that type."


@pytest.mark.parametrize("status", [429, 503])
@respx.mock
async def test_get_429_and_503_ask_for_a_pause_and_never_retry(status: int) -> None:
    route = respx.get(f"{API}/discover/search/objects").mock(
        return_value=httpx.Response(status)
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get("/discover/search/objects")
    assert str(exc.value) == (
        "The repository is rate-limiting requests. Wait before retrying."
    )
    assert route.call_count == 1


@pytest.mark.parametrize("status", [400, 418, 500, 502])
@respx.mock
async def test_get_other_errors_report_the_status_code(status: int) -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(status)
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert str(status) in str(exc.value)
    assert f"/core/items/{VALID_UUID}" in str(exc.value)


@respx.mock
async def test_get_connect_error_names_the_base_url() -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        side_effect=httpx.ConnectError("nope")
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert str(exc.value) == f"Repository unreachable at {BASE}."


@respx.mock
async def test_get_timeout_suggests_narrowing_the_query() -> None:
    respx.get(f"{API}/discover/search/objects").mock(
        side_effect=httpx.ReadTimeout("slow")
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get("/discover/search/objects")
    assert str(exc.value) == (
        "The repository did not respond in time; try narrowing the query."
    )


@respx.mock
async def test_get_rejects_non_json_body() -> None:
    respx.get(f"{API}/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(200, text="<html>proxy error</html>")
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().get(f"/core/items/{VALID_UUID}")
    assert "JSON" in str(exc.value)


# --- probe ------------------------------------------------------------------


@respx.mock
async def test_probe_reads_name_urls_and_version() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_root"))
    )
    info = await make_client().probe()
    assert info == {
        "name": "DSpace Demo",
        "ui_url": "https://demo.dspace.org",
        "server_url": "https://demo.dspace.org/server",
        "version": "DSpace 10.1-SNAPSHOT",
        "version_tuple": (10, 1),
    }


@respx.mock
async def test_probe_handles_dspace7_root() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(200, json=fixture_json("dspace7_root"))
    )
    info = await make_client().probe()
    assert info["version"] == "DSpace 7.6.5"
    assert info["version_tuple"] == (7, 6)


@respx.mock
async def test_probe_retries_with_server_suffix_after_404() -> None:
    """Najczęstsza pomyłka konfiguracyjna: base_url bez „/server"."""
    config = Config(base_url="https://repo.test")
    client = DSpaceClient(config, DSpaceClient.build_http(config))
    bad = respx.get("https://repo.test/api").mock(return_value=httpx.Response(404))
    good = respx.get("https://repo.test/server/api").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_root"))
    )
    info = await client.probe()
    assert info["name"] == "DSpace Demo"
    assert bad.call_count == 1
    assert good.call_count == 1
    assert client.api_url == "https://repo.test/server/api"


@respx.mock
async def test_probe_corrected_api_url_is_used_by_later_requests() -> None:
    config = Config(base_url="https://repo.test")
    client = DSpaceClient(config, DSpaceClient.build_http(config))
    respx.get("https://repo.test/api").mock(return_value=httpx.Response(404))
    respx.get("https://repo.test/server/api").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_root"))
    )
    item = respx.get(f"https://repo.test/server/api/core/items/{VALID_UUID}").mock(
        return_value=httpx.Response(200, json={"uuid": VALID_UUID})
    )
    await client.probe()
    await client.get(f"/core/items/{VALID_UUID}")
    assert item.call_count == 1


@respx.mock
async def test_probe_reraises_when_the_retry_also_fails() -> None:
    config = Config(base_url="https://repo.test")
    client = DSpaceClient(config, DSpaceClient.build_http(config))
    respx.get("https://repo.test/api").mock(return_value=httpx.Response(404))
    respx.get("https://repo.test/server/api").mock(return_value=httpx.Response(404))
    with pytest.raises(DSpaceError):
        await client.probe()
    assert client.api_url == "https://repo.test/api"


@respx.mock
async def test_probe_result_is_cached() -> None:
    route = respx.get(API).mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_root"))
    )
    client = make_client()
    first = await client.probe()
    second = await client.probe()
    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_probe_tolerates_missing_version() -> None:
    respx.get(API).mock(
        return_value=httpx.Response(200, json={"dspaceName": "Repo", "type": "root"})
    )
    info = await make_client().probe()
    assert info["version"] is None
    assert info["version_tuple"] is None
    assert info["ui_url"] is None


# --- get_page ---------------------------------------------------------------


@respx.mock
async def test_get_page_returns_items_and_page_envelope() -> None:
    respx.get(f"{API}/core/communities/search/top").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_communities_top"))
    )
    items, page = await make_client().get_page(
        "/core/communities/search/top", {"size": 2}, key="communities"
    )
    assert len(items) == 2
    assert page["totalElements"] == 40
    assert page["number"] == 0


@respx.mock
async def test_get_page_without_embedded_returns_empty() -> None:
    respx.get(f"{API}/core/collections").mock(
        return_value=httpx.Response(
            200, json={"page": {"size": 20, "totalElements": 0}, "_links": {}}
        )
    )
    items, page = await make_client().get_page("/core/collections", key="collections")
    assert items == []
    assert page == {}


@respx.mock
async def test_get_page_reads_facet_envelope_without_total() -> None:
    respx.get(f"{API}/discover/facets/author").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_facets_author"))
    )
    items, page = await make_client().get_page(
        "/discover/facets/author", {"size": 3}, key="values"
    )
    assert [v["label"] for v in items][:1] == ["Simmons, Cameron"]
    assert "totalElements" not in page


@respx.mock
async def test_get_page_unwraps_search_result_envelope() -> None:
    """W /discover/search/objects koperta stron siedzi w _embedded.searchResult."""
    respx.get(f"{API}/discover/search/objects").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_search_objects"))
    )
    items, page = await make_client().get_page(
        "/discover/search/objects", {"query": "cancer"}, key="objects"
    )
    assert len(items) == 2
    assert page["totalElements"] == 21


# --- get_all ----------------------------------------------------------------


@respx.mock
async def test_get_all_follows_next_across_two_pages() -> None:
    page2 = f"{API}/core/collections?page=1&size=2"
    # Kolejność rejestracji ma znaczenie: respx dopasowuje parametry „zawiera się
    # w", więc trasa strony 2 (węższa) musi być sprawdzana pierwsza.
    respx.get(page2).mock(
        return_value=httpx.Response(
            200, json=hal_page("collections", [{"n": 3}], total=3, number=1)
        )
    )
    respx.get(f"{API}/core/collections", params={"size": "2"}).mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections", [{"n": 1}, {"n": 2}], next_href=page2, total=3
            ),
        )
    )
    items, total, truncated = await make_client().get_all(
        "/core/collections", {"size": 2}, key="collections", limit=10
    )
    assert [i["n"] for i in items] == [1, 2, 3]
    assert total == 3
    assert truncated is False
    assert len(respx.calls) == 2


@respx.mock
async def test_get_all_truncates_at_limit() -> None:
    page2 = f"{API}/core/collections?page=1&size=2"
    respx.get(page2).mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections",
                [{"n": 3}, {"n": 4}],
                next_href=f"{API}/core/collections?page=2&size=2",
                total=40,
                number=1,
            ),
        )
    )
    respx.get(f"{API}/core/collections", params={"size": "2"}).mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections", [{"n": 1}, {"n": 2}], next_href=page2, total=40
            ),
        )
    )
    items, total, truncated = await make_client().get_all(
        "/core/collections", {"size": 2}, key="collections", limit=3
    )
    assert [i["n"] for i in items] == [1, 2, 3]
    assert total == 40
    assert truncated is True


@respx.mock
async def test_get_all_respects_config_max_results() -> None:
    respx.get(f"{API}/core/collections").mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections",
                [{"n": i} for i in range(10)],
                next_href=f"{API}/core/collections?page=1",
                total=100,
                size=10,
            ),
        )
    )
    items, total, truncated = await make_client(max_results=4).get_all(
        "/core/collections", key="collections", limit=50
    )
    assert len(items) == 4
    assert total == 100
    assert truncated is True


@respx.mock
async def test_get_all_marks_truncated_when_total_exceeds_collected() -> None:
    """Serwer nie podał `next`, ale twierdzi, że rekordów jest więcej."""
    respx.get(f"{API}/core/collections").mock(
        return_value=httpx.Response(
            200, json=hal_page("collections", [{"n": 1}], total=7)
        )
    )
    items, total, truncated = await make_client().get_all(
        "/core/collections", key="collections", limit=10
    )
    assert len(items) == 1
    assert total == 7
    assert truncated is True


@respx.mock
async def test_get_all_on_facets_has_no_total() -> None:
    """Endpoint faset nie zwraca totalElements — jedynym sygnałem jest `next`."""
    respx.get(f"{API}/discover/facets/author").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_facets_author"))
    )
    items, total, truncated = await make_client().get_all(
        "/discover/facets/author", {"size": 3}, key="values", limit=3
    )
    assert len(items) == 3
    assert total is None
    assert truncated is True
    assert len(respx.calls) == 1


@respx.mock
async def test_get_all_stops_after_hard_request_ceiling() -> None:
    """Bezpiecznik: serwer w kółko podaje `next`, my i tak przerywamy."""
    route = respx.get(url__startswith=f"{API}/core/collections").mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections",
                [{"n": 1}],
                next_href=f"{API}/core/collections?page=99",
                total=None,
            ),
        )
    )
    items, total, truncated = await make_client(max_results=1000).get_all(
        "/core/collections", key="collections", limit=1000
    )
    assert route.call_count == 20
    assert len(items) == 20
    assert total is None
    assert truncated is True


@respx.mock
async def test_get_all_stops_on_empty_page() -> None:
    respx.get(f"{API}/core/collections").mock(
        return_value=httpx.Response(
            200,
            json=hal_page(
                "collections", [], next_href=f"{API}/core/collections?page=1", total=0
            ),
        )
    )
    items, total, truncated = await make_client().get_all(
        "/core/collections", key="collections", limit=10
    )
    assert items == []
    assert total == 0
    assert truncated is False
    assert len(respx.calls) == 1


# --- capabilities -----------------------------------------------------------

SEARCH_SUPPORT = {
    "filters": [
        {"filter": "title", "hasFacets": False, "type": "text"},
        {"filter": "author", "hasFacets": True, "type": "text"},
        {"filter": "dateIssued", "hasFacets": True, "type": "date"},
    ],
    "sortOptions": [
        {"name": "score", "actualName": "score", "sortOrder": "DESC"},
        {"name": "dc.title", "actualName": "dc.title_sort", "sortOrder": "ASC"},
    ],
    "type": "discover",
}


@respx.mock
async def test_capabilities_lists_filters_and_sorts() -> None:
    respx.get(f"{API}/discover/search").mock(
        return_value=httpx.Response(200, json=SEARCH_SUPPORT)
    )
    caps = await make_client().capabilities()
    assert caps == {
        "filters": ["title", "author", "dateIssued"],
        "sorts": ["score", "dc.title"],
    }


@respx.mock
async def test_capabilities_is_cached() -> None:
    route = respx.get(f"{API}/discover/search").mock(
        return_value=httpx.Response(200, json=SEARCH_SUPPORT)
    )
    client = make_client()
    first = await client.capabilities()
    second = await client.capabilities()
    assert first == second
    assert route.call_count == 1


@respx.mock
async def test_capabilities_falls_back_to_empty_on_error() -> None:
    respx.get(f"{API}/discover/search").mock(return_value=httpx.Response(500))
    caps = await make_client().capabilities()
    assert caps == {"filters": [], "sorts": []}


@respx.mock
async def test_capabilities_falls_back_when_repository_is_unreachable() -> None:
    respx.get(f"{API}/discover/search").mock(side_effect=httpx.ConnectError("nope"))
    caps = await make_client().capabilities()
    assert caps == {"filters": [], "sorts": []}


@respx.mock
async def test_capabilities_tolerates_missing_keys() -> None:
    respx.get(f"{API}/discover/search").mock(
        return_value=httpx.Response(200, json={"type": "discover"})
    )
    caps = await make_client().capabilities()
    assert caps == {"filters": [], "sorts": []}


# --- stream_bytes -----------------------------------------------------------

CONTENT_URL = f"{API}/core/bitstreams/{VALID_UUID}/content"
ONE_MB = 1024 * 1024


def chunked_response(chunks: list[bytes], counter: list[int]) -> httpx.Response:
    async def body():
        for chunk in chunks:
            counter[0] += 1
            yield chunk

    return httpx.Response(200, content=body())


@respx.mock
async def test_stream_bytes_returns_full_content() -> None:
    respx.get(CONTENT_URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))
    data = await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert data == b"%PDF-1.4"


@respx.mock
async def test_stream_bytes_works_without_content_length() -> None:
    """Transfer-Encoding: chunked — nagłówka Content-Length nie będzie."""
    counter = [0]
    respx.get(CONTENT_URL).mock(
        side_effect=lambda request: chunked_response([b"abc", b"def"], counter)
    )
    data = await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert data == b"abcdef"
    assert counter[0] == 2


@respx.mock
async def test_stream_bytes_aborts_when_stream_exceeds_limit() -> None:
    counter = [0]
    respx.get(CONTENT_URL).mock(
        side_effect=lambda request: chunked_response([b"x" * 512 * 1024] * 4, counter)
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert str(exc.value) == (
        f"File is larger than the 1 MB limit; "
        f"give the user this link instead: {CONTENT_URL}"
    )
    # przerwaliśmy w trakcie, nie po pobraniu całości
    assert counter[0] < 4


@respx.mock
async def test_stream_bytes_rejects_oversized_content_length_upfront() -> None:
    respx.get(CONTENT_URL).mock(
        return_value=httpx.Response(200, content=b"x" * (2 * ONE_MB))
    )
    with pytest.raises(DSpaceError) as exc:
        await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert "larger than the 1 MB limit" in str(exc.value)
    assert CONTENT_URL in str(exc.value)


@respx.mock
async def test_stream_bytes_follows_redirect_to_external_storage() -> None:
    """/content bywa przekierowaniem do S3 — stąd follow_redirects."""
    s3 = "https://s3.example.org/bucket/file.pdf?signature=abc"
    respx.get(CONTENT_URL).mock(
        return_value=httpx.Response(302, headers={"location": s3})
    )
    respx.get(s3).mock(return_value=httpx.Response(200, content=b"%PDF-1.7"))
    data = await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert data == b"%PDF-1.7"


@respx.mock
async def test_stream_bytes_maps_http_errors() -> None:
    respx.get(CONTENT_URL).mock(return_value=httpx.Response(403))
    with pytest.raises(DSpaceError) as exc:
        await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert "Not publicly available" in str(exc.value)


@respx.mock
async def test_stream_bytes_maps_timeout() -> None:
    respx.get(CONTENT_URL).mock(side_effect=httpx.ReadTimeout("slow"))
    with pytest.raises(DSpaceError) as exc:
        await make_client().stream_bytes(CONTENT_URL, max_bytes=ONE_MB)
    assert "did not respond in time" in str(exc.value)


# --- test architektoniczny --------------------------------------------------


@respx.mock
async def test_client_only_ever_sends_get() -> None:
    """Gwarancja bezpieczeństwa całego projektu: żadnej metody poza GET."""
    respx.get(API).mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_root"))
    )
    respx.get(f"{API}/discover/search").mock(
        return_value=httpx.Response(200, json=SEARCH_SUPPORT)
    )
    respx.get(f"{API}/core/communities/search/top").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_communities_top"))
    )
    respx.get(f"{API}/discover/facets/author").mock(
        return_value=httpx.Response(200, json=fixture_json("dspace10_facets_author"))
    )
    respx.get(CONTENT_URL).mock(return_value=httpx.Response(200, content=b"%PDF-1.4"))

    client = make_client()
    await client.probe()
    await client.capabilities()
    await client.get("/core/communities/search/top")
    await client.get_page("/core/communities/search/top", key="communities")
    await client.get_all("/discover/facets/author", key="values", limit=3)
    await client.stream_bytes(CONTENT_URL, max_bytes=ONE_MB)

    assert len(respx.calls) > 0
    assert {call.request.method for call in respx.calls} == {"GET"}
