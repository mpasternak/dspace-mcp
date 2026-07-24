"""Testy adaptera MCP.

Sprawdzamy trzy rzeczy, których nie widać w `tools.py`: że wszystkie narzędzia
są zarejestrowane i opisane, że żadne narzędzie zapisujące nie istnieje, oraz
że wyjątki przeznaczone dla modelu nie wyciekają jako ślad stosu.
"""

from __future__ import annotations

import pytest

from dspace_mcp import server
from dspace_mcp.client import DSpaceError
from dspace_mcp.config import Config

EXPECTED_TOOLS = {
    "search_items",
    "get_item",
    "list_communities",
    "list_collections",
    "list_bitstreams",
    "get_bitstream_text",
    "list_facet_values",
    "get_item_statistics",
    "get_repository_info",
}

# Czasowniki, których nazwa narzędzia read-only nie ma prawa zawierać.
WRITE_VERBS = ("create", "update", "delete", "patch", "upload", "submit", "deposit")


@pytest.fixture
def mcp():
    return server.build_server(Config(base_url="https://repo.test/server"))


async def test_all_tools_are_registered(mcp):
    names = {tool.name for tool in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_no_write_tool_exists(mcp):
    """Gwarancja z decyzji D1 — sprawdzana, a nie tylko deklarowana."""
    for tool in await mcp.list_tools():
        assert not any(verb in tool.name.lower() for verb in WRITE_VERBS), tool.name


async def test_every_tool_has_a_description(mcp):
    for tool in await mcp.list_tools():
        assert tool.description, f"{tool.name} bez opisu — model nie wybierze narzędzia"
        assert len(tool.description) > 40, tool.name


async def test_tool_schemas_expose_parameters(mcp):
    tools = {tool.name: tool for tool in await mcp.list_tools()}
    search = tools["search_items"].inputSchema["properties"]
    assert {"query", "scope", "year_from", "year_to", "author", "limit"} <= set(search)
    # `ctx` jest wstrzykiwany przez FastMCP i nie może trafić do schematu.
    assert "ctx" not in search


async def test_guard_turns_dspace_error_into_answer():
    @server._guard
    async def failing():
        raise DSpaceError("Not found: no such object.")

    assert await failing() == {"error": "Not found: no such object."}


async def test_guard_lets_real_bugs_through():
    """Błąd programisty ma się wysypać, a nie udawać odpowiedź dla modelu."""

    @server._guard
    async def broken():
        raise ZeroDivisionError("bug")

    with pytest.raises(ZeroDivisionError):
        await broken()


def test_main_reports_configuration_error(capsys):
    with pytest.raises(SystemExit):
        server.main([])
    assert "base-url" in capsys.readouterr().err.lower()
