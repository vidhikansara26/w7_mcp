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
  test_client.py             # Phase 3 — stdio handshake client
  requirements.txt
  .env.example               # later: OLLAMA_HOST, OLLAMA_MODEL
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Phase 2 — unit tests

```bash
pytest test_business_logic.py -v
```

## Phase 3 — MCP handshake

```bash
python test_client.py
```

Expected: `initialize` → `list_tools` shows `get_employee_info` → EMP-101 payload.
