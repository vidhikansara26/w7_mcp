'''
This script starts the IT-Equipment-Server over stdio.
1. Initializes the connection
2. Lists the tools
3. Calls the get_employee_info() tool
4. Prints the result
'''

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import FastMCP

from business_logic import get_employee_info as _get_employee_info

mcp = FastMCP("IT-Equipment-Server")


@mcp.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Look up an employee record from the mock IT equipment database.

    Problem: agents need authoritative role, tenure, and hardware inventory
    before applying replacement policy; free-text claims about role are not trusted.

    When to call: at the start of an equipment request, whenever you have an
    employee_id (pattern EMP-###) and need role / hire_date / inventory.

    Args:
        employee_id: Employee identifier such as \"EMP-101\".

    Returns:
        On success: {ok, employee_id, name, role, hire_date, inventory}.
        On failure: {ok: false, error, message, ...} for invalid or missing ids.
    """
    return _get_employee_info(employee_id)


if __name__ == "__main__":
    mcp.run(transport="stdio")
