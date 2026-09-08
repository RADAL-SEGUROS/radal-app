# Radal v4 — Agente: the single agentic AI system

> **Status:** authoritative build spec for standing decision **5** (one AI agent). Extends the
> existing chat (`agent_thread` / `agent_message`, `app/services/ai.py`) into a tool-using
> agent with a **typed, server-side tool registry** — MCP-style architecture, built natively,
> no external MCP servers.
>
> **The one rule that shapes everything here — CLAUDE.md rule 6, non-negotiable:**
> AI is *suggest → human confirm → commit*. READ tools execute freely inside the model loop;
> WRITE tools **never execute**. A write becomes a persisted `agent_action` row in status
> `proposed`, the chat renders a **Confirmar / Descartar** card, and only the confirm endpoint
> — under the caller's **real RBAC gate for the target module**, with the activity trail —
> executes it.
>
> Other laws inherited unchanged: English identifiers everywhere (Spanish only in locale
> values and LLM prompts); every workspace query filters `broker_id` (a foreign row is a 404,
> never a 403); an LLM hiccup is **never a 500** (the `AIError` family, §5.4); no dead buttons;
> permissions come from the server. `frontend/src/lib/api.ts` and the SSE header mechanics in
> `frontend/src/api/ai.ts` are **frozen** — this pass adds hooks, it does not touch either
> mechanism. Do **not** change any Lambda invoke mode (OAC constraint 4).

**Change policy: ADDITIVE ONLY.** No existing route is renamed, no column dropped, no enum
member removed. `POST /ai/threads/{id}/messages` and `POST /ai/threads/{id}/stream` keep
working byte-identically (the plain-chat paths and their tests do not move). The registry-driven
document flow (`/ai/documents/extract` + `SuggestionForm`) stays exactly as is — it simply loses
its nav entry, not its reachability (decision 5: it remains reachable from documents).

---

## 0. The idea in one breath

```
user types:  "/comparar @EXP-2026-0004 contra la vigencia anterior"
             └ slash command + @-mention → context_refs=[{case_file, 5}]

POST /ai/agent/messages  {thread_id?, content, context_refs[]}
  ├─ resolve refs broker-scoped → compact Spanish context blocks (§6)
  ├─ model loop (§5): ChatOpenAI + OpenAI-compatible `tools` param, ≤ 6 iterations
  │    READ tool call  → execute inline under the caller's own grants → fold result back
  │    WRITE tool call → persist agent_action(status=proposed), fold back
  │                      {"status":"pending_confirmation","action_id":N} — NOTHING written
  └─ persist the whole turn atomically: user msg + assistant/tool msgs + final answer

chat renders the answer + one card per pending action
  Confirmar → POST /ai/agent/actions/{id}/confirm   (re-checks the target module's gate,
              executes the same transaction the normal router would, writes activity)
  Descartar → POST /ai/agent/actions/{id}/discard
```

---

## 1. What is added, what is replaced

| Surface | Fate |
|---|---|
| `POST /ai/agent/messages`, `GET /ai/agent/actions`, `POST /ai/agent/actions/{id}/confirm`, `POST /ai/agent/actions/{id}/discard` | **NEW** (§7) |
| `backend/app/services/agent_tools.py` (typed registry) + `backend/app/services/agent.py` (the loop) | **NEW** (§4, §5) |
| `agent_action` table; `agent_message.context_refs` column | **NEW** (§3) — the column needs the migration script (rule 10) |
| `frontend/src/pages/agent/index.tsx` | **REWRITTEN** (§8) — chat with @-mentions, slash commands, context chips, pending-action cards |
| Sidebar entries "Lector documental" + "Comparador" | replaced by ONE "Agente" entry — owned by the sidebar work package (decision 2), not by this one; this spec only guarantees `/agent` is the page it points to |
| `/ai/threads/*` chat + stream endpoints, `SuggestionForm`, `/ai/documents/extract` | **UNCHANGED** |
| `frontend/src/api/ai.ts` | **ADDITIVE** hooks only; `streamAgentMessage` and its OAC header re-implementation untouched |

The new agent turn endpoint is **non-streaming by design**. A tool loop cannot stream tokens
usefully (the answer is produced after the last iteration), CloudFront buffers the body anyway
(OAC constraint 4), and the existing page already treats production as buffered. The page shows
staged progress states instead (§8.6). A per-iteration SSE variant is explicitly out of scope —
do not add one by changing invoke modes.

---

## 2. Files & ownership (summary — full split in §10)

```
backend/app/services/agent_tools.py     NEW  the typed tool registry (B1)
backend/app/services/agent.py           NEW  run_agent_turn(), confirm/discard executors (B1)
backend/app/schemas/agent.py            NEW  request/response + per-tool args models (B1)
backend/app/models/ai.py                +AgentAction, +AgentActionStatus, +context_refs (B1)
backend/app/api/routers/ai.py           +4 routes under /ai/agent (B1)
backend/tests/test_agent_tools.py       NEW  (B1)
backend/tests/test_agent_actions.py     NEW  (B1)
frontend/src/pages/agent/**             REWRITE (F5)
frontend/src/api/ai.ts                  additive hooks (F5)
frontend/src/api/{keys.ts,types.ts}     additive (F5)
frontend/src/locales/{es,en}/agent.json NEW namespace, es complete + en mirror (F5)
```

---

## 3. Data model

### 3.1 `agent_action` — a proposed write, awaiting a human. **A table, not JSON.**

Applying the shape rule from `docs/v2-data-modeling-decisions.md`: a value becomes a **column
when something sorts, filters or sums on it**, a **child table when rows have their own
lifecycle**, and **JSON only for a heterogeneous tail no query touches**. A pending action
fails the JSON-on-message test on every axis:

- **It is addressed by id.** `POST /ai/agent/actions/{id}/confirm` needs a stable PK and a row
  lock; an index into a JSON array on a message is neither.
- **It is filtered by status.** The page rehydrates `status=proposed` per thread on reload;
  a dashboard "acciones pendientes" count is a `WHERE`, not a JSON scan.
- **It has its own lifecycle columns** (`confirmed_by_id`, `confirmed_at`, `result`, `error`)
  written *after* the message that proposed it is immutable history. Mutating a persisted
  `agent_message` to record a later confirmation would corrupt the transcript-as-audit-trail.
- **It needs FK integrity** to `broker`, `agent_thread`, `agent_message`, `user`.

What stays JSON is exactly the heterogeneous tail: `arguments` (per-tool shape, never queried)
and `result` (per-tool shape, rendered only).

| column | type | notes |
|---|---|---|
| `id` | PK | |
| `broker_id` | FK broker CASCADE, NN, IX | tenant boundary, as everywhere |
| `thread_id` | FK agent_thread CASCADE, NN, IX | |
| `message_id` | FK agent_message SET NULL, IX | the assistant message that proposed it |
| `tool` | VARCHAR(64) NN, IX | registry key, e.g. `create_group` |
| `arguments` | JSON NN | the model's arguments, **validated through the tool's Pydantic args schema before the row is written** — garbage never persists |
| `summary` | TEXT | one Spanish sentence for the card ("Crear el grupo 'Viña Indómita' y adjuntar 2 RUT") — built server-side by the tool's `summarize()`, never by the model |
| `module` | VARCHAR(32) NN | the RBAC module the confirm re-checks (denormalised for display; the registry stays authoritative at confirm time) |
| `action` | VARCHAR(16) NN | the RBAC action, idem |
| `status` | VARCHAR(21) NN, IX, enum `AgentActionStatus`, def=`proposed` | `proposed · confirmed · discarded · failed` |
| `result` | JSON | executor output on success (`{entity_type, entity_id, url, detail}`) |
| `error` | TEXT | executor failure detail (the action lands `failed`; a retry is a NEW proposal) |
| `proposed_by_id` | FK user SET NULL, IX | the user whose turn produced it |
| `confirmed_by_id` | FK user SET NULL, IX | who clicked Confirmar/Descartar |
| `confirmed_at` | DATETIME | stamp for either resolution |

Plus `TimestampMixin`. Indexes: `ix_agent_action_thread_status (thread_id, status)`,
`ix_agent_action_broker_status (broker_id, status)`. Enum via `sql_enum(AgentActionStatus)`
(non-native VARCHAR, `validate_strings=True`; longest member `discarded` (9) → VARCHAR(21)).

New enum, in `app/models/ai.py`:

```python
class AgentActionStatus(StrEnum):
    PROPOSED = "proposed"
    CONFIRMED = "confirmed"
    DISCARDED = "discarded"
    FAILED = "failed"        # confirmed by a human, but the executor raised
```

There is deliberately **no `expired`**: a stale proposal is caught at confirm time by
re-validation against live data (§7.3) — a 409/422 with the reason beats a background sweeper.

### 3.2 Additive columns on `agent_message`

| column | type | notes |
|---|---|---|
| `context_refs` | JSON NULL | the refs attached to a **user** message: `[{"entity_type": "...", "entity_id": N}]`. JSON is correct here (heterogeneous tail, never filtered on); it is provenance for the transcript, resolved fresh every turn |

`tool_calls` (already exists, JSON) now actually gets used:

- on an **assistant** message: the provider's `tool_calls` array verbatim
  (`[{id, type:"function", function:{name, arguments}}]`) — the audit of what the model asked for;
- on a **tool** message: `{"tool_call_id": "...", "name": "...", "status": "ok|error|pending_confirmation"}`,
  with the (truncated) result JSON in `content`.

### 3.3 Migration

`agent_action` is a **new table** → `create_all` creates it on boot. `agent_message.context_refs`
is a **new column on an existing table** → per rule 10 it must be applied to the dev RDS with the
schema-migration script **before** the branch is pushed. The script compiles from live metadata,
so the only work is running it (`--dialect mysql` to read the plan; expect
`CREATE TABLE agent_action`, its indexes, and `ALTER TABLE agent_message ADD COLUMN context_refs JSON`).

---

## 4. The typed tool registry — `backend/app/services/agent_tools.py`

One dataclass, one dict, mirroring the extraction registry's shape so the pattern is already
familiar to the codebase:

```python
@dataclass(frozen=True)
class ToolSpec:
    name: str                          # registry key AND the OpenAI function name
    kind: Literal["read", "write"]
    module: str                        # RBAC module gate
    action: str                        # RBAC action gate
    description: str                   # Spanish, shown to the model (LLM prompt — Spanish allowed)
    args_schema: type[BaseModel]       # validates model arguments BEFORE anything runs/persists
    execute: Callable[..., dict]       # read: runs inline; write: runs ONLY at confirm time
    summarize: Callable[..., str]      # write only: the Spanish card sentence
    max_result_chars: int = 6_000      # fold-back truncation budget (§5.3)

TOOL_REGISTRY: dict[str, ToolSpec] = {...}

def openai_tools() -> list[dict]:
    """The registry as OpenAI-compatible `tools` dicts: name, description,
    parameters = args_schema.model_json_schema() run through the existing
    _compact_schema() pruning in services/ai.py (title/default noise stripped)."""
```

Every `execute` takes `(db, *, broker_id, user, args)` — the **caller's** identity, always.
There is no service account, no ambient privilege: a tool can never read or write anything the
human in the chair could not.

### 4.1 Gate behaviour — the loop must not die on a 403

Before executing a READ tool, the loop checks `user_has_permission(user, spec.module,
spec.action)` (honouring `partial` exactly as `require_permission` does — `PARTIAL_IS_ALLOWED`,
with the tool applying the same narrowing the router applies, §4.2). A missing grant does **not**
raise: the tool result folded back to the model is

```json
{"error": "permission_denied", "module": "Proposals", "action": "View",
 "detail": "El usuario no tiene permiso de Ver sobre Propuestas"}
```

so the model can answer gracefully ("no tengo acceso a las propuestas con tu perfil") instead of
the whole turn 403ing. The same shape carries `"error": "not_found"` for a broker-foreign or
missing id (the tool never confirms a foreign id exists — same 404-not-403 doctrine as the API)
and `"error": "invalid_arguments"` with the Pydantic error list when `args_schema` rejects.

### 4.2 READ tools (execute inline)

All results are compact JSON designed for a model, not a UI: ids always included (so follow-up
tool calls can chain), Spanish labels, money as strings with UF units, lists capped.

| name | args (`args_schema`) | gate | executes | result sketch |
|---|---|---|---|---|
| `search_entities` | `{query: str(2..120), kinds?: list["groups","clients","assets","quotes","proposals"]}` | `Dashboard.View` | the same per-kind, broker-scoped queries as `GET /search` (import and reuse the router's `_group_hits` etc. — **do not fork the queries**); 5 hits per kind | `{groups:[{id,label,sublabel}], clients:[...], ...}` |
| `get_group_tree` | `{group_id: int}` | `Groups.View` **and** `CaseFiles.View` (both, matching the two routes it merges) | `account_groups._get_group` + `services.navigator.build_group_tree` — the tree already applies the caller's visibility (inspector partial, post-sale child filtering by `Endorsements/Collections/Claims View`) | group header (name, clients+RUTs) + vigencias → ramos → node ids/stages/counts |
| `get_case_file` | `{case_file_id: int}` | `CaseFiles.View` (partial → the same inspection-attached narrowing `case_files._get_case` applies — reuse that helper) | case detail: reference, title, kind, stage, status, period, client(s), per-section document counts, children, **allowed transitions with server reasons** (from the same guard evaluation as `GET /transitions`) | `{id, reference, stage, transitions:[{to_stage,allowed,reason}], ...}` |
| `get_case_history` | `{case_file_id: int}` | `CaseFiles.View` | the `GET /case-files/{id}/history` payload: origin chain + prior folder's read-only context (what was insured, policies, claims cost) | `{chain:[...], prior:{...}}` |
| `get_quote_comparison` | `{quote_request_id: int, include_rejected?: bool}` | `Proposals.View` (the comparator's real gate) | the comparator builder behind `GET /quotes/{id}/comparison` | columns (per-proposal money block), aligned deductibles/coverages, highlights |
| `list_documents` | `{case_file_id?: int, entity_type?: str, entity_id?: int, category?: str, section?: str}` — at least one anchor required (422 otherwise) | `Documents.View` | the broker-scoped document listing (`GET /documents` filters) | `[{id, category, section, document_code, original_name, uploaded_at}]` — **never S3 keys or URLs**; the model reasons over metadata only |

### 4.3 WRITE tools (never execute in the loop)

In the loop, a WRITE tool call runs **only** `args_schema` validation + broker-scoped
existence checks on referenced ids (so a doomed proposal is refused immediately as
`invalid_arguments` / `not_found`), then persists the `agent_action` row and folds back
`{"status": "pending_confirmation", "action_id": N, "summary": "..."}`. The `execute`
callable runs exclusively inside the confirm endpoint (§7.3), under the confirmer's grants.

Each executor **reuses the exact transaction the normal router performs** — same helpers, same
invariants, same activity rows. Where the logic currently lives inline in a router, B1 extracts
it into a small shared function that the router and the executor both call (listed per tool);
behaviour of the existing route must stay byte-identical (its tests do not change).

| name | args | gate re-checked at confirm | executor = the same transaction as | notes |
|---|---|---|---|---|
| `create_group` | `{name: str(1..255), notes?: str, client_ids?: list[int]}` | `Groups.Create` (+ `Groups.Edit` when `client_ids` given, matching attach) | `POST /account-groups` (unique slug via `_unique_slug`), then per client the attach path below | duplicate name → the slug uniquifies, never a silent merge; foreign `client_ids` → 404 at proposal time |
| `attach_client_to_group` | `{group_id: int, client_id: int}` | `Groups.Edit` | `POST /account-groups/{id}/clients` incl. `_adopt_client_case_files` | |
| `create_case_file` | `{client_id: int, kind?: "account", title: str, insurance_line_id?: int, group_id?: int, period_start?: date, period_end?: date}` | `CaseFiles.Create` | `POST /case-files` — the server owns `reference`, `sequence_no`, `version`, stage seeding | post-sale kinds are **not** proposable by the agent this pass (422 `unsupported_kind`): they hang off policies with guards the chat cannot review |
| `renew_case` | `{case_file_id: int, period_start?: date, period_end?: date, note?: str}` | `CaseFiles.Create` (the renew route's real gate) | `POST /case-files/{id}/renew` (`CaseRenewBody` — mirror its exact fields) | the route's own 422s (already renewed, not renewable from this stage) surface verbatim |
| `add_note` | `{entity_type: str, entity_id: int, body: str(1..4000), is_internal?: bool = true, follow_up_on?: date}` | **dynamic**: `MODULE_BY_ENTITY[entity_type]` + `Comment` — resolved through the same map `notes.py` exports | `POST /notes` | unmapped entity_type → 422 at proposal time |

`module`/`action` stored on the row are what the card displays; **the registry is re-consulted
at confirm time** so a later matrix change cannot be bypassed by an old row.

Adding a tool = one `ToolSpec` + one args schema + tests. No router change, no frontend change
(the card renders from `summary` + the generic argument table).

---

## 5. The model loop — `backend/app/services/agent.py`

### 5.1 Provider call

The loop uses the **existing** LangChain surface: `ai._chat_model(temperature=0.2,
max_tokens=1600)` (the DeepInfra `ChatOpenAI`) with the OpenAI-compatible `tools` param bound
via `llm.bind_tools(agent_tools.openai_tools())` — raw OpenAI dicts, **not** LangChain tool
objects, so the schemas stay the registry's own. `tool_choice` is left to `auto`.
`ensure_ai_configured()` runs first (unset key → clean 503, as everywhere).

DeepInfra caveat the implementer must handle exactly like `_run_completion` handles
`response_format`: if the provider rejects the `tools` param (400/422 `APIStatusError`), retry
the same messages **once without tools** and answer plainly — a degraded answer beats a dead
endpoint. Log a warning; do not loop.

### 5.2 The loop

```python
MAX_TOOL_ITERATIONS = 6      # counted as provider round-trips that returned tool_calls
MAX_TOOL_CALLS_PER_TURN = 12 # absolute cap across iterations

def run_agent_turn(db, *, thread, broker_id, user, content, context_refs) -> AgentTurnResult:
    messages = _agent_prompt(db, thread, broker_id, user, content, context_refs)  # §5.5
    transcript: list[PendingMessage] = [user_message(content, context_refs)]
    pending: list[AgentAction] = []          # built in memory, flushed with the turn

    for iteration in range(MAX_TOOL_ITERATIONS + 1):
        answer = _invoke(llm, messages)      # every exception → _wrap_provider_error → AIError
        calls = getattr(answer, "tool_calls", None) or []
        if not calls or iteration == MAX_TOOL_ITERATIONS:
            break                            # final prose answer (or budget exhausted → §5.3)
        transcript.append(assistant_tool_message(answer))       # tool_calls persisted verbatim
        for call in calls:
            result = _dispatch(db, call, broker_id=broker_id, user=user, pending=pending)
            transcript.append(tool_message(call, result))       # truncated to max_result_chars
            messages.append(...)             # OpenAI tool-role message, same truncated JSON
    transcript.append(assistant_final(answer))
    _persist_turn(db, thread, transcript, pending)              # ONE commit — §5.6
    return AgentTurnResult(...)
```

`_dispatch` is where §4.1/§4.3 live: unknown tool name → `{"error": "unknown_tool"}` folded
back (the model hallucinated; never an exception); READ → gate check + execute inside
`try/except Exception` where any executor crash folds back as
`{"error": "tool_failed", "detail": type(exc).__name__}` (and is logged) — **a tool bug is not
a 500 either**; WRITE → validate, build the `AgentAction`, fold back the pending stub.

READ tools are executed against the same request-scoped `Session` but must treat it read-only;
`_dispatch` calls `db.rollback()` after a failed executor so a poisoned transaction cannot leak
into the next call.

### 5.3 Budget exhaustion

When `MAX_TOOL_ITERATIONS` is hit and the model still wants tools, the loop makes one final
call with an appended system nudge (“Responde ahora con lo que tienes; no hay más herramientas
en este turno.”) and **no** tools bound. The turn always ends in prose.

### 5.4 Errors — the AIError family, unchanged

Every provider/parse failure funnels through the existing `_wrap_provider_error` →
`AINotConfigured` 503 · `AITimeout` 504 · `AIProviderError` 502 · `AIParseError` 422, with the
stable code on `X-Radal-AI-Error` via the router's existing `_ai_http_error`. **A mid-loop
provider failure persists nothing** (same contract as `send_message`: a stored user message with
no reply would poison the replayed history). READ tools are side-effect-free and WRITE tools
proposed in the failed turn were never flushed, so a retry replays cleanly.

Two agent-specific failures are *not* AI errors and are answered before the model is called:
404 unknown/foreign thread (`_load_own_thread` reused — a thread is private to its user) and
422 on unresolvable `context_refs` (§6.3).

### 5.5 The prompt

System prompt (Spanish; new constant `_AGENT_SYSTEM` in `agent.py`): who the assistant is
(asistente operativo de una corredora chilena), the tool doctrine (read freely; writes only
propose and the human confirms — say so in the answer), money discipline (UF, never invent
figures; cite tool results), and the standing instruction to answer in Spanish, briefly,
with entity references by their visible labels (`EXP-2026-0004`), never raw ids.

Then, in order: the resolved `context_refs` blocks (§6), the thread history
(last `MAX_CHAT_HISTORY_MESSAGES = 24` user/assistant messages — tool messages from prior turns
are **not** replayed; the prose answers already summarise them), and the new user content.
Total context is clamped to ~24 000 chars; refs are trimmed first, oldest history second.

### 5.6 Persistence

`_persist_turn` writes, in one commit: the user `agent_message` (with `context_refs`), one
assistant message per iteration that carried `tool_calls` (content NULL, `tool_calls` verbatim),
one `role=tool` message per call (truncated result JSON in `content`, linkage in `tool_calls`),
the final assistant message (content, `model`, `tokens` summed across iterations), the
`agent_action` rows (each `message_id` = the assistant message that proposed it), and
`thread.last_message_at` / first-turn `title` exactly as `_persist_exchange` does today.

---

## 6. `context_refs` resolution

### 6.1 Supported entity types

`account_group · client · case_file · policy · quote_request · proposal · document ·
sales_lead`. Anything else → 422 `{"code": "unsupported_ref", "entity_type": ...}` before the
model runs. Every lookup filters `broker_id` (via the entity's own scoping helper — e.g.
`case_files._get_case` so the inspector's partial view holds); a miss or a foreign row → 404
`"{entity_type} {id} not found"` — the agent endpoint never confirms a foreign id exists.

### 6.2 What each ref contributes (compact, Spanish, id-bearing)

| entity_type | context block (≤ its budget) |
|---|---|
| `account_group` | name, clients + RUTs, vigencia labels, per-ramo case ids/stages (a pruned `build_group_tree`) |
| `case_file` | reference, title, kind/stage/status + allowed transitions with reasons, period, client, doc counts per section, children ids. **When the case is an account/renewal folder, the immediate prior folder's summary from `/history` is appended automatically** — this is what makes "compara contra la vigencia anterior" work without the user hunting for ids (decision 5's budget-comparison example) |
| `policy` | policy_number, insurer, period, money block, endorsement/collection/claim children ids+stages |
| `quote_request` | round, declared value, line items, recipients, per-proposal one-liners (insurer, total UF, status, outcome) |
| `proposal` | insurer, full money block, rates, validity, deductible perils, coverage/exclusion counts, confirmation state |
| `client` | legal/trade name, RUT, status, open case ids, policy count |
| `document` | metadata only (category label, code, section, name, case) + **its confirmed extraction's `parsed` payload when one exists** — never raw bytes, never an inline LLM read (that is `SuggestionForm`'s job) |
| `sales_lead` | name, status, line, estimated premium, follow-up |

### 6.3 Budgets

`MAX_CONTEXT_REFS = 6` (422 above). Per-ref budget: `account_group`/`case_file` 2 000 chars,
everything else 1 200 — trimmed with an explicit `"… (contexto truncado)"` marker, never
silently. Refs are resolved fresh on **every** turn they ride on (the stored `context_refs` on
old messages are provenance, not cache).

---

## 7. API — four routes, mounted in `app/api/routers/ai.py`

All under the house stack (`get_db`, `get_current_broker_id`); coarse gate
`require_permission("Dashboard", "View")` like the existing chat — the *real* authority per
tool is enforced inside the loop (reads) and at confirm (writes).

### 7.1 `POST /ai/agent/messages` → 201 `AgentTurnResponse`

```jsonc
// request
{"thread_id": 12,                 // optional — absent: a new general thread is created
 "content": "…", ,                // 1..8000, same bound as MessageCreate
 "context_refs": [{"entity_type": "case_file", "entity_id": 5}]}   // ≤ 6
// response
{"thread": ThreadRead,
 "messages": [MessageRead...],    // every message this turn persisted, in order
 "pending_actions": [AgentActionRead...]}
```

`MessageRead` gains `tool_calls` and `context_refs` (additive, both optional).
`AgentActionRead`: `{id, thread_id, message_id, tool, arguments, summary, module, action,
status, result, error, confirmed_at, created_at, allowed}` — **`allowed` is computed per
response for the caller** (`user_has_permission(caller, module, action)`) so the card can render
Confirmar disabled-with-reason without a second request (no-dead-buttons; the server still
re-checks on confirm).

Errors: 404 foreign/unknown thread or ref · 403 foreign-owner thread · 422 unsupported ref /
too many refs · 502/503/504 per §5.4 (nothing persisted).

### 7.2 `GET /ai/agent/actions?thread_id=&status=` → `{items, total}`

Rehydration for the page. Scoped to the caller's broker **and** to threads the caller owns
(the thread privacy rule extends to its actions). `status` optional multi-value.

### 7.3 `POST /ai/agent/actions/{action_id}/confirm` → 200 `AgentActionRead`

The COMMIT of rule 6. In order:

1. Load the action broker-scoped; owner-of-thread check (404 / 403 as above).
2. **409** `{"code": "action_not_pending", "status": ...}` unless `status == proposed` —
   double-click, stale tab and replay all land here. Load with `with_for_update()` where the
   engine supports it so two racing confirms serialise (the loser sees the 409).
3. **Re-check the real gate now, against the registry**: `TOOL_REGISTRY[action.tool]` →
   `require`-style check of `(module, action)` for the **confirming** user. Missing → **403**
   `"Not permitted to {action} on {module}"` and the row stays `proposed` (someone with the
   grant may still confirm it).
4. Re-validate `arguments` through `args_schema` and re-resolve every referenced id
   broker-scoped (the world may have moved since the proposal): a vanished target → **404**;
   a domain-rule violation raised by the executor (case already renewed, kind not allowed,
   note target unmapped…) → the underlying route's own **422/409 detail, verbatim** — the
   agent adds no second vocabulary of errors.
5. Execute `spec.execute(db, broker_id=..., user=confirmer, args=...)` — the same transaction
   the normal router runs, including its own `record_activity` writes — then, in the same
   commit: `status=confirmed`, `result`, `confirmed_by/at`, an extra
   `record_activity(action="agent.action_confirmed", entity_type/entity_id = the created/
   touched entity, meta={action_id, tool, thread_id})`, and a `role=tool` agent_message
   appended to the thread (`{"status": "confirmed", "action_id": N, ...result}`) so the model
   sees the outcome next turn and the transcript stays honest.
6. An executor crash after the checks: rollback, then a **separate** small transaction writes
   `status=failed` + `error` (mirroring how pack failures persist), and the HTTP answer is the
   mapped error. A `failed` action is terminal; the fix is a fresh proposal.

### 7.4 `POST /ai/agent/actions/{action_id}/discard` → 200 `AgentActionRead`

Same loading/ownership/409 rules; no RBAC re-check beyond thread ownership (declining requires
no privilege). Writes `status=discarded`, `confirmed_by/at`, an
`agent.action_discarded` activity, and the `role=tool` transcript note.

---

## 8. Frontend — `pages/agent` rewrite (F5)

Single page, Porcelana-native (decision 1: ink primary button, pine accents, DM Mono for ids,
card radius 16/well 12). One column of conversation, the composer fixed at the bottom, thread
history behind a compact selector (a `+ Nueva conversación` ghost button and a recent-threads
dropdown — not a second rail; decision 2 killed rails, decision 6 says fewer choices).

### 8.1 Composer

Textarea with three affordances:

- **`@` mentions** (§8.2) → context chips;
- **`/` commands** at position 0 (§8.3);
- **Enviar** — the view's ONE ink button. Enter sends, Shift+Enter newlines.

**Context chips** render above the input: entity-kind icon + label (`EXP-2026-0004 · Coccolino`),
`×` to remove. Chips persist across sends within the conversation until removed (the working
context stays visible — decision 6); each send serialises the current chips as `context_refs`.

### 8.2 @-mention popover

Opens on `@`; the text after `@` filters. Two data sources, merged into grouped sections:

1. **Zero-query state** (instant, from cache): `useNavigator()` — the groups list and the 5
   recent cases; label + sublabel exactly as the navigator provides them.
2. **Typed query ≥ 2 chars**: `GET /search?q=` (debounced 200 ms, TanStack Query
   `keepPreviousData`), sections Grupos / Clientes / Cotizaciones / Propuestas from
   `SearchResults` (assets are omitted — not a supported ref type).

Kind → `entity_type` mapping: navigator group → `account_group`, navigator case → `case_file`,
search groups → `account_group`, clients → `client`, quotes → `quote_request`, proposals →
`proposal`.

Keyboard: `↑/↓` move (wrapping), `Enter`/`Tab` select, `Esc` closes and leaves the literal text,
typing keeps filtering; the popover is `role="listbox"` with `aria-activedescendant`. Selecting
replaces the `@query` text with nothing and adds a chip + ref (duplicates are ignored).

### 8.3 Slash commands

`/` at position 0 opens the palette (same keyboard behaviour). Each command is **only** a
prefill: it writes template text into the composer and (where marked) demands a ref — the model
does the tool work. No command calls an endpoint directly.

| command | needs a chip? | prefills |
|---|---|---|
| `/comparar` | yes — `case_file`, `quote_request` or `account_group` (opens the @ popover if none) | `Compara las propuestas de {chip} y recomiéndame una.` |
| `/crear-grupo` | no | `Crea un grupo llamado "" con ` (caret inside the quotes) |
| `/renovar` | yes — `case_file` | `Prepara la renovación de {chip}: revisa la vigencia anterior y propone la nueva carpeta.` |
| `/nota` | yes — any chip | `Agrega una nota a {chip}: ` |

The palette footer teaches ("las escrituras siempre te pedirán confirmación") — empty states
that teach, decision 6.

### 8.4 Message rendering

User bubbles show their chips. Assistant turns render prose; iterations that used tools render
a single collapsed line per tool ("🔍 Buscó `get_quote_comparison`") expandable to the stored
result JSON — progressive disclosure, not a wall of JSON. `role=tool` confirmation notes render
as system-style hairline rows ("Acción confirmada · Grupo creado").

### 8.5 The pending-action card

Rendered inline where the assistant proposed it (from `pending_actions` on the turn, rehydrated
via `GET /ai/agent/actions?thread_id&status=proposed` on load):

- header: Spanish tool label — `t(\`agent:tools.${action.tool}\`)` (**dynamic i18n key: add every
  tool name to BOTH `es` and `en` `agent.json`; `tsc` will not catch a miss**) + the `summary`;
- body: a two-column argument table (label per known key, monospace values);
- footer: **Confirmar** — ink button, rendered from `action.allowed`; when `false` it renders
  disabled with the reason ("Requiere permiso de {action} sobre {module}") — never hidden;
  **Descartar** — ghost. Both disabled while mutating.
- resolved states: confirmed → success row with `result.detail` and a link built from
  `result.url`; discarded → muted "Descartada"; failed → danger row with `error`.

On confirm success, invalidate per tool: `create_group`/`attach_client_to_group` →
`qk.accountGroups.*` + navigator; `create_case_file`/`renew_case` → `qk.caseFiles.*` +
navigator; `add_note` → `qk.notes.*` of the target.

### 8.6 Sending & progress

The page calls the **buffered** `POST /ai/agent/messages` (`useAgentTurn`). While in flight it
shows the user bubble optimistically plus a staged indicator (pulsing "Pensando…" → after 4 s
"Consultando datos…") — honest about latency without pretending to stream. The existing
`streamAgentMessage` SSE path and its 5 s fallback remain in the file, untouched, unused by
this page (plain streaming chat may return later; the OAC header re-implementation is frozen).

### 8.7 `api/ai.ts` additions (additive only)

```ts
useAgentTurn()                    // POST /ai/agent/messages
useAgentActions(threadId, status?) // GET /ai/agent/actions
useConfirmAgentAction()           // POST /ai/agent/actions/{id}/confirm
useDiscardAgentAction()           // POST /ai/agent/actions/{id}/discard
```

plus `AgentTurnRequest/Response`, `AgentAction`, `ContextRef` in `types.ts` and
`qk.agentActions` in `keys.ts`. Nothing above `// --- Streaming chat` moves.

### 8.8 i18n

New namespace `agent` registered by the existing `import.meta.glob` trick in `i18n/index.ts`:
composer placeholders, command palette entries + hints, tool labels (**one per registry tool**),
card verbs, progress states, empty state. `es` complete, `en` an exact key mirror.

---

## 9. Tests

### 9.1 Backend (hermetic — the two blanking lines in `conftest.py` stay sacred)

`tests/test_agent_tools.py` — the loop, with a `_FakeToolChatModel` following
`test_ai_streaming.py`'s existing double pattern (`invoke` returns scripted messages; a
`bind_tools(tools)` method records the schemas and returns self), monkeypatched over
`agent._chat_model`:

1. **A read-tool turn**: fake scripts `[tool_call(search_entities), final answer]` → 201; the
   persisted transcript is user + assistant(tool_calls) + tool + assistant(final); the folded
   tool message content matches what the registry executor returned; tokens summed.
2. **Registry sanity**: every `ToolSpec.module`/`action` exists in `MODULES`/`ACTIONS`
   (import-time typo guard, like `require_permission`'s); every write spec has `summarize`;
   `openai_tools()` schemas are valid JSON schema objects with pruned noise.
3. **Permission-denied read folds, not 403s**: an inspector (no `Proposals.View`) asking for a
   comparison gets a 201 turn whose tool message carries `permission_denied`.
4. **Cross-tenant read is `not_found` in-loop**: broker A's user, broker B's case id — folded
   `not_found`; nothing of B's leaks into any persisted message.
5. **A write proposes, never executes**: scripted `create_group` call → `agent_action` row
   `proposed`, **zero `account_group` rows created**, folded `pending_confirmation`.
6. **Provider failure mid-loop persists nothing**: fake raises `APIConnectionError`-shaped on
   iteration 2 → 502 with `X-Radal-AI-Error: ai_provider`; `agent_message` and `agent_action`
   counts unchanged.
7. **Unset key is a clean 503** before any model call (`no_ai_key` fixture pattern).
8. **Iteration cap**: a fake that always wants tools ends in prose at `MAX_TOOL_ITERATIONS`.
9. **Bad ref**: unsupported `entity_type` → 422; foreign `case_file` ref → 404.

`tests/test_agent_actions.py` — the lifecycle (no model needed: seed actions via the service or
a scripted turn):

10. **Confirm executes under RBAC and writes the trail**: executive confirms `create_group` →
    group exists, action `confirmed` with `result.entity_id`, `agent.action_confirmed` activity
    row, a `role=tool` transcript message appended.
11. **RBAC refusal at confirm**: an inspector confirming a `create_case_file` action → 403,
    row **stays `proposed`**, no case file created.
12. **Double confirm / confirm-after-discard** → 409 `action_not_pending`.
13. **Cross-tenant action id** → 404; another user's thread's action → 403.
14. **Stale target**: confirm `attach_client_to_group` after the group was deleted → 404,
    row stays `proposed`; a domain 422 from `renew_case` (already renewed) surfaces verbatim
    and leaves the row `proposed` per step 4 ordering (validation before execution).
15. **Executor crash lands `failed`** with `error` persisted and no partial entity.
16. **add_note dynamic gate**: technician (`Leads.Comment`? no — has only View) proposing a
    note on a `sales_lead` is confirmable only by roles holding `Leads.Comment`.
17. **Discard** needs no write grant and resolves the row.

Suite health: the whole run stays hermetic (no DeepInfra socket, ~current runtime + these).

### 9.2 Frontend gate

`npx tsc --noEmit && npm run build` clean. (No component test harness exists in the repo; the
type gate plus the backend contract tests are the bar, and every dynamic i18n key added here is
click-verified per the CLAUDE.md rule-4 trap.)

---

## 10. Work-package split

**B1 — backend** (exclusive files):

```
backend/app/services/agent_tools.py          NEW
backend/app/services/agent.py                NEW
backend/app/schemas/agent.py                 NEW
backend/app/models/ai.py                     +AgentAction, +AgentActionStatus, +agent_message.context_refs
backend/app/db/base.py                       import AgentAction (aggregation list only)
backend/app/api/routers/ai.py                +4 routes (§7), additive
backend/app/api/routers/account_groups.py    ONLY IF needed: extract attach/create bodies into
                                             module-level helpers the executor shares — routes byte-identical
backend/app/api/routers/case_files.py        idem for create/renew bodies
backend/tests/test_agent_tools.py            NEW
backend/tests/test_agent_actions.py          NEW
```

B1 must NOT touch: `tests/conftest.py` AI blanking, extraction registry/schemas, the stream
endpoint, `models/base_class.py`. Done means: full suite green (existing count + new tests, no
skips), `verify_schema.py` clean on both dialects, the migration script prints the
`agent_action` + `context_refs` plan, and `python -c "from app.main import app"` imports clean.

**F5 — frontend** (exclusive files):

```
frontend/src/pages/agent/**                  REWRITE (index.tsx + local components:
                                             MentionPopover, CommandPalette, ContextChips,
                                             PendingActionCard, ToolTraceRow)
frontend/src/api/ai.ts                       additive hooks (§8.7); frozen zones untouched
frontend/src/api/keys.ts  types.ts           additive
frontend/src/locales/es/agent.json  en/agent.json   NEW, mirrored
```

F5 must NOT touch: `lib/api.ts`, `App.tsx` routes (the `/agent` route already exists), the
sidebar (decision-2 package owns the single "Agente" entry), any other locale namespace.
Done means: `tsc` + `build` clean, both locale files key-identical, and a manual pass — mention
a group, run `/comparar`, receive a `create_group` proposal as an executive (Confirmar works,
group appears), retry as inspector (Confirmar renders disabled with the permission reason;
the server 403 backs it up if forced).

**Ordering**: B1 lands first (F5 consumes the contract). The RDS migration for
`agent_message.context_refs` runs **before** the branch is pushed to `dev` (rule 10).

---

## 11. Acceptance

1. Suite green, hermetic, plus §9.1's 17 behaviours; `tsc`/`build` clean.
2. **Rule 6 holds mechanically**: grep-level guarantee that no `ToolSpec(kind="write").execute`
   is reachable from `run_agent_turn` — the only call site is the confirm endpoint.
3. An LLM outage at any point in a turn → 502/503/504 with `X-Radal-AI-Error`, zero rows
   written; never a 500.
4. Cross-tenant: every ref, tool argument and action id from another broker answers exactly as
   the rest of the API does — 404, indistinguishable from absent.
5. A confirmed action is indistinguishable in the database from the same operation performed
   through the normal router (same rows, same invariants, same activity), plus its
   `agent.action_confirmed` provenance row.
6. The existing chat endpoints, stream fallback, and proposal/document extraction flows are
   byte-identical in behaviour; their tests did not change.
