# Radal v3 — Groups & Accounts: final design specification

> Synthesised 2026-09-04 from the 2026-08-24 team meeting, the team's `Estructura Sidebar.xlsx`,
> three candidate designs and three judge reviews. Paths are relative to
> `/Users/bgg/Documents/repos/radal/radal-app/`. Line numbers were verified against the `dev`
> working tree on 2026-09-04. `docs/v2-architecture.md` and `docs/v2-case-files-spec.md` still
> apply; this document only adds to them.

## 1. Executive summary

The broker's daily loop is *open group → see vigencias → see ramos → jump to antecedentes / pólizas /
renovación → act*. This pass adds a broker-private **Group** (`account_group`) above the existing
expediente and makes `case_file(kind=account|renewal)` **the Account** — one insurance line × one
validity period × N RUTs. No parallel `account` table. The UI becomes a Notion-style double
sidebar: a main rail (groups + the Excel column-A items) and a contextual rail inside a group
(vigencia → ramo → antecedentes / cotizaciones / propuestas / pólizas → endoso / cobranza / siniestros
→ renovación), with a descending timeline as the main pane.

**The one modelling decision:** *vigencia is a label over per-account full dates, not a shared
date range.* The corpus proves it: Coccolino has Vehículos 2026-08-01→2027-08-01 and Incendio
2026-04-01→2027-04-01 under one RUT (`backend/app/db/import_expedientes.py:372-426`). The tree
groups accounts by `period_label` and shows full dates on every ramo/policy node.

Three axes never mix on `case_file`: **origin** (`origin` + `origin_case_file_id`: new / renewal /
period_change — sibling folders in time), **version** (`supersedes_case_file_id`: rework), and
**post-sale** (`parent_case_file_id` + `policy_id`: children of a policy). A renewal folder is a
sibling of the prior vigencia, never a child of it.

### Binary rules (from the meeting; each has one enforcement point)

1. **A vigencia date change creates a new folder.** Period fields are editable only while the account
   has no stage event beyond `intake`; afterwards any period edit returns 422 `period_locked` and the
   only door is `POST /case-files/{id}/reperiod`, which opens a sibling folder (`origin=period_change`)
   and closes the source. No in-place edit, no "extended account".
2. **Every endorsement is tied to exactly one policy.** `endorsement.policy_id` NOT NULL RESTRICT
   (`backend/app/models/endorsement.py:66-69`) and `case_file(kind=endorsement).policy_id` stay
   mandatory. ENDOSO nodes only ever render under a policy node.
3. **A prórroga is a manual multi-select.** `POST /endorsements/batch` takes explicit `policy_ids[]`,
   all in one group; the server fans out one endorsement + one endorsement case per policy sharing a
   `batch_key`. It moves `policy.end_date` only — never the account period (isolated in
   `apply_period_extension` so the team can flip it).
4. **Renewal is managed at ramo-vigencia level.** `POST /case-files/{id}/renew` is invoked on the
   account folder, never on a policy; it opens a new commercial folder (`kind=renewal`,
   `origin=renewal`) in the next vigencia under the same group with cloned placements, and reads prior
   history through `GET /case-files/{id}/history` without being constrained by it.
5. **No hybrid states.** One open folder per `(broker, group, line, period_start, period_end)` (422
   carrying the existing id); `origin` is exactly one value; historic periods are read-only in the UI;
   full dates (with months) are always shown at ramo and policy level.
6. **An account has N RUTs.** Membership = `account_client` rows ∪ `placement.case_file_id` rows;
   `case_file.client_id` is the contratante. Members must belong to the folder's group (422).
7. **A group carries no money and no stage.** Deleting/archiving a group detaches (SET NULL), never
   cascades.
8. **Access is decided by the server matrix.** Process profiles (comercial / cobranza / siniestros)
   narrow visibility by case kind in one mechanism used by both `_visible()` and `get_case_or_404()`.

## 2. Data model

All identifiers English (rule 1); every workspace table carries `broker_id` (rule 2); every added
column is nullable or has a scalar default so `migrate_case_files.py` can `ADD COLUMN` on MySQL and
SQLite. **No `use_alter` FK on any new table** — SQLAlchemy's `CreateTable` omits them and the
migrator's create-table path never emits `AddConstraint` (`backend/scripts/migrate_case_files.py:194-196`).

### 2.1 New table `account_group` — `backend/app/models/account_group.py`

Never name it `group` (MySQL reserved; `test_case_file_tables_are_not_mysql_reserved_words`).

| column | type | nullability | notes |
|---|---|---|---|
| `id` | Integer PK | | |
| `broker_id` | FK `broker.id` CASCADE, index | NOT NULL | |
| `name` | String(255) | NOT NULL | broker-private label ("JO PASTELERÍA" ≠ legal "Pacto Food SpA") |
| `slug` | String(80) | NOT NULL | slugified name; importer/backfill key |
| `status` | `sql_enum(AccountGroupStatus)` = `active\|archived` | NOT NULL default `active` | |
| `notes` | Text | NULL | |
| `created_at`, `updated_at` | TimestampMixin | | |

Indexes (as `Index`, so `_index_sqls` emits them): `ix_account_group_broker_slug (broker_id, slug) UNIQUE`,
`ix_account_group_broker_status (broker_id, status)`. **No `primary_client_id`** (would need
`use_alter`); the primary RUT is derived: `client_id` of the account with the latest `period_start`.

### 2.2 New table `account_client` — `backend/app/models/account_client.py`

RUT membership of an account without inventing an asset (`placement.asset_id` is NOT NULL,
`backend/app/models/placement.py:59-61`).

| column | type | nullability |
|---|---|---|
| `id` | PK | |
| `broker_id` | FK `broker.id` CASCADE, index | NOT NULL |
| `case_file_id` | FK `case_file.id` CASCADE, index | NOT NULL — the account folder |
| `client_id` | FK `client.id` RESTRICT, index | NOT NULL |
| `role` | `sql_enum(AccountClientRole)` = `policyholder\|insured` | NOT NULL default `insured` |
| `is_primary` | Boolean | NOT NULL default false |

`Index("ix_account_client_case_client", case_file_id, client_id, unique=True)`. No FK cycle
(`case_file` does not point back). Service rule: `client.account_group_id == case_file.account_group_id`
or 422 `client_not_in_group`.

### 2.3 Added columns

| table | column | type | notes |
|---|---|---|---|
| `client` | `account_group_id` | FK `account_group.id` SET NULL, index, NULL | a broker×RUT belongs to ≤1 group |
| `case_file` | `account_group_id` | FK SET NULL, index, NULL | denormalised so tree counts never join `client` |
| `case_file` | `period_start`, `period_end` | Date, NULL | **authoritative** period of the folder; sort key of the timeline |
| `case_file` | `period_label` | String(32), NULL | `f"{start.year}-{end.year}"`, grouping label only |
| `case_file` | `origin` | `sql_enum(CaseOrigin)` = `new\|renewal\|period_change`, NOT NULL default `new` | migrator injects the scalar default (`_column_spec(..., inject_default=True)`, `migrate_case_files.py:162-176`) |
| `case_file` | `origin_case_file_id` | self-FK SET NULL, index, NULL | the folder this one was renewed/re-perioded from |
| `case_file` | index | `ix_case_file_group_period (broker_id, account_group_id, period_start)` | tree query |
| `endorsement` | `batch_key` | String(36), index, NULL | UUID shared by the N endorsements of one prórroga |
| `sales_lead` | `account_group_id` | FK SET NULL, index, NULL | "crear grupo → crear cuenta" starts at the lead |
| `sales_lead` | `period_start`, `period_end` | Date, NULL | forwarded by `LeadConvert` |

`placement.period_start/period_end/period` remain as the **per-RUT operating copy**, written only by
create / convert / renew / reperiod and locked together with the folder (rule 1). Post-sale children
(`kind ∈ endorsement|collection|claim`) inherit `account_group_id`, `period_*` and `period_label`
from their policy's account at creation, so the tree never joins through `policy` for counts.

### 2.4 Enum additions (both locales in the same commit — rule 4)

- `CaseOrigin` (new): `new`, `renewal`, `period_change`.
- `AccountGroupStatus` (new): `active`, `archived`. `AccountClientRole` (new): `policyholder`, `insured`.
- `EndorsementKind.PERIOD_EXTENSION = "period_extension"` (16 chars < `aggregate_reinstatement` 23 → no MODIFY). **Update `tests/test_case_file_model.py:170` from 15 to 16 motives.**
- `EntityType.ACCOUNT_GROUP = "account_group"` (13 < 18 → longest stays `inspection_request`; extend the assertion at `:174-181`).
- `DocumentCategory.ARCHIVE_PACK = "archive_pack"` (12 < 25) — the ZIP of a group/period, stored as a `document` row with `entity_type=account_group`.

### 2.5 Cardinality change without DDL

`placement.case_file_id` (`placement.py:85-93`) becomes the authority for "placements of an account":
one folder → N placements. `case_file.placement_id` = primary placement. Relax
`backend/app/api/routers/case_files.py:441-455` to "a placement is wrapped by ≤1 account case",
`_guard_proposal_issued` (`backend/app/services/case_files.py:315-325`) to "≥1 accepted, ≤1 per
placement", and fan `STAGE_TO_PLACEMENT_STATUS` (`:184-199`) out over every placement with
`case_file_id == case.id` in the same transaction. The single-placement path stays byte-identical.

### 2.6 How existing tables link

| table | link |
|---|---|
| `placement` | `case_file_id` → account folder (N:1); period copy locked with the folder |
| `case_file` | `account_group_id`; `origin_case_file_id` (sibling in time); `parent_case_file_id`+`policy_id` unchanged (post-sale) |
| `policy` | unchanged; reached via `policy.case_file_id`/`placement_id`; `renews_policy_id` (`policy.py:120`, dead today) is written when a `kind=renewal` folder's policy is issued |
| `client` | `account_group_id` (≤1 group) |
| `sales_lead` | `account_group_id`, `period_*`; convert forwards both into the account folder + `account_client(is_primary)` |
| `endorsement` | `batch_key`; `policy_id` still NOT NULL |
| `document` | unchanged; group archives are `entity_type=account_group` rows (rule 8: the key lives only here) |

## 3. Migration and importer mapping

### 3.1 Migrator (`backend/scripts/migrate_case_files.py`)

Add `GROUPS_NEW_TABLES = ["account_group", "account_client"]` and
`GROUPS_ADDED_COLUMNS = {"client": ["account_group_id"], "case_file": ["account_group_id",
"period_start", "period_end", "period_label", "origin", "origin_case_file_id"], "endorsement":
["batch_key"], "sales_lead": ["account_group_id", "period_start", "period_end"]}`; no widened
columns. `validate_manifest()` and `build_offline_plan()` iterate both manifests. New test
`tests/test_migrate_manifest.py`: every manifest name appears in the `--dialect mysql` offline plan
and no new table declares a `use_alter` FK. **Same RDS window** as the still-pending case-files
migration: dry-run → `--apply` → zero-drift, before any push to `dev` (rule 10).

`backend/scripts/backfill_groups.py --dry-run|--apply` (idempotent, for live DBs that will not be
re-imported): client → group from `client.source`, else `insured.trade_name`; account case → group
from `meta.commercial_account`, else its client's group; `period_*` copied from the primary placement;
`account_client(is_primary)` from `client_id`; post-sale children inherit from their policy's account;
the renewal case (placement NULL) is rebuilt as in §3.3 row 16.

### 3.2 `import_fixtures.py`

One `account_group` per fixture client from `insured.trade_name`: **1 Santa Elisa** (broker 1),
**2 Altamira Logística** (broker 2), **3 Clínica del Valle** (broker 3); `client.account_group_id`
set; 0 accounts (placements 1-3 have no case file and stay visible under Colocaciones — an honest
empty state, never a folder-less account).

### 3.3 `import_expedientes.py` (ids as produced by the current import order; verified in `radal.db`)

`InsuredIdentity.account` (`backend/app/db/expediente_mappings.py:352-415`) → get-or-create group by
`(broker, slug)` in the client step: **4 JO PASTELERÍA** (b1, client 4), **5 COCCOLINO** (b1, client 5),
**6 GRUPO VIÑA INDÓMITA** (b3, clients **6 and 7** — one group, two RUTs), **7 LA FAVORITA** (b1, client 8).
Total **7 groups**.

| case | reference | kind / stage | group | line | period (label) | origin | members |
|---|---|---|---|---|---|---|---|
| 1 | EXP-2026-0001 (Ossa) | account / lead | 4 JO | 7 AP | 2026-07-01→2027-07-01 (2026-2027) — finally persisted from `CaseSpec` | new | ac(client 4, primary); no placement |
| 2 | EXP-2026-0002 | account / intake | 5 COCCOLINO | 5 fleet | 2026-08-01→2027-08-01 (2026-2027) | new | ac(5) + placement 4 |
| 3 | EXP-2026-0003 | account / market_submission | 4 JO | 2 RC | 2026-06-12→2027-06-12 (2026-2027) | new | ac(4) + placement 5 |
| 4 | EXP-2026-0001 (Fuenzalida) | account / comparison | 6 VIÑA INDÓMITA | 1 property | 2026-10-15→2027-10-15 (2026-2027) | new | ac(6) + placement 6 |
| 5 | EXP-2026-0004 | account / proposal_issued | 5 COCCOLINO | 1 property | 2026-04-01→2027-04-01 (2026-2027) | new | ac(5) + placement 7 |
| 6 | EXP-2025-0001 | account / active | 6 VIÑA INDÓMITA | 1 | 2025-10-15→2026-10-15 (2025-2026) | new | ac(7) + placement 8 |
| 7-10 | -E1, -E2, -CB1, -SN1 | post-sale on policy 1 | 6 (inherited) | 1 | inherited from case 6 | new | none |
| 11 | EXP-2026-0005 | account / active | 7 LA FAVORITA | 1 | 2027-01-05→2028-01-05 (2027-2028) | new | ac(8) + placement 9 |
| 12-15 | -E1, -E2, -CB1, -SN1 | post-sale on policy 2 | 7 (inherited) | 1 | inherited from case 11 | new | none |
| 16 | EXP-2026-0005-RN1 (**unchanged**) | renewal / renewal_review | 7 LA FAVORITA | 1 | 2028-01-05→2029-01-05 (2028-2029) | **renewal**, `origin_case_file_id=11` | ac(8) + **new placement 10** (client 8, asset 9, line 1, draft, `case_file_id=16`); `policy_id=NULL`, `parent_case_file_id=NULL` |

Case 16 keeps its reference so the `--reset-cases` idempotency key does not move; folders created
through the API mint `build_reference(db, broker_id=…, year=period_start.year)` (new explicit `year`
parameter on `services/case_files.py:611`). Case 4 stays `origin=new` — the corpus never states it
renews case 6 (different RUT); see §9.

`--reset-cases` (`import_expedientes.py:4000-4019`) must also purge placements by
`Placement.case_file_id` (not only `CaseFile.placement_id`), `account_client` rows, and groups with
no remaining clients/cases. Self-check after import: 7 groups; Viña Indómita = 1 group / 2 clients /
2 folders; Coccolino = 1 group / 2 folders with different dates under label 2026-2027; case 16
`origin=renewal`, `origin_case_file_id=11`, `policy_id IS NULL`, placement 10 attached; 16 cases,
89 documents, 2 policies, 4 endorsements, 2 plans, 2 claims, 19 warranties; a second run is identical.

## 4. API contract

All under `/api/v1`, scoped by the caller's `broker_id`; foreign rows 404; business refusals are
**422** with English `detail` (matches `CaseTransitionError` handling). Structured refusals use
`detail={"code": "...", ...}`.

### 4.1 Groups — `backend/app/api/routers/account_groups.py`, module `Groups`

| method / path | permission | params / body | response |
|---|---|---|---|
| `GET /account-groups` | Groups.View | `q, status, page, page_size` | `Page<AccountGroupRead>` |
| `POST /account-groups` | Groups.Create | `{name, notes?, client_ids?: int[]}` | `AccountGroupDetail` 201 |
| `GET /account-groups/{id}` | Groups.View | | `AccountGroupDetail` |
| `PATCH /account-groups/{id}` | Groups.Edit | `{name?, status?, notes?}` | `AccountGroupDetail` |
| `POST /account-groups/{id}/clients` | Groups.Edit | `{client_id}` — 404 foreign broker; 422 `client_in_other_group` | `AccountGroupDetail` |
| `DELETE /account-groups/{id}/clients/{client_id}` | Groups.Edit | 422 if the client has open folders in the group | 204 |
| `GET /account-groups/{id}/tree` | CaseFiles.View | | `GroupTree` (§4.2) |
| `GET /account-groups/{id}/timeline` | CaseFiles.View | `limit=50, before=<iso>` | `{entries: TimelineEntry[], next_before}` |
| `POST /account-groups/{id}/archives` | Documents.View + CaseFiles.View | `{period_label?}` | `{document_id, download: DocumentDownload}` 201 |

`AccountGroupRead = {id, name, slug, status, primary_client: {id, rut, legal_name} | null,
clients_count, accounts_count, open_count, latest_period_label, latest_period_start, updated_at}`.
`AccountGroupDetail = AccountGroupRead + {notes, clients: [{id, rut, legal_name, trade_name, status}]}`.
`TimelineEntry = {kind: "stage"|"activity"|"note"|"policy"|"endorsement"|"claim"|"collection",
occurred_at, case_file_id, policy_id, title, detail}` — merged over every visible case of the group,
**sorted descending before truncation** (unlike the per-case timeline at `case_files.py:727-805`).

Archives are generated by `services/packs.py` into a `document(entity_type=account_group,
category=archive_pack)` row — layout `{slug}/{period}/{line}/{section}/{code}-{filename}` over the
visible cases — and handed back as the existing presigned `DocumentDownload`; nothing streams through
the buffered Lambda (OAC constraint 4). Only documents of cases visible to the caller are included.

### 4.2 Navigator — `backend/app/services/navigator.py`, `backend/app/api/routers/navigator.py`, gate CaseFiles.View

`GET /navigator` (main rail; a separate router avoids the `/{id}` route-order trap):

```json
{"groups":[{"id":6,"name":"GRUPO VIÑA INDÓMITA","slug":"grupo-vina-indomita","accounts_count":2,"open_count":1,
   "latest_period_label":"2026-2027","latest_period_start":"2026-10-15",
   "periods":[{"label":"2026-2027","accounts_count":1},{"label":"2025-2026","accounts_count":1}]}],
 "recent":[{"case_file_id":4,"reference":"EXP-2026-0001","title":"Viña Santa Alicia · Incendio","group_id":6,
   "period_label":"2026-2027","line_name":"Todo Riesgo Bienes Físicos","stage":"comparison","origin":"new"}],
 "ungrouped_clients_count":0,
 "record_folders":[{"key":"amounts","categories":["insured_values_schedule","insured_amounts","valuation"]},
   {"key":"loss_history","categories":["loss_history","claims_history"]},
   {"key":"report","categories":["inspection_report","risk_engineering_plan"]},
   {"key":"questionnaire","categories":["business_questionnaire","prospect_request"]},
   {"key":"slip","categories":["technical_brief","submission_letter"]}]}
```

`record_folders` is the server-owned mapping from the Excel's ANTECEDENTES leaves (G8–G12: MONTOS,
SINIESTRALIDAD, INFORME, CUESTIONARIO, SLIP DE T&C) to `DocumentCategory`; every category listed
exists in `backend/app/models/document.py:50-108`. The UI never hardcodes it. `groups` ordered by
`latest_period_start` desc (`col.is_(None)` ordering, no `NULLS LAST`), max 8; `recent` = 5 most
recently updated visible account/renewal cases.

`GET /account-groups/{id}/tree` (secondary rail):

```json
{"group":{"id":6,"name":"GRUPO VIÑA INDÓMITA","status":"active",
   "clients":[{"id":6,"rut":"96688830-K","legal_name":"Viña Santa Alicia SpA"},{"id":7,"rut":"99568600-7","legal_name":"Viña Indómita SpA"}]},
 "periods":[
  {"label":"2026-2027","start":"2026-10-15","end":"2027-10-15","is_latest":true,
   "lines":[{"insurance_line_id":1,"name":"Todo Riesgo Bienes Físicos","requires_inspection":true,
     "account":{"case_file_id":4,"reference":"EXP-2026-0001","kind":"account","stage":"comparison","status":"open",
       "origin":"new","origin_case_file_id":null,"renewed_by_case_file_id":null,
       "period_start":"2026-10-15","period_end":"2027-10-15","period_locked":true,
       "client_ids":[6],"placement_ids":[6],
       "documents_count":11,"record_counts":{"amounts":1,"loss_history":1,"report":1,"questionnaire":1,"slip":1},
       "quotes_count":1,"proposals_count":3,"packs_count":2,"notes_count":0},
     "policies":[],
     "renewal":{"case_file_id":null,"allowed":false,"reason":"account_not_active"}}]},
  {"label":"2025-2026","start":"2025-10-15","end":"2026-10-15","is_latest":false,
   "lines":[{"insurance_line_id":1,"name":"Todo Riesgo Bienes Físicos","requires_inspection":true,
     "account":{"case_file_id":6,"reference":"EXP-2025-0001","kind":"account","stage":"active","status":"open",
       "origin":"new","origin_case_file_id":null,"renewed_by_case_file_id":null,
       "period_start":"2025-10-15","period_end":"2026-10-15","period_locked":true,
       "client_ids":[7],"placement_ids":[8],"documents_count":15,
       "record_counts":{"amounts":1,"loss_history":1,"report":1,"questionnaire":1,"slip":1},
       "quotes_count":1,"proposals_count":3,"packs_count":0,"notes_count":2},
     "policies":[{"id":1,"policy_number":"0020119904","insurer_name":"Southbridge","status":"active",
       "start_date":"2025-10-15","end_date":"2026-10-15","extended_end_date":null,
       "children":{
         "endorsement":[{"case_file_id":7,"reference":"EXP-2025-0001-E1","sequence_no":1,"version":1,"stage":"endorsement_applied","status":"closed","effective_at":"2026-03-20T12:00:00Z","batch_key":null},
                        {"case_file_id":8,"reference":"EXP-2025-0001-E2","sequence_no":2,"version":1,"stage":"endorsement_applied","status":"closed","effective_at":"2026-05-02T12:00:00Z","batch_key":null}],
         "collection":[{"case_file_id":9,"reference":"EXP-2025-0001-CB1","sequence_no":1,"version":1,"stage":"collection_overdue","status":"open","effective_at":null,"batch_key":null}],
         "claim":[{"case_file_id":10,"reference":"EXP-2025-0001-SN1","sequence_no":1,"version":1,"stage":"claim_settled","status":"closed","effective_at":null,"batch_key":null}]}}],
     "renewal":{"case_file_id":null,"allowed":true,"reason":null}}]}]}
```

`renewal.reason ∈ {account_not_active, already_renewed, period_open}`; `renewed_by_case_file_id` is
the folder whose `origin_case_file_id` points here. Implementation: five grouped queries over
`_visible()` (cases of the group ⨝ line; `Document` counts by `case_file_id` and by category;
`Policy` by `case_file_id`/`placement_id`; post-sale children `group_by(policy_id, kind)`;
quotes/proposals/packs/notes counts) assembled in Python; `ONLY_FULL_GROUP_BY`-safe; never calls
`_detail`. Child nodes are filtered by the caller's `Endorsements|Collections|Claims View` grants.

### 4.3 Binary flows — `routers/case_files.py`, `routers/endorsements.py`, `services/case_files.py`

- `POST /case-files` — `kind=account` requires `period_start`, `period_end`; accepts `account_group_id`
  (default: the client's group) and `client_ids[]` (→ `account_client`, contratante primary). `kind=renewal`
  is accepted **without** `policy_id` when `origin_case_file_id` is given (relaxes `:458-463`).
  Uniqueness (rule 5) → 422 `{"code":"folder_exists","case_file_id":N}`.
- `POST /case-files/{id}/renew` (CaseFiles.Create) `{period_start, period_end, period_label?,
  copy_sections?: ["root_prospect"], client_ids?}`. Guards: source `kind ∈ {account, renewal}` and
  `stage ∈ {active, mirror_validation, closed}` else 422 `renewal_not_allowed`; `folder_exists` 422.
  One transaction: clone each source placement (asset, line, client, new period, `draft`,
  `case_file_id=new`); copy `account_client`; create `case_file(kind=renewal, stage=renewal_review,
  origin=renewal, origin_case_file_id=source, account_group_id, period_*, policy_id=NULL,
  parent_case_file_id=NULL, reference=build_reference(year=period_start.year))`;
  `record_stage_event(None→renewal_review, meta={origin_case_file_id, prior_policy_ids})`; for each
  `copy_sections` document create a **new `document` row with bytes copied to a new S3 key**
  (`meta.copied_from_document_id`) — never two rows on one key. `_RENEWAL_CHAIN`
  (`services/case_files.py:85`) replays the account journey. Returns `CaseFileDetail` 201.
- `POST /case-files/{id}/reperiod` (CaseFiles.Create) `{period_start, period_end, reason}`. Source must
  be `kind ∈ {account, renewal}`, `status=open`. New folder: `kind=account`, `origin=period_change`,
  `origin_case_file_id=source`, `stage = source.stage if source.stage ≤ technical_basis else intake`,
  cloned placements + members, all antecedentes copied (new keys). Source: `status=closed`,
  `closed_at=now`, `meta.closed_reason="period_change"`, stage event `→ closed` with `meta.reason`.
- `period_locked`: `PATCH /case-files/{id}` never exposes period fields. `PATCH /placements/{id}`
  (`routers/placements.py:518-545`) accepts period fields only while the wrapping account has no stage
  event with `to_stage ∉ {lead, intake}`; then the edit syncs to the folder. Otherwise 422
  `{"code":"period_locked","detail":"The validity period is a folder; use POST /case-files/{id}/reperiod"}`.
- When a policy is created for a `kind=renewal` folder, set `policy.renews_policy_id` to the prior
  folder's policy for the same client.
- `GET /case-files/{id}/history` (CaseFiles.View) → `{origin_chain: [{case_file_id, reference,
  period_label, origin, stage}], prior: {case_file_id, records: {folder_key: [DocumentRead]},
  policies: [PolicyRead], claims: [ClaimSummary], loss_ratio_pct} | null}` — read-only context.
- `POST /case-files/{id}/clients {client_id, role?}` / `DELETE …/clients/{client_id}` (CaseFiles.Edit)
  — 422 `client_not_in_group`; cannot remove the contratante.
- `POST /endorsements/batch` (Endorsements.Create) `{policy_ids: int[], kind: "period_extension",
  new_end_date, effective_at, issued_document_id?, note?}`. Every policy via `get_policy_or_404` and
  the same `account_group_id` (else 422 `policies_span_groups`). One transaction: N `Endorsement`
  rows (own `sequence_no`, shared `batch_key=uuid4`, `effect={"period_end":{"before","after"}}`, zero
  premium deltas), N `case_file(kind=endorsement)` children. Returns
  `{batch_key, items: [{policy_id, endorsement_id, case_file_id}]}` 201.
- `POST /endorsements/batch/{batch_key}/issue {issued_document_id?}` — issues all members through the
  existing path and calls `apply_period_extension(policy, endorsement)` once per policy (moves
  `policy.end_date/period_end_at`; the collection re-pricing block no-ops on zero deltas).
  `GET /endorsements?batch_key=`.
- `POST /leads/{id}/convert` forwards `account_group_id` (lead's, else client's) and `period_*`.
- Filters: `account_group_id`, `period_label`, `origin` on `/case-files`; `account_group_id` on
  `/placements`, `/policies`, `/clients`; `case_file_id` on `/quotes`, `/proposals`; `GET /search`
  returns `groups[]`.
- `CaseFileRead` gains `account_group_id, account_group_name, period_start, period_end, period_label,
  origin, origin_case_file_id, period_locked, client_ids`.

## 5. Frontend

### 5.1 Shell

`components/layout/AppShell.tsx` → three columns: `Sidebar` | `Topbar` + `<Outlet/>`. The
`key={location.pathname}` remount and `mx-auto max-w-[1380px] px-7` move into
`components/layout/StandardPage.tsx`, applied as a layout route around every existing page. The group
family renders `GroupShell` (contextual rail + scroll pane) without it, so the tree never remounts.

### 5.2 Main sidebar — `components/layout/Sidebar.tsx` (266px, server-filtered)

1. Tenant pill (unchanged).
2. **GRUPOS** (`Groups.View`): ≤8 groups from `GET /navigator`; row = folder icon · name · `open_count`
   chip · chevron; expanded → up to 3 periods "2026–2027 · 1" → `/groups/:id/periods/:label`. Footer
   "Ver todos los grupos" (`/groups`) and "+ Nuevo grupo" (`Groups.Create`). Empty: "Sin grupos" + CTA.
3. **COMERCIAL**: Cotizaciones (`/quotes`, Quotes) · Propuestas – Emisión (`/proposals`, Proposals).
4. Portafolio – Cartera (`/policies`, Policies).
5. **ASISTENTES IA** (module `Dashboard` proxy, existing precedent at `Sidebar.tsx:65`): Lector
   documental (`/ai/reader`) · Comparador (`/ai/comparator`) — real pages (§5.4).
6. Compañías y apetito (`/insurers`; the appetite column inside is a "pronto" chip).
7. Finanzas · Reportería → `disabledItems` "pronto".
8. **MÁS** (collapsed by default, persisted): Inicio, Leads, Expedientes, Clientes, Colocaciones,
   Inspecciones, Siniestros, Ofertas, Agente — every existing route stays reachable.
9. Admin section unchanged.

State: `localStorage["radal.nav.expanded"]` keyed `group:6`, `group:6/period:2026-2027`,
`section:more` (via `lib/treeState.ts` `useExpanded(storageKey)`); active = leaf exact / parent
prefix on `location.pathname`; parents never auto-expand; Cmd/Ctrl-click opens a tab. Data via React
Query `qk.navigator.all` (`staleTime` 5 min, `placeholderData: keepPreviousData`).

### 5.3 Contextual sidebar — `components/groups/GroupSidebar.tsx` (260px; `Sheet` below `lg`)

Header: group name (→ overview), client chips (RUT · razón social), actions: **Descargar expediente**
(whole group or current period → `POST /archives`, spinner + toast, `Documents.View`), **Ayuda**
(Popover legend explaining vigencia/ramo/origin badges), **+ Nueva cuenta** (`CaseFiles.Create`).

Tree from `GET /account-groups/{id}/tree`, `period_start` desc, latest expanded:

- **VIGENCIA** `2025–2026` — count = accounts; historic (`!is_latest`) render read-only (no create /
  upload / renew affordances, "histórico" chip).
  - **RAMO** — icon per line, caption with full dates "15 oct 2025 – 15 oct 2026", stage badge,
    **origin badge** (Nueva / Renovación / Cambio de vigencia), lock icon when `period_locked`.
    - **ANTECEDENTES** (n) → MONTOS · SINIESTRALIDAD · INFORME · CUESTIONARIO · SLIP DE T&C from
      `record_folders` × `record_counts`; empty folders greyed but navigable
      (`…/accounts/:id?tab=records&folder=amounts`, upload CTA gated `Documents.Upload`, hidden on historic).
    - **COTIZACIONES** (n, `Quotes.View`) · **PROPUESTAS** (n, `Proposals.View`).
    - **PÓLIZAS** (n) → `P1 · 0020119904 · Southbridge` (caption "prorrogada hasta …" when
      `extended_end_date`) → **ENDOSO** (`Endorsements.View`) / **COBRANZA** (`Collections.View`) /
      **SINIESTROS** (`Claims.View`), leaves "E1 · 20 mar 2026" with stage dot; batch endorsements
      render once with N policy chips.
    - **RENOVACIÓN**: link "→ 2026–2027" when `renewal.case_file_id`; else "Iniciar renovación"
      (`CaseFiles.Create && renewal.allowed`); else `DisabledHint` with `t(\`accounts:renewal.reason.${reason}\`)`.

Expanded map `localStorage["radal.tree.expanded"]` keyed `g6/p2025-2026/l1/policies/pol1`; active
path auto-expands on first mount only. Skeleton rows while loading; `ErrorBanner` with retry.

### 5.4 Routes (`App.tsx`) and component tree

```
/groups                                pages/groups/index.tsx        DataTable + search + NewGroupDialog
/groups/new                            pages/groups/new.tsx
/groups/:groupId                       components/groups/GroupShell.tsx (GroupSidebar + <Outlet/>; real 404 page)
   index                               pages/groups/overview.tsx     descending timeline grouped by vigencia
   periods/:periodLabel                pages/groups/period.tsx       one card per ramo/account
   accounts/new?period=&lines=         pages/groups/account-new.tsx  vigencia + ramos multi-select REQUIRED → N folders
   accounts/:caseId?tab=&folder=       pages/groups/account.tsx      tabs records|quotes|proposals|policies|renewal|notes|timeline
   accounts/:caseId/renew              pages/groups/renew.tsx        history panel (GET /history) + dates + copy_sections
   accounts/:caseId/reperiod           pages/groups/reperiod.tsx
   policies/:policyId?tab=             pages/groups/policy.tsx       reuses PolicyDetailBody + SubFunnel.group
   policies/extend?ids=                pages/groups/extend.tsx       prórroga multi-select, none pre-selected
/ai/reader                             pages/ai/reader.tsx           document picker + components/common/SuggestionForm.tsx (never a third copy)
/ai/comparator                         pages/ai/comparator.tsx       quotes with ≥2 proposals → /quotes/:id/comparison
components/groups/{TreeNode,RecordFolderRow,PolicyNode,OriginBadge,DownloadArchiveButton}.tsx
components/cases/CaseDetailBody.tsx    extracted from pages/cases/detail.tsx:127-702 (no behaviour change)
components/policies/PolicyDetailBody.tsx extracted from pages/policies/detail.tsx
lib/treeState.ts · api/{accountGroups,navigator}.ts · keys.ts · types.ts · api/index.ts barrel
locales/{es,en}/accounts.json          new namespace (add "accounts" to NAMESPACES in i18n/index.ts:17)
```

Legacy `/cases/:id` and `/policies/:id` keep working inside `StandardPage` and show "Ver en grupo"
when `account_group_id` is set. Main pane: breadcrumb *Grupos › Viña Indómita › 2025–2026 › Incendio*,
title + meta ("2 cuentas · 1 póliza"), tab row, filter chips (Estado / Ramo / Compañía / Ejecutivo)
echoing the active value, body = descending timeline (latest vigencia is the card title, older ones
collapsed beneath; `TimelineTab` rail from `cases/detail.tsx:653-702`).

**Empty states:** no groups → CTA "Nuevo grupo"; group without accounts → "Nueva cuenta"; period
without policies → explanatory text; ANTECEDENTES folder empty → greyed + upload CTA; history absent
on `/renew` → "Sin vigencia anterior". **Pronto:** Finanzas, Reportería, apetito column, "enviar
carpeta", renewal execution past `renewal_review` remains the existing chain.

**Hooks / query keys:** `qk.navigator = {all: ["navigator"]}`; `qk.accountGroups = scope("accountGroups")
+ tree(id) + timeline(id, params) + archives(id)`; `qk.caseFiles.history(id)`; `qk.endorsements.batch(key)`.
Hooks: `useNavigator`, `useAccountGroups`, `useAccountGroup`, `useGroupTree`, `useGroupTimeline`,
`useCreateGroup`, `useUpdateGroup`, `useAttachGroupClient`, `useDetachGroupClient`, `useCreateArchive`,
`useRenewCase`, `useReperiodCase`, `useCaseHistory`, `useAddCaseClient`, `useBatchEndorsements`,
`useIssueBatch`. Mutations invalidate `qk.navigator.all`, `qk.accountGroups.all`, `qk.caseFiles.all`
(+ `qk.policies.all`, `qk.endorsements.all` for batches). `permissions.ts` `MODULES` gains `"Groups"`.

**i18n `accounts.json` key groups:** `nav.*` (groups, newGroup, allGroups, more, commercial,
portfolio, aiAssistants, reader, comparator, insurersAppetite, finance, reports), `tree.*` (period,
line, records, quotes, proposals, policies, renewal, endorsement, collection, claims, download, help,
startRenewal, historic, locked), `folders.{amounts,loss_history,report,questionnaire,slip}`,
`origin.{new,renewal,period_change}`, `renewal.reason.{account_not_active,already_renewed,period_open}`,
`renew.*`, `reperiod.*`, `extend.*`, `history.*`, `group.*` (fields, status, actions), `errors.{period_locked,
folder_exists,client_not_in_group,policies_span_groups}`, `empty.*`, `help.*`. Additions elsewhere:
`postsale.kinds.period_extension`, `documents.categories.archive_pack`, `cases.entities.account_group`.
Dynamic keys (`t(\`origin.${o}\`)`, `t(\`folders.${k}\`)`) must exist in both locales.

## 6. RBAC changes (`backend/app/core/roles_config.py`, mirrored in `frontend/src/lib/permissions.ts`)

- Module **`Groups`** after `Leads`: `platform_admin`/`broker_admin` full; `broker_executive`
  View/Create/Edit/Comment; `broker_technician` View/Comment; `broker_inspector` View=`partial`
  (groups containing a visible case); insured/insurer `no`.
- **`CASE_VIEW_SCOPE: dict[str, Callable[[Select, int], Select]]`** replaces `_inspector_scoped`
  (`routers/case_files.py:92-112`): `broker_inspector` → inspection subquery (unchanged);
  `broker_collections` → `kind IN (collection) OR id IN (parent ids of visible collections)`;
  `broker_claims` → same for `claim`. Applied in `_visible()` **and** in `get_case_or_404()`, which
  gains a `user` parameter (`:125-135` applies no narrowing today; packs/documents would leak). The
  tree, timeline and archives inherit it. **Ships before any process role exists.**
- Process profiles, additive under `USER_TYPE_ROLES["broker"]`: `broker_commercial` (= executive
  matrix + Groups), `broker_collections` (Collections full incl. Approve; Policies View; Endorsements
  View; Groups View; CaseFiles View=partial; Documents View=partial; Claims/Quotes/Proposals/Leads no),
  `broker_claims` (Claims full incl. Approve; Policies View/Edit; Groups View; CaseFiles View=partial;
  Collections no). Role fixtures stay additive (`ensure_role_user`); `test_unknown_role_is_denied_everywhere`
  covers the new module fail-closed. The UI never hardcodes roles; nodes hide per `can()`.

## 7. Tests

Backend (target 315 + ~50, ~60 s, AI hermetic via `tests/conftest.py`):

- `tests/test_case_file_model.py`: `account_group`, `account_client` in `NEW_TABLES` (both dialects,
  `broker_id`, bounded VARCHAR, not reserved, no `use_alter`); `EndorsementKind` == **16**;
  `EntityType.ACCOUNT_GROUP` and `DocumentCategory.ARCHIVE_PACK` without widening; `create_all` on SQLite.
- `tests/test_migrate_manifest.py`: both manifests present in the `--dialect mysql` offline plan;
  `origin` ADD COLUMN carries `DEFAULT 'new'`.
- `tests/test_account_groups.py`: CRUD; slug unique per broker (same name allowed in broker B);
  attach foreign-broker client → 404; client in another group → 422; detach with open folders → 422;
  cross-tenant 404; timeline descending + cursor; archive document is `entity_type=account_group`,
  contains only visible cases' documents, download via `GET /documents/{id}/content`.
- `tests/test_navigator.py`: payload shape; counts equal DB; Viña Indómita 1 group / 2 clients /
  2 periods; Coccolino two lines, different dates, one label; `record_folders` categories all valid;
  inspector narrowing; children hidden without Collections/Claims View; query count cap (≤ 8).
- `tests/test_renewal.py`: `/renew` creates N placements + members + renewal folder at
  `renewal_review` with `origin=renewal`, `policy_id NULL`, reference year = `period_start.year`;
  source untouched; `folder_exists` 422; `renewal_not_allowed` at `comparison`; `copy_sections`
  creates new document rows with distinct S3 keys; `POST /case-files kind=renewal` without `policy_id`
  → 201; `renews_policy_id` set on policy issue; `renewed_by_case_file_id` in tree.
- `tests/test_case_files.py`: `period_locked` after `technical_basis`, editable at `intake` (syncs);
  `/reperiod` closes source (`closed_reason`), successor `origin=period_change` with copied
  antecedentes; two placements on one folder + status fan-out; `≥1 accepted` guard; `client_ids[]`
  → `account_client`; `client_not_in_group` 422; `get_case_or_404(user)` narrowing over packs/documents.
- `tests/test_post_sale.py` prórroga: batch over 2 policies → 2 endorsements + 2 cases, shared
  `batch_key` and `issued_document_id`; cross-group 422; batch issue moves `end_date`/`period_end_at`
  on both; ledger sums unchanged; `GET /endorsements?batch_key`.
- `tests/test_rbac.py::TestProcessProfiles`: matrix rows + HTTP for the three roles.
- `tests/test_tenant_isolation.py`: broker B's groups invisible in `/navigator`, `/account-groups`, `/search`.
- Importer smoke (documented, manual): `import_fixtures --reset --no-upload` + `import_expedientes
  --no-upload` twice → identical counts (§3.3).

Frontend gate: `npx tsc --noEmit && npm run build`, plus a locale key-set parity check (`es` vs `en`
for `accounts.json` and the touched namespaces).

## 8. Implementation plan — parallel-safe work packages

Lane A is serial and first; B, C, D run in parallel after A; E alongside each owner; F last.

| WP | size | scope | owning files (exclusive) |
|---|---|---|---|
| **A1** | M | models `account_group`, `account_client`; columns on `case_file`, `client`, `endorsement`, `sales_lead`; enums (§2.4); `models/__init__.py`, `db/base.py`; update `test_case_file_model.py` | `backend/app/models/{account_group,account_client,case_file,client,endorsement,sales_lead,enums,document}.py`, `models/__init__.py`, `db/base.py`, `tests/test_case_file_model.py` |
| **A2** | S | migrator manifests, offline plan, `tests/test_migrate_manifest.py`, `backfill_groups.py` | `backend/scripts/migrate_case_files.py`, `backend/scripts/backfill_groups.py`, `tests/test_migrate_manifest.py` |
| **A3** | S | schemas: `schemas/account_group.py`, `schemas/navigator.py`; extend `schemas/case_file.py` (`CaseFileRead/Create`, renew/reperiod/history bodies), `schemas/endorsement.py` (batch) | those schema files only |
| **B1** | L | `services/navigator.py`, `routers/navigator.py`, `routers/account_groups.py` (+ archives via `services/packs.py` extension), `main.py` registration, RBAC `Groups` module | `backend/app/services/navigator.py`, `routers/{navigator,account_groups}.py`, `services/packs.py` (archive function only), `main.py`, `core/roles_config.py` (Groups row only), `tests/test_account_groups.py`, `tests/test_navigator.py` |
| **B2** | L | case_files: create fields/filters, uniqueness, `/renew`, `/reperiod`, `/history`, `/clients`, `period_locked` in placements, N-placement guards + fan-out, `build_reference(year)`, `renews_policy_id` on policy create, lead convert forwarding | `routers/case_files.py`, `routers/placements.py`, `routers/leads.py`, `routers/policies.py` (create path only), `services/case_files.py`, `tests/test_renewal.py`, `tests/test_case_files.py` |
| **B3** | M | endorsements `/batch`, `/batch/{key}/issue`, `apply_period_extension`, `?batch_key`; `POST /endorsements` refuses `period_extension` | `routers/endorsements.py`, `services/endorsements.py` (new), `tests/test_post_sale.py` |
| **B4** | S | filters on `/quotes`, `/proposals`, `/clients`; `GET /search` groups | `routers/{quotes,proposals,clients,search}.py`, `tests/test_search.py` |
| **B5** | M | `CASE_VIEW_SCOPE`, `get_case_or_404(user)`, process roles, `TestProcessProfiles`, `docs/usuarios-de-prueba.md` role rows | `core/roles_config.py` (roles + scope map; coordinate one-line merge with B1), `routers/case_files.py::_visible/get_case_or_404` (B2 owner applies B5's patch), `routers/packs.py`, `routers/documents.py`, `tests/test_rbac.py`, `tests/test_tenant_isolation.py` |
| **C1** | S | `import_fixtures.py` groups | `backend/app/db/import_fixtures.py` |
| **C2** | L | `import_expedientes.py` groups, periods, members, case 16 rebuild, placement 10, `--reset-cases` purge, self-checks | `backend/app/db/import_expedientes.py`, `expediente_mappings.py` |
| **D1** | M | `AppShell` three-column, `StandardPage`, layout route in `App.tsx`; `CaseDetailBody`/`PolicyDetailBody` extraction | `components/layout/{AppShell,StandardPage}.tsx`, `components/cases/CaseDetailBody.tsx`, `components/policies/PolicyDetailBody.tsx`, `pages/cases/detail.tsx`, `pages/policies/detail.tsx`, `App.tsx` (layout + route stubs) |
| **D2** | M | main `Sidebar.tsx` restructure, `lib/treeState.ts`, `permissions.ts` `Groups` | `components/layout/Sidebar.tsx`, `lib/treeState.ts`, `lib/permissions.ts` |
| **D3** | L | `GroupShell`, `GroupSidebar`, `TreeNode`, `RecordFolderRow`, `PolicyNode`, `OriginBadge`, `DownloadArchiveButton`, 404 page | `components/groups/**` |
| **D4** | S | `api/{accountGroups,navigator}.ts`, `keys.ts`, `types.ts`, `api/index.ts`, hooks on `caseFiles`/`endorsements` | `src/api/**` |
| **D5** | L | group pages (§5.4) incl. renew/reperiod/extend/account-new | `pages/groups/**` |
| **D6** | M | `/ai/reader`, `/ai/comparator` | `pages/ai/**` |
| **D7** | S | `locales/{es,en}/accounts.json` + additions in `postsale`, `documents`, `cases`, `common`; `NAMESPACES`; parity script | `locales/**`, `i18n/index.ts` |
| **E** | — | tests live with their owners above; **F1** RDS dry-run/apply/zero-drift (both passes); **F2** commit + push `dev`; **F3** importers with upload (`AWS_PROFILE=radal`); **F4** smoke tree, archive download, prórroga, renew on dev; **F5** docs (`v2-case-files-as-built.md` delta, `CLAUDE.md` §2/§4/§9, `usuarios-de-prueba.md`) | serial, last |

Contract freeze: A3's schemas are the interface between B* and D4/D5; D4 starts from A3, not from
B* code. `App.tsx` is touched by D1 only (route stubs for D5/D6 pages are added there up front).

## 9. Open questions for the team (blocking only)

1. **Is Viña Santa Alicia 2026-2027 (case 4) a renewal of Viña Indómita 2025-2026 (case 6)?** Different
   RUTs, same group. If yes the importer sets `origin=renewal, origin_case_file_id=6`; today it stays `new`.
2. **Vigencia as a label over per-ramo full dates** — confirm against Coccolino (Vehículos ago–ago,
   Incendio abr–abr both "2026-2027"). The alternative (one folder per exact date range) is a one-line
   change in the tree grouping key but changes what "cambio de vigencia" means.
3. **Prórroga scope**: confirmed as policy-only (moves `policy.end_date`, never the account folder)?
   Isolated in `apply_period_extension` if the answer flips.
4. **Process-profile matrix**: the three roles in §6 are a proposal; the team's permission matrix is
   still unfilled and blocks final grants (not the mechanism).
5. **One renewal example** from a carrier (the 60–70-day renewal terms document) so the renewal
   folder can be demoed past `renewal_review`; without it `/renew` stays test-only.
