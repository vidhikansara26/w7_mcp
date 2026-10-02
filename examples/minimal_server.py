"""Suggested Approach step 1–3: one trivial MCP tool before business logic.

Run: python examples/minimal_server.py
(Usually started by minimal_client.py over stdio.)
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Minimal-Ping-Server")


@mcp.tool()
def ping() -> str:
    """Trivial connectivity check. Returns the string pong."""
    return "pong"


if __name__ == "__main__":
    mcp.run(transport="stdio")
