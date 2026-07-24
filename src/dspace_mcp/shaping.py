"""Spłaszczanie odpowiedzi HAL z REST API DSpace 7+ do zwięzłych struktur.

Moduł jest w całości **czysty**: żadnego I/O, żadnej sieci, żadnych zależności
poza standardową biblioteką. Dzięki temu testuje się go na surowych fixture'ach
(patrz ``tests/fixtures/README.md``) i nie trzeba do tego serwera.

Dwie zasady rządzą tym plikiem:

1. **Skracamy, ale nie interpretujemy** (decyzja D3 ze specyfikacji). Autorzy
   zostają oryginalnymi stringami, ``dc.type`` wraca surowe. Skracanie jest
   odwracalne (``full=True`` dokłada pełne metadane), interpretacja nie byłaby.
2. **Nic tu nie ma prawa rzucić wyjątkiem.** Funkcje dostają kawałki cudzych
   odpowiedzi HTTP z instancji, których nie kontrolujemy — brakujący klucz,
   ``null`` zamiast obiektu albo lista zamiast słownika to normalne wejście,
   nie sytuacja wyjątkowa. Wynik ma wtedy być pusty, a nie wybuchowy.

Format metadanych: w API 7+ istnieje wyłącznie mapa kluczy DC na listy
obiektów, ``{"dc.title": [{"value", "language", "authority", "confidence",
"place"}]}``. Płaska lista ``{key, value}`` należy do zlikwidowanego API
DSpace 5/6 i świadomie NIE jest obsługiwana.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "auth_methods",
    "flatten_metadata",
    "link_href",
    "metadata_value",
    "metadata_values",
    "parse_version",
    "parse_year",
    "search_hits",
    "shape_bitstream",
    "shape_collection",
    "shape_community",
    "shape_facet_value",
    "shape_item",
]

# Pierwsza czterocyfrowa liczba w stringu — patrz parse_year().
_YEAR_RE = re.compile(r"\d{4}")

# Major i (opcjonalny) minor z opisowego "DSpace 10.1-SNAPSHOT".
_VERSION_RE = re.compile(r"(\d+)(?:\.(\d+))?")

# Klucze, z których budujemy listę słów kluczowych w trybie pełnym.
_SUBJECT_PREFIX = "dc.subject"

# Nazwa metody logowania z nagłówka WWW-Authenticate — patrz auth_methods().
_AUTH_METHOD_RE = re.compile(r"([A-Za-z][A-Za-z0-9_.-]*)\s+realm=")


def _as_dict(value: Any) -> dict:
    """Zwróć ``value``, jeśli to słownik, w przeciwnym razie pusty słownik.

    Skrót używany wszędzie zamiast ``isinstance`` w każdej linijce: instancje
    DSpace potrafią przysłać ``null`` albo listę tam, gdzie kontrakt obiecuje
    obiekt.
    """
    return value if isinstance(value, dict) else {}


def link_href(obj: dict, rel: str) -> str | None:
    """Zwróć ``obj["_links"][rel]["href"]`` albo ``None``.

    Wartością relacji bywa **lista**, nie obiekt — tak jest z ``workflowGroups``
    w kolekcji (trzy wpisy: reviewer, editor, finaleditor). Naiwne
    ``links[rel]["href"]`` wywala się tam TypeError-em, dlatego z listy bierzemy
    href pierwszego elementu.
    """
    entry = _as_dict(_as_dict(obj).get("_links")).get(rel)
    if isinstance(entry, list):
        entry = entry[0] if entry else None
    href = _as_dict(entry).get("href")
    return href if isinstance(href, str) and href else None


def _place(entry: dict) -> float:
    """Klucz sortowania wpisu metadanych: brak ``place`` idzie na koniec.

    ``place`` bywa ujemne (np. ``-1`` w ``relation.*.latestForDiscovery``),
    więc nie da się użyć zera jako wartości domyślnej.
    """
    place = entry.get("place")
    if isinstance(place, bool) or not isinstance(place, int):
        return float("inf")
    return float(place)


def _sorted_values(entries: Any) -> list[str]:
    """Wyciągnij niepuste ``value`` z listy wpisów metadanych, wg ``place``."""
    if not isinstance(entries, list):
        return []
    usable = [
        e for e in entries if isinstance(e, dict) and isinstance(e.get("value"), str)
    ]
    usable = [e for e in usable if e["value"]]
    return [e["value"] for e in sorted(usable, key=_place)]


def metadata_values(metadata: dict, key: str) -> list[str]:
    """Wszystkie wartości pola ``key``, posortowane po ``place`` rosnąco.

    Brak klucza albo nieprawidłowy kształt danych → pusta lista.

    Dopasowanie jest najpierw dokładne, a dopiero potem — bez rozróżniania
    wielkości liter. Powód jest empiryczny: schemat Dublin Core w DSpace bywa
    zapisany niejednolicie i w żywym repozytorium na wersji 7.6.5 pole nosi
    nazwę ``dc.relation.isPartOf``, podczas gdy dokumentacja i większość
    instancji używa ``dc.relation.ispartof``. Wariant bez rozróżniania
    wielkości liter jest tańszy niż lista aliasów utrzymywana per pole i nie
    grozi kolizją — DSpace nie pozwala na dwa pola różniące się wyłącznie
    wielkością liter.
    """
    meta = _as_dict(metadata)
    if key in meta:
        return _sorted_values(meta[key])
    lowered = key.lower()
    for actual, entries in meta.items():
        if isinstance(actual, str) and actual.lower() == lowered:
            return _sorted_values(entries)
    return []


def metadata_value(metadata: dict, key: str) -> str | None:
    """Pierwsza (po ``place``) wartość pola ``key`` albo ``None``."""
    values = metadata_values(metadata, key)
    return values[0] if values else None


def flatten_metadata(metadata: dict) -> dict[str, list[str]]:
    """Cała mapa metadanych → ``{klucz: [wartości]}`` (tryb full_metadata).

    Klucze bez ani jednej użytecznej wartości pomijamy — pusta lista niczego
    modelowi nie mówi, a kosztuje tokeny (decyzja D4).
    """
    flat = {}
    for key, entries in _as_dict(metadata).items():
        values = _sorted_values(entries)
        if values:
            flat[key] = values
    return flat


def parse_year(date_issued: str | None) -> int | None:
    """Rok z ``dc.date.issued`` — pierwsza czterocyfrowa liczba w stringu.

    Świadomie nie używamy parsera dat ISO: w prawdziwych repozytoriach
    ``dc.date.issued`` bywa kompletnie niesformatowane. Spotkane wartości to
    m.in. ``"2025"``, ``"2025-03"``, ``"2025-03-17"`` i ``"04/05/16"`` (ta
    ostatnia z demo.dspace.org — nie ma w niej czterech cyfr pod rząd, więc
    zwracamy ``None`` zamiast zgadywać stulecie).
    """
    if date_issued is None:
        return None
    if not isinstance(date_issued, str):
        date_issued = str(date_issued)
    match = _YEAR_RE.search(date_issued)
    return int(match.group()) if match else None


def parse_version(dspace_version: str | None) -> tuple[int, int] | None:
    """``"DSpace 10.1-SNAPSHOT"`` → ``(10, 1)``; ``"DSpace 7.6.5"`` → ``(7, 6)``.

    Pole ``dspaceVersion`` z ``GET /api`` jest opisowym stringiem, nie numerem —
    stąd regex. Brak minora (``"DSpace 8"``) traktujemy jak ``.0``. Wersji nie
    używamy do warunkowania zachowań (decyzja D8), służy tylko do raportowania.
    """
    if not isinstance(dspace_version, str):
        return None
    match = _VERSION_RE.search(dspace_version)
    if not match:
        return None
    major, minor = match.groups()
    return int(major), int(minor or 0)


def _flag(raw: dict, key: str) -> bool | None:
    """Wartość logiczna z odpowiedzi albo ``None``, gdy jej nie ma.

    Nie przepuszczamy tu ``bool(cokolwiek)``: instancja, która przysyła string
    albo pomija pole, ma dać „nie wiadomo", a nie przypadkowe „prawda".
    """
    value = raw.get(key)
    return value if isinstance(value, bool) else None


def auth_methods(header: Any) -> list[str]:
    """Metody logowania ogłoszone przez instancję w ``WWW-Authenticate``.

    DSpace odpowiada na ``GET /api/authn/status`` nagłówkiem w rodzaju
    ``password realm="DSpace REST API", orcid realm="DSpace REST API",
    location="..."``. Zestaw jest konfigurowalny per-instancja, więc pytamy
    zamiast zakładać (decyzja D8 rozciągnięta w A6): dzięki temu wiemy, zanim
    wyślemy hasło, czy ta instancja w ogóle przyjmuje logowanie hasłem.

    Metodą jest token stojący **przed** ``realm=``; ``location=`` nią nie jest.
    Pusta lista znaczy „nie wiadomo" i nie blokuje próby logowania.
    """
    if not isinstance(header, str):
        return []
    found: list[str] = []
    for name in _AUTH_METHOD_RE.findall(header):
        lowered = name.lower()
        if lowered not in found:
            found.append(lowered)
    return found


def search_hits(payload: dict) -> tuple[list[dict], dict]:
    """Z odpowiedzi ``/api/discover/search/objects`` → ``(rekordy, koperta)``.

    Zagnieżdżenie jest czterowarstwowe:
    ``_embedded.searchResult._embedded.objects[i]._embedded.indexableObject``.
    Koperta paginacji siedzi w ``_embedded.searchResult.page``.

    Uwaga: fasety wyszukiwania leżą w ``payload["_embedded"]["facets"]``, czyli
    na NAJWYŻSZYM poziomie, a nie w ``searchResult`` — ta funkcja ich nie
    dotyczy. Odpada też ``hitHighlights``, które siedzi obok
    ``indexableObject`` (znaczniki ``<em>`` i encje HTML są modelowi zbędne).
    """
    search_result = _as_dict(
        _as_dict(_as_dict(payload).get("_embedded")).get("searchResult")
    )
    objects = _as_dict(search_result.get("_embedded")).get("objects")
    hits = []
    if isinstance(objects, list):
        for obj in objects:
            hit = _as_dict(_as_dict(obj).get("_embedded")).get("indexableObject")
            if isinstance(hit, dict):
                hits.append(hit)
    return hits, _as_dict(search_result.get("page"))


def shape_item(raw: dict, *, ui_url: str = "", full: bool = False) -> dict:
    """Item (z wyszukiwania albo z ``/core/items/{uuid}``) → płaski rekord.

    Zawsze zwracamy komplet kluczy, także gdy są puste (``None`` dla skalarów,
    ``[]`` dla list): model ma widzieć stały kształt i odróżniać „pole jest
    puste" od „pola nie ma w tym API".

    ``authors`` i ``type`` zostają surowe — bez rozbijania nazwisk i bez
    mapowania typów na jakikolwiek zewnętrzny słownik (decyzja D3).
    """
    raw = _as_dict(raw)
    metadata = _as_dict(raw.get("metadata"))
    handle = raw.get("handle")

    url = None
    if ui_url and isinstance(handle, str) and handle:
        url = f"{ui_url.rstrip('/')}/handle/{handle}"

    owning = _as_dict(_as_dict(raw.get("_embedded")).get("owningCollection"))
    collection = owning.get("name")

    shaped = {
        "uuid": raw.get("uuid") or raw.get("id"),
        "handle": handle,
        "url": url,
        "title": metadata_value(metadata, "dc.title"),
        "authors": metadata_values(metadata, "dc.contributor.author"),
        "year": parse_year(metadata_value(metadata, "dc.date.issued")),
        "date_issued": metadata_value(metadata, "dc.date.issued"),
        "type": metadata_value(metadata, "dc.type"),
        "doi": metadata_value(metadata, "dc.identifier.doi"),
        "collection": collection if isinstance(collection, str) else None,
        # Stan rekordu w repozytorium. Bez tego model nie odróżnia „wycofany"
        # od „niedostępny dla ciebie" i zgaduje, czemu czegoś nie widać.
        # ``None`` znaczy „instancja tego nie podała", a nie „fałsz".
        "withdrawn": _flag(raw, "withdrawn"),
        "discoverable": _flag(raw, "discoverable"),
        "in_archive": _flag(raw, "inArchive"),
    }
    if not full:
        return shaped

    subjects: list[str] = []
    for key in sorted(metadata):
        if key.startswith(_SUBJECT_PREFIX):
            subjects.extend(metadata_values(metadata, key))

    shaped.update(
        {
            "abstract": metadata_value(metadata, "dc.description.abstract"),
            "subjects": subjects,
            "language": metadata_value(metadata, "dc.language.iso"),
            "publisher": metadata_value(metadata, "dc.publisher"),
            "ispartof": metadata_value(metadata, "dc.relation.ispartof"),
            "rights": metadata_value(metadata, "dc.rights"),
            "sponsorship": metadata_value(metadata, "dc.description.sponsorship"),
            "metadata": flatten_metadata(metadata),
        }
    )
    return shaped


def _shape_dso(raw: dict) -> dict:
    """Wspólny kształt społeczności i kolekcji — różnią się tylko endpointem."""
    raw = _as_dict(raw)
    items_count = raw.get("archivedItemsCount")
    return {
        "uuid": raw.get("uuid") or raw.get("id"),
        "name": raw.get("name"),
        "handle": raw.get("handle"),
        # archivedItemsCount bywa nieobecne (zależy od projekcji i wersji) —
        # None znaczy "nie wiadomo", nie "zero".
        "items_count": items_count if isinstance(items_count, int) else None,
    }


def shape_community(raw: dict) -> dict:
    """Społeczność → ``{uuid, name, handle, items_count}``."""
    return _shape_dso(raw)


def shape_collection(raw: dict) -> dict:
    """Kolekcja → ``{uuid, name, handle, items_count}``."""
    return _shape_dso(raw)


def shape_bitstream(raw: dict, *, mimetype: str | None = None) -> dict:
    """Bitstream → płaski opis pliku.

    Nazwy pól w API są nieregularne i trzeba je brać dosłownie: ``sizeBytes``,
    ``sequenceId``, ``bundleName`` oraz ``checkSum`` przez **wielkie S** (wewnątrz
    ``{"checkSumAlgorithm", "value"}`` — zostawiamy samą sumę, algorytm modelowi
    do niczego).

    ``mimetype`` przychodzi z zewnątrz, bo bitstream go nie zawiera: typ MIME
    żyje w ``/core/bitstreams/{uuid}/format`` (albo w ``?embed=format``).
    """
    raw = _as_dict(raw)
    checksum = _as_dict(raw.get("checkSum")).get("value")
    return {
        "uuid": raw.get("uuid") or raw.get("id"),
        "name": raw.get("name"),
        "size_bytes": raw.get("sizeBytes"),
        "checksum": checksum if isinstance(checksum, str) else None,
        "mimetype": mimetype,
        "sequence_id": raw.get("sequenceId"),
        "bundle": raw.get("bundleName"),
        "download_url": link_href(raw, "content"),
    }


def shape_facet_value(raw: dict) -> dict:
    """Wartość fasety → ``{label, count, authority_key}``.

    Bez ``total`` — endpoint faset nie podaje ``totalElements`` (decyzja D4).
    """
    raw = _as_dict(raw)
    return {
        "label": raw.get("label"),
        "count": raw.get("count"),
        "authority_key": raw.get("authorityKey"),
    }
