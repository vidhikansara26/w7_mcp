## Summary
- Merge submission polish: MCP `evaluate_request`, agent `[ROUTE]` + decision lock, minimal one-tool handshake, rebuilt PDF packet
- Prefer explicit `EMP-###` over conflicting `find_employee` name matches (fixes Sam/EMP-101 drift)
- Add `docs/architecture.md` and refresh graded scenario traces

## Test plan
- [x] `pytest test_business_logic.py -v` (27 passed)
- [x] `python examples/minimal_client.py`
- [x] `python test_client.py`
- [x] `python run_scenarios.py` (approve / deny / escalate×2)
- [x] `python agent.py "EMP-101: My name is Sam and I want a new keyboard." --no-reflect` stays on EMP-101
- [ ] Confirm GitHub Actions green on this PR
- [ ] Optional: replace `docs/submission_assets/ci_green.png` with Actions UI screenshot and re-run `python assemble_submission.py --pdf`
