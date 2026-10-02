"""Suggested Approach step 3: connect to the one-tool server and call ping."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    server = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "minimal_server.py")],
        cwd=str(ROOT.parent),
    )
    print("Connecting to Minimal-Ping-Server over stdio...")
    async with stdio_client(server) as (read, write):
        async with ClientSession(read, write) as session:
            init = await session.initialize()
            print("=== Handshake (initialize) ===")
            print(f"Protocol: {init.protocolVersion}")
            print(f"Server:   {init.serverInfo.name}")

            tools = (await session.list_tools()).tools
            names = [t.name for t in tools]
            print(f"Registered tools: {names}")

            result = await session.call_tool("ping", {})
            texts = [
                getattr(block, "text", None)
                for block in (result.content or [])
                if getattr(block, "text", None) is not None
            ]
            print(f"ping() -> {texts[0] if texts else result}")
            print("Minimal one-tool server/client connection confirmed.")


if __name__ == "__main__":
    asyncio.run(main())
