"""ReAct agent over the IT Equipment MCP server (Ollama + stdio)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

import llm
from prompts import PARSE_RETRY_HINT, REFLECTION_PROMPT, build_system_prompt

load_dotenv()

ROOT = Path(__file__).resolve().parent
TRACES_DIR = ROOT / "traces"

ACTION_RE = re.compile(
    r"Action\s*:\s*(\{.*\})",
    re.IGNORECASE | re.DOTALL,
)
FINAL_RE = re.compile(
    r"Final\s*Decision\s*:\s*(approve|deny|escalate)\s*",
    re.IGNORECASE,
)
REASON_RE = re.compile(
    r"Reason\s*:\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)
THOUGHT_RE = re.compile(
    r"Thought\s*:\s*(.+?)(?=\n\s*(?:Action|Final\s*Decision|Reflection)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)
REFLECTION_RE = re.compile(
    r"Reflection\s*:\s*(.+?)(?=\n\s*Final\s*Decision\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _max_steps() -> int:
    return int(os.getenv("AGENT_MAX_STEPS", "8"))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    match = ACTION_RE.search(text)
    raw = match.group(1).strip() if match else None
    if not raw:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        raw = text[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            data = json.loads(raw.split("\n")[0])
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def parse_assistant(text: str) -> dict[str, Any]:
    """Parse Thought/Action or Thought/Final Decision from model output."""
    thought_m = THOUGHT_RE.search(text)
    thought = thought_m.group(1).strip() if thought_m else ""

    final_m = FINAL_RE.search(text)
    if final_m:
        reason_m = REASON_RE.search(text)
        return {
            "kind": "final",
            "thought": thought,
            "decision": final_m.group(1).lower(),
            "reason": (reason_m.group(1).strip() if reason_m else ""),
            "raw": text,
        }

    action_obj = _extract_json_object(text)
    if action_obj:
        tool = action_obj.get("tool") or action_obj.get("name")
        arguments = action_obj.get("arguments") or action_obj.get("args") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        if tool and str(tool).strip().lower().replace("_", " ") in {
            "final decision",
            "finaldecision",
            "decide",
        }:
            decision = (
                arguments.get("decision")
                or arguments.get("Final Decision")
                or arguments.get("result")
                or "approve"
            )
            decision_s = str(decision).lower().strip()
            if decision_s not in {"approve", "deny", "escalate"}:
                decision_s = "approve"
            return {
                "kind": "final",
                "thought": thought,
                "decision": decision_s,
                "reason": str(
                    arguments.get("reason")
                    or arguments.get("Reason")
                    or thought
                    or "Decision inferred from tool-shaped Final Decision action."
                ),
                "raw": text,
            }
        if tool and isinstance(arguments, dict):
            return {
                "kind": "action",
                "thought": thought,
                "tool": str(tool),
                "arguments": arguments,
                "raw": text,
            }

    return {"kind": "invalid", "thought": thought, "raw": text}


def parse_reflection(text: str) -> dict[str, Any]:
    """Parse Reflection / Final Decision / Reason from the reflection call."""
    reflection_m = REFLECTION_RE.search(text)
    final = parse_assistant(text)
    return {
        "reflection": (reflection_m.group(1).strip() if reflection_m else text.strip()),
        "decision": final.get("decision") if final.get("kind") == "final" else None,
        "reason": final.get("reason") if final.get("kind") == "final" else "",
        "raw": text,
    }


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


def _parse_tool_payload(observation: str) -> dict[str, Any] | None:
    """Best-effort parse of a tool observation that is JSON (possibly pretty-printed)."""
    text = observation.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def run_reflection(
    *,
    user_request: str,
    draft: str,
    observations: str,
) -> dict[str, Any]:
    """Second Ollama call: validate or correct the drafted decision."""
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
                    "You are reviewing an IT equipment agent's draft. "
                    "Correct errors using tool observations only."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
    )
    parsed = parse_reflection(raw)
    return parsed


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
    observations_log: list[str] = []
    tools_called: list[str] = []
    policy_decision: str | None = None
    policy_rule: str | None = None

    def emit(block: str) -> None:
        print(block, flush=True)
        trace_lines.append(block)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            known = {t.name for t in tools}
            system_prompt = build_system_prompt(tools)
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            emit(f"=== User request ===\n{user_prompt}\n")
            emit(
                f"=== MCP tools ({len(tools)}) ===\n"
                + ", ".join(t.name for t in tools)
                + "\n"
            )

            decision: str | None = None
            reason = ""
            parse_retries = 0
            step = 0

            while step < _max_steps():
                step += 1
                emit(f"=== Step {step} ===")
                assistant = llm.chat(messages)
                messages.append({"role": "assistant", "content": assistant})
                parsed = parse_assistant(assistant)

                if parsed["kind"] == "invalid":
                    emit(f"Thought: {parsed.get('thought') or '(parse failed)'}")
                    emit(f"Raw model output:\n{assistant}")
                    if parse_retries < 1:
                        parse_retries += 1
                        emit("Observation: invalid format — requesting one parse retry\n")
                        messages.append({"role": "user", "content": PARSE_RETRY_HINT})
                        continue
                    decision = "escalate"
                    reason = "Agent output unparseable after retry; escalate rather than guess."
                    emit(f"Final Decision: {decision}")
                    emit(f"Reason: {reason}\n")
                    break

                thought = parsed.get("thought") or ""
                if thought:
                    emit(f"Thought: {thought}")

                if parsed["kind"] == "final":
                    decision = parsed["decision"]
                    reason = parsed.get("reason") or ""
                    emit(f"Draft Final Decision: {decision}")
                    emit(f"Draft Reason: {reason}\n")
                    break

                tool = str(parsed["tool"])
                arguments = parsed["arguments"]
                if tool not in known:
                    emit(f"Action: {json.dumps({'tool': tool, 'arguments': arguments})}")
                    observation = (
                        f"Unknown tool {tool!r}. Valid tools: {sorted(known)}. "
                        "Final Decision is NOT a tool — never Action tool=deny/approve/"
                        "escalate. If evaluate_request already returned, reply with "
                        "Final Decision / Reason (or flag_for_human_review if escalate)."
                    )
                    emit(f"Observation:\n{observation}\n")
                    messages.append({"role": "user", "content": observation})
                    continue

                emit(f"Action: {json.dumps({'tool': tool, 'arguments': arguments})}")
                tools_called.append(tool)

                result = await session.call_tool(tool, arguments)
                observation = _observation_text(result)
                observations_log.append(f"{tool}: {observation}")
                emit(f"Observation:\n{observation}\n")

                route_note = ""
                if tool == "evaluate_request":
                    payload = _parse_tool_payload(observation)
                    if payload and payload.get("decision") in {
                        "approve",
                        "deny",
                        "escalate",
                    }:
                        policy_decision = str(payload["decision"])
                        policy_rule = str(payload.get("rule") or "")
                        if policy_decision == "escalate":
                            route_note = (
                                f"[ROUTE] evaluate_request → escalate "
                                f"(rule={policy_rule}). "
                                "Next: call flag_for_human_review, then Final Decision: escalate."
                            )
                        else:
                            route_note = (
                                f"[ROUTE] evaluate_request → {policy_decision} "
                                f"(rule={policy_rule}). "
                                f"Do NOT call flag_for_human_review. "
                                f"Next: Final Decision: {policy_decision}."
                            )
                        emit(route_note + "\n")

                user_obs = f"Observation from {tool}:\n{observation}"
                if route_note:
                    user_obs += f"\n{route_note}"
                messages.append({"role": "user", "content": user_obs})
            else:
                decision = policy_decision or "escalate"
                reason = (
                    f"Exceeded max steps ({_max_steps()}); "
                    f"using evaluate_request={policy_decision!r} "
                    f"(rule={policy_rule}) or escalate."
                )
                emit(f"Draft Final Decision: {decision}")
                emit(f"Draft Reason: {reason}\n")

            draft_decision = decision
            draft_reason = reason
            draft_text = f"Final Decision: {decision}\nReason: {reason}"

            if inject_draft:
                emit("=== Injected bad draft (reflection demo) ===")
                emit(inject_draft + "\n")
                draft_text = inject_draft
                injected = parse_assistant(inject_draft)
                if injected.get("kind") == "final":
                    draft_decision = injected["decision"]
                    draft_reason = injected.get("reason") or draft_reason

            reflection_note = ""
            if enable_reflection and decision:
                emit("=== Reflection ===")
                reflected = run_reflection(
                    user_request=user_prompt,
                    draft=draft_text,
                    observations="\n\n".join(observations_log),
                )
                emit(reflected["raw"] + "\n")
                reflection_note = reflected.get("reflection") or ""
                if reflected.get("decision"):
                    reflected_decision = reflected["decision"]
                    if reflected_decision != draft_decision:
                        emit(
                            "=== Reflection outcome ===\n"
                            f"draft={draft_decision!r} → reflected={reflected_decision!r}\n"
                        )
                    else:
                        emit(
                            "=== Reflection outcome ===\n"
                            f"draft validated: {draft_decision!r}\n"
                        )
                    decision = reflected_decision
                    reason = reflected.get("reason") or reason

                # Decision lock: evaluate_request wins over LLM draft/reflection.
                if policy_decision and decision != policy_decision:
                    emit(
                        "=== Decision lock ===\n"
                        f"Snapping {decision!r} → {policy_decision!r} "
                        f"(evaluate_request rule={policy_rule}).\n"
                    )
                    decision = policy_decision
                    if policy_rule and policy_rule not in reason:
                        reason = (
                            f"{reason} [locked to evaluate_request rule={policy_rule}]"
                        ).strip()

                if (
                    decision == "escalate"
                    and "flag_for_human_review" not in tools_called
                ):
                    emit(
                        "=== Decision lock ===\n"
                        "escalate requires flag_for_human_review; "
                        "keeping escalate but note missing flag in this run.\n"
                    )

                emit(f"Final Decision: {decision}")
                emit(f"Reason: {reason}\n")
            else:
                if policy_decision and decision != policy_decision:
                    emit(
                        "=== Decision lock ===\n"
                        f"Snapping {decision!r} → {policy_decision!r} "
                        f"(evaluate_request rule={policy_rule}).\n"
                    )
                    decision = policy_decision
                emit(f"Final Decision: {decision}")
                emit(f"Reason: {reason}\n")

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
                "steps": step,
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
        help="Run Phase 6 reflection after the draft (default: on)",
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
