# IT Equipment Request Lab using MCP

MCP week 7 assignment: policy design → plain Python → MCP server → ReAct agent.

## Layout

```
w7_mcp/
  docs/requirements.md       # Phase 1 — policy design
  data/
    employees.json           # mock employee DB
    policies.json            # role × item limits
  business_logic.py          # Phase 2 — plain functions
  test_business_logic.py     # Phase 2 — pytest
  server.py                  # Phase 3–4 — FastMCP (stdio)
  test_client.py             # stdio MCP client (handshake + optional --all-tools)
  agent.py                   # ReAct agent (Ollama + MCP)
  llm.py                     # Ollama /api/chat wrapper
  prompts.py                 # system + parse-retry prompts
  run_scenarios.py           # Phase 6 graded scenarios + reflection demo
  assemble_submission.py     # Phase 7 submission packet assembler
  traces/                    # ad-hoc Thought/Action/Observation logs
  traces/scenarios/          # curated Phase 6 evidence traces
  docs/submission_assets/    # handshake + CI screenshots for the PDF
  .github/workflows/ci.yml   # Phase 7 CI (pytest only; no Ollama)
  requirements.txt
  .env.example               # OLLAMA_HOST, OLLAMA_MODEL
```

## Setup (required)

Use a project virtualenv — do not install deps into system Python.

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # edit OLLAMA_HOST if needed
```

Confirm the venv is active (`which python` should point at `.venv/bin/python`) before
running tests, the MCP client, or the agent. `.venv/` is gitignored.

## Unit tests

```bash
pytest test_business_logic.py -v
```

## MCP server (stdio)

MCP tools (thin wrappers over `business_logic.py`):

- `get_employee_info` — lookup by `EMP-###`
- `find_employee` — resolve `EMP-###` from name (+ optional department)
- `get_policy_limits`
- `check_request_eligibility`
- `flag_for_human_review`

```bash
# Handshake + EMP-101
python test_client.py

# Smoke-call all four tools
python test_client.py --all-tools
```

## ReAct agent (Ollama)

Requires a running Ollama with `OLLAMA_MODEL` pulled (default `llama3.1:8b`).
Inside Docker Desktop, you may need `OLLAMA_HOST=http://host.docker.internal:11434`.

```bash
python agent.py "EMP-101: My laptop is about 4 years old and slowing down; can I get a replacement?"

# Or name + department (uses find_employee → EMP-###):
python agent.py "I'm Alex Rivera in Engineering. My laptop is 4 years old — can I get a replacement?"
```

Logs Thought → Action → Observation to stdout and `traces/`. Reflection runs by default
(`--no-reflect` to skip).

## Phase 6 scenarios + reflection

```bash
python run_scenarios.py
```

Writes curated traces under `traces/scenarios/`:

- `00_reflection_correction.txt` — injected bad deny draft corrected to approve
- `01_approve.txt` — EMP-101 clear approve
- `02_deny.txt` — EMP-102 clear deny
- `03_escalate_damage.txt` — EMP-103 early + damage → flag + escalate
- `04_escalate_nonstandard.txt` — non-catalog item → flag + escalate

## CI (Phase 7)

GitHub Actions (`.github/workflows/ci.yml`) on push/PR:

1. Python 3.11
2. `pip install -r requirements.txt`
3. `pytest test_business_logic.py -v`

No Ollama / agent steps in CI.

## Submission packet (Phase 7)

Assembles the graded PDF sections into Markdown (and optional PDF):

```bash
source .venv/bin/activate
# optional screenshots:
#   docs/submission_assets/phase3_handshake.png
#   docs/submission_assets/ci_green.png
python assemble_submission.py          # → submission/IT_Equipment_MCP_Submission.md
pip install fpdf2                      # PDF export (no pandoc required)
python assemble_submission.py --pdf    # → submission/*.pdf (headings + code formatting)
```

Open the PDF in Chrome, Preview, or a system viewer — Cursor’s built-in preview is limited.
The Markdown file is always the reliable readable source.
