"""Entry point for the `.mcpb` bundle (one-click install into Claude Desktop).

A bundle's `server.entry_point` has to be a plain script, but
`dspace_mcp.server` uses package-relative imports (`from .client import ...`)
and so cannot be executed as a top-level file. This launcher imports the
package instead: `uv` installs the project from the bundled `pyproject.toml`
before running us, which puts `dspace_mcp` on the path.

Kept out of `src/` on purpose — it is packaging glue, not part of the
distributed wheel.
"""

from dspace_mcp.server import main

if __name__ == "__main__":
    main()
