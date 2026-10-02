"""Stdio MCP client smoke test — handshake and equipment policy tools."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent

EXPECTED_TOOLS = {
    "get_employee_info",
    "find_employee",
    "get_policy_limits",
    "check_request_eligibility",
    "evaluate_request",
    "flag_for_human_review",
}


def _print_tool_result(label: str, result: Any) -> None:
    print(f"\n=== {label} ===")
    if getattr(result, "isError", False):
        print("Tool returned an error:")
    for block in result.content:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            print(json.dumps(json.loads(text), indent=2))
        except json.JSONDecodeError:
            print(text)


async def run(all_tools: bool) -> None:
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
            names = {t.name for t in tools}
            for tool in tools:
                desc = (tool.description or "").splitlines()[0]
                print(f"- {tool.name}: {desc}")

            missing = EXPECTED_TOOLS - names
            if missing:
                raise SystemExit(f"Missing tools: {sorted(missing)}")
            print(f"\nRegistered tools OK: {sorted(names)}")

            result = await session.call_tool(
                "get_employee_info",
                {"employee_id": "EMP-101"},
            )
            _print_tool_result(
                'call_tool("get_employee_info", {"employee_id": "EMP-101"})',
                result,
            )

            if all_tools:
                r2 = await session.call_tool(
                    "get_policy_limits",
                    {"role": "Engineering"},
                )
                _print_tool_result(
                    'call_tool("get_policy_limits", {"role": "Engineering"})',
                    r2,
                )

                r3 = await session.call_tool(
                    "check_request_eligibility",
                    {"employee_id": "EMP-101", "item": "laptop"},
                )
                _print_tool_result(
                    'call_tool("check_request_eligibility", '
                    '{"employee_id": "EMP-101", "item": "laptop"})',
                    r3,
                )

                r_eval = await session.call_tool(
                    "evaluate_request",
                    {
                        "employee_id": "EMP-101",
                        "item": "laptop",
                        "reason": "Laptop is 4 years old and slow.",
                    },
                )
                _print_tool_result(
                    'call_tool("evaluate_request", '
                    '{"employee_id": "EMP-101", "item": "laptop", ...})',
                    r_eval,
                )

                r4 = await session.call_tool(
                    "flag_for_human_review",
                    {
                        "employee_id": "EMP-103",
                        "request": "laptop",
                        "reason": "Smoke test escalation entry",
                    },
                )
                _print_tool_result(
                    'call_tool("flag_for_human_review", ...)',
                    r4,
                )

            print("\nMCP client smoke test complete.")


def main() -> None:
    parser = argparse.ArgumentParser(description="IT Equipment MCP stdio client")
    parser.add_argument(
        "--all-tools",
        action="store_true",
        help="Smoke-call all policy tools (default: handshake + get_employee_info)",
    )
    args = parser.parse_args()
    asyncio.run(run(all_tools=args.all_tools))


if __name__ == "__main__":
    main()
