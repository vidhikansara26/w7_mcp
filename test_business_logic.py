'''
This script contains the unit tests for the business logic.
1. get_employee_info()
2. get_policy_limits()
3. check_request_eligibility()
4. evaluate_request()
5. flag_for_human_review().
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


def test_find_employee_by_name_and_role():
    result = bl.find_employee("Alex Rivera", "Engineering")
    assert result["ok"] is True
    assert result["employee_id"] == "EMP-101"
    assert result["role"] == "Engineering"


def test_find_employee_name_only():
    result = bl.find_employee("Jordan Lee")
    assert result["ok"] is True
    assert result["employee_id"] == "EMP-102"


def test_find_employee_not_found():
    result = bl.find_employee("Nobody Here")
    assert result["ok"] is False
    assert result["error"] == "employee_not_found"


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
    reason = result["reasons"][0].lower()
    assert "standard catalog" in reason or "not on the standard" in reason


def test_missing_tenure_data_escalate():
    """EMP-105: laptop with null issued_date → escalate, do not guess."""
    result = bl.check_request_eligibility("EMP-105", "laptop")
    assert result["ok"] is True
    assert result["eligible"] is False
    assert result["decision_hint"] == "escalate"
    reason = result["reasons"][0].lower()
    assert "issued_date" in reason or "missing" in reason


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


# --- evaluate_request: authoritative approve / deny / escalate ---


def test_evaluate_approve_engineering_laptop():
    """Graded approve: EMP-101 laptop within 3-year Engineering rule."""
    result = bl.evaluate_request(
        "EMP-101",
        "laptop",
        "My laptop is 4 years old and slow; need a replacement.",
    )
    assert result["ok"] is True
    assert result["decision"] == "approve"
    assert result["rule"] == "within_policy"
    assert result["eligibility"]["eligible"] is True


def test_evaluate_deny_sales_laptop_no_exception():
    """Graded deny: EMP-102 early laptop with only performance complaints."""
    result = bl.evaluate_request(
        "EMP-102",
        "laptop",
        "Laptop is freezing and old; please replace it.",
    )
    assert result["ok"] is True
    assert result["decision"] == "deny"
    assert result["rule"] == "exceeds_frequency"
    assert result["has_exceptional_justification"] is False


def test_evaluate_escalate_early_refresh_exceptional():
    """Graded escalate A: EMP-103 early laptop + crushed/stolen justification."""
    result = bl.evaluate_request(
        "EMP-103",
        "laptop",
        "Screen was crushed on a client site / stolen bag — broken screen.",
    )
    assert result["ok"] is True
    assert result["decision"] == "escalate"
    assert result["rule"] == "early_refresh_exceptional"
    assert result["has_exceptional_justification"] is True
    assert result["next_action"] == "flag_for_human_review"
    # Eligibility alone still says deny; evaluate_request upgrades on phrases.
    assert result["eligibility"]["decision_hint"] == "deny"
    assert result["eligibility"]["exceeds_frequency"] is True


def test_evaluate_escalate_non_standard_item():
    """Graded escalate B: non-standard catalog item."""
    result = bl.evaluate_request(
        "EMP-101",
        "ergonomic_split_keyboard",
        "Need a split keyboard for RSI.",
    )
    assert result["ok"] is True
    assert result["decision"] == "escalate"
    assert result["rule"] == "non_standard_item"
    assert result["next_action"] == "flag_for_human_review"


def test_evaluate_escalate_missing_tenure():
    """EMP-105 missing issued_date → escalate / missing_tenure."""
    result = bl.evaluate_request(
        "EMP-105",
        "laptop",
        "Need a laptop refresh; unsure when mine was issued.",
    )
    assert result["ok"] is True
    assert result["decision"] == "escalate"
    assert result["rule"] == "missing_tenure"


def test_evaluate_early_without_exception_stays_deny():
    """EMP-103 early laptop without exceptional phrases stays deny."""
    result = bl.evaluate_request(
        "EMP-103",
        "laptop",
        "It feels slow and I want a newer model.",
    )
    assert result["ok"] is True
    assert result["decision"] == "deny"
    assert result["rule"] == "exceeds_frequency"


def test_evaluate_unknown_employee():
    result = bl.evaluate_request("EMP-999", "laptop", "Need a laptop.")
    assert result["ok"] is False
    assert result["decision"] == "escalate"
    assert result["rule"] == "employee_not_found"


def test_evaluate_requires_nonempty_reason():
    result = bl.evaluate_request("EMP-101", "laptop", "")
    assert result["ok"] is False
    assert result["error"] == "invalid_reason"


def test_has_exceptional_justification_phrases():
    assert bl.has_exceptional_justification("crushed on client site") is True
    assert bl.has_exceptional_justification("just slow") is False
    assert bl.has_exceptional_justification(None) is False


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
    raw = bl.ESCALATIONS_PATH.read_text(encoding="utf-8")
    lines = [ln for ln in raw.splitlines() if ln.strip()]
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