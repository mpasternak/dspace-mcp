# dspace-mcp

[![CI](https://github.com/mpasternak/dspace-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/mpasternak/dspace-mcp/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/dspace-mcp)](https://pypi.org/project/dspace-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/dspace-mcp)](https://pypi.org/project/dspace-mcp/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

[![Install in Claude Desktop](https://img.shields.io/badge/Install_in-Claude_Desktop-D97757?style=for-the-badge&logo=anthropic&logoColor=white)](https://github.com/mpasternak/dspace-mcp/releases/latest/download/dspace-mcp.mcpb)
[![Install in Cursor](https://img.shields.io/badge/Install_in-Cursor-000000?style=for-the-badge&logo=cursor&logoColor=white)](https://cursor.com/en/install-mcp?name=dspace-mcp&config=eyJjb21tYW5kIjoidXZ4IiwiYXJncyI6WyJkc3BhY2UtbWNwIl0sImVudiI6eyJEU1BBQ0VfQkFTRV9VUkwiOiIifX0=)
[![Install in VS Code](https://img.shields.io/badge/Install_in-VS_Code-0098FF?style=for-the-badge&logo=visualstudiocode&logoColor=white)](https://vscode.dev/redirect?url=vscode:mcp/install?%7B%22name%22%3A%22dspace-mcp%22%2C%22command%22%3A%22uvx%22%2C%22args%22%3A%5B%22dspace-mcp%22%5D%2C%22env%22%3A%7B%22DSPACE_BASE_URL%22%3A%22%22%7D%7D)

> The Claude Desktop installer prompts for your repository URL. The Cursor and
> VS Code links carry `DSPACE_BASE_URL` empty on purpose — fill in your own
> instance (the `/server` REST endpoint), or the server refuses to start rather
> than guess and hand you someone else's repository.

A read-only [MCP](https://modelcontextprotocol.io/) server that lets an AI
assistant talk to any [DSpace](https://dspace.org/) 7+ repository.

Ask your repository questions in plain language — *"how many theses did we
publish in 2025?"*, *"which authors appear most often in this collection?"*,
*"summarise the PDF attached to this item"* — and the assistant answers by
querying the DSpace REST API directly.

It works **anonymously out of the box**: no account, no configuration beyond the
repository URL, and it sees exactly what any visitor sees. You can **optionally**
give it a DSpace username and password, and it will then also read what that
account can read — embargoed items, restricted collections and closed files —
and can tell you which files the public cannot reach. Logging in is never
required, and it never grants the ability to change anything.

## Read-only by construction, not by promise

The server cannot deposit, edit or delete anything. Every request that carries
data is a `GET`; the only other request it can make in its entire codebase is
the login `POST` described below, to one hard-coded path. There is no code
path that sends `PUT`, `PATCH`, `DELETE`, or a `POST` anywhere else — so there
is nothing to prompt-inject your way into.

That is a property of the code, not of the model's behaviour, and the test
suite holds it in place: one test asserts that the only non-`GET` request ever
sent goes to exactly `<base-url>/api/authn/login`, and another reads the
package source to confirm no mutating method appears anywhere in it.

By default the server queries **anonymously** and sees exactly what any visitor
sees: embargoed items, workflow submissions and restricted collections stay
invisible. Give it an account (below) and it reads what that account can read —
and still cannot change a thing.

## Reading non-public material (optional)

Set a username and password and the server logs in at startup, then reads with
your account's permissions:

```bash
export DSPACE_USERNAME='you@example.org'
export DSPACE_PASSWORD='...'
uvx dspace-mcp --base-url https://repo.example.org/server
```

**Use the least-privileged account that covers what you need.** The server
reads everything that account is allowed to read, so pointing it at an
administrator account gives the assistant an administrator's view. It cannot
modify anything either way, but it can *see* a great deal.

Logging in adds one tool, `compare_access`, which answers the question this
feature exists for — *"the user says files are missing"*:

```
compare_access(item="123456789/4271")
→ { "visible_to_anonymous": true,
    "files": { "both": ["abstract.pdf"],
               "authenticated_only": ["full-text.pdf"] },
    "summary": "1 of 2 file(s) are not available to anonymous users." }
```

It asks twice — once as your account, once anonymously through a separate
connection with its own cookie jar — and reports only the difference.

Three things worth knowing:

- **If the login fails, the server stops and asks.** It does not quietly fall
  back to anonymous access, because that would make the assistant report *"no
  such record"* for material you can plainly see. Instead every tool returns a
  question, and the assistant puts the choice to you: fix the credentials and
  restart, or explicitly continue with public data only.
- **Only password login is supported.** Repositories that authenticate solely
  through ORCID or Shibboleth need a browser, which a stdio process has no way
  to open. The server detects this from the instance itself and says so rather
  than failing obscurely.
- **Prefer the environment variable over `--password`.** A command line is
  visible to every process on the machine. The `.mcpb` bundle stores the
  password in your operating system's keychain.

## Install

Nothing to install by hand, and nothing to keep running. Your assistant starts
the server itself, when it needs it, and talks to it over the process's standard
input and output. So the only real step is
[telling your client how to launch it](#configure-your-mcp-client) — with
[uv](https://docs.astral.sh/uv/) present, that is the whole setup.

If you would rather have the command on your `PATH`, `pip install dspace-mcp`
works too, but it is not required.

### Checking it works, before wiring it up

You can run it yourself to see whether it reaches your repository:

```bash
uvx dspace-mcp --base-url https://demo.dspace.org/server
```

It prints a startup line and then sits there in silence. **That is what success
looks like**: it is waiting for an MCP client on its standard input. Press Ctrl-C
to quit.

This is a smoke test, not a service. You cannot point an assistant at the process
you just started — every client launches its own copy — and there is no reason to
leave it running. It is useful for exactly two things: confirming the base URL is
right, and, if you configured an account, seeing whether the login succeeded. A
failed login says so on that first line, naming the reason.

## Configure your MCP client

### Claude Code (command line)

**Anonymously** — sees what any visitor sees:

```bash
claude mcp add dspace -- uvx dspace-mcp@latest \
  --base-url https://repo.example.org/server
```

**With an account** — also reads what that account can read. Credentials go in
`-e` variables, and they must come **before** the `--`, because everything after
it is handed to the server as its own arguments:

```bash
claude mcp add dspace \
  -e DSPACE_USERNAME='you@example.org' \
  -e DSPACE_PASSWORD='your-password' \
  -- uvx dspace-mcp@latest --base-url https://repo.example.org/server
```

To keep the password out of your shell history, read it in first:

```bash
read -rs DSPACE_PASSWORD
claude mcp add dspace \
  -e DSPACE_USERNAME='you@example.org' \
  -e "DSPACE_PASSWORD=$DSPACE_PASSWORD" \
  -- uvx dspace-mcp@latest --base-url https://repo.example.org/server
```

Then check it:

```bash
claude mcp list          # should show: dspace: uvx dspace-mcp@latest … - ✔ Connected
```

Inside a Claude Code session, `/mcp` lists the server and its tools. Ask
*"what repository are you connected to?"* and the assistant will call
`get_repository_info`, which also reports whether it is querying anonymously or
as your account.

Useful extras:

- `-s user` registers the server for all your projects instead of only the
  current directory.
- `claude mcp remove dspace` undoes it; re-add to change the URL or credentials.

**Why `dspace-mcp@latest` and not plain `dspace-mcp`:** `uvx` may reuse a copy
already sitting in uv's cache, which can be an older release — that is how you
end up running a version you thought you had upgraded past. `@latest` asks for
the newest each time; pin a specific one (`dspace-mcp@0.3.2`) if you would rather
control upgrades yourself. In a terminal, `uvx --refresh dspace-mcp …` forces a
one-off update.

**Where the password ends up:** in Claude Code's configuration file, in plain
text. Use an account with the least privilege that covers what you need. If you
want it in your operating system's keychain instead, install the `.mcpb` bundle —
the badge at the top of this page — which asks for the password in a form and
stores it there.

### Claude Desktop, Cursor, VS Code — any client using `mcp.json`

Anonymously:

```json
{
  "mcpServers": {
    "dspace": {
      "command": "uvx",
      "args": ["dspace-mcp@latest", "--base-url", "https://repo.example.org/server"]
    }
  }
}
```

With an account, add an `env` block. Omit it, or leave both fields empty, and the
server queries anonymously:

```json
{
  "mcpServers": {
    "dspace": {
      "command": "uvx",
      "args": ["dspace-mcp@latest", "--base-url", "https://repo.example.org/server"],
      "env": {
        "DSPACE_USERNAME": "you@example.org",
        "DSPACE_PASSWORD": "your-password"
      }
    }
  }
}
```

For Claude Desktop the `.mcpb` bundle is easier than editing this file by hand,
and it keeps the password in your keychain rather than in plain text.

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
| `get_bitstream_text` | Extract the text of a bitstream so the assistant can read or summarise it — PDF, Word (.docx/.doc), OpenDocument (.odt/.ods/.odp) and Office XML (.pptx/.xlsx). |
| `list_facet_values` | Count values of a facet (authors, subjects, years) — the repository does the counting, so no records are downloaded. |
| `get_item_statistics` | View count of an item. |
| `get_repository_info` | Name, version, item counts, which search filters, sort fields and facets this instance actually supports, and whether the server is querying anonymously or as an account. |

Two more tools appear **only if you configure an account** — an anonymous
install never sees them:

| Tool | What it does |
|---|---|
| `compare_access` | Compare what your account can see against what the public can see, for one item. Answers *"the user says files are missing"*. |
| `continue_anonymously` | Only reachable if the login failed: lets you choose to carry on with public data instead of fixing the credentials. |

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
| `DSPACE_EXTRACT_MAX_MB` | `20` | refuse to download bitstreams larger than this for text extraction |
| `DSPACE_USERNAME` | *(none)* | log in as this account to also read non-public material |
| `DSPACE_PASSWORD` | *(none)* | password for that account |

Every variable has a matching flag: `--base-url`, `--timeout`,
`--max-results`, `--extract-max-mb`, `--username`, `--password`.

Set both `DSPACE_USERNAME` and `DSPACE_PASSWORD` or neither — half an account
is a configuration mistake, and the server says so at startup instead of
silently querying anonymously.

`DSPACE_PDF_MAX_MB` / `--pdf-max-mb` still work as backward-compatible aliases
for `DSPACE_EXTRACT_MAX_MB` / `--extract-max-mb` from before text extraction
covered more than PDF.

## Compatibility

Nothing here branches on a version number. The server asks each instance
which search filters, sort fields and facets it supports and works from that
answer, which is what actually varies between installations — two sites on the
same DSpace version can differ more than two versions of the same site.

The offline test suite runs against recorded responses from a live DSpace
10.1-SNAPSHOT (`demo.dspace.org`), covering every endpoint the server touches,
plus root and search responses from a vanilla 7.6.5, a DSpace-CRIS 8.2 and an
11.0-SNAPSHOT for comparing response shape. The contract tests
(`pytest -m live`) run against one instance at a time — `demo.dspace.org` by
default, or point `DSPACE_TEST_URL` at your own.

While the tool was being designed, its assumptions about the REST API were
checked by hand against live instances of 7.2.1, 7.5, 7.6.5, 8.2, 8.4, 9.2,
10.1 and 11.0-SNAPSHOT. That was a survey of the API, not a test run of this
code. If it misbehaves on a version or a configuration I could not test,
[a bug report](https://github.com/mpasternak/dspace-mcp/issues) is welcome.

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
