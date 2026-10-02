'''
This script connects to the IT-Equipment-Server over stdio and performs the following steps:
1. Initializes the connection
2. Lists the tools
3. Calls the get_employee_info() tool
4. Prints the result
'''

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent


async def main() -> None:
    """Main function to connect to the IT-Equipment-Server over stdio."""
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        cwd=str(ROOT),
    )

    print("Connecting to IT-Equipment-Server over stdio...")
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            init_result = await session.initialize()
            print("\n=== Handshake (initialize) ===")
            print(f"Protocol: {init_result.protocolVersion}")
            print(f"Server:   {init_result.serverInfo.name}")
            if init_result.serverInfo.version:
                print(f"Version:  {init_result.serverInfo.version}")

            print("\n=== Tools discovered (list_tools) ===")
            tools = (await session.list_tools()).tools
            for tool in tools:
                desc = (tool.description or "").splitlines()[0]
                print(f"- {tool.name}: {desc}")
                if tool.inputSchema:
                    print(f"  inputSchema: {json.dumps(tool.inputSchema, indent=2)}")

            print('\n=== call_tool("get_employee_info", {"employee_id": "EMP-101"}) ===')
            result = await session.call_tool(
                "get_employee_info",
                {"employee_id": "EMP-101"},
            )
            if result.isError:
                print("Tool returned an error:")
            for block in result.content:
                text = getattr(block, "text", None)
                if text is not None:
                    try:
                        parsed = json.loads(text)
                        print(json.dumps(parsed, indent=2))
                    except json.JSONDecodeError:
                        print(text)

            print("\nPhase 3 handshake complete.")


if __name__ == "__main__":
    asyncio.run(main())
