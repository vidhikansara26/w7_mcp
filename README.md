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
  requirements.txt
  .env.example               # later: OLLAMA_HOST, OLLAMA_MODEL
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Unit tests

```bash
pytest test_business_logic.py -v
```

## MCP server (stdio)

Four tools (thin wrappers over `business_logic.py`):

- `get_employee_info`
- `get_policy_limits`
- `check_request_eligibility`
- `flag_for_human_review`

```bash
# Handshake + EMP-101
python test_client.py

# Smoke-call all four tools
python test_client.py --all-tools
```
