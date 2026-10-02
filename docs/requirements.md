# IT Equipment Request System — Requirements & Policy Design

**Phase 1 deliverable.** This document alone defines request schema, catalog, role-based frequency limits, escalate rules, decision outcomes, and mock employee fixtures. Later phases implement these rules in code; they must not invent policy beyond what is stated here.

---

## 1. Request schema

Every equipment request carries the following fields:

| Field | Type | Description |
|---|---|---|
| `employee_id` | string | Stable employee identifier, pattern `EMP-###` (e.g. `"EMP-101"`). |
| `role` | string | One of `Engineering`, `Sales`, `Operations`, `Management`. **Authoritative role comes from the employee record**, not free-text in the request. Free-text role claims are ignored when they conflict with the record. |
| `item_requested` | string | Catalog key for the item (e.g. `"laptop"`, `"monitor"`, `"ergonomic_chair"`, or a non-standard name such as `"standing_desk"`). |
| `reason` | string | Free-text justification supplied by the requester. Used only to detect exceptional circumstances when frequency limits are exceeded; it does not override catalog or role rules. |

Natural-language prompts (agent phase) are parsed into this schema before policy evaluation. Missing `employee_id` or `item_requested` is invalid input and must not produce an approve decision.

---

## 2. Role-based policy limits

Frequency is measured from the **most recent issue date** of that item type on the employee’s inventory (or from hire / tenure fields when inventory lacks an issue date — see escalate rule 3).

| Role | Laptop | Monitor | Ergonomic chair |
|---|---|---|---|
| **Engineering** | 1 every **3** years | up to **2** every **2** years | 1 every **5** years |
| **Sales** | 1 every **4** years | 1 every **3** years | 1 every **5** years |
| **Operations** | 1 every **4** years | 1 every **3** years | 1 every **5** years |
| **Management** | 1 every **2** years | **2** every **2** years | 1 every **5** years |

### Policy interpretation rules

- **Within limit** means: for the requested item, either the employee has never been issued that item, or enough full years have elapsed since the last issue date to meet the role’s minimum interval, and (for monitors under Engineering/Management) current owned count is below the max quantity.
- **Exceeds frequency** means: the minimum interval has not elapsed (or quantity would exceed the role max).
- Year intervals use calendar years from `issued_date` to “today” at evaluation time (mock “today” may be fixed in fixtures for deterministic tests).
- `ergonomic_chair` is a **standard** catalog item with a simple default of **1 every 5 years for all four roles**, so chair requests remain testable even though the official laptop/monitor tables do not list chairs.

Unknown roles (anything outside the four named roles) are a structured error from policy lookup — not approve, deny, or escalate.

---

## 3. Standard vs non-standard catalog

### Standard items (auto-eligible for approve/deny against policy)

| Item key | Notes |
|---|---|
| `laptop` | Primary compute device; frequency by role (table above). |
| `monitor` | Display; quantity + frequency by role. |
| `ergonomic_chair` | Seating; 1 every 5 years for all standard roles. |

### Non-standard items (always escalate)

Examples (non-exhaustive; any item **not** in the standard list is non-standard):

- Mechanical keyboard
- Ergonomic split keyboard
- Standing desk
- Docking station variants not on the standard list
- Any free-text item that does not normalize to `laptop`, `monitor`, or `ergonomic_chair`

Non-standard requests **must not** be approved or denied by automatic policy; they require human review.

---

## 4. Escalate rules (bounded)

The system escalates **only** when one of these three criteria holds. Escalation **must** invoke `flag_for_human_review` (employee id, request summary, reason) and return decision `escalate`.

1. **Non-standard item** — `item_requested` is not on the standard catalog (`laptop` / `monitor` / `ergonomic_chair`).
2. **Early refresh with exceptional justification** — request **exceeds frequency**, but `reason` credibly claims exceptional circumstances. Recognized signal phrases (case-insensitive substring match is sufficient for the lab): `damaged`, `crushed`, `stolen`, `broken screen`, `client site` (as in damaged on client site). Without such justification, an early request is a clear **deny**, not escalate.
3. **Missing or conflicting tenure / issue-date data** — employee record exists but inventory issue dates are missing, malformed, or conflict with tenure fields so eligibility cannot be computed deterministically. Do not guess timelines; escalate.

Borderline timing without exceptional justification is **deny**, not escalate. Contractors, cost caps, and manager overrides are **out of scope** for this lab.

---

## 5. Decision outcomes

Every evaluated request ends in exactly one of:

| Outcome | When |
|---|---|
| `approve` | Item is standard; employee found; role known; within quantity/frequency limits; tenure/issue data sufficient. |
| `deny` | Item is standard; employee found; role known; **exceeds frequency** (or would exceed quantity) **and** reason does **not** contain exceptional justification. |
| `escalate` | Any of the three bounded escalate rules; after calling `flag_for_human_review`. |

Supporting structured results (not final outcomes) include:

- Employee not found / invalid id → structured error (tests & tools); agent must not invent an approve.
- Unknown role on record → structured error from `get_policy_limits`.

Eligibility helpers may expose flags such as `eligible`, `decision_hint` (`approve` / `deny` / `escalate`), `exceeds_frequency`, `last_issued`, `next_eligible_date`, and human-readable `reasons`. They do **not** inspect free-text justification. The authoritative final outcome comes from `evaluate_request(employee_id, item, reason)`, which applies exceptional-justification phrases on top of eligibility and returns `{decision, rule}`.

---

## 6. Mock employee fixtures (seed for scenarios & tests)

Reference “today” for age calculations in design and tests: **2026-10-02**.

| ID | Role | Inventory / history | Purpose |
|---|---|---|---|
| **EMP-101** | Engineering | Laptop issued **2022-09-01** (~4 years ago) | Clear **approve** — Engineering laptop ≥ 3 years; standard replacement reason. |
| **EMP-102** | Sales | Laptop issued **2026-04-01** (~6 months ago) | Clear **deny** — Sales laptop interval is 4 years; reason has **no** damage/exception claim. |
| **EMP-103** | Engineering | Laptop issued **2025-10-01** (~1 year ago) | **Escalation A** — early vs 3-year Engineering rule, but reason claims crushed / stolen / broken screen → must `flag_for_human_review` → escalate. |
| **EMP-104** | Management | Monitor ×1 issued **2024-10-01**; chair none | Supporting monitor/chair cases (within Management limits when applicable). |
| **EMP-105** | Engineering | Laptop present but **`issued_date` missing/conflicting** with tenure | Tenure-data escalate (optional extra / unit tests). |
| **EMP-999** | — | **Absent** from DB | Not found — supporting edge in unit tests. |

### Target graded scenarios (Phase 6 evidence)

1. **Clear approval** — EMP-101 requests `laptop` with a normal wear/performance reason → `evaluate_request` → **approve** (`within_policy`). Must not escalate.
2. **Clear denial** — EMP-102 requests `laptop` replacement after ~6 months with no exceptional justification → `evaluate_request` → **deny** (`exceeds_frequency`). Must not escalate.
3. **Escalation A** — EMP-103 early laptop + exceptional reason → `evaluate_request` → **escalate** (`early_refresh_exceptional`) → call **`flag_for_human_review`** → **escalate**.
4. **Escalation B** — Any seeded employee (e.g. EMP-101 or EMP-104) requests non-standard gear (e.g. ergonomic split keyboard or standing desk) → `evaluate_request` → **escalate** (`non_standard_item`) → call **`flag_for_human_review`** → **escalate**.

---

## 7. Escalation queue (side effect)

`flag_for_human_review(employee_id, request, reason)` appends a record:

```json
{
  "timestamp": "<ISO-8601 UTC>",
  "employee_id": "EMP-103",
  "request": "laptop",
  "reason": "<human-readable escalation reason>"
}
```

Storage for the lab: in-memory list for the process lifetime, plus optional append to `data/escalations.jsonl`. Return a confirmation dict including the written fields.

---

## 8. Plain-function + MCP surface (implemented)

These functions encode this document in `business_logic.py` and are exposed as MCP tools in `server.py`.

| Function | Responsibility |
|---|---|
| `get_employee_info(employee_id)` | Role, tenure, hardware inventory; structured error if missing/invalid. |
| `get_policy_limits(role)` | Per-item max quantity + min years; error if unknown role. |
| `check_request_eligibility(employee_id, item)` | Compose employee + policy; eligibility hints only (no free-text reason). |
| `evaluate_request(employee_id, item, reason)` | Authoritative `{decision, rule}` including exceptional-justification matching. |
| `flag_for_human_review(employee_id, request, reason)` | Queue append + confirmation (required whenever decision is escalate). |

Stable `evaluate_request.rule` ids include: `within_policy`, `exceeds_frequency`, `early_refresh_exceptional`, `non_standard_item`, `missing_tenure`.

---

## Done criteria

A grader can reconstruct approve / deny / escalate outcomes for the four scenarios from this document alone, and the agent must copy `evaluate_request.decision` rather than inventing policy.
