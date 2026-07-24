"""Read-only MCP server for DSpace 7+ repositories."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Wersja pochodzi z metadanych zainstalowanego pakietu, więc jedynym
    # źródłem prawdy zostaje `pyproject.toml` — tak samo, jak przy pakowaniu
    # `.mcpb` traktuje go workflow. Wcześniej numer był tu zaszyty i nikt go
    # nie podbijał: `--version` oraz nagłówek `User-Agent` wysyłany do KAŻDEGO
    # odpytywanego repozytorium przedstawiały się jako 0.1.0 długo po wydaniu
    # 0.2. Przy identyfikacji, która ma chronić przed banem IP, fałszywy numer
    # wersji jest gorszy niż jego brak.
    __version__ = version("dspace-mcp")
except PackageNotFoundError:  # uruchomienie z drzewa źródeł, bez instalacji
    __version__ = "0.0.0+unknown"
