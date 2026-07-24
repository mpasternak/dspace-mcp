"""Testy adaptera MCP.

Sprawdzamy trzy rzeczy, których nie widać w `tools.py`: że wszystkie narzędzia
są zarejestrowane i opisane, że żadne narzędzie zapisujące nie istnieje, oraz
że wyjątki przeznaczone dla modelu nie wyciekają jako ślad stosu.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dspace_mcp import server
from dspace_mcp.client import AuthState, DSpaceClient, DSpaceError
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


def stub_ctx(client):
    """Minimalny kontekst FastMCP — bramka sięga po klienta z lifespanu."""
    return SimpleNamespace(
        request_context=SimpleNamespace(lifespan_context=SimpleNamespace(client=client))
    )


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


# --- rejestracja zależna od stanu i bramka decyzji (A3, A9) ------------------

ACCOUNT = Config(
    base_url="https://repo.test/server",
    username="reader@repo.test",
    password="s3kret",
)


class StubClient:
    """Atrapa klienta wystarczająca dla bramki w ``_guard``."""

    def __init__(self, state: AuthState, reason: str = "wrong password") -> None:
        self.auth_state = state
        self.auth_reason = reason
        self.config = ACCOUNT
        self.accepted = False

    # Prawdziwa implementacja, nie kopia: inaczej test sprawdzałby treść
    # komunikatu wymyśloną w atrapie, a nie tę, którą dostanie model.
    decision_question = DSpaceClient.decision_question

    def accept_anonymous(self) -> None:
        self.accepted = True
        self.auth_state = AuthState.ANONYMOUS_BY_CHOICE


async def test_anonymous_install_keeps_exactly_the_original_tools(mcp):
    """Bez podanego konta nic się nie zmienia — także w opisach narzędzi."""
    names = {tool.name for tool in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS
    assert "continue_anonymously" not in names
    assert "compare_access" not in names


async def test_account_install_adds_the_decision_and_comparison_tools():
    mcp = server.build_server(ACCOUNT)
    names = {tool.name for tool in await mcp.list_tools()}
    assert names == EXPECTED_TOOLS | {"continue_anonymously", "compare_access"}


async def test_read_tools_gain_no_parameters_when_an_account_is_configured():
    """Sygnatury dziewięciu narzędzi muszą zostać nietknięte (decyzja A9)."""
    anonymous = {
        tool.name: tool.inputSchema
        for tool in await server.build_server(
            Config(base_url="https://repo.test/server")
        ).list_tools()
    }
    with_account = {
        tool.name: tool.inputSchema
        for tool in await server.build_server(ACCOUNT).list_tools()
    }
    for name in EXPECTED_TOOLS:
        assert anonymous[name] == with_account[name], name


async def test_gate_asks_the_user_instead_of_querying_anonymously():
    """A3: po nieudanym logowaniu narzędzie nie rusza sieci, tylko stawia pytanie."""
    client = StubClient(AuthState.NEEDS_DECISION)
    result = await server.search_items(ctx=stub_ctx(client), query="cancer")

    assert result["needs_user_decision"] is True
    assert "continue_anonymously" in result["error"]
    assert "wrong password" in result["error"]


async def test_gate_lets_work_through_once_the_user_chose_anonymous():
    client = StubClient(AuthState.ANONYMOUS_BY_CHOICE)
    result = await server.continue_anonymously(ctx=stub_ctx(client))
    assert result["mode"] == "anonymous_by_choice"


async def test_continue_anonymously_is_not_blocked_by_the_gate():
    """Narzędzie odblokowujące, zablokowane przez bramkę, byłoby ślepą uliczką."""
    client = StubClient(AuthState.NEEDS_DECISION)
    result = await server.continue_anonymously(ctx=stub_ctx(client))
    assert client.accepted is True
    assert "needs_user_decision" not in result
