"""Gold-trace scorer. A single miss fails."""

from __future__ import annotations

from score_traces import decision_in_trace, passes, score_cases, score_directory


def test_recorded_scenarios_meet_gold() -> None:
    matched, misses = score_directory()
    assert misses == []
    assert passes(matched, 4)


def test_one_miss_fails() -> None:
    found = {
        "01_approve": "approve",
        "02_deny": "approve",
        "03_escalate_damage": "escalate",
        "04_escalate_nonstandard": "escalate",
    }
    matched, misses = score_cases(found)
    assert matched == 3
    assert misses == ["02_deny: expected deny, got approve"]
    assert not passes(matched, 4)


def test_decision_line_ignores_the_draft_prefix() -> None:
    text = "Draft Final Decision: deny\nFinal Decision: approve\n"
    assert decision_in_trace(text) == "approve"
