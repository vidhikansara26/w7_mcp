"""Phase 6 — run graded scenarios + reflection correction demo; save curated traces."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Any

from agent import run_agent
from business_logic import clear_escalation_queue

ROOT = Path(__file__).resolve().parent
SCENARIO_DIR = ROOT / "traces" / "scenarios"

SCENARIOS: list[dict[str, Any]] = [
    {
        "id": "01_approve",
        "title": "Clear Approval",
        "expected": "approve",
        "required_tools_any": [
            ["check_request_eligibility"],
            ["get_employee_info", "get_policy_limits"],
        ],
        "forbid_tools": ["flag_for_human_review"],
        "prompt": (
            "EMP-101: My laptop is about 4 years old and slowing down during builds; "
            "can I get a replacement laptop?"
        ),
        "retry_hint": (
            "Reminder: EMP-101 Engineering laptop issued ~4 years ago is within the "
            "3-year policy when check_request_eligibility says approve. Do NOT escalate "
            "or call flag_for_human_review for normal wear. Final Decision must be approve."
        ),
    },
    {
        "id": "02_deny",
        "title": "Clear Denial",
        "expected": "deny",
        "required_tools_any": [
            ["check_request_eligibility"],
            ["get_employee_info", "get_policy_limits"],
        ],
        "forbid_tools": ["flag_for_human_review"],
        "prompt": (
            "EMP-102: I am in Sales. My laptop is only about 6 months old and I want a "
            "second/replacement laptop because mine feels slow in meetings. No damage."
        ),
        "retry_hint": (
            "Reminder: Sales laptop interval is 4 years. EMP-102 exceeds frequency with "
            "NO exceptional justification (no crushed/stolen/broken screen). "
            "Final Decision must be deny. Do not escalate."
        ),
    },
    {
        "id": "03_escalate_damage",
        "title": "Escalation A — early + damage",
        "expected": "escalate",
        "required_tools_any": [
            ["check_request_eligibility", "flag_for_human_review"],
            ["get_employee_info", "flag_for_human_review"],
        ],
        "forbid_tools": [],
        "prompt": (
            "EMP-103: I am in Engineering. My laptop was issued about a year ago but the "
            "screen was crushed on a client site / stolen bag incident — broken screen. "
            "I need an early replacement."
        ),
        "retry_hint": (
            "Reminder: early refresh WITH exceptional justification (crushed/stolen/"
            "broken screen) must call flag_for_human_review then Final Decision: escalate."
        ),
    },
    {
        "id": "04_escalate_nonstandard",
        "title": "Escalation B — non-standard item",
        "expected": "escalate",
        "required_tools_any": [
            ["flag_for_human_review"],
        ],
        "forbid_tools": [],
        "prompt": (
            "EMP-104: I am in Management. Please order an ergonomic split keyboard "
            "(or standing desk) — not a standard catalog laptop/monitor/chair."
        ),
        "retry_hint": (
            "Reminder: non-standard gear is always escalate. You MUST call "
            "flag_for_human_review before Final Decision: escalate."
        ),
    },
]


def _tools_ok(called: list[str], scenario: dict[str, Any]) -> bool:
    called_set = set(called)
    for forbidden in scenario.get("forbid_tools") or []:
        if forbidden in called_set:
            return False
    groups = scenario.get("required_tools_any") or [[]]
    for group in groups:
        if all(t in called_set for t in group):
            return True
    return False


async def _run_one(scenario: dict[str, Any], *, max_attempts: int = 3) -> dict[str, Any]:
    path = SCENARIO_DIR / f"{scenario['id']}.txt"
    last: dict[str, Any] = {}
    prompt = scenario["prompt"]

    for attempt in range(1, max_attempts + 1):
        clear_escalation_queue(truncate_file=True)
        print(f"\n######## {scenario['id']} attempt {attempt}: {scenario['title']} ########\n")
        last = await run_agent(
            prompt,
            enable_reflection=True,
            trace_path=path,
            scenario_id=scenario["id"],
        )
        decision_ok = last.get("decision") == scenario["expected"]
        tools_ok = _tools_ok(last.get("tools_called") or [], scenario)
        if decision_ok and tools_ok:
            print(
                f"OK {scenario['id']}: decision={last['decision']} "
                f"tools={last.get('tools_called')}"
            )
            return last
        print(
            f"RETRY {scenario['id']}: decision={last.get('decision')} "
            f"(expected {scenario['expected']}), tools={last.get('tools_called')}"
        )
        prompt = scenario["prompt"] + "\n\n" + scenario["retry_hint"]

    raise SystemExit(
        f"Scenario {scenario['id']} failed after {max_attempts} attempts: {last}"
    )


async def run_reflection_correction_demo() -> Path:
    """Force a wrong draft; reflection should correct using tool observations."""
    path = SCENARIO_DIR / "00_reflection_correction.txt"
    clear_escalation_queue(truncate_file=True)
    print("\n######## 00_reflection_correction ########\n")
    bad_draft = (
        "Final Decision: deny\n"
        "Reason: I am guessing the laptop is too new without trusting the tools."
    )
    result = await run_agent(
        (
            "EMP-101: My laptop is about 4 years old and slowing down; "
            "can I get a replacement laptop?"
        ),
        enable_reflection=True,
        inject_draft=bad_draft,
        trace_path=path,
        scenario_id="00_reflection_correction",
    )
    if result.get("decision") != "approve":
        print(
            "WARNING: reflection demo did not correct to approve; "
            f"got {result.get('decision')}. Trace still saved for inspection."
        )
    else:
        print(
            f"OK reflection correction: draft={result.get('draft_decision')} "
            f"→ {result.get('decision')}"
        )
    return path


async def main() -> None:
    SCENARIO_DIR.mkdir(parents=True, exist_ok=True)
    await run_reflection_correction_demo()
    results = []
    for scenario in SCENARIOS:
        results.append(await _run_one(scenario))

    print("\n=== Scenario summary ===")
    for scenario, result in zip(SCENARIOS, results, strict=True):
        print(
            f"{scenario['id']}: expected={scenario['expected']} "
            f"got={result['decision']} draft={result.get('draft_decision')} "
            f"tools={result.get('tools_called')} → {result['trace_path']}"
        )
    print(f"\nCurated traces in {SCENARIO_DIR}")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(130)
