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

import business_logic as bl

mcp = FastMCP("IT-Equipment-Server")


@mcp.tool()
def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Look up an employee record from the mock IT equipment database.

    Problem this solves: agents need the authoritative role, tenure, and hardware
    inventory before applying replacement policy. Free-text role claims are not trusted.

    When to call: at the start of an equipment request once you have an employee_id
    (pattern EMP-###), or whenever inventory/role must be re-checked.

    Args:
        employee_id: Employee identifier such as "EMP-101".

    Returns:
        Success: {ok, employee_id, name, role, hire_date, inventory}.
        Failure: {ok: false, error, message, ...} for invalid or missing ids.
    """
    return bl.get_employee_info(employee_id)


@mcp.tool()
def find_employee(name: str, role: str = "") -> dict[str, Any]:
    """Resolve EMP-### from a person's name and optional department/role.

    Problem this solves: users often identify themselves by name and department
    instead of employee_id. Other tools require EMP-### and must not receive a
    display name as employee_id.

    When to call: at the start when the request has a name (e.g. "Alex Rivera")
    and/or department but no EMP-###. Then call get_employee_info with the
    returned employee_id. Claimed department only disambiguates; record role wins.

    Args:
        name: Full or partial employee name, e.g. "Alex Rivera".
        role: Optional claimed department/role (Engineering, Sales, Operations,
              Management). Pass "" if unknown.

    Returns:
        Success: {ok, employee_id, name, role, matched_by, note}.
        Failure: {ok: false, error, message, candidates?} if missing/ambiguous.
    """
    return bl.find_employee(name, role or None)


@mcp.tool()
def get_policy_limits(role: str) -> dict[str, Any]:
    """Fetch per-item quantity and frequency limits for a job role.

    Problem this solves: eligibility depends on role-specific max quantity and
    minimum years between issuances for laptop, monitor, and ergonomic_chair.

    When to call: after you know the employee's authoritative role from
    get_employee_info, and before or while interpreting check_request_eligibility.
    Do not invent limits from memory — always read them from this tool.

    Args:
        role: One of Engineering, Sales, Operations, Management
              (alias "Manager" is normalized to Management).

    Returns:
        Success: {ok, role, limits: {item: {max_qty, min_years}}, standard_items}.
        Failure: {ok: false, error, message} for unknown or empty roles.
    """
    return bl.get_policy_limits(role)


@mcp.tool()
def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    """Evaluate whether an employee may receive a requested equipment item.

    Problem this solves: composes employee inventory with role policy into a
    deterministic eligibility result (approve / deny / escalate hints) without
    duplicating policy math in the agent. Does NOT inspect free-text reason.

    When to call: optional diagnostic after resolving employee_id and item.
    Prefer evaluate_request for the authoritative approve/deny/escalate decision
    (it applies exceptional-justification phrases on top of this eligibility).

    Args:
        employee_id: Employee identifier such as "EMP-101".
        item: Requested item key (e.g. "laptop", "monitor", "ergonomic_chair",
              or a non-standard name like "standing_desk").

    Returns:
        Success shape includes ok, eligible, decision_hint, exceeds_frequency,
        last_issued, next_eligible_date, owned_count, reasons, and often policy.
        Failure: structured error if the employee id is invalid or not found.
    """
    return bl.check_request_eligibility(employee_id, item)


@mcp.tool()
def evaluate_request(employee_id: str, item: str, reason: str) -> dict[str, Any]:
    """Apply the full policy procedure and return {decision, rule}.

    Problem this solves: eligibility math alone cannot decide early-refresh cases;
    free-text exceptional justification must be applied deterministically. This
    tool is the authoritative decision — do not invent approve/deny/escalate.

    When to call: after you know employee_id and the requested item, with the
    requester's free-text reason. Then:
      - decision=approve or deny → draft Final Decision with that decision
      - decision=escalate → call flag_for_human_review, then Final Decision escalate

    Args:
        employee_id: Employee identifier such as "EMP-101".
        item: Requested item key (e.g. "laptop", "standing_desk").
        reason: Free-text justification from the requester.

    Returns:
        Success: {ok, decision, rule, eligibility, reasons,
        has_exceptional_justification, next_action?}.
        Failure: ok=false with rule/error when lookup fails or reason is empty.
    """
    return bl.evaluate_request(employee_id, item, reason)


@mcp.tool()
def flag_for_human_review(employee_id: str, request: str, reason: str) -> dict[str, Any]:
    """Queue an equipment request for human review (required for all escalations).

    Problem this solves: bounded ambiguity must not be resolved by guessing —
    non-standard gear, early refresh with exceptional justification, or missing /
    conflicting tenure data need a human. This tool records the escalation.

    When to call: when evaluate_request returns decision=escalate (or next_action
    is flag_for_human_review). Call before Final Decision: escalate. Do not call
    for clear approve or clear deny paths.

    Args:
        employee_id: Employee identifier such as "EMP-103".
        request: Short request summary (usually the item key, e.g. "laptop").
        reason: Why human review is required (policy conflict, damage claim, etc.).

    Returns:
        Success: {ok, flagged, queue_size, entry, message}.
        entry is {timestamp, employee_id, request, reason}.
        Failure: {ok: false, error, message} if required fields are empty.
    """
    return bl.flag_for_human_review(employee_id, request, reason)


if __name__ == "__main__":
    mcp.run(transport="stdio")
