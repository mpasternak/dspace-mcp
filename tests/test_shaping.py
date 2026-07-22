"""Testy czystych funkcji spłaszczających z ``dspace_mcp.shaping``.

Wszystkie asercje opierają się na SUROWYCH fixture'ach z żywych instancji
DSpace (7.6.5, 10.1-SNAPSHOT itd.) — patrz ``tests/fixtures/README.md``.
Wymyślony JSON pojawia się wyłącznie tam, gdzie sprawdzamy odporność na
niekompletne dane, których żywy serwer akurat nie przysłał.
"""

from __future__ import annotations

import pytest

from conftest import fixture_json
from dspace_mcp.shaping import (
    flatten_metadata,
    link_href,
    metadata_value,
    metadata_values,
    parse_version,
    parse_year,
    search_hits,
    shape_bitstream,
    shape_collection,
    shape_community,
    shape_facet_value,
    shape_item,
)

# --- pomocnicze wyciągacze fixture'ów -------------------------------------


def _first_collection() -> dict:
    """Pierwsza kolekcja z listy kolekcji społeczności (DSpace 10)."""
    payload = fixture_json("dspace10_community_collections")
    return payload["_embedded"]["collections"][0]


def _first_community() -> dict:
    payload = fixture_json("dspace10_communities_top")
    return payload["_embedded"]["communities"][0]


def _first_bitstream() -> dict:
    payload = fixture_json("dspace10_bitstreams")
    return payload["_embedded"]["bitstreams"][0]


def _dspace10_hits() -> list[dict]:
    hits, _page = search_hits(fixture_json("dspace10_search_objects"))
    return hits


def _dspace7_hits() -> list[dict]:
    hits, _page = search_hits(fixture_json("dspace7_search_objects"))
    return hits


# --- link_href ------------------------------------------------------------


def test_link_href_zwykla_relacja_obiektowa():
    item = fixture_json("dspace10_item")
    assert link_href(item, "self") == (
        "https://demo.dspace.org/server/api/core/items/"
        "4109f8db-ff30-4a46-9148-268b7fe18a17"
    )
    assert link_href(item, "bundles").endswith("/bundles")


def test_link_href_relacja_bedaca_lista():
    # workflowGroups w kolekcji to LISTA trzech wpisów - naiwne
    # links[rel]["href"] wywala sie tu TypeError-em.
    collection = _first_collection()
    assert isinstance(collection["_links"]["workflowGroups"], list)
    href = link_href(collection, "workflowGroups")
    assert href.endswith("/workflowGroups/reviewer")


def test_link_href_lista_takze_w_zagniezdzonej_kolekcji_itemu():
    item = fixture_json("dspace10_item")
    owning = item["_embedded"]["owningCollection"]
    assert link_href(owning, "workflowGroups").endswith("/workflowGroups/reviewer")


def test_link_href_bitstream_content():
    bitstream = _first_bitstream()
    assert link_href(bitstream, "content").endswith(
        "/core/bitstreams/45382064-a29a-402f-bb1b-5304f5031f30/content"
    )


@pytest.mark.parametrize(
    "obj",
    [
        {},
        {"_links": {}},
        {"_links": None},
        {"_links": []},
        {"_links": {"self": None}},
        {"_links": {"self": {}}},
        {"_links": {"self": []}},
        {"_links": {"self": "https://example.org"}},
        {"_links": {"self": [{"name": "brak href"}]}},
        None,
        [],
    ],
)
def test_link_href_braki_daja_none(obj):
    assert link_href(obj, "self") is None


def test_link_href_nieznana_relacja():
    assert link_href(fixture_json("dspace10_item"), "nie-ma-takiej") is None


# --- metadata_values / metadata_value -------------------------------------


def test_metadata_values_autorzy_w_kolejnosci_place():
    md = _dspace7_hits()[0]["metadata"]
    assert metadata_values(md, "dc.contributor.author") == [
        "Cassim, Shemana",
        "Chepulis, Lynne Merran",
        "Keenan, Rawiri",
        "Kidd, Jacquie",
        "Firth, Melissa",
        "Lawrenson, Ross",
    ]


def test_metadata_values_sortuje_po_place_a_nie_po_kolejnosci_w_json():
    # Odwracamy fizyczna kolejnosc wpisow - wynik ma zalezec od "place".
    md = {
        "dc.contributor.author": list(
            reversed(_dspace7_hits()[0]["metadata"]["dc.contributor.author"])
        )
    }
    assert metadata_values(md, "dc.contributor.author")[0] == "Cassim, Shemana"


def test_metadata_values_brak_place_na_koniec():
    md = {
        "dc.subject": [
            {"value": "bez place"},
            {"value": "drugi", "place": 1},
            {"value": "pierwszy", "place": 0},
        ]
    }
    assert metadata_values(md, "dc.subject") == ["pierwszy", "drugi", "bez place"]


def test_metadata_values_place_ujemny_z_fixture():
    # relation.*.latestForDiscovery ma place == -1 (realna wartosc z DSpace 10).
    md = _dspace10_hits()[0]["metadata"]
    assert metadata_values(
        md, "relation.isProjectOfPublication.latestForDiscovery"
    ) == ["ba2bbf71-4300-4f2f-8f65-b53e4bb6def4"]


def test_metadata_values_pomija_puste_wartosci():
    md = {
        "dc.title": [
            {"value": "", "place": 0},
            {"value": None, "place": 1},
            {"place": 2},
            {"value": "cos", "place": 3},
        ]
    }
    assert metadata_values(md, "dc.title") == ["cos"]


@pytest.mark.parametrize(
    "md",
    [
        {},
        None,
        [],
        {"dc.title": None},
        {"dc.title": []},
        {"dc.title": "napis"},
        {"dc.title": [None, 5]},
    ],
)
def test_metadata_values_odporne_na_smieci(md):
    assert metadata_values(md, "dc.title") == []


def test_metadata_values_legacy_plaska_lista_nie_jest_obslugiwana():
    # Format {key, value} to API DSpace 5/6 - spec zabrania go normalizowac.
    legacy = [{"key": "dc.title", "value": "Cos"}]
    assert metadata_values(legacy, "dc.title") == []


def test_metadata_value_zwraca_pierwsza_wartosc():
    md = _dspace7_hits()[0]["metadata"]
    assert metadata_value(md, "dc.contributor.author") == "Cassim, Shemana"
    assert metadata_value(md, "dc.type") == "Journal Article"


def test_metadata_value_brak_klucza():
    assert metadata_value(fixture_json("dspace10_item")["metadata"], "dc.type") is None
    assert metadata_value({}, "dc.title") is None


# --- flatten_metadata -----------------------------------------------------


def test_flatten_metadata_z_realnego_itemu():
    md = fixture_json("dspace10_item")["metadata"]
    flat = flatten_metadata(md)
    assert flat["dc.title"] == ["Test PhD Thesis"]
    assert flat["creativework.editor"] == ["Test, Phil"]
    assert flat["dspace.entity.type"] == ["Journal"]
    assert set(flat) == set(md)
    assert all(isinstance(v, list) for v in flat.values())


def test_flatten_metadata_sortuje_kazdy_klucz_po_place():
    md = _dspace10_hits()[0]["metadata"]
    flat = flatten_metadata(md)
    assert flat["dc.contributor.author"] == [
        "Guinot, Anna",
        "Oeztuerk-Winder, Feride",
        "Ventura, Juan-Jose",
    ]


@pytest.mark.parametrize("md", [{}, None, [], "napis"])
def test_flatten_metadata_odporne_na_smieci(md):
    assert flatten_metadata(md) == {}


# --- parse_year -----------------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2025", 2025),
        ("2025-03", 2025),
        ("2025-03-17", 2025),
        ("2019-01-08", 2019),
        ("2016-01", 2016),
        ("2026-07-22T11:41:41Z", 2026),
        ("04/05/16", None),  # realna wartosc z demo.dspace.org
        ("", None),
        (None, None),
        ("brak daty", None),
        ("[1998]", 1998),
        ("ca. 1900", 1900),
    ],
)
def test_parse_year(raw, expected):
    assert parse_year(raw) == expected


def test_parse_year_na_realnym_hicie_z_dziwna_data():
    # dc.date.issued == "04/05/16" -> zadnej czterocyfrowej liczby -> None
    md = _dspace10_hits()[0]["metadata"]
    assert metadata_value(md, "dc.date.issued") == "04/05/16"
    assert parse_year(metadata_value(md, "dc.date.issued")) is None


def test_parse_year_nie_rzuca_na_typie_nie_string():
    assert parse_year(2025) == 2025
    assert parse_year({"a": 1}) is None


# --- parse_version --------------------------------------------------------


@pytest.mark.parametrize(
    "fixture_name,expected",
    [
        ("dspace7_root", (7, 6)),
        ("dspace8_root", (8, 2)),
        ("dspace10_root", (10, 1)),
        ("dspace11_root", (11, 0)),
    ],
)
def test_parse_version_z_fixture_root(fixture_name, expected):
    assert parse_version(fixture_json(fixture_name)["dspaceVersion"]) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("DSpace 10.1-SNAPSHOT", (10, 1)),
        ("DSpace 7.6.5", (7, 6)),
        ("DSpace 8", (8, 0)),
        (None, None),
        ("", None),
        ("DSpace", None),
        ("smieci bez cyfr", None),
        (7.6, None),
    ],
)
def test_parse_version(raw, expected):
    assert parse_version(raw) == expected


# --- search_hits ----------------------------------------------------------


def test_search_hits_dspace10():
    hits, page = search_hits(fixture_json("dspace10_search_objects"))
    assert len(hits) == 2
    assert [h["uuid"] for h in hits] == [
        "5f116a15-d156-46ce-9eb8-d0c820eb6c05",
        "866d5671-0206-4b3d-ac86-ba273ac9106a",
    ]
    assert page == {"number": 0, "size": 2, "totalPages": 11, "totalElements": 21}


def test_search_hits_dspace10_druga_strona():
    hits, page = search_hits(fixture_json("dspace10_search_objects_page1"))
    assert page["number"] == 1
    assert len(hits) == 2


def test_search_hits_dspace7():
    hits, page = search_hits(fixture_json("dspace7_search_objects"))
    assert [h["handle"] for h in hits] == ["10289/12325", "10289/17783"]
    assert page["totalElements"] == 940


def test_search_hits_nie_zwraca_faset():
    # Fasety leza w payload["_embedded"]["facets"] (top-level) - nie nasza sprawa.
    payload = fixture_json("dspace10_search_objects")
    assert "facets" in payload["_embedded"]
    hits, _page = search_hits(payload)
    assert all(h.get("type") == "item" for h in hits)


def test_search_hits_odrzuca_hitHighlights():
    # hitHighlights siedza na poziomie "objects[i]", nie w indexableObject.
    payload = fixture_json("dspace10_search_objects")
    objects = payload["_embedded"]["searchResult"]["_embedded"]["objects"]
    assert "hitHighlights" in objects[0]
    hits, _page = search_hits(payload)
    assert "hitHighlights" not in hits[0]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        None,
        [],
        {"_embedded": None},
        {"_embedded": {}},
        {"_embedded": {"searchResult": None}},
        {"_embedded": {"searchResult": {}}},
        {"_embedded": {"searchResult": {"_embedded": {}}}},
        {"_embedded": {"searchResult": {"_embedded": {"objects": None}}}},
        {"_embedded": {"searchResult": {"_embedded": {"objects": []}, "page": None}}},
    ],
)
def test_search_hits_braki_daja_puste(payload):
    assert search_hits(payload) == ([], {})


def test_search_hits_pomija_obiekty_bez_indexableObject():
    payload = {
        "_embedded": {
            "searchResult": {
                "_embedded": {
                    "objects": [
                        {},
                        {"_embedded": {}},
                        {"_embedded": {"indexableObject": {"uuid": "x"}}},
                    ]
                },
                "page": {"number": 0},
            }
        }
    }
    hits, page = search_hits(payload)
    assert hits == [{"uuid": "x"}]
    assert page == {"number": 0}


# --- shape_item -----------------------------------------------------------


def test_shape_item_kompaktowy_dspace7():
    raw = _dspace7_hits()[0]
    shaped = shape_item(raw, ui_url="https://researchcommons.waikato.ac.nz")
    assert shaped == {
        "uuid": "74860545-ea47-4632-a2d6-9a06cb0b0c9a",
        "handle": "10289/12325",
        "url": "https://researchcommons.waikato.ac.nz/handle/10289/12325",
        "title": (
            "Patient and carer perceived barriers to early presentation and "
            "diagnosis of lung cancer: A systematic review"
        ),
        "authors": [
            "Cassim, Shemana",
            "Chepulis, Lynne Merran",
            "Keenan, Rawiri",
            "Kidd, Jacquie",
            "Firth, Melissa",
            "Lawrenson, Ross",
        ],
        "year": 2019,
        "date_issued": "2019-01-08",
        "type": "Journal Article",
        "doi": "10.1186/s12885-018-5169-9",
        "collection": None,
    }


def test_shape_item_kompaktowy_dspace10():
    raw = _dspace10_hits()[1]
    shaped = shape_item(raw, ui_url="https://demo.dspace.org")
    assert shaped["uuid"] == "866d5671-0206-4b3d-ac86-ba273ac9106a"
    assert shaped["url"] == "https://demo.dspace.org/handle/123456789/1139"
    assert shaped["type"] == "Article"
    assert shaped["year"] == 2016
    assert shaped["date_issued"] == "2016-01"
    assert shaped["doi"] is None  # DOI siedzi tu w dc.identifier.uri, nie .doi


def test_shape_item_ma_zawsze_ten_sam_zestaw_kluczy():
    oczekiwane = {
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
    assert set(shape_item(_dspace10_hits()[0])) == oczekiwane
    assert set(shape_item({})) == oczekiwane


def test_shape_item_puste_pola_sa_none_i_pustymi_listami():
    shaped = shape_item({})
    assert shaped["uuid"] is None
    assert shaped["title"] is None
    assert shaped["authors"] == []
    assert shaped["year"] is None
    assert shaped["url"] is None


def test_shape_item_url_wymaga_ui_url_i_handle():
    raw = _dspace10_hits()[0]
    assert shape_item(raw)["url"] is None  # brak ui_url
    assert shape_item(raw, ui_url="")["url"] is None
    assert shape_item({"handle": None}, ui_url="https://x.org")["url"] is None
    assert (
        shape_item(raw, ui_url="https://demo.dspace.org/")["url"]
        == "https://demo.dspace.org/handle/123456789/443"
    )


def test_shape_item_collection_z_owningCollection():
    item = fixture_json("dspace10_item")
    shaped = shape_item(item, ui_url="https://demo.dspace.org")
    assert shaped["collection"] == "Ph.D Thesis"
    assert shaped["uuid"] == "4109f8db-ff30-4a46-9148-268b7fe18a17"
    assert shaped["title"] == "Test PhD Thesis"
    assert shaped["url"] == "https://demo.dspace.org/handle/10673/1263"
    # ten item nie ma ani autorow, ani typu, ani daty
    assert shaped["authors"] == []
    assert shaped["type"] is None
    assert shaped["date_issued"] is None
    assert shaped["year"] is None


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"_embedded": None},
        {"_embedded": {}},
        {"_embedded": {"owningCollection": None}},
        {"_embedded": {"owningCollection": {}}},
        {"_embedded": {"owningCollection": []}},
        {"_embedded": "napis"},
    ],
)
def test_shape_item_collection_odporne_na_braki(raw):
    assert shape_item(raw)["collection"] is None


def test_shape_item_autorzy_zostaja_surowymi_stringami():
    # Decyzja D3: NIE rozbijamy na imie/nazwisko.
    shaped = shape_item(_dspace7_hits()[0])
    assert shaped["authors"][0] == "Cassim, Shemana"


def test_shape_item_full_dspace7():
    raw = _dspace7_hits()[1]
    shaped = shape_item(raw, ui_url="https://researchcommons.waikato.ac.nz", full=True)
    assert shaped["type"] == "Thesis"
    assert shaped["subjects"] == [
        "Person-centred",
        "Equitable",
        "Cancer nursing care",
        "Perspectives and expectations",
    ]
    assert shaped["language"] == "en"
    assert shaped["publisher"] == "The University of Waikato"
    assert shaped["abstract"].startswith("Abstract")
    assert shaped["rights"].startswith("All items in Research Commons")
    assert shaped["sponsorship"] is None
    assert shaped["metadata"]["dc.title"] == [shaped["title"]]
    assert shaped["metadata"]["dc.contributor.advisor"]


def test_shape_item_full_dodaje_dokladnie_te_klucze():
    dodatkowe = {
        "abstract",
        "subjects",
        "language",
        "publisher",
        "ispartof",
        "rights",
        "sponsorship",
        "metadata",
    }
    kompakt = set(shape_item(_dspace7_hits()[0]))
    pelny = set(shape_item(_dspace7_hits()[0], full=True))
    assert pelny - kompakt == dodatkowe


def test_shape_item_full_na_pustym_obiekcie():
    shaped = shape_item({}, full=True)
    assert shaped["abstract"] is None
    assert shaped["subjects"] == []
    assert shaped["metadata"] == {}


def test_shape_item_subjects_zbiera_wszystkie_klucze_dc_subject():
    raw = {
        "metadata": {
            "dc.subject": [{"value": "ogolny", "place": 0}],
            "dc.subject.mesh": [
                {"value": "mesh-1", "place": 1},
                {"value": "mesh-0", "place": 0},
            ],
            "dc.subject.other": [{"value": "inny", "place": 0}],
            "dc.title": [{"value": "nie-temat", "place": 0}],
        }
    }
    subjects = shape_item(raw, full=True)["subjects"]
    # kolejnosc: klucze alfabetycznie, w kluczu - po "place"
    assert subjects == ["ogolny", "mesh-0", "mesh-1", "inny"]


def test_shape_item_subjects_z_realnego_hitu():
    shaped = shape_item(_dspace10_hits()[1], full=True)
    assert shaped["subjects"] == [
        "hepatocarcinogenesis",
        "hepatocellular carcinoma",
        "steatohepatitis",
        "type 2 diabetes",
    ]


def test_shape_item_ispartof_z_dc_relation_ispartof():
    raw = {"metadata": {"dc.relation.ispartof": [{"value": "Seria X", "place": 0}]}}
    assert shape_item(raw, full=True)["ispartof"] == "Seria X"


def test_shape_item_ispartof_toleruje_wielkosc_liter():
    # Instancja 7.6.5 (waikato) zapisuje to pole jako "dc.relation.isPartOf".
    # Wyszukiwanie klucza jest dlatego case-insensitive - patrz metadata_values.
    raw = {"metadata": {"dc.relation.isPartOf": [{"value": "BMC Cancer", "place": 0}]}}
    assert shape_item(raw, full=True)["ispartof"] == "BMC Cancer"


def test_shape_item_nie_rzuca_na_smieciach():
    for raw in ({}, {"metadata": None}, {"metadata": []}, {"metadata": "x"}):
        assert shape_item(raw, ui_url="https://x.org", full=True)["title"] is None


# --- shape_community / shape_collection -----------------------------------


def test_shape_community():
    assert shape_community(_first_community()) == {
        "uuid": "93a27dc5-be27-4ef4-a8ab-9b82fb9e3e6d",
        "name": "aarthi",
        "handle": "10673/1251",
        "items_count": 0,
    }


def test_shape_collection():
    assert shape_collection(_first_collection()) == {
        "uuid": "282164f5-d325-4740-8dd1-fa4d6d3e7200",
        "name": "Articles",
        "handle": "123456789/3",
        "items_count": 210,
    }


def test_shape_collection_z_embedded_owningCollection():
    owning = fixture_json("dspace10_item")["_embedded"]["owningCollection"]
    assert shape_collection(owning) == {
        "uuid": "120aea60-7f66-4e5f-93e1-b84073a14f88",
        "name": "Ph.D Thesis",
        "handle": "10673/1213",
        "items_count": 3,
    }


@pytest.mark.parametrize("shaper", [shape_community, shape_collection])
@pytest.mark.parametrize("raw", [{}, None, [], {"uuid": None}])
def test_shape_community_collection_odporne_na_braki(shaper, raw):
    shaped = shaper(raw)
    assert set(shaped) == {"uuid", "name", "handle", "items_count"}
    assert shaped["items_count"] is None


def test_shape_collection_bez_archivedItemsCount():
    # np. w niektorych projekcjach licznika po prostu nie ma
    assert shape_collection({"uuid": "u", "name": "N"})["items_count"] is None


# --- shape_bitstream ------------------------------------------------------


def test_shape_bitstream_z_fixture():
    assert shape_bitstream(_first_bitstream(), mimetype="application/pdf") == {
        "uuid": "45382064-a29a-402f-bb1b-5304f5031f30",
        "name": "Test.pdf",
        "size_bytes": 14884,
        "checksum": "9e55704856c7798762321e4011451d98",
        "mimetype": "application/pdf",
        "sequence_id": 2,
        "bundle": "ORIGINAL",
        "download_url": (
            "https://demo.dspace.org/server/api/core/bitstreams/"
            "45382064-a29a-402f-bb1b-5304f5031f30/content"
        ),
    }


def test_shape_bitstream_mimetype_domyslnie_none():
    # Bitstream NIE MA pola mimetype - MIME przychodzi z /format osobno.
    raw = _first_bitstream()
    assert "mimetype" not in raw
    assert shape_bitstream(raw)["mimetype"] is None


def test_shape_bitstream_mimetype_z_fixture_formatu():
    fmt = fixture_json("dspace10_bitstreamformat")
    shaped = shape_bitstream(_first_bitstream(), mimetype=fmt["mimetype"])
    assert shaped["mimetype"] == "application/pdf"


def test_shape_bitstream_na_obiekcie_bundla_nie_wybucha():
    # Realny obiekt bundla (nie bitstreamu): ma uuid i name, nie ma sizeBytes,
    # checkSum ani _links.content. Ma wyjsc staly ksztalt z None-ami.
    bundle = fixture_json("dspace10_bundles")["_embedded"]["bundles"][0]
    shaped = shape_bitstream(bundle)
    assert shaped["name"] == "ORIGINAL"
    assert shaped["size_bytes"] is None
    assert shaped["checksum"] is None
    assert shaped["download_url"] is None


@pytest.mark.parametrize(
    "raw",
    [
        {},
        None,
        [],
        {"checkSum": None},
        {"checkSum": {}},
        {"checkSum": []},
        {"checkSum": "9e5570"},
        {"checkSum": {"checkSumAlgorithm": "MD5"}},
    ],
)
def test_shape_bitstream_odporny_na_braki(raw):
    shaped = shape_bitstream(raw)
    assert set(shaped) == {
        "uuid",
        "name",
        "size_bytes",
        "checksum",
        "mimetype",
        "sequence_id",
        "bundle",
        "download_url",
    }
    assert shaped["checksum"] is None
    assert shaped["download_url"] is None


def test_shape_bitstream_nie_czyta_malego_checksum():
    # Pole nazywa sie "checkSum" (wielkie S) - "checksum" to nie to samo.
    assert shape_bitstream({"checksum": {"value": "x"}})["checksum"] is None


# --- shape_facet_value ----------------------------------------------------


def test_shape_facet_value_z_fixture():
    values = fixture_json("dspace10_facets_author")["_embedded"]["values"]
    assert shape_facet_value(values[0]) == {
        "label": "Simmons, Cameron",
        "count": 190,
        "authority_key": None,
    }
    assert [shape_facet_value(v)["label"] for v in values] == [
        "Simmons, Cameron",
        "De Wael, Karolien",
        "Eens, Marcel",
    ]


def test_shape_facet_value_authority_key():
    raw = {"label": "Kowalski, Jan", "count": 3, "authorityKey": "abc-123"}
    assert shape_facet_value(raw)["authority_key"] == "abc-123"


@pytest.mark.parametrize("raw", [{}, None, []])
def test_shape_facet_value_odporny_na_braki(raw):
    shaped = shape_facet_value(raw)
    assert set(shaped) == {"label", "count", "authority_key"}
    assert shaped["label"] is None
    assert shaped["count"] is None


# --- odporność ogólna -----------------------------------------------------


def test_zadna_funkcja_nie_rzuca_na_pustym_dict():
    assert link_href({}, "self") is None
    assert metadata_values({}, "dc.title") == []
    assert metadata_value({}, "dc.title") is None
    assert flatten_metadata({}) == {}
    assert search_hits({}) == ([], {})
    assert shape_item({})["uuid"] is None
    assert shape_community({})["uuid"] is None
    assert shape_collection({})["uuid"] is None
    assert shape_bitstream({})["uuid"] is None
    assert shape_facet_value({})["label"] is None
