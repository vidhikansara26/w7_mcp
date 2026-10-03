"""ReAct agent over the IT Equipment MCP server (Ollama + stdio)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import jsonschema
from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

import llm
from prompts import REFLECTION_PROMPT, build_system_prompt

load_dotenv()

ROOT = Path(__file__).resolve().parent
TRACES_DIR = ROOT / "traces"

DecisionName = Literal["approve", "deny", "escalate"]
ReflectionAction = Literal["PROCEED", "DENY", "ESCALATE"]
Complete = Callable[[str], str]
CallTool = Callable[[str, dict[str, Any]], Awaitable[str]]
Emit = Callable[[str], None]

REPEAT_NOTICE = (
    "Already called with these arguments. "
    "Choose a different action, or finish if the trace is enough."
)
_GUARD_PREFIXES = (
    "Schema rejected:",
    "Unknown tool:",
    "Reply was not a JSON object.",
    "evaluate_request is not in the trace.",
    "evaluate_request.decision is ",
    "escalate requires flag_for_human_review",
    "Already called with these arguments.",
    "Blocked find_employee:",
)
_NEXT_MARKERS = (
    "Thought:",
    "Action:",
    "Final Decision:",
    "Reason:",
    "Draft ",
    "[HOST]",
    "===",
    "Reflection:",
    "Schema rejected:",
    "Request:",
    "[ROUTE]",
    "accept:",
)

FINAL_RE = re.compile(
    r"Final\s*Decision\s*:\s*(approve|deny|escalate)\b",
    re.IGNORECASE,
)
REASON_RE = re.compile(
    r"Reason\s*:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
EMP_ID_RE = re.compile(r"\bEMP-\d+\b", re.IGNORECASE)


def _max_steps() -> int:
    return int(os.getenv("AGENT_MAX_STEPS", "8"))


class AgentStep(BaseModel):
    """One model turn: a tool call, or finish with a copied decision."""

    model_config = ConfigDict(extra="ignore")
    thought: str = Field(min_length=1)
    action: str = Field(min_length=1)
    action_input: dict[str, Any] = Field(default_factory=dict)
    decision: DecisionName | None = None
    reason: str | None = None

    @field_validator("action_input", mode="before")
    @classmethod
    def parse_action_input(cls, value: object) -> object:
        if value is None:
            return {}
        if isinstance(value, str):
            try:
                loaded = json.loads(value)
            except json.JSONDecodeError:
                return value
            return loaded
        return value

    @model_validator(mode="after")
    def finish_fields(self) -> AgentStep:
        if self.action == "finish":
            if self.decision is None:
                raise ValueError("decision is required when action is finish")
            if not (self.reason and self.reason.strip()):
                raise ValueError("reason is required when action is finish")
        elif self.decision is not None:
            raise ValueError("decision must be null unless action is finish")
        return self


class ReflectionReply(BaseModel):
    """Checkpoint after the draft: proceed, deny, or escalate."""

    model_config = ConfigDict(extra="ignore")
    is_valid: bool
    critique: str = Field(min_length=1)
    action: ReflectionAction

    @field_validator("is_valid", mode="before")
    @classmethod
    def parse_is_valid(cls, value: object) -> object:
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered == "true":
                return True
            if lowered == "false":
                return False
        return value

    @field_validator("action", mode="before")
    @classmethod
    def parse_action(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip().upper()
        return value


class EvaluateResult(BaseModel):
    """Authoritative payload from evaluate_request. Extra fields are kept."""

    model_config = ConfigDict(extra="allow")
    decision: DecisionName
    rule: str = ""
    ok: bool | None = None


class FlagResult(BaseModel):
    """Successful flag_for_human_review payload."""

    model_config = ConfigDict(extra="allow")
    ok: bool
    flagged: bool


def extract_explicit_employee_id(text: str) -> str | None:
    """Return the first EMP-### in the prompt, or None if absent."""
    match = EMP_ID_RE.search(text or "")
    if not match:
        return None
    raw = match.group(0)
    return "EMP-" + raw.split("-", 1)[1]


def _schema_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err["loc"]) or "body"
        parts.append(loc + ": " + err["msg"])
    return "Schema rejected: " + "; ".join(parts)


def _load_object(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def _without_tool_decision(raw: str) -> str:
    """Drop a decision that arrived on a tool call. finish keeps its decision."""
    data = _load_object(raw)
    if not isinstance(data, dict) or data.get("action") == "finish" or data.get("decision") is None:
        return raw
    cleaned = dict(data)
    cleaned["decision"] = None
    return json.dumps(cleaned)


def parse_step(raw: str) -> AgentStep | str:
    """Validate one model reply. A string return is an observation for the model."""
    data = _load_object(_without_tool_decision(raw))
    if data is None:
        return "Reply was not a JSON object."
    try:
        return AgentStep.model_validate(data)
    except ValidationError as exc:
        return _schema_error(exc)


def _coerce_reflection(data: dict[str, Any]) -> dict[str, Any] | None:
    """Accept the checkpoint object, or the older decision/reason object."""
    if "action" in data and "is_valid" in data:
        critique = data.get("critique") or data.get("reflection") or data.get("reason")
        if not critique:
            return None
        return {
            "is_valid": data["is_valid"],
            "critique": str(critique),
            "action": data["action"],
        }
    decision = data.get("decision")
    critique = str(data.get("critique") or data.get("reflection") or data.get("reason") or "")
    critique = critique.strip()
    if not critique or decision not in {"approve", "deny", "escalate", "undecided"}:
        return None
    if decision == "undecided":
        return {"is_valid": False, "critique": critique, "action": "ESCALATE"}
    if decision == "deny":
        return {"is_valid": False, "critique": critique, "action": "DENY"}
    if decision == "escalate":
        return {"is_valid": True, "critique": critique, "action": "ESCALATE"}
    return {"is_valid": True, "critique": critique, "action": "PROCEED"}


def parse_reflection(text: str) -> dict[str, Any]:
    """Parse a reflection checkpoint. A bad reply leaves action unset."""
    data = _load_object(text)
    if data is None:
        return {
            "reflection": "Reply was not a JSON object.",
            "is_valid": None,
            "critique": "",
            "action": None,
            "raw": text,
        }
    coerced = _coerce_reflection(data)
    if coerced is None:
        return {
            "reflection": "Reply was not a reflection checkpoint.",
            "is_valid": None,
            "critique": "",
            "action": None,
            "raw": text,
        }
    try:
        reply = ReflectionReply.model_validate(coerced)
    except ValidationError as exc:
        return {
            "reflection": _schema_error(exc),
            "is_valid": None,
            "critique": "",
            "action": None,
            "raw": text,
        }
    return {
        "reflection": reply.critique,
        "is_valid": reply.is_valid,
        "critique": reply.critique,
        "action": reply.action,
        "raw": text,
    }


def reflection_checkpoint(
    *,
    draft: str | None,
    policy: str | None,
    action: str | None,
) -> dict[str, Any]:
    """Choose the saved decision after reflection.

    PROCEED keeps a draft that matches the tool. DENY sticks only when the
    tool said deny. ESCALATE sticks when the tool said escalate, or when no
    tool result exists. Any other disagreement snaps back to the tool.
    """
    if action not in {"PROCEED", "DENY", "ESCALATE"}:
        return {"decision": draft, "flag": False, "label": "unparsed"}
    if action == "PROCEED" and (policy is None or draft == policy):
        return {"decision": draft, "flag": False, "label": "proceed"}
    if action == "DENY" and policy in {None, "deny"}:
        return {"decision": "deny", "flag": False, "label": "deny"}
    if action == "ESCALATE" and policy in {None, "escalate"}:
        return {"decision": "escalate", "flag": True, "label": "escalate"}
    return {
        "decision": policy or draft,
        "flag": policy == "escalate",
        "label": "lock",
    }


def parse_injected_draft(text: str) -> dict[str, Any]:
    """Read the reflection-demo draft. Accepts the legacy text shape or JSON."""
    final_m = FINAL_RE.search(text)
    if final_m:
        reason_m = REASON_RE.search(text)
        return {
            "kind": "final",
            "decision": final_m.group(1).lower(),
            "reason": (reason_m.group(1).strip() if reason_m else ""),
        }
    data = _load_object(text)
    if data and data.get("decision") in {"approve", "deny", "escalate"}:
        return {
            "kind": "final",
            "decision": str(data["decision"]),
            "reason": str(data.get("reason") or ""),
        }
    return {"kind": "invalid"}


def _tool_name(tool: Any) -> str:
    if isinstance(tool, dict):
        return str(tool.get("name") or "")
    return str(getattr(tool, "name", "") or "")


def _tool_schema(tool: Any) -> dict[str, Any]:
    if isinstance(tool, dict):
        schema = tool.get("inputSchema")
    else:
        schema = getattr(tool, "inputSchema", None)
    if isinstance(schema, dict):
        return schema
    return {}


def _json_schema_gap(schema: dict[str, Any], data: object) -> str | None:
    if not schema:
        return None
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(data), key=lambda err: list(err.absolute_path))
    if not errors:
        return None
    parts: list[str] = []
    for err in errors:
        loc = ".".join(str(part) for part in err.absolute_path) or "body"
        parts.append(loc + ": " + err.message)
    return "Schema rejected: " + "; ".join(parts)


def _validate_tool_result(action: str, text: str) -> str | None:
    """Check evaluate and flag payloads. Error objects (ok false) are left alone."""
    data = _load_object(text)
    if data is None:
        return "Schema rejected: Tool result was not a JSON object."
    if data.get("ok") is False:
        return None
    if action == "evaluate_request":
        model: type[BaseModel] = EvaluateResult
    elif action == "flag_for_human_review":
        model = FlagResult
    else:
        return None
    try:
        model.model_validate(data)
    except ValidationError as exc:
        return _schema_error(exc)
    return None


def _read_evaluate(text: str) -> EvaluateResult | None:
    data = _load_object(text)
    if not isinstance(data, dict) or "decision" not in data:
        return None
    try:
        return EvaluateResult.model_validate(data)
    except ValidationError:
        return None


def _flag_succeeded(text: str) -> bool:
    data = _load_object(text)
    if not isinstance(data, dict) or data.get("ok") is False:
        return False
    try:
        return FlagResult.model_validate(data).flagged is True
    except ValidationError:
        return False


def _is_guard(body: str) -> bool:
    return body.startswith(_GUARD_PREFIXES)


def iter_actions(lines: list[str]) -> list[tuple[str, str]]:
    """Pair each Action line with the observation body that follows it."""
    pairs: list[tuple[str, str]] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.startswith("Action: "):
            index += 1
            continue
        rest = line[len("Action: ") :]
        name = rest.split(" ", 1)[0]
        body_lines: list[str] = []
        if index + 1 < len(lines) and lines[index + 1].startswith("Observation:"):
            first = lines[index + 1][len("Observation:") :].strip()
            if first:
                body_lines.append(first)
            cursor = index + 2
            while cursor < len(lines) and not lines[cursor].startswith(_NEXT_MARKERS):
                body_lines.append(lines[cursor])
                cursor += 1
        pairs.append((name, "\n".join(body_lines).strip()))
        index += 1
    return pairs


def policy_from_lines(lines: list[str]) -> tuple[str | None, str | None]:
    """Latest evaluate_request decision and rule recorded in a trace."""
    found: EvaluateResult | None = None
    for name, body in iter_actions(lines):
        if name != "evaluate_request" or _is_guard(body):
            continue
        record = _read_evaluate(body)
        if record is not None:
            found = record
    if found is None:
        return None, None
    return found.decision, found.rule


def tools_called_from_lines(lines: list[str]) -> list[str]:
    """Tool names that were actually sent. Refusals and finish are omitted."""
    called: list[str] = []
    for name, body in iter_actions(lines):
        if name == "finish" or not body or _is_guard(body):
            continue
        called.append(name)
    return called


def last_decision(lines: list[str]) -> tuple[str | None, str]:
    """Last Final Decision line and the Reason line that follows it."""
    decision: str | None = None
    reason = ""
    for index, line in enumerate(lines):
        if not line.startswith("Final Decision:"):
            continue
        decision = line[len("Final Decision:") :].strip().lower()
        if index + 1 < len(lines) and lines[index + 1].startswith("Reason:"):
            reason = lines[index + 1][len("Reason:") :].strip()
    return decision, reason


def _call_key(action: str, arguments: dict[str, Any]) -> str:
    return action + " " + json.dumps(arguments, sort_keys=True)


def _finish_gap(
    policy: EvaluateResult | None,
    flagged: bool,
    decision: str,
) -> str | None:
    if policy is None:
        return "evaluate_request is not in the trace. Call it before finish."
    if decision != policy.decision:
        return (
            f"evaluate_request.decision is {policy.decision}. "
            f"Copy that decision. Finish with {policy.decision}."
        )
    if policy.decision == "escalate" and not flagged:
        return "escalate requires flag_for_human_review before finish."
    return None


def _flag_gap(policy: EvaluateResult | None) -> str | None:
    if policy is None:
        return (
            "evaluate_request is not in the trace. "
            "Call evaluate_request before flag_for_human_review."
        )
    if policy.decision in {"approve", "deny"}:
        return (
            f"evaluate_request.decision is {policy.decision}. "
            f"Do not call flag_for_human_review. Finish with {policy.decision}."
        )
    return None


def _step_prompt(
    request_text: str,
    trace: str,
    steps_remaining: int,
    completed: list[str],
    explicit_employee_id: str | None,
) -> str:
    finished = ""
    if completed:
        finished = (
            "Calls that already returned an observation:\n"
            + "\n".join(completed)
            + "\nDo not repeat them.\n"
        )
    hint = ""
    if explicit_employee_id:
        hint = (
            f"[HOST HINT] Explicit employee_id {explicit_employee_id} is present. "
            "Use that id. Do not call find_employee.\n"
        )
    return (
        "Reply with one JSON object and nothing else.\n"
        'Keys are "thought", "action", "action_input", "decision", and "reason".\n'
        "thought is your reasoning for this turn: what you know, what is missing, "
        "and why this action follows.\n"
        "action is finish, or a tool name.\n"
        "action_input is the tool arguments, or {} when action is finish.\n"
        'decision is "approve", "deny", or "escalate" only when action is finish, otherwise null.\n'
        "reason is required when action is finish, otherwise null.\n"
        "Decide the next action from the request and the trace. "
        "Call a tool when a fact you need is still missing. "
        "Finish when evaluate_request supports the decision. "
        "Copy that tool's decision. "
        "Call flag_for_human_review only after that tool returns escalate, and before finish.\n"
        + hint
        + finished
        + "Steps remaining: "
        + str(steps_remaining)
        + ".\n\nRequest:\n"
        + request_text
        + "\n\nTrace so far:\n"
        + (trace or "(empty)")
        + "\n"
    )


def _observation_text(result: Any) -> str:
    parts: list[str] = []
    if getattr(result, "isError", False):
        parts.append("ERROR")
    for block in getattr(result, "content", []) or []:
        text = getattr(block, "text", None)
        if text is None:
            continue
        try:
            parts.append(json.dumps(json.loads(text), indent=2))
        except json.JSONDecodeError:
            parts.append(str(text))
    return "\n".join(parts) if parts else str(result)


async def run_react(
    request_text: str,
    tools: list[Any],
    complete: Complete,
    call_tool: CallTool,
    *,
    explicit_employee_id: str | None = None,
    max_steps: int | None = None,
    emit: Emit | None = None,
) -> list[str]:
    """TAO loop with no Ollama and no MCP session.

    ``complete`` returns one JSON step. ``call_tool`` performs a tool call the
    host already accepted. Illegal flag and finish moves become observations.
    """
    limit = _max_steps() if max_steps is None else max_steps
    lines: list[str] = []
    by_name = {_tool_name(tool): tool for tool in tools}
    seen: dict[str, str] = {}
    policy: EvaluateResult | None = None
    policy_args: dict[str, Any] = {}
    flagged = False

    def add(text: str) -> None:
        for line in text.splitlines() or [""]:
            lines.append(line)
            if emit is not None:
                emit(line)

    add("Request: " + request_text)

    def remember_result(action: str, arguments: dict[str, Any], observation: str) -> str:
        nonlocal policy, policy_args, flagged
        gap = _validate_tool_result(action, observation)
        if action == "evaluate_request":
            record = _read_evaluate(observation)
            if record is not None:
                policy = record
                policy_args = arguments
        if action == "flag_for_human_review" and _flag_succeeded(observation):
            flagged = True
        if gap:
            observation = observation + "\n" + gap
        seen[_call_key(action, arguments)] = observation
        return observation

    for index in range(limit):
        parsed = parse_step(
            complete(
                _step_prompt(
                    request_text,
                    "\n".join(lines),
                    limit - index,
                    list(seen),
                    explicit_employee_id,
                )
            )
        )
        if isinstance(parsed, str):
            add("Observation: " + parsed)
            continue

        add("Thought: " + parsed.thought)
        if parsed.action == "finish":
            decision = parsed.decision
            reason = parsed.reason
            add("Action: finish {}")
            if decision is None or not (reason and reason.strip()):
                add(
                    "Observation: Schema rejected: "
                    "decision and reason are required when action is finish"
                )
                continue
            gap = _finish_gap(policy, flagged, decision)
            if gap is not None:
                add("Observation: " + gap)
                continue
            add("Final Decision: " + decision)
            add("Reason: " + reason.strip())
            return lines

        if parsed.action not in by_name:
            add("Action: " + _call_key(parsed.action, parsed.action_input))
            add("Observation: Unknown tool: " + parsed.action)
            continue

        arguments = dict(parsed.action_input)
        if parsed.action == "find_employee" and explicit_employee_id:
            add("Action: " + _call_key(parsed.action, arguments))
            add(
                "Observation: Blocked find_employee: prompt already contains "
                f"{explicit_employee_id}. Do not resolve by name. "
                f'Next call get_employee_info with employee_id="{explicit_employee_id}", '
                "then evaluate_request with the same id."
            )
            continue

        claimed_id = str(arguments.get("employee_id", "")).strip().upper()
        if (
            explicit_employee_id
            and "employee_id" in arguments
            and claimed_id != explicit_employee_id.upper()
        ):
            arguments["employee_id"] = explicit_employee_id
            add(
                f"[HOST] Rewrote employee_id → {explicit_employee_id} "
                "(explicit id in prompt wins)."
            )

        schema_gap = _json_schema_gap(_tool_schema(by_name[parsed.action]), arguments)
        if schema_gap is not None:
            add("Action: " + _call_key(parsed.action, arguments))
            add("Observation: " + schema_gap)
            continue

        if parsed.action == "flag_for_human_review":
            flag_gap = _flag_gap(policy)
            if flag_gap is not None:
                add("Action: " + _call_key(parsed.action, arguments))
                add("Observation: " + flag_gap)
                continue

        key = _call_key(parsed.action, arguments)
        if key in seen:
            add("Action: " + key)
            add("Observation: " + REPEAT_NOTICE)
            continue

        add("Action: " + key)
        observation = await call_tool(parsed.action, arguments)
        observation = remember_result(parsed.action, arguments, observation)
        add("Observation:\n" + observation)
        if parsed.action == "evaluate_request" and policy is not None:
            add(
                f"[ROUTE] evaluate_request returned {policy.decision} "
                f"(rule={policy.rule})."
            )

    if policy is not None and policy.decision == "escalate" and not flagged:
        flag_args = {
            "employee_id": str(policy_args.get("employee_id") or explicit_employee_id or ""),
            "request": str(policy_args.get("item") or policy_args.get("request") or "equipment"),
            "reason": f"evaluate_request rule={policy.rule}",
        }
        add("Thought: The step budget is spent. Store the escalation, then finish.")
        add("Action: " + _call_key("flag_for_human_review", flag_args))
        observation = remember_result(
            "flag_for_human_review",
            flag_args,
            await call_tool("flag_for_human_review", flag_args),
        )
        add("Observation:\n" + observation)
        add("Final Decision: escalate")
        add("Reason: " + (policy.rule or "escalate"))
        return lines

    if policy is not None:
        add("Thought: The step budget is spent. Copy evaluate_request.")
        add("Final Decision: " + policy.decision)
        add("Reason: " + (policy.rule or policy.decision))
        return lines

    add("Final Decision: escalate")
    add(f"Reason: Exceeded max steps ({limit}); no evaluate_request result.")
    return lines


def _slug(prompt: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", prompt.strip())[:48].strip("-").lower()
    return cleaned or "request"


def _save_trace(
    prompt: str,
    lines: list[str],
    decision: str | None,
    *,
    path: Path | None = None,
    extra_header: dict[str, str] | None = None,
) -> Path:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    if path is None:
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = TRACES_DIR / f"{ts}_{_slug(prompt)}.txt"
    else:
        path.parent.mkdir(parents=True, exist_ok=True)

    header = [
        f"prompt: {prompt}",
        f"model: {llm.ollama_model()}",
        f"host: {llm.ollama_host()}",
        f"decision: {decision or 'none'}",
    ]
    if extra_header:
        for key, value in extra_header.items():
            header.append(f"{key}: {value}")
    header.append("")
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return path


def run_reflection(
    *,
    user_request: str,
    draft: str,
    observations: str,
) -> dict[str, Any]:
    """Review the draft before it is final. Evidence is the tool observations."""
    prompt = REFLECTION_PROMPT.format(
        user_request=user_request,
        draft=draft,
        observations=observations or "(no tool observations)",
    )
    raw = llm.chat(
        [
            {
                "role": "system",
                "content": (
                    "You are an IT compliance auditor. "
                    "The draft is not final. "
                    "PROCEED only when it matches evaluate_request. "
                    "DENY only when that tool said deny. "
                    "ESCALATE when that tool said escalate, or when you cannot tell. "
                    "Reply with one JSON object."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        format="json",
    )
    return parse_reflection(raw)


def _action_arguments(lines: list[str], tool_name: str) -> dict[str, Any]:
    """Last JSON arguments recorded on an Action line for tool_name."""
    found: dict[str, Any] = {}
    prefix = f"Action: {tool_name} "
    for line in lines:
        if not line.startswith(prefix):
            continue
        data = _load_object(line[len(prefix) :])
        if isinstance(data, dict):
            found = data
    return found


def _ensure_policy_rule(reason: str, policy_rule: str | None) -> str:
    """Keep evaluate_request.rule in the saved reason when the draft omitted it."""
    if not policy_rule or policy_rule in reason:
        return reason
    return f"{reason} [evaluate_request rule={policy_rule}]".strip()


def _observations_for_reflection(lines: list[str]) -> str:
    chunks: list[str] = []
    for name, body in iter_actions(lines):
        if name == "finish" or not body or _is_guard(body):
            continue
        chunks.append(f"{name}: {body}")
    return "\n\n".join(chunks)


async def run_agent(
    user_prompt: str,
    *,
    enable_reflection: bool = True,
    inject_draft: str | None = None,
    trace_path: Path | None = None,
    scenario_id: str | None = None,
) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        cwd=str(ROOT),
    )
    trace_lines: list[str] = []
    explicit_emp_id = extract_explicit_employee_id(user_prompt)

    def emit(block: str) -> None:
        print(block, flush=True)
        trace_lines.append(block)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools

            def complete(prompt: str) -> str:
                return llm.chat(
                    [
                        {"role": "system", "content": build_system_prompt(tools)},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.0,
                    format="json",
                )

            async def call_tool(name: str, arguments: dict[str, Any]) -> str:
                result = await session.call_tool(name, arguments)
                return _observation_text(result)

            emit(f"=== User request ===\n{user_prompt}")
            if explicit_emp_id:
                emit(
                    "=== Host EMP-id lock ===\n"
                    f"Using explicit {explicit_emp_id}; find_employee blocked."
                )
            emit(
                f"=== MCP tools ({len(tools)}) ===\n"
                + ", ".join(tool.name for tool in tools)
            )

            loop_lines = await run_react(
                user_prompt,
                list(tools),
                complete,
                call_tool,
                explicit_employee_id=explicit_emp_id,
                emit=emit,
            )
            policy_decision, policy_rule = policy_from_lines(loop_lines)
            tools_called = tools_called_from_lines(loop_lines)
            decision, reason = last_decision(loop_lines)
            draft_decision = decision
            draft_reason = reason
            draft_text = f"Final Decision: {decision}\nReason: {reason}"

            if inject_draft:
                emit("=== Injected bad draft (reflection demo) ===")
                emit(inject_draft)
                draft_text = inject_draft
                injected = parse_injected_draft(inject_draft)
                if injected.get("kind") == "final":
                    draft_decision = injected["decision"]
                    draft_reason = injected.get("reason") or draft_reason

            reflection_note = ""
            if enable_reflection and decision:
                emit("=== Reflection ===")
                reflected = run_reflection(
                    user_request=user_prompt,
                    draft=draft_text,
                    observations=_observations_for_reflection(loop_lines),
                )
                emit(reflected["raw"])
                reflection_note = reflected.get("critique") or reflected.get("reflection") or ""
                checkpoint = reflection_checkpoint(
                    draft=draft_decision,
                    policy=policy_decision,
                    action=reflected.get("action"),
                )
                label = str(checkpoint["label"])
                if label == "proceed":
                    emit(
                        "=== Reflection outcome ===\n"
                        f"PROCEED draft={draft_decision!r}"
                    )
                    reason = reflection_note or reason
                elif label == "deny":
                    emit("=== Reflection outcome ===\nDENY")
                    reason = reflection_note or reason
                elif label == "escalate":
                    emit(
                        "=== Reflection outcome ===\n"
                        "ESCALATE for human review"
                    )
                    reason = reflection_note or "Reflection could not confirm the draft."
                elif label == "lock":
                    emit(
                        "=== Decision lock ===\n"
                        f"Snapping reflection {reflected.get('action')!r} "
                        f"→ {checkpoint['decision']!r} "
                        f"(evaluate_request rule={policy_rule})."
                    )
                    if policy_rule:
                        reason = f"evaluate_request rule={policy_rule}"
                else:
                    emit(
                        "=== Reflection outcome ===\n"
                        "unparsed reply; draft kept"
                    )
                if label != "unparsed" and checkpoint.get("decision"):
                    decision = str(checkpoint["decision"])
                if checkpoint["flag"] and "flag_for_human_review" not in tools_called:
                    evaluated = _action_arguments(loop_lines, "evaluate_request")
                    flag_args = {
                        "employee_id": str(
                            evaluated.get("employee_id") or explicit_emp_id or ""
                        ),
                        "request": str(evaluated.get("item") or "equipment"),
                        "reason": "Reflection escalated; human review required.",
                    }
                    emit("Action: " + _call_key("flag_for_human_review", flag_args))
                    observation = await call_tool("flag_for_human_review", flag_args)
                    emit("Observation:\n" + observation)
                    tools_called = [*tools_called, "flag_for_human_review"]
                reason = _ensure_policy_rule(reason, policy_rule)
                emit(f"Final Decision: {decision}")
                emit(f"Reason: {reason}")
            else:
                snapped = bool(policy_decision and decision != policy_decision)
                if snapped:
                    emit(
                        "=== Decision lock ===\n"
                        f"Snapping {decision!r} → {policy_decision!r} "
                        f"(evaluate_request rule={policy_rule})."
                    )
                    decision = policy_decision
                    if policy_rule:
                        reason = f"evaluate_request rule={policy_rule}"
                updated = _ensure_policy_rule(reason, policy_rule)
                if snapped or updated != reason:
                    reason = updated
                    emit(f"Final Decision: {decision}")
                    emit(f"Reason: {reason}")
                else:
                    reason = updated

            saved = _save_trace(
                user_prompt,
                trace_lines,
                decision,
                path=trace_path,
                extra_header={
                    **({"scenario": scenario_id} if scenario_id else {}),
                    "draft_decision": str(draft_decision),
                    "policy_decision": str(policy_decision or ""),
                    "policy_rule": str(policy_rule or ""),
                    "reflection": "on" if enable_reflection else "off",
                },
            )
            emit(f"=== Trace saved ===\n{saved}")

            return {
                "decision": decision,
                "reason": reason,
                "draft_decision": draft_decision,
                "draft_reason": draft_reason,
                "policy_decision": policy_decision,
                "policy_rule": policy_rule,
                "reflection": reflection_note,
                "tools_called": tools_called,
                "trace_path": str(saved),
                "steps": sum(1 for line in loop_lines if line.startswith("Thought:")),
            }


def main() -> None:
    parser = argparse.ArgumentParser(description="IT equipment ReAct agent (Ollama + MCP)")
    parser.add_argument(
        "prompt",
        nargs="?",
        default=(
            "EMP-101: My laptop is about 4 years old and slowing down during builds; "
            "can I get a replacement laptop?"
        ),
        help="Natural-language equipment request",
    )
    parser.add_argument(
        "--reflect",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run reflection after the draft (default: on)",
    )
    parser.add_argument(
        "--inject-draft",
        default=None,
        help="Replace draft with this text before reflection (demo correction)",
    )
    parser.add_argument(
        "--trace-path",
        default=None,
        help="Optional path to write the trace file",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run_agent(
            args.prompt,
            enable_reflection=args.reflect,
            inject_draft=args.inject_draft,
            trace_path=Path(args.trace_path) if args.trace_path else None,
        )
    )
    if not result.get("decision"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
