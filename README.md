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
  traces/                    # Thought/Action/Observation logs
  requirements.txt
  .env.example               # OLLAMA_HOST, OLLAMA_MODEL
```

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env         # edit OLLAMA_HOST if needed
```

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

Logs Thought → Action → Observation to stdout and `traces/`.
