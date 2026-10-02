'''
This script contains the unit tests for the business logic.
1. get_employee_info()
2. get_policy_limits()
3. check_request_eligibility()
4. flag_for_human_review().
'''

from __future__ import annotations

import json
from datetime import datetime

import pytest

import business_logic as bl


@pytest.fixture(autouse=True)
def _clean_escalations():
    """Clear the escalation queue and truncate the file."""
    bl.clear_escalation_queue(truncate_file=True)
    yield
    bl.clear_escalation_queue(truncate_file=True)


# --- get_employee_info ---


def test_get_employee_info_valid_engineering():
    """EMP-101: valid engineering employee."""
    result = bl.get_employee_info("EMP-101")
    assert result["ok"] is True
    assert result["employee_id"] == "EMP-101"
    assert result["role"] == "Engineering"
    assert result["hire_date"] == "2019-03-15"
    assert any(i["item"] == "laptop" for i in result["inventory"])


def test_get_employee_info_nonexistent():
    """EMP-999: nonexistent employee."""
    result = bl.get_employee_info("EMP-999")
    assert result["ok"] is False
    assert result["error"] == "employee_not_found"


def test_get_employee_info_invalid_id():
    """Invalid employee ID: empty string."""
    result = bl.get_employee_info("")
    assert result["ok"] is False
    assert result["error"] == "invalid_employee_id"

    result = bl.get_employee_info("USER-1")
    assert result["ok"] is False
    assert result["error"] == "invalid_employee_id"


# --- get_policy_limits ---


def test_get_policy_limits_engineering():
    """Engineering role policy limits."""
    result = bl.get_policy_limits("Engineering")
    assert result["ok"] is True
    assert result["limits"]["laptop"] == {"max_qty": 1, "min_years": 3}
    assert result["limits"]["monitor"] == {"max_qty": 2, "min_years": 2}
    assert result["limits"]["ergonomic_chair"] == {"max_qty": 1, "min_years": 5}


def test_get_policy_limits_unknown_role():
    """Unknown role: Intern."""
    result = bl.get_policy_limits("Intern")
    assert result["ok"] is False
    assert result["error"] == "unknown_role"


# --- check_request_eligibility: approve / deny / escalate hints ---


def test_eligible_approve_engineering_laptop_four_years():
    """EMP-101: laptop issued 2022-09-01 vs Engineering 3-year rule → approve."""
    result = bl.check_request_eligibility("EMP-101", "laptop")
    assert result["ok"] is True
    assert result["eligible"] is True
    assert result["decision_hint"] == "approve"
    assert result["exceeds_frequency"] is False
    assert result["last_issued"] == "2022-09-01"
    assert result["years_since_last"] >= 3


def test_deny_sales_laptop_six_months():
    """EMP-102: laptop ~6 months ago, Sales 4-year rule → deny / exceeds_frequency."""
    result = bl.check_request_eligibility("EMP-102", "laptop")
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["decision_hint"] == "deny"
    assert result["exceeds_frequency"] is True
    assert result["last_issued"] == "2026-04-01"
    assert result["next_eligible_date"] == "2030-04-01"


def test_exceeds_frequency_early_engineering_for_agent_escalation():
    """EMP-103 early laptop: exceeds_frequency so agent can escalate on damage claim."""
    result = bl.check_request_eligibility("EMP-103", "laptop")
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["exceeds_frequency"] is True
    assert result["decision_hint"] == "deny"
    assert result["last_issued"] == "2025-10-01"


def test_non_standard_item_escalate_hint():
    """EMP-101: standing desk → escalate, do not guess."""
    result = bl.check_request_eligibility("EMP-101", "standing_desk")
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["decision_hint"] == "escalate"
    assert result["exceeds_frequency"] is False
    assert "standard catalog" in result["reasons"][0].lower() or "not on the standard" in result["reasons"][0]


def test_missing_tenure_data_escalate():
    """EMP-105: laptop with null issued_date → escalate, do not guess."""
    result = bl.check_request_eligibility("EMP-105", "laptop")
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["decision_hint"] == "escalate"
    assert "issued_date" in result["reasons"][0].lower() or "missing" in result["reasons"][0].lower()


def test_eligibility_invalid_employee():
    """EMP-999: nonexistent employee."""
    result = bl.check_request_eligibility("EMP-999", "laptop")
    assert result["ok"] is False
    assert result["error"] == "employee_not_found"


def test_management_chair_never_owned_approve():
    """EMP-104: ergonomic chair never owned → approve."""
    result = bl.check_request_eligibility("EMP-104", "ergonomic_chair")
    assert result["ok"] is True
    assert result["eligible"] is True
    assert result["decision_hint"] == "approve"


def test_management_second_monitor_under_max_qty():
    """EMP-104 has 1 monitor; Management max is 2 → approve additional."""
    result = bl.check_request_eligibility("EMP-104", "monitor")
    assert result["ok"] is True
    assert result["eligible"] is True
    assert result["decision_hint"] == "approve"
    assert result["owned_count"] == 1


# --- flag_for_human_review side effects ---


def test_flag_for_human_review_queue_and_timestamp():
    """EMP-103: laptop → flag for human review."""
    result = bl.flag_for_human_review(
        "EMP-103",
        "laptop",
        "Screen crushed on client site; early replacement needed",
    )
    assert result["ok"] is True
    assert result["flagged"] is True
    assert result["queue_size"] == 1

    entry = result["entry"]
    assert entry["employee_id"] == "EMP-103"
    assert entry["request"] == "laptop"
    assert "crushed" in entry["reason"].lower()
    # ISO-8601 parseable timestamp
    datetime.fromisoformat(entry["timestamp"])

    assert len(bl.ESCALATION_QUEUE) == 1
    assert bl.ESCALATION_QUEUE[0]["employee_id"] == "EMP-103"

    # JSONL append side effect
    assert bl.ESCALATIONS_PATH.exists()
    lines = [ln for ln in bl.ESCALATIONS_PATH.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    saved = json.loads(lines[0])
    assert saved["employee_id"] == "EMP-103"
    assert saved["reason"] == entry["reason"]


def test_flag_for_human_review_requires_fields():
    """Empty fields: employee_id, request, reason."""
    assert bl.flag_for_human_review("", "laptop", "reason")["ok"] is False
    assert bl.flag_for_human_review("EMP-101", "", "reason")["ok"] is False
    assert bl.flag_for_human_review("EMP-101", "laptop", "")["ok"] is False
    assert bl.flag_for_human_review("EMP-101", "laptop", None)["ok"] is False