"""Wspólne pomocniki testowe.

Fixture'y w ``tests/fixtures/`` to **surowe** odpowiedzi zebrane 2026-07-22 z
żywych instancji DSpace (7.6.5, 8.x, 10.1, 11.0-SNAPSHOT) — patrz
``tests/fixtures/README.md``. Nie modyfikujemy ich: ich wartość polega na tym,
że są dokładnie tym, co przysyła prawdziwy serwer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def fixture_json(name: str) -> Any:
    """Wczytaj fixture JSON po nazwie pliku (z rozszerzeniem lub bez)."""
    path = FIXTURES / name
    if not path.suffix:
        path = path.with_suffix(".json")
    if not path.exists():
        available = sorted(p.name for p in FIXTURES.iterdir() if p.is_file())
        raise FileNotFoundError(f"Brak fixture {path.name}. Dostępne: {available}")
    return json.loads(path.read_text(encoding="utf-8"))


def fixture_text(name: str) -> str:
    """Wczytaj fixture tekstowy (np. zrzut nagłówków HTTP)."""
    return (FIXTURES / name).read_text(encoding="utf-8")
