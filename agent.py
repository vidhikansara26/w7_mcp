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
    r"Thought\s*:\s*(.+?)(?=\n\s*(?:Action|Final\s*Decision)\s*:|\Z)",
    re.IGNORECASE | re.DOTALL,
)


def _max_steps() -> int:
    return int(os.getenv("AGENT_MAX_STEPS", "8"))


def _extract_json_object(text: str) -> dict[str, Any] | None:
    match = ACTION_RE.search(text)
    raw = match.group(1).strip() if match else None
    if not raw:
        # Fallback: first {...} block
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        raw = text[start : end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to tighten to first balanced object
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
        # Local models sometimes emit Final Decision as a fake tool call.
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


def _save_trace(prompt: str, lines: list[str], decision: str | None) -> Path:
    TRACES_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = TRACES_DIR / f"{ts}_{_slug(prompt)}.txt"
    header = [
        f"prompt: {prompt}",
        f"model: {llm.ollama_model()}",
        f"host: {llm.ollama_host()}",
        f"decision: {decision or 'none'}",
        "",
    ]
    path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    return path


def reflect_stub(draft: str, observations: str) -> str | None:
    """Phase 6 hook — not required for Phase 5 done gate."""
    _ = REFLECTION_PROMPT.format(draft=draft, observations=observations)
    return None


async def run_agent(user_prompt: str, *, enable_reflection: bool = False) -> dict[str, Any]:
    server_params = StdioServerParameters(
        command=sys.executable,
        args=[str(ROOT / "server.py")],
        cwd=str(ROOT),
    )

    trace_lines: list[str] = []
    observations_log: list[str] = []

    def emit(block: str) -> None:
        print(block, flush=True)
        trace_lines.append(block)

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            system_prompt = build_system_prompt(tools)
            messages: list[dict[str, str]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            emit(f"=== User request ===\n{user_prompt}\n")
            emit(f"=== MCP tools ({len(tools)}) ===\n" + ", ".join(t.name for t in tools) + "\n")

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
                    emit(f"Final Decision: {decision}")
                    emit(f"Reason: {reason}\n")
                    break

                tool = parsed["tool"]
                arguments = parsed["arguments"]
                known = {t.name for t in tools}
                if tool not in known:
                    emit(f"Action: {json.dumps({'tool': tool, 'arguments': arguments})}")
                    observation = (
                        f"Unknown tool {tool!r}. Valid tools: {sorted(known)}. "
                        "If you are ready to decide, reply with Final Decision / Reason "
                        "(not an Action)."
                    )
                    emit(f"Observation:\n{observation}\n")
                    messages.append({"role": "user", "content": observation})
                    continue

                emit(f"Action: {json.dumps({'tool': tool, 'arguments': arguments})}")

                result = await session.call_tool(tool, arguments)
                observation = _observation_text(result)
                observations_log.append(f"{tool}: {observation}")
                emit(f"Observation:\n{observation}\n")
                messages.append(
                    {
                        "role": "user",
                        "content": f"Observation from {tool}:\n{observation}",
                    }
                )
            else:
                decision = "escalate"
                reason = f"Exceeded max steps ({_max_steps()}); escalate rather than guess."
                emit(f"Final Decision: {decision}")
                emit(f"Reason: {reason}\n")

            if enable_reflection and decision:
                draft = f"Final Decision: {decision}\nReason: {reason}"
                reflected = reflect_stub(draft, "\n\n".join(observations_log))
                if reflected:
                    emit("=== Reflection (Phase 6) ===")
                    emit(reflected)

            trace_path = _save_trace(user_prompt, trace_lines, decision)
            emit(f"=== Trace saved ===\n{trace_path}")

            return {
                "decision": decision,
                "reason": reason,
                "trace_path": str(trace_path),
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
        action="store_true",
        help="Enable Phase 6 reflection hook (stub unless implemented)",
    )
    args = parser.parse_args()
    result = asyncio.run(run_agent(args.prompt, enable_reflection=args.reflect))
    if not result.get("decision"):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
