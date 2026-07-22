"""Konfiguracja serwera: dataclass ``Config`` plus odczyt ze środowiska i z CLI.

Jedna instancja DSpace na proces (decyzja D2 w specyfikacji), więc konfiguracja
powstaje raz przy starcie i dalej podróżuje jako niemutowalny obiekt.

Wszystkie komunikaty widoczne dla użytkownika są po angielsku - to pakiet
międzynarodowy.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TypeVar

ENV_BASE_URL = "DSPACE_BASE_URL"
ENV_TIMEOUT = "DSPACE_TIMEOUT"
ENV_MAX_RESULTS = "DSPACE_MAX_RESULTS"
ENV_PDF_MAX_MB = "DSPACE_PDF_MAX_MB"

DEFAULT_TIMEOUT = 15.0
DEFAULT_MAX_RESULTS = 50
DEFAULT_PDF_MAX_MB = 20

EXAMPLE_BASE_URL = "https://demo.dspace.org/server"

_N = TypeVar("_N", int, float)


@dataclass(frozen=True)
class Config:
    """Komplet ustawień serwera.

    Pola ``username``, ``password`` i ``enable_write`` istnieją od początku, żeby
    format konfiguracji nie musiał się zmieniać, gdyby kiedyś doszedł tryb zapisu
    (decyzja D7). Dzisiaj nie są przez nic czytane.
    """

    base_url: str
    timeout: float = DEFAULT_TIMEOUT
    max_results: int = DEFAULT_MAX_RESULTS
    pdf_max_mb: int = DEFAULT_PDF_MAX_MB
    username: str | None = None
    password: str | None = None
    enable_write: bool = False

    @property
    def api_url(self) -> str:
        """Korzeń REST API - wszystkie endpointy wiszą pod nim."""
        return f"{self.base_url}/api"

    @property
    def pdf_max_bytes(self) -> int:
        """Limit z konfiguracji w bajtach, bo strumień liczymy w bajtach."""
        return self.pdf_max_mb * 1024 * 1024


def normalize_base_url(raw: str) -> str:
    """Sprowadź podany adres do postaci katalogu serwera, bez końcowego ukośnika.

    ``/server`` nie jest dopisywane automatycznie - robi to dopiero sonda startowa
    w ``client.py``, która potrafi sprawdzić, czy taki adres w ogóle odpowiada.
    """
    cleaned = raw.strip().rstrip("/")

    # Ludzie kopiują URL-e z przeglądarki razem z /api; base_url ma wskazywać
    # poziom wyżej, bo klient dokleja /api sam.
    if cleaned.endswith("/api"):
        cleaned = cleaned[: -len("/api")].rstrip("/")

    if not cleaned:
        raise ValueError(
            "Base URL must not be empty. Point it at your DSpace server, "
            f"e.g. {EXAMPLE_BASE_URL}"
        )

    if "://" not in cleaned:
        cleaned = f"https://{cleaned}"

    return cleaned


def _coerce_positive(
    raw: str,
    *,
    label: str,
    converter: Callable[[str], _N],
    kind: str,
) -> _N:
    """Zamień string na liczbę dodatnią albo powiedz dokładnie, co było nie tak."""
    try:
        value = converter(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"Invalid value for {label}: {raw!r} is not a valid {kind}."
        ) from exc
    if value <= 0:
        raise ValueError(f"Invalid value for {label}: {value} must be greater than 0.")
    return value


def _number_from_env(
    env: Mapping[str, str],
    var: str,
    *,
    converter: Callable[[str], _N],
    kind: str,
    default: _N,
) -> _N:
    # Zmienna obecna, ale pusta, to zwykle literówka w konfiguracji klienta MCP -
    # lepiej krzyknąć niż po cichu użyć domyślnej wartości.
    raw = env.get(var)
    if raw is None:
        return default
    return _coerce_positive(raw, label=var, converter=converter, kind=kind)


def config_from_env(env: Mapping[str, str] | None = None) -> Config:
    """Zbuduj ``Config`` ze zmiennych środowiskowych (domyślnie ``os.environ``)."""
    if env is None:
        env = os.environ

    raw_base_url = env.get(ENV_BASE_URL)
    if raw_base_url is None or not raw_base_url.strip():
        raise ValueError(
            f"{ENV_BASE_URL} is not set. Set it to the base URL of your DSpace "
            f"server, e.g. {ENV_BASE_URL}={EXAMPLE_BASE_URL} "
            "(or pass --base-url on the command line)."
        )

    return Config(
        base_url=normalize_base_url(raw_base_url),
        timeout=_number_from_env(
            env, ENV_TIMEOUT, converter=float, kind="number", default=DEFAULT_TIMEOUT
        ),
        max_results=_number_from_env(
            env,
            ENV_MAX_RESULTS,
            converter=int,
            kind="integer",
            default=DEFAULT_MAX_RESULTS,
        ),
        pdf_max_mb=_number_from_env(
            env,
            ENV_PDF_MAX_MB,
            converter=int,
            kind="integer",
            default=DEFAULT_PDF_MAX_MB,
        ),
    )


def _build_parser() -> argparse.ArgumentParser:
    from dspace_mcp import __version__  # lokalny import: unikamy cyklu przy starcie

    parser = argparse.ArgumentParser(
        prog="dspace-mcp",
        description="Read-only MCP server for DSpace 7+ repositories.",
    )
    parser.add_argument(
        "--base-url",
        metavar="URL",
        default=None,
        help=(
            f"Base URL of the DSpace server, e.g. {EXAMPLE_BASE_URL}. "
            f"Defaults to ${ENV_BASE_URL}."
        ),
    )
    parser.add_argument(
        "--timeout",
        metavar="SECONDS",
        type=float,
        default=None,
        help=(
            f"HTTP request timeout in seconds (default: {DEFAULT_TIMEOUT:g}, "
            f"or ${ENV_TIMEOUT})."
        ),
    )
    parser.add_argument(
        "--max-results",
        metavar="N",
        type=int,
        default=None,
        help=(
            f"Hard cap on how many objects any tool may return "
            f"(default: {DEFAULT_MAX_RESULTS}, or ${ENV_MAX_RESULTS})."
        ),
    )
    parser.add_argument(
        "--pdf-max-mb",
        metavar="MB",
        type=int,
        default=None,
        help=(
            f"Refuse to download bitstreams larger than this for text extraction "
            f"(default: {DEFAULT_PDF_MAX_MB}, or ${ENV_PDF_MAX_MB})."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"dspace-mcp {__version__}"
    )
    return parser


def _resolve_number(
    parser: argparse.ArgumentParser,
    cli_value: _N | None,
    flag: str,
    env: Mapping[str, str],
    var: str,
    *,
    converter: Callable[[str], _N],
    kind: str,
    default: _N,
) -> _N:
    """Flaga wygrywa ze środowiskiem, a środowisko z wartością domyślną.

    Gdy flaga jest podana, zmiennej nie czytamy w ogóle - zepsuta zmienna nie może
    blokować wywołania, które i tak jej nie używa.
    """
    if cli_value is not None:
        if cli_value <= 0:
            parser.error(f"{flag}: {cli_value} must be greater than 0")
        return cli_value
    try:
        return _number_from_env(
            env, var, converter=converter, kind=kind, default=default
        )
    except ValueError as exc:
        parser.error(str(exc))


def parse_args(argv: list[str] | None = None) -> Config:
    """Zbuduj ``Config`` z argumentów CLI, biorąc domyślne wartości ze środowiska."""
    env: Mapping[str, str] = os.environ
    parser = _build_parser()
    args = parser.parse_args(argv)

    raw_base_url = args.base_url if args.base_url is not None else env.get(ENV_BASE_URL)
    if raw_base_url is None or not raw_base_url.strip():
        parser.error(
            f"no DSpace base URL given: pass --base-url {EXAMPLE_BASE_URL} "
            f"or set ${ENV_BASE_URL}"
        )
    try:
        base_url = normalize_base_url(raw_base_url)
    except ValueError as exc:
        parser.error(str(exc))

    return Config(
        base_url=base_url,
        timeout=_resolve_number(
            parser,
            args.timeout,
            "--timeout",
            env,
            ENV_TIMEOUT,
            converter=float,
            kind="number",
            default=DEFAULT_TIMEOUT,
        ),
        max_results=_resolve_number(
            parser,
            args.max_results,
            "--max-results",
            env,
            ENV_MAX_RESULTS,
            converter=int,
            kind="integer",
            default=DEFAULT_MAX_RESULTS,
        ),
        pdf_max_mb=_resolve_number(
            parser,
            args.pdf_max_mb,
            "--pdf-max-mb",
            env,
            ENV_PDF_MAX_MB,
            converter=int,
            kind="integer",
            default=DEFAULT_PDF_MAX_MB,
        ),
    )
