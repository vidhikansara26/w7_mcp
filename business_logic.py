"""Plain IT equipment policy functions (Phase 2). No MCP dependency."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
EMPLOYEES_PATH = DATA_DIR / "employees.json"
POLICIES_PATH = DATA_DIR / "policies.json"
ESCALATIONS_PATH = DATA_DIR / "escalations.jsonl"

# Fixed "today" for deterministic lab / tests (see docs/requirements.md).
REFERENCE_TODAY = date(2026, 10, 2)

STANDARD_ITEMS = frozenset({"laptop", "monitor", "ergonomic_chair"})

# In-memory escalation queue (process lifetime).
ESCALATION_QUEUE: list[dict[str, Any]] = []


def _load_json(path: Path) -> Any:
    """Load a JSON file and return the parsed data."""
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _employees() -> dict[str, Any]:
    """Load the employees data and return the parsed data."""
    return _load_json(EMPLOYEES_PATH)


def _policies() -> dict[str, Any]:
    """Load the policies data and return the parsed data."""
    return _load_json(POLICIES_PATH)


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    """Create an error response."""
    payload: dict[str, Any] = {"ok": False, "error": code, "message": message}
    payload.update(extra)
    return payload


def get_employee_info(employee_id: str) -> dict[str, Any]:
    """Return role, tenure fields, and hardware inventory for an employee."""
    if not employee_id or not isinstance(employee_id, str):
        return _error("invalid_employee_id", "employee_id must be a non-empty string")

    employee_id = employee_id.strip()
    if not employee_id.startswith("EMP-"):
        return _error(
            "invalid_employee_id",
            f"employee_id must look like EMP-###, got {employee_id!r}",
            employee_id=employee_id,
        )

    employees = _employees()
    record = employees.get(employee_id)
    if record is None:
        return _error(
            "employee_not_found",
            f"No employee found for id {employee_id}",
            employee_id=employee_id,
        )

    return {
        "ok": True,
        "employee_id": record["employee_id"],
        "name": record.get("name"),
        "role": record["role"],
        "hire_date": record.get("hire_date"),
        "inventory": list(record.get("inventory") or []),
    }


def find_employee(name: str, role: str | None = None) -> dict[str, Any]:
    """Resolve an employee_id from a person's name and optional claimed role/department.

    Role is used only to disambiguate matches. The returned record's role remains
    authoritative for policy (claimed department may differ).
    """
    if not name or not isinstance(name, str):
        return _error("invalid_name", "name must be a non-empty string")

    needle = " ".join(name.strip().lower().split())
    claimed_role = None
    if role and isinstance(role, str) and role.strip():
        claimed_role = role.strip()
        if claimed_role == "Manager":
            claimed_role = "Management"

    matches: list[dict[str, Any]] = []
    for record in _employees().values():
        record_name = " ".join(str(record.get("name", "")).lower().split())
        if record_name != needle and needle not in record_name:
            continue
        matches.append(
            {
                "employee_id": record["employee_id"],
                "name": record.get("name"),
                "role": record["role"],
            }
        )

    if not matches:
        return _error(
            "employee_not_found",
            f"No employee found matching name {name!r}",
            name=name,
            role=claimed_role,
        )

    if claimed_role:
        role_matches = [m for m in matches if m["role"] == claimed_role]
        if len(role_matches) == 1:
            chosen = role_matches[0]
            return {
                "ok": True,
                "employee_id": chosen["employee_id"],
                "name": chosen["name"],
                "role": chosen["role"],
                "matched_by": "name+role",
                "note": (
                    "Use this employee_id with get_employee_info / "
                    "check_request_eligibility. Record role is authoritative."
                ),
            }
        if not role_matches:
            return {
                "ok": False,
                "error": "role_mismatch",
                "message": (
                    f"Found name match(es) but none with role {claimed_role!r}. "
                    "Do not invent an id; ask the user to confirm or escalate."
                ),
                "name": name,
                "claimed_role": claimed_role,
                "candidates": matches,
            }

    if len(matches) == 1:
        chosen = matches[0]
        return {
            "ok": True,
            "employee_id": chosen["employee_id"],
            "name": chosen["name"],
            "role": chosen["role"],
            "matched_by": "name",
            "note": (
                "Use this employee_id with get_employee_info / "
                "check_request_eligibility. Record role is authoritative."
            ),
        }

    return {
        "ok": False,
        "error": "ambiguous_match",
        "message": "Multiple employees match that name; provide role or employee_id.",
        "name": name,
        "candidates": matches,
    }


def get_policy_limits(role: str) -> dict[str, Any]:
    """Return per-item max quantity and min years for a role."""
    if not role or not isinstance(role, str):
        return _error("invalid_role", "role must be a non-empty string")

    role = role.strip()
    # Accept common alias from free text; authoritative store uses Management.
    if role == "Manager":
        role = "Management"

    policies = _policies()
    role_limits = policies.get("roles", {}).get(role)
    if role_limits is None:
        return _error(
            "unknown_role",
            f"Unknown role {role!r}; expected Engineering, Sales, Operations, or Management",
            role=role,
        )

    return {
        "ok": True,
        "role": role,
        "limits": role_limits,
        "standard_items": list(policies.get("standard_items", list(STANDARD_ITEMS))),
    }


def _parse_date(value: Any) -> date | None:
    """Parse a date string and return a date object."""
    if value is None or value == "":
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _years_elapsed(start: date, end: date) -> float:
    """Approximate year span using days / 365.25 for interval checks."""
    return (end - start).days / 365.25


def _add_years(start: date, years: int) -> date:
    """Add years to a date and return a new date object."""
    try:
        return start.replace(year=start.year + years)
    except ValueError:
        # Feb 29 → Feb 28 in non-leap target year
        return start.replace(year=start.year + years, day=28)


def _normalize_item(item: str) -> str:
    """Normalize an item string to lowercase and replace spaces with underscores."""
    return item.strip().lower().replace(" ", "_")


def check_request_eligibility(employee_id: str, item: str) -> dict[str, Any]:
    """Compose employee + policy into an eligibility assessment.

    Does not inspect free-text justification. When frequency is exceeded, sets
    ``exceeds_frequency`` so the agent layer can escalate on exceptional reasons.
    """
    if not item or not isinstance(item, str):
        return _error("invalid_item", "item must be a non-empty string")

    item_key = _normalize_item(item)
    employee = get_employee_info(employee_id)
    if not employee.get("ok"):
        return employee

    # Non-standard catalog → escalate hint (no policy math).
    if item_key not in STANDARD_ITEMS:
        return {
            "ok": True,
            "eligible": False,
            "decision_hint": "escalate",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": None,
            "next_eligible_date": None,
            "owned_count": 0,
            "reasons": [
                f"Item {item_key!r} is not on the standard catalog "
                f"(laptop, monitor, ergonomic_chair); requires human review."
            ],
        }

    policy = get_policy_limits(employee["role"])
    if not policy.get("ok"):
        return policy

    limits = policy["limits"][item_key]
    max_qty = int(limits["max_qty"])
    min_years = int(limits["min_years"])

    matching = [e for e in employee["inventory"] if _normalize_item(e.get("item", "")) == item_key]
    owned_count = len(matching)

    # Detect missing / conflicting issue dates on matching inventory.
    parsed_dates: list[date] = []
    missing_or_invalid = False
    for entry in matching:
        raw = entry.get("issued_date")
        parsed = _parse_date(raw)
        if raw is None or raw == "" or parsed is None:
            missing_or_invalid = True
        else:
            parsed_dates.append(parsed)

    hire_date = _parse_date(employee.get("hire_date"))
    if matching and missing_or_invalid:
        return {
            "ok": True,
            "eligible": False,
            "decision_hint": "escalate",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": None,
            "next_eligible_date": None,
            "owned_count": owned_count,
            "policy": {"max_qty": max_qty, "min_years": min_years},
            "reasons": [
                "Missing or conflicting issued_date on inventory; "
                "cannot compute eligibility without guessing timelines."
            ],
        }

    # Optional conflict: issued before hire.
    if hire_date and parsed_dates and any(d < hire_date for d in parsed_dates):
        return {
            "ok": True,
            "eligible": False,
            "decision_hint": "escalate",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": max(parsed_dates).isoformat(),
            "next_eligible_date": None,
            "owned_count": owned_count,
            "policy": {"max_qty": max_qty, "min_years": min_years},
            "reasons": [
                "Inventory issued_date conflicts with hire_date; escalate for data cleanup."
            ],
        }

    today = REFERENCE_TODAY
    last_issued = max(parsed_dates) if parsed_dates else None
    last_issued_str = last_issued.isoformat() if last_issued else None

    # Never owned → approve path.
    if owned_count == 0:
        return {
            "ok": True,
            "eligible": True,
            "decision_hint": "approve",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": None,
            "next_eligible_date": today.isoformat(),
            "owned_count": 0,
            "policy": {"max_qty": max_qty, "min_years": min_years},
            "reasons": [f"No prior {item_key} on record; within policy."],
        }

    # Additional unit allowed under max quantity (e.g. second monitor).
    if owned_count < max_qty:
        return {
            "ok": True,
            "eligible": True,
            "decision_hint": "approve",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": last_issued_str,
            "next_eligible_date": today.isoformat(),
            "owned_count": owned_count,
            "policy": {"max_qty": max_qty, "min_years": min_years},
            "reasons": [
                f"Owned count {owned_count} is below max_qty {max_qty} for {employee['role']}."
            ],
        }

    assert last_issued is not None
    elapsed = _years_elapsed(last_issued, today)
    next_eligible = _add_years(last_issued, min_years)
    next_eligible_str = next_eligible.isoformat()

    if elapsed >= min_years:
        return {
            "ok": True,
            "eligible": True,
            "decision_hint": "approve",
            "exceeds_frequency": False,
            "employee_id": employee["employee_id"],
            "role": employee["role"],
            "item": item_key,
            "last_issued": last_issued_str,
            "next_eligible_date": next_eligible_str,
            "owned_count": owned_count,
            "policy": {"max_qty": max_qty, "min_years": min_years},
            "years_since_last": round(elapsed, 2),
            "reasons": [
                f"Last {item_key} issued {last_issued_str}; "
                f"{elapsed:.2f} years elapsed meets {min_years}-year {employee['role']} minimum."
            ],
        }

    return {
        "ok": True,
        "eligible": False,
        "decision_hint": "deny",
        "exceeds_frequency": True,
        "employee_id": employee["employee_id"],
        "role": employee["role"],
        "item": item_key,
        "last_issued": last_issued_str,
        "next_eligible_date": next_eligible_str,
        "owned_count": owned_count,
        "policy": {"max_qty": max_qty, "min_years": min_years},
        "years_since_last": round(elapsed, 2),
        "reasons": [
            f"Request exceeds frequency: last {item_key} issued {last_issued_str}; "
            f"only {elapsed:.2f} years elapsed, policy requires {min_years} for {employee['role']}. "
            "Agent may escalate if reason contains exceptional justification."
        ],
    }


def flag_for_human_review(employee_id: str, request: str, reason: str) -> dict[str, Any]:
    """Append an escalation record (in-memory + JSONL) and return confirmation."""
    if not employee_id or not isinstance(employee_id, str):
        return _error("invalid_employee_id", "employee_id must be a non-empty string")
    if not request or not isinstance(request, str):
        return _error("invalid_request", "request must be a non-empty string")
    if not reason or not isinstance(reason, str):
        return _error("invalid_reason", "reason must be a non-empty string")

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "employee_id": employee_id.strip(),
        "request": request.strip(),
        "reason": reason.strip(),
    }
    ESCALATION_QUEUE.append(entry)

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with ESCALATIONS_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")

    return {
        "ok": True,
        "flagged": True,
        "queue_size": len(ESCALATION_QUEUE),
        "entry": entry,
        "message": "Request flagged for human review.",
    }


def clear_escalation_queue(*, truncate_file: bool = False) -> None:
    """Test helper: clear in-memory queue; optionally truncate JSONL file."""
    ESCALATION_QUEUE.clear()
    if truncate_file and ESCALATIONS_PATH.exists():
        ESCALATIONS_PATH.write_text("", encoding="utf-8")
