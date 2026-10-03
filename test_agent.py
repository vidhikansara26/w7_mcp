"""Scripted-model tests for the ReAct loop. No Ollama and no MCP server."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent import last_decision, parse_reflection, reflection_checkpoint, run_react

EVAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "employee_id": {"type": "string"},
        "item": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["employee_id", "item", "reason"],
}
FLAG_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "employee_id": {"type": "string"},
        "request": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["employee_id", "request", "reason"],
}


class ListedTool:
    def __init__(self, name: str, schema: dict[str, Any]) -> None:
        self.name = name
        self.description = name
        self.inputSchema = schema


def _tools() -> list[ListedTool]:
    return [
        ListedTool("evaluate_request", EVAL_SCHEMA),
        ListedTool("flag_for_human_review", FLAG_SCHEMA),
        ListedTool("get_employee_info", {
            "type": "object",
            "properties": {"employee_id": {"type": "string"}},
            "required": ["employee_id"],
        }),
    ]


def _step(action: str, action_input: dict[str, Any], **extra: Any) -> str:
    body: dict[str, Any] = {
        "thought": "next",
        "action": action,
        "action_input": action_input,
        "decision": None,
        "reason": None,
    }
    body.update(extra)
    return json.dumps(body)


def _finish(decision: str, reason: str = "copied from evaluate_request") -> str:
    return json.dumps(
        {
            "thought": "the tool result is enough",
            "action": "finish",
            "action_input": {},
            "decision": decision,
            "reason": reason,
        }
    )


def _evaluate_args() -> dict[str, str]:
    return {"employee_id": "EMP-101", "item": "laptop", "reason": "slowing down"}


def _scripted(replies: list[str]):
    queue = list(replies)

    def complete(prompt: str) -> str:
        if not queue:
            raise AssertionError("model called after the script ended")
        del prompt
        return queue.pop(0)

    return complete


def _drive(
    replies: list[str],
    *,
    decision: str = "approve",
    rule: str = "within_policy",
    max_steps: int = 8,
) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
    calls: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        calls.append((name, arguments))
        if name == "evaluate_request":
            return json.dumps({"ok": True, "decision": decision, "rule": rule})
        if name == "flag_for_human_review":
            return json.dumps({"ok": True, "flagged": True, "queue_size": 1, "entry": {}})
        raise AssertionError(name)

    lines = asyncio.run(
        run_react(
            "EMP-101 needs a laptop",
            _tools(),
            _scripted(replies),
            call_tool,
            max_steps=max_steps,
        )
    )
    return lines, calls


def test_approve_finishes_without_a_flag() -> None:
    lines, calls = _drive(
        [
            _step("evaluate_request", _evaluate_args()),
            _finish("approve"),
        ]
    )
    assert [name for name, _args in calls] == ["evaluate_request"]
    assert last_decision(lines) == ("approve", "copied from evaluate_request")


def test_flag_before_evaluate_is_not_sent() -> None:
    lines, calls = _drive(
        [
            _step(
                "flag_for_human_review",
                {"employee_id": "EMP-103", "request": "laptop", "reason": "early"},
            ),
            _step("evaluate_request", _evaluate_args()),
            _finish("escalate"),
            _step(
                "flag_for_human_review",
                {"employee_id": "EMP-103", "request": "laptop", "reason": "early"},
            ),
            _finish("escalate"),
        ],
        decision="escalate",
        rule="early_refresh_exceptional",
    )
    assert [name for name, _args in calls] == ["evaluate_request", "flag_for_human_review"]
    assert "Call evaluate_request before flag_for_human_review." in "\n".join(lines)
    assert "escalate requires flag_for_human_review before finish." in "\n".join(lines)
    assert last_decision(lines)[0] == "escalate"


def test_flag_after_approve_is_not_sent() -> None:
    _lines, calls = _drive(
        [
            _step("evaluate_request", _evaluate_args()),
            _step(
                "flag_for_human_review",
                {"employee_id": "EMP-101", "request": "laptop", "reason": "no"},
            ),
            _finish("approve"),
        ]
    )
    assert [name for name, _args in calls] == ["evaluate_request"]


def test_finish_must_copy_evaluate_request() -> None:
    lines, calls = _drive(
        [
            _step("evaluate_request", _evaluate_args()),
            _finish("deny"),
            _finish("approve"),
        ]
    )
    assert [name for name, _args in calls] == ["evaluate_request"]
    text = "\n".join(lines)
    assert "Copy that decision" in text
    assert last_decision(lines)[0] == "approve"


def test_unknown_tool_and_non_json_stay_in_the_loop() -> None:
    lines, calls = _drive(
        [
            "hello",
            _step("deny", {}),
            _step("evaluate_request", _evaluate_args()),
            _finish("approve"),
        ]
    )
    text = "\n".join(lines)
    assert "Reply was not a JSON object." in text
    assert "Unknown tool: deny" in text
    assert [name for name, _args in calls] == ["evaluate_request"]
    assert last_decision(lines)[0] == "approve"


def test_duplicate_evaluate_is_not_sent_twice() -> None:
    _lines, calls = _drive(
        [
            _step("evaluate_request", _evaluate_args()),
            _step("evaluate_request", _evaluate_args()),
            _finish("approve"),
        ]
    )
    assert [name for name, _args in calls] == ["evaluate_request"]


def test_missing_required_argument_is_not_sent() -> None:
    lines, calls = _drive(
        [
            _step("evaluate_request", {"employee_id": "EMP-101", "item": "laptop"}),
            _step("evaluate_request", _evaluate_args()),
            _finish("approve"),
        ]
    )
    assert "Schema rejected:" in "\n".join(lines)
    assert [name for name, _args in calls] == ["evaluate_request"]
    assert calls[0][1]["reason"] == "slowing down"


def test_tool_call_drops_a_decision() -> None:
    _lines, calls = _drive(
        [
            _step(
                "evaluate_request",
                _evaluate_args(),
                decision="deny",
                reason="should be stripped",
            ),
            _finish("approve"),
        ]
    )
    assert [name for name, _args in calls] == ["evaluate_request"]


def test_step_budget_flags_an_unescalated_result() -> None:
    lines, calls = _drive(
        [_step("evaluate_request", {
            "employee_id": "EMP-103",
            "item": "laptop",
            "reason": "broken screen",
        })],
        decision="escalate",
        rule="early_refresh_exceptional",
        max_steps=1,
    )
    assert [name for name, _args in calls] == ["evaluate_request", "flag_for_human_review"]
    assert calls[1][1]["employee_id"] == "EMP-103"
    assert calls[1][1]["request"] == "laptop"
    assert "early_refresh_exceptional" in calls[1][1]["reason"]
    assert last_decision(lines)[0] == "escalate"


def test_reflection_parses_checkpoint() -> None:
    parsed = parse_reflection(
        json.dumps(
            {
                "is_valid": True,
                "critique": "The draft matches evaluate_request.",
                "action": "proceed",
            }
        )
    )
    assert parsed["action"] == "PROCEED"
    assert parsed["is_valid"] is True


def test_reflection_legacy_undecided_escalates() -> None:
    parsed = parse_reflection(
        json.dumps(
            {
                "reflection": "The observations do not answer both questions.",
                "decision": "undecided",
                "reason": "No evaluate_request result supports a decision.",
            }
        )
    )
    assert parsed["action"] == "ESCALATE"
    assert parsed["is_valid"] is False


def test_reflection_checkpoint_keeps_a_supported_draft() -> None:
    result = reflection_checkpoint(draft="approve", policy="approve", action="PROCEED")
    assert result == {"decision": "approve", "flag": False, "label": "proceed"}


def test_reflection_checkpoint_deny_sticks_only_for_a_denial() -> None:
    denied = reflection_checkpoint(draft="deny", policy="deny", action="DENY")
    assert denied["decision"] == "deny"
    assert denied["flag"] is False
    locked = reflection_checkpoint(draft="approve", policy="escalate", action="DENY")
    assert locked["decision"] == "escalate"
    assert locked["label"] == "lock"
    assert locked["flag"] is True


def test_reflection_checkpoint_does_not_escalate_a_clear_deny() -> None:
    result = reflection_checkpoint(draft="deny", policy="deny", action="ESCALATE")
    assert result["decision"] == "deny"
    assert result["flag"] is False
    assert result["label"] == "lock"
