"""Testy konfiguracji: normalizacja URL-a, odczyt ze środowiska i z CLI."""

from __future__ import annotations

import dataclasses

import pytest

from dspace_mcp import __version__
from dspace_mcp.config import (
    Config,
    config_from_env,
    normalize_base_url,
    parse_args,
)

ENV_VARS = (
    "DSPACE_BASE_URL",
    "DSPACE_TIMEOUT",
    "DSPACE_MAX_RESULTS",
    "DSPACE_PDF_MAX_MB",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """``parse_args`` czyta ``os.environ`` — izolujemy testy od środowiska hosta."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


# --- normalize_base_url -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://demo.dspace.org/server", "https://demo.dspace.org/server"),
        ("https://demo.dspace.org/server/", "https://demo.dspace.org/server"),
        ("https://demo.dspace.org/server///", "https://demo.dspace.org/server"),
        ("  https://demo.dspace.org/server  ", "https://demo.dspace.org/server"),
        ("\thttps://demo.dspace.org/server\n", "https://demo.dspace.org/server"),
        ("https://x.org/server/api", "https://x.org/server"),
        ("https://x.org/server/api/", "https://x.org/server"),
        ("https://x.org/api", "https://x.org"),
        ("demo.dspace.org/server", "https://demo.dspace.org/server"),
        ("demo.dspace.org/server/api/", "https://demo.dspace.org/server"),
        ("demo.dspace.org", "https://demo.dspace.org"),
        ("http://localhost:8080/server", "http://localhost:8080/server"),
        ("http://localhost:8080/server/api", "http://localhost:8080/server"),
        # "/server" NIE jest dopisywane automatycznie - robi to sonda w client.py.
        ("https://repo.example.org", "https://repo.example.org"),
    ],
)
def test_normalize_base_url(raw: str, expected: str) -> None:
    assert normalize_base_url(raw) == expected


def test_normalize_base_url_keeps_path_that_only_contains_api_as_substring() -> None:
    assert normalize_base_url("https://x.org/apiary") == "https://x.org/apiary"


def test_normalize_base_url_strips_only_one_api_segment() -> None:
    assert normalize_base_url("https://x.org/api/api") == "https://x.org/api"


@pytest.mark.parametrize("raw", ["", "   ", "\t\n", "/", "///"])
def test_normalize_base_url_rejects_empty(raw: str) -> None:
    with pytest.raises(ValueError) as excinfo:
        normalize_base_url(raw)
    assert "empty" in str(excinfo.value).lower()


# --- Config -----------------------------------------------------------------


def test_config_defaults() -> None:
    config = Config(base_url="https://demo.dspace.org/server")
    assert config.timeout == 15.0
    assert config.max_results == 50
    assert config.pdf_max_mb == 20
    assert config.username is None
    assert config.password is None
    assert config.enable_write is False


def test_config_api_url() -> None:
    config = Config(base_url="https://demo.dspace.org/server")
    assert config.api_url == "https://demo.dspace.org/server/api"


def test_config_pdf_max_bytes() -> None:
    assert Config(base_url="https://x.org").pdf_max_bytes == 20 * 1024 * 1024
    assert Config(base_url="https://x.org", pdf_max_mb=1).pdf_max_bytes == 1048576


def test_config_is_frozen() -> None:
    config = Config(base_url="https://x.org")
    with pytest.raises(dataclasses.FrozenInstanceError):
        config.base_url = "https://other.org"  # type: ignore[misc]


# --- config_from_env --------------------------------------------------------


def test_config_from_env_minimal() -> None:
    config = config_from_env({"DSPACE_BASE_URL": "https://demo.dspace.org/server/"})
    assert config.base_url == "https://demo.dspace.org/server"
    assert config.timeout == 15.0
    assert config.max_results == 50
    assert config.pdf_max_mb == 20


def test_config_from_env_all_values() -> None:
    config = config_from_env(
        {
            "DSPACE_BASE_URL": "repo.example.org/server/api",
            "DSPACE_TIMEOUT": "2.5",
            "DSPACE_MAX_RESULTS": "10",
            "DSPACE_PDF_MAX_MB": "3",
        }
    )
    assert config.base_url == "https://repo.example.org/server"
    assert config.api_url == "https://repo.example.org/server/api"
    assert config.timeout == 2.5
    assert config.max_results == 10
    assert config.pdf_max_bytes == 3 * 1024 * 1024


def test_config_from_env_reads_os_environ_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DSPACE_BASE_URL", "https://from-os-environ.example/server")
    monkeypatch.setenv("DSPACE_MAX_RESULTS", "7")
    config = config_from_env()
    assert config.base_url == "https://from-os-environ.example/server"
    assert config.max_results == 7


@pytest.mark.parametrize("env", [{}, {"DSPACE_BASE_URL": ""}, {"DSPACE_BASE_URL": " "}])
def test_config_from_env_requires_base_url(env: dict[str, str]) -> None:
    with pytest.raises(ValueError) as excinfo:
        config_from_env(env)
    message = str(excinfo.value)
    assert "DSPACE_BASE_URL" in message
    # Komunikat ma mówić, co ustawić - nie tylko, że czegoś brakuje.
    assert "https://" in message


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("DSPACE_TIMEOUT", "abc"),
        ("DSPACE_TIMEOUT", ""),
        ("DSPACE_MAX_RESULTS", "10.5"),
        ("DSPACE_MAX_RESULTS", "many"),
        ("DSPACE_PDF_MAX_MB", "20MB"),
    ],
)
def test_config_from_env_rejects_non_numeric(var: str, value: str) -> None:
    env = {"DSPACE_BASE_URL": "https://x.org/server", var: value}
    with pytest.raises(ValueError) as excinfo:
        config_from_env(env)
    message = str(excinfo.value)
    assert var in message
    assert repr(value) in message or value in message


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("DSPACE_TIMEOUT", "0"),
        ("DSPACE_TIMEOUT", "-1.5"),
        ("DSPACE_MAX_RESULTS", "0"),
        ("DSPACE_MAX_RESULTS", "-10"),
        ("DSPACE_PDF_MAX_MB", "0"),
        ("DSPACE_PDF_MAX_MB", "-1"),
    ],
)
def test_config_from_env_rejects_non_positive(var: str, value: str) -> None:
    env = {"DSPACE_BASE_URL": "https://x.org/server", var: value}
    with pytest.raises(ValueError) as excinfo:
        config_from_env(env)
    assert var in str(excinfo.value)


def test_config_from_env_ignores_unrelated_variables() -> None:
    config = config_from_env(
        {"DSPACE_BASE_URL": "https://x.org/server", "PATH": "/usr/bin"}
    )
    assert config.base_url == "https://x.org/server"


# --- parse_args -------------------------------------------------------------


def test_parse_args_flags_only() -> None:
    config = parse_args(
        [
            "--base-url",
            "https://demo.dspace.org/server/api/",
            "--timeout",
            "30",
            "--max-results",
            "100",
            "--pdf-max-mb",
            "5",
        ]
    )
    assert config.base_url == "https://demo.dspace.org/server"
    assert config.timeout == 30.0
    assert config.max_results == 100
    assert config.pdf_max_mb == 5


def test_parse_args_defaults_when_only_base_url_given() -> None:
    config = parse_args(["--base-url", "demo.dspace.org/server"])
    assert config.base_url == "https://demo.dspace.org/server"
    assert config.timeout == 15.0
    assert config.max_results == 50
    assert config.pdf_max_mb == 20


def test_parse_args_takes_defaults_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_BASE_URL", "https://env.example/server")
    monkeypatch.setenv("DSPACE_TIMEOUT", "3")
    monkeypatch.setenv("DSPACE_MAX_RESULTS", "11")
    monkeypatch.setenv("DSPACE_PDF_MAX_MB", "2")
    config = parse_args([])
    assert config.base_url == "https://env.example/server"
    assert config.timeout == 3.0
    assert config.max_results == 11
    assert config.pdf_max_mb == 2


def test_parse_args_flags_override_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_BASE_URL", "https://env.example/server")
    monkeypatch.setenv("DSPACE_TIMEOUT", "3")
    monkeypatch.setenv("DSPACE_MAX_RESULTS", "11")
    monkeypatch.setenv("DSPACE_PDF_MAX_MB", "2")
    config = parse_args(
        [
            "--base-url",
            "https://cli.example/server",
            "--timeout",
            "9",
            "--max-results",
            "44",
            "--pdf-max-mb",
            "8",
        ]
    )
    assert config.base_url == "https://cli.example/server"
    assert config.timeout == 9.0
    assert config.max_results == 44
    assert config.pdf_max_mb == 8


def test_parse_args_partial_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_BASE_URL", "https://env.example/server")
    monkeypatch.setenv("DSPACE_MAX_RESULTS", "11")
    config = parse_args(["--max-results", "44"])
    assert config.base_url == "https://env.example/server"
    assert config.max_results == 44


def test_parse_args_missing_base_url_exits_2(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args([])
    assert excinfo.value.code == 2
    stderr = capsys.readouterr().err
    assert "--base-url" in stderr
    assert "DSPACE_BASE_URL" in stderr


@pytest.mark.parametrize(
    "argv",
    [
        ["--base-url", "https://x.org/server", "--timeout", "abc"],
        ["--base-url", "https://x.org/server", "--timeout", "0"],
        ["--base-url", "https://x.org/server", "--timeout", "-2"],
        ["--base-url", "https://x.org/server", "--max-results", "0"],
        ["--base-url", "https://x.org/server", "--max-results", "-1"],
        ["--base-url", "https://x.org/server", "--max-results", "1.5"],
        ["--base-url", "https://x.org/server", "--pdf-max-mb", "0"],
        ["--base-url", "https://x.org/server", "--pdf-max-mb", "nope"],
        ["--base-url", "   "],
    ],
)
def test_parse_args_rejects_bad_values(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(argv)
    assert excinfo.value.code == 2


def test_parse_args_reports_bad_env_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DSPACE_BASE_URL", "https://env.example/server")
    monkeypatch.setenv("DSPACE_TIMEOUT", "abc")
    with pytest.raises(SystemExit) as excinfo:
        parse_args([])
    assert excinfo.value.code == 2


def test_parse_args_flag_rescues_bad_env_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Skoro flaga nadpisuje środowisko, zepsuta zmienna nie ma prawa przeszkadzać."""
    monkeypatch.setenv("DSPACE_BASE_URL", "https://env.example/server")
    monkeypatch.setenv("DSPACE_TIMEOUT", "abc")
    config = parse_args(["--timeout", "4"])
    assert config.timeout == 4.0


def test_parse_args_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--version"])
    assert excinfo.value.code == 0
    assert __version__ in capsys.readouterr().out


def test_parse_args_help_is_english(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        parse_args(["--help"])
    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--base-url" in out
    assert "--pdf-max-mb" in out
