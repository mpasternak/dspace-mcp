# dspace-mcp

[![CI](https://github.com/mpasternak/dspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mpasternak/dspace-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dspace-mcp.svg)](https://pypi.org/project/dspace-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/dspace-mcp.svg)](https://pypi.org/project/dspace-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A read-only [MCP](https://modelcontextprotocol.io/) server that lets an AI
assistant talk to any [DSpace](https://dspace.org/) 7+ repository.

Ask your repository questions in plain language — *"how many theses did we
publish in 2025?"*, *"which authors appear most often in this collection?"*,
*"summarise the PDF attached to this item"* — and the assistant answers by
querying the DSpace REST API directly.

## Read-only by construction, not by promise

The server holds no credentials and never issues a request other than `GET`.
It cannot deposit, edit or delete anything, and it cannot read what the public
cannot already see — embargoed items, workflow submissions and restricted
collections stay invisible.

That is a property of the code, not of the model's behaviour: there is nothing
to prompt-inject your way into. A test in the suite asserts that no other HTTP
method ever leaves the process.

## Install

Nothing to install if you have [uv](https://docs.astral.sh/uv/):

```bash
uvx dspace-mcp --base-url https://demo.dspace.org/server
```

Or with pip:

```bash
pip install dspace-mcp
```

## Configure your MCP client

**Claude Code:**

```bash
claude mcp add dspace -- uvx dspace-mcp --base-url https://demo.dspace.org/server
```

**Claude Desktop / any client using `mcp.json`:**

```json
{
  "mcpServers": {
    "dspace": {
      "command": "uvx",
      "args": ["dspace-mcp", "--base-url", "https://demo.dspace.org/server"]
    }
  }
}
```

Point `--base-url` at your own repository's REST API — usually your DSpace URL
with `/server` appended. If you leave `/server` off, the server detects it and
corrects the URL for you.

To connect to several repositories, add several entries under different names.
One process serves exactly one repository, which keeps the assistant from
mixing them up.

## Tools

| Tool | What it does |
|---|---|
| `search_items` | Search items; filter by year range, author, collection; sort by relevance, date or title. `limit=0` returns just the count. |
| `get_item` | Fetch one item by **UUID, Handle or DOI** — whichever identifier you happen to have. |
| `list_communities` | Walk the community tree (up to 3 levels). |
| `list_collections` | List collections, of one community or of the whole repository. |
| `list_bitstreams` | List an item's files with sizes, MIME types, checksums and download URLs. |
| `get_bitstream_text` | Extract the text of a PDF so the assistant can read or summarise it. |
| `list_facet_values` | Count values of a facet (authors, subjects, years) — the repository does the counting, so no records are downloaded. |
| `get_item_statistics` | View count of an item. |
| `get_repository_info` | Name, version, item counts, and which search filters, sort fields and facets this instance actually supports. |

### Two things worth knowing

**Ask `get_repository_info` first.** DSpace installations differ: the set of
search filters and facets is configured per instance, so a filter that exists
on one repository returns an error on another. This tool reports what the
instance in front of you supports.

**Counting is free.** `search_items` with `limit=0`, and `list_facet_values`,
answer "how many" questions with a single request and a handful of tokens,
instead of downloading records and counting them.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `DSPACE_BASE_URL` | *(required)* | REST API root, e.g. `https://demo.dspace.org/server` |
| `DSPACE_TIMEOUT` | `15` | seconds per HTTP request |
| `DSPACE_MAX_RESULTS` | `50` | hard ceiling on how many records any tool may return |
| `DSPACE_PDF_MAX_MB` | `20` | refuse to download PDFs larger than this |

Every variable has a matching flag: `--base-url`, `--timeout`,
`--max-results`, `--pdf-max-mb`.

## Compatibility

Verified against DSpace 7.2.1, 7.5, 7.6.5, 8.2, 8.4, 9.2, 10.1 and
11.0-SNAPSHOT. The test suite runs against recorded responses from real
instances of versions 7, 8, 10 and 11.

## Development

```bash
git clone https://github.com/mpasternak/dspace-mcp
cd dspace-mcp
uv sync --dev
uv run pytest              # unit tests, offline
uv run pytest -m live      # contract tests against demo.dspace.org
uv run ruff check .
```

Contributions are welcome. The design and its rationale — including why
several plausible-sounding assumptions about the DSpace API turned out to be
wrong — live in `docs/superpowers/specs/`.

## License

MIT — see [LICENSE](LICENSE).
