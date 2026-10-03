# IT Equipment Request Lab using MCP

Week 7 lab: an internal IT equipment request handler exposed as an MCP server, plus a
ReAct agent that investigates requests with those tools and escalates ambiguous cases
instead of guessing.

Pipeline: **Planner → TAO → Reflector**

- **Planner**: each turn the model writes a thought that says what it knows, what is still missing, and why the next action follows.
- **TAO**: Thought / Action / Observation over MCP tools. After `evaluate_request`,
  the trace records that tool's decision and rule. The next thought chooses flag or finish.
- **Reflector**: checkpoint after the draft. `PROCEED` keeps a draft the tool supports.
  `DENY` sticks only when the tool said deny. `ESCALATE` flags for a human when the
  tool said escalate, or when the review cannot tell. Any other disagreement snaps
  back to `evaluate_request.decision`.

Policy decisions are **deterministic** in `evaluate_request`. The LLM extracts fields,
calls tools, routes, and drafts — it does not invent approve/deny/escalate.

## Layout

```
w7_mcp/
  docs/requirements.md       # policy design (testable rules)
  docs/architecture.md       # end-to-end system flow (granular)
  data/                      # employees.json, policies.json
  business_logic.py          # plain functions (tested directly)
  test_business_logic.py
  server.py                  # FastMCP stdio server
  test_client.py             # full-server handshake / smoke client
  examples/                  # minimal one-tool ping server + client
  agent.py                   # ReAct agent (Ollama + MCP)
  llm.py / prompts.py
  run_scenarios.py           # graded approve/deny/escalate traces
  assemble_submission.py     # Markdown + PDF packet
  traces/scenarios/          # curated evidence
  docs/submission_assets/    # handshake + CI screenshots
  .github/workflows/ci.yml
```

## Tools

| Tool | What it does |
|---|---|
| `get_employee_info(employee_id)` | role, tenure, equipment on file |
| `find_employee(name, role?)` | resolve EMP-### from name/department |
| `get_policy_limits(role)` | role eligibility intervals / quantities |
| `check_request_eligibility(employee_id, item)` | eligibility math (no free-text reason) |
| `evaluate_request(employee_id, item, reason)` | authoritative `{decision, rule}` |
| `flag_for_human_review(employee_id, request, reason)` | side-effecting escalation queue |

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # edit OLLAMA_HOST if needed
```

Default model: `qwen3:8b` with thinking off, via `OLLAMA_HOST` (often `http://host.docker.internal:11434`
from Docker Desktop).

## Run

```bash
# unit tests (no LLM)
pytest test_business_logic.py test_agent.py test_score.py -v

# Suggested Approach step 3 — minimal one-tool proof
python examples/minimal_client.py

# full-server handshake + EMP-101
python test_client.py
python test_client.py --all-tools

# one natural-language request
python agent.py "EMP-101: My laptop is about 4 years old and slowing down; can I get a replacement?"

# graded scenarios → traces/scenarios/
python run_scenarios.py

# submission packet
python assemble_submission.py --pdf
```

## Scenarios

| Trace | Outcome |
|---|---|
| `00_reflection_correction.txt` | injected deny draft → corrected to approve |
| `01_approve.txt` | EMP-101 → `within_policy` approve |
| `02_deny.txt` | EMP-102 → `exceeds_frequency` deny |
| `03_escalate_damage.txt` | EMP-103 → `early_refresh_exceptional` + flag |
| `04_escalate_nonstandard.txt` | non-catalog item → `non_standard_item` + flag |

## CI

`.github/workflows/ci.yml` runs Ruff, mypy, and
`pytest test_business_logic.py test_agent.py test_score.py -v` on push/PR.
No Ollama in CI.
