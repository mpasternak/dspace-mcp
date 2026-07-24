# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A **read-only** MCP server that lets an LLM query any DSpace 7+ repository over its
REST API. No writes, one DSpace instance per process. Published on PyPI as `dspace-mcp`.

Anonymous by default. Optionally, credentials can be configured to *also read*
non-public material (design `2026-07-24-optional-read-authentication-design.md`,
decisions A1–A9) — which makes the login `POST` the single non-GET request in the
codebase. Read scope became configurable; inability to write did not.

## Commands

```bash
uv sync --dev                          # install (including dev deps)
uv run pytest                          # unit tests, offline (default: -m 'not live')
uv run pytest -m live                  # contract tests against a live instance
uv run pytest tests/test_tools.py      # one file
uv run pytest -k some_test_name        # one test by name substring
uv run ruff check .                    # lint
uv run ruff format --check .           # format check (CI runs this; --fix to apply)
uv run dspace-mcp --base-url https://demo.dspace.org/server   # run the server
```

- Live tests hit `demo.dspace.org` by default; point `DSPACE_TEST_URL` at another instance to retarget.
- CI runs lint + format check + `pytest -q` across Python 3.10–3.13. `pytest -q` excludes live tests via `addopts`.

## Architecture

A strict layered pipeline. Data flows **inward** (MCP → tools → client → network)
and shaped results flow back **outward**. Respect the layer boundaries when editing.

- **`server.py`** — thin MCP adapter. Each tool is a `@_guard`-wrapped async function
  whose **docstring is the model-facing tool description** (that text is what the LLM
  reads to choose a tool — treat it as UX, not comments). Bodies just unwrap the shared
  client and delegate to `tools.py`. `_guard` turns `DSpaceError` into a plain
  `{"error": "..."}` dict so the model gets an English sentence, never a stack trace.
  A single `DSpaceClient` lives for the whole process via the FastMCP lifespan.
  `_guard` is also the gate that blocks every tool after a failed login (decision A3),
  which is why `continue_anonymously` is the one tool **not** wrapped: guarding the tool
  that lifts the block would make it unreachable.
- **`tools.py`** — all orchestration logic, and it knows **nothing about MCP**. Every
  function takes a `DSpaceClient` and returns a plain dict, so tools are tested without
  running a server. List responses use the shared `_envelope(results, total, truncated)`.
- **`client.py`** — the **only** module that touches the network. Every data request is
  a **GET**; the one exception in the whole package is `_login()`, whose path is
  hard-coded (`f"{self._api_url}/authn/login"`, no path parameter) and which runs with
  `follow_redirects=False` — httpx replays 307/308 *with the body*, so a redirect would
  hand the password to another host. Owns URL joining, HTTP-status → model-readable
  error mapping, HAL pagination (`get_all` follows `_links.next` up to
  `MAX_PAGE_REQUESTS`), the startup probe, capability detection, and the auth state
  machine. This funnel is the project's core safety guarantee.
  Every request method takes `anonymous: bool` — the token lives in a client *field* and
  is attached per request, never on the shared `httpx.AsyncClient`, so the anonymous
  track (its own client, its own cookie jar) is genuinely anonymous.
- **`shaping.py`** — **pure** functions (no I/O, no imports beyond stdlib) that flatten
  HAL/DSpace JSON into compact records. **Nothing here may raise**: instance responses
  are untrusted input, so a missing key or wrong-typed value must yield empty output, not
  an exception (`_as_dict` is the workhorse for this). Tested directly against raw fixtures.
- **`config.py`** — frozen `Config` dataclass, built from env vars or CLI flags (flag >
  env > default). **`extractors/`** — a package of pure `bytes → text` extractors
  (`pdf`, `ooxml` for docx/pptx/xlsx, `opendocument` for odt/ods/odp, `msword` for
  legacy `.doc`) behind a mimetype→extractor `dispatch()`; all raise `ExtractError`.
  Non-stdlib deps here are only `olefile` (legacy `.doc`) and `defusedxml`
  (safe XML parsing of untrusted files); the ZIP+XML formats use `zipfile` +
  `defusedxml.ElementTree`.

## Conventions that carry real intent

- **Two error types cross the boundary to the model**: `DSpaceError` and `ExtractError`.
  Their `message` is always **English** (this is an international package whose consumer
  is an LLM). When adding failures, raise one of these with an actionable sentence — a
  message the model can turn into a corrected query or a question to the user. Never let a
  raw Spring Boot error body (`"An exception has occurred"`) or a stack trace reach the model.
- **Validate UUIDs before sending** (`require_uuid`): DSpace answers a malformed UUID in a
  path with **401 "Authentication required"** (not 400), which sends the model hunting for
  a login. See `tests/fixtures/dspace10_401_malformed_uuid.json`.
- **Never branch on DSpace version.** Capabilities (search filters, sort fields, facets)
  are configured per-instance via `discovery.xml` and vary between two sites on the *same*
  version. Ask the instance (`client.capabilities()`, `get_repository_info`) instead of
  assuming; `parse_version` exists only for reporting. Using an unknown filter → 422.
- **Case-insensitive metadata fallback** (`metadata_values`): exact key match first, then
  case-insensitive — real repos ship inconsistent DC casing (`dc.relation.isPartOf` vs
  `.ispartof`). DSpace forbids keys differing only in case, so this is collision-free.
- **`limit=0` means "count only"** in `search_items` (sends `size=1`, returns just `total`).
  This plus `list_facet_values` answers "how many" questions in one request — prefer them
  over downloading records to count.
- Some empirically-hard-won HTTP details live in `build_http`: `follow_redirects=True` is
  required (`/pid/find` returns 302; content redirects to S3), and the `Origin` header is
  **never** set (DSpace 403s even plain GETs when it's present).

## Tests

- Fixtures in `tests/fixtures/` are **raw, unmodified** responses captured from live DSpace
  instances (7.6.5, 8.x, 10.1, 11.0-SNAPSHOT) — their value is being byte-for-byte real, so
  don't hand-edit them (pre-commit excludes them from whitespace fixers).
- Unit tests mock HTTP with `respx` (`@respx.mock`). Two tests guard the read-only
  guarantee, and any new request path must keep both green:
  `test_client_sends_get_everywhere_except_the_login_endpoint` asserts every request is a
  GET except POSTs whose URL **equals** `f"{API}/authn/login"` (equality, not `endswith` —
  `https://evil.test/authn/login` must not pass), and
  `test_no_mutating_http_method_exists_anywhere_in_the_package` greps the package source
  for exactly one `.post(` and zero `.put(`/`.patch(`/`.delete(`/`.request(`.
- Some auth fixtures were necessarily captured **logged in** and via **POST**, unlike every
  other fixture — see `tests/fixtures/README.md`. No successful-login response is stored,
  because it would carry a real JWT.
- `asyncio_mode = "auto"` — async tests need no decorator.

## Design rationale

The full design and its rationale — including several plausible-sounding assumptions about
the DSpace API that turned out to be wrong — live in
`docs/superpowers/specs/2026-07-22-dspace-mcp-read-only-design.md`. Code comments reference
its decision numbers (D1–D8); read it before changing scope (e.g. adding write support or
multi-instance handling, both deliberately out of scope). Note: `Config` already carries
unused `username`/`password`/`enable_write` fields so the config format need not change if a
write mode is ever added (D7) — they are read by nothing today.

Note: source docstrings and comments are in **Polish**; all model- and user-facing strings
are in **English**. Keep that split.
