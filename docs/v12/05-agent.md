# v12 — the agentic system (lane B7 backend, F6 frontend)

Existing foundation (read it first): `backend/app/services/agent_tools.py` (typed registry `ToolSpec`, READ tools run inline,
WRITE tools only PROPOSE an `agent_action` that a human confirms — rule 6), `backend/app/services/ai.py::run_agent_turn`
(GLM-5.3 native tool calling, `MAX_TOOL_ITERATIONS`, `_AGENT_SYSTEM` prompt, context refs), tests `tests/test_agent_tools.py`,
`tests/test_ai_tool_call.py` (fake chat model). Keep every existing tool and contract.

## 1. New READ tools (inline, caller's own grants)
| name | gate | returns |
|---|---|---|
| `list_policies` | Policies.View | filtered policies (same filters as `GET /policies`, max 20 rows) |
| `get_policy` | Policies.View | policy detail incl. parties, missing[], collection_summary, journey |
| `list_installments` | Collections.View | Cobranza rows (bucket/insurer/level filters, max 30) |
| `get_collection_overview` | Collections.View | `/collections/summary` + top overdue + pending evidence count |
| `get_collection_mode` | Collections.View | resolver output for an installment (level + reason + missing) |
| `list_status_inquiries` | Collections.View | inquiries + status |
| `list_pending_evidence` | Collections.View | evidence inbox |
| `check_case_files` | CaseFiles.View | for an account: entered/skipped stages, recommended files of its subtype/ramo vs. uploaded, missing items |
| `check_policy_files` | Policies.View | source document present?, plan?, payer contact?, commission? (the "Falta: X" list) |
| `get_catalog` | Dashboard.View | ramo/subtipo catalogue (codes + names + suggested route) |
| `list_portal_searches` / `get_portal_search` | Integrations.View | RPA runs and quotes |
| `list_notifications` | Dashboard.View | the user's open notifications |

## 2. New WRITE tools (propose → confirm)
| name | gate | executes (reusing the router/service functions) |
|---|---|---|
| `create_policy_manual` | Policies.Create | `POST /policies` service path |
| `update_policy_fields` | Policies.Edit | "Completar" (fill-null only) |
| `register_payment` | Collections.Edit | manual payment |
| `record_payment_promise` | Collections.Edit | promise + note |
| `start_collection` | Collections.Edit | plan start (assisted works for any insurer) |
| `send_status_inquiry` | Collections.Edit | inquiry to the insurer (email) |
| `send_payment_reminder` | Collections.Edit | payer reminder now |
| `create_share_link` | Policies.View | share link (insured or insurer) — returns url + whatsapp url in the confirmed result |
| `create_payment_link` | Collections.Edit | native intent (fails with reason if not native) |
| `skip_case_to_stage` | CaseFiles.Edit | partial journey skip |
| `run_portal_search` | Integrations.Submit | queue an RPA search |
Each write tool: `summarize` (Spanish card sentence built server-side), `validate_proposal` (broker-scoped existence checks),
`anchor`. Card copy and descriptions are Spanish (LLM/user copy).

## 3. Skills (`backend/app/services/agent_skills.py`)
A skill = a named, versioned instruction pack + a recommended tool subset, loaded on demand (progressive disclosure: the base
prompt lists skill titles/one-liners; the model calls the native tool `load_skill(key)` to pull the full instructions into the
conversation; the thread remembers `active_skills`).
```python
@dataclass(frozen=True)
class SkillSpec:
    key: str                 # english id
    title: str               # Spanish
    description: str         # Spanish one-liner (shown in the base prompt + UI)
    instructions: str        # Spanish, the full playbook (LLM prompt — Spanish allowed)
    tools: tuple[str, ...]   # tool names it relies on (must exist in TOOL_REGISTRY)
    triggers: tuple[str, ...]  # Spanish keywords for auto-suggestion in the UI
```
Ship these skills: `collections_review` (revisar cobranza: vencidas, riesgo de término, qué consultar a cada compañía, proponer
consultas y recordatorios), `policy_intake` (registrar una póliza: desde PDF o manual, completar partes, plan de pago, iniciar
cobranza), `partial_journey` (ubicar en qué etapa está una colocación, qué se omitió, cuándo saltar o activar una etapa),
`file_check` (revisar antecedentes y documentos faltantes de una cuenta o póliza), `portfolio_analysis` (análisis de cartera:
primas, compañías, ramos, vencimientos — solo lectura), `portal_quotes` (buscar cotizaciones en portales; explicar fallas y
alternativas). Validate at import that every skill's tools exist. `load_skill` is a READ tool with gate Dashboard.View that
returns `{key, title, instructions, tools}` and appends the key to `thread.active_skills`. When `active_skills` is non-empty the
system prompt includes their instructions (budgeted, truncated with the existing marker).

Tool budget: raise `MAX_TOOL_ITERATIONS`/`MAX_TOOL_CALLS_PER_TURN` modestly only if tests show multi-step skills need it (state
the change). Keep `openai_tools()` compact (`_compact_schema`).

## 4. Tests (hermetic)
Registry sanity (every tool's module/action valid; every write has summarize/validate), each new read tool returns
broker-scoped data and 404/permission_denied folds for another tenant, each new write tool proposes (no DB write) and executes
only on confirm, skills endpoint, `load_skill` loop with the fake model.
