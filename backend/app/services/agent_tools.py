"""The agent's typed tool registry (v4 agent spec §4).

One dataclass, one dict — mirroring the extraction registry's shape so the
pattern is already familiar to the codebase. READ tools execute inline inside
the model loop under the CALLER's own grants; WRITE tools never execute there:
the loop persists an ``agent_action`` row and only the confirm endpoint runs
``execute`` — under the confirming user's real RBAC gate (CLAUDE.md rule 6).

Every ``execute`` takes ``(db, *, broker_id, user, args)`` — the caller's
identity, always. There is no service account and no ambient privilege: a tool
can never read or write anything the human in the chair could not. Executors
REUSE the exact transaction the normal router performs (they call the router's
own functions and helpers), so a confirmed action is indistinguishable in the
database from the same operation performed through the UI.

Tool descriptions and card summaries are Spanish — they are LLM prompt /
user-facing copy, the two places Spanish belongs (CLAUDE.md rule 1).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Annotated, Any, Callable, Literal

from fastapi import HTTPException, status
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    WithJsonSchema,
    field_validator,
    model_validator,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.permissions import user_has_permission
from app.core.roles_config import ACTIONS, MODULES
from app.models.enums import EntityType, PaymentCurrency, PaymentMode, PolicyCurrency
from app.models.user import User

__all__ = [
    "ToolSpec",
    "TOOL_REGISTRY",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "openai_tools",
    "gates_for",
    "action_anchor",
    "action_allowed",
]

# Fold-back truncation budget for a tool result (spec §5.3 / §4).
DEFAULT_MAX_RESULT_CHARS = 6_000

#: Spanish labels for permission-denied folds — the model answers gracefully
#: instead of the turn 403ing.
_MODULE_ES = {
    "Dashboard": "el panel",
    "Groups": "los grupos",
    "CaseFiles": "los expedientes",
    "Proposals": "las propuestas",
    "Quotes": "las cotizaciones",
    "Documents": "los documentos",
    "Clients": "los clientes",
    "Policies": "las pólizas",
    "Leads": "los leads",
    "Collections": "la cobranza",
    "Integrations": "las búsquedas en portales",
    "Settings": "la configuración",
    "Mail": "el correo",
}


# =============================================================================
# Args schemas — validate the model's arguments BEFORE anything runs/persists.
# =============================================================================


class _Args(BaseModel):
    """Base for tool arguments: tolerate hallucinated extra keys, strip noise."""

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)


class SearchEntitiesArgs(_Args):
    query: str = Field(..., min_length=2, max_length=120)
    kinds: list[Literal["groups", "clients", "assets", "quotes", "proposals"]] | None = None


class GetGroupTreeArgs(_Args):
    group_id: int = Field(..., gt=0)


class GetCaseFileArgs(_Args):
    case_file_id: int = Field(..., gt=0)


class GetCaseHistoryArgs(_Args):
    case_file_id: int = Field(..., gt=0)


class GetQuoteComparisonArgs(_Args):
    quote_request_id: int = Field(..., gt=0)
    include_rejected: bool = False


class ListDocumentsArgs(_Args):
    case_file_id: int | None = Field(default=None, gt=0)
    entity_type: str | None = None
    entity_id: int | None = Field(default=None, gt=0)
    category: str | None = None
    section: str | None = None

    @model_validator(mode="after")
    def _at_least_one_anchor(self) -> "ListDocumentsArgs":
        if self.case_file_id is None and not (self.entity_type and self.entity_id):
            raise ValueError(
                "list_documents needs an anchor: case_file_id, or entity_type + entity_id"
            )
        return self


class CreateGroupArgs(_Args):
    name: str = Field(..., min_length=1, max_length=255)
    notes: str | None = None
    client_ids: list[int] | None = None


class AttachClientToGroupArgs(_Args):
    group_id: int = Field(..., gt=0)
    client_id: int = Field(..., gt=0)


class CreateCaseFileArgs(_Args):
    client_id: int = Field(..., gt=0)
    # Post-sale kinds are NOT proposable by the agent this pass: they hang off
    # policies with guards the chat cannot review.
    kind: Literal["account"] = "account"
    title: str = Field(..., min_length=1, max_length=255)
    insurance_line_id: int | None = Field(default=None, gt=0)
    group_id: int | None = Field(default=None, gt=0)
    period_start: date | None = None
    period_end: date | None = None


class RenewCaseArgs(_Args):
    case_file_id: int = Field(..., gt=0)
    period_start: date
    period_end: date
    period_label: str | None = Field(default=None, min_length=1, max_length=32)
    note: str | None = Field(default=None, max_length=4000)


class AddNoteArgs(_Args):
    entity_type: str = Field(..., min_length=1, max_length=30)
    entity_id: int = Field(..., gt=0)
    body: str = Field(..., min_length=1, max_length=4000)
    is_internal: bool = True
    follow_up_on: date | None = None


# =============================================================================
# The spec
# =============================================================================


@dataclass(frozen=True)
class ToolSpec:
    """One tool: registry key + OpenAI function name, gate, schema, executor."""

    name: str
    kind: Literal["read", "write"]
    module: str  # RBAC module gate (registry-sanity checked against MODULES)
    action: str  # RBAC action gate (checked against ACTIONS)
    description: str  # Spanish, shown to the model
    args_schema: type[BaseModel]
    # read: runs inline in the loop; write: runs ONLY at confirm time.
    execute: Callable[..., dict]
    # write only: the Spanish card sentence, built server-side, never by the model.
    summarize: Callable[..., str] | None = None
    # write only: broker-scoped existence checks run at PROPOSAL time so a
    # doomed proposal is refused immediately. Raises HTTPException.
    validate_proposal: Callable[..., None] | None = None
    # Dynamic gate resolution (add_note). Returns [(module, action), ...];
    # None means [(self.module, self.action)]. May raise ValueError for
    # unresolvable arguments.
    resolve_gates: Callable[[Any], list[tuple[str, str]]] | None = None
    # Which entity family the action is ABOUT — anchors the activity trail even
    # for a discard, where nothing was created. Returns (EntityType, id|None).
    anchor: Callable[[dict], tuple[EntityType, int | None]] | None = None
    max_result_chars: int = DEFAULT_MAX_RESULT_CHARS
    # v12: a READ tool that needs the conversation (``load_skill`` records the
    # skill on ``agent_thread.active_skills``) receives ``thread=`` as well.
    thread_aware: bool = False


def gates_for(spec: ToolSpec, args: Any) -> list[tuple[str, str]]:
    """The (module, action) pairs a caller must hold for this call."""
    if spec.resolve_gates is not None:
        return spec.resolve_gates(args)
    return [(spec.module, spec.action)]


def permission_denied_payload(module: str, action: str) -> dict:
    label = _MODULE_ES.get(module, module)
    return {
        "error": "permission_denied",
        "module": module,
        "action": action,
        "detail": f"El usuario no tiene permiso de {action} sobre {label}",
    }


# =============================================================================
# Shared result helpers
# =============================================================================

_STRIP_KEYS = {
    "s3_key",
    "bucket",
    "hashed_password",
    # v12 secrets are write-only everywhere; belt and braces for any fold.
    "secret_ciphertext",
    "password_ciphertext",
    "secret",
    "password",
}


def _strip_keys(node: Any) -> Any:
    """Remove storage keys and secrets from a folded tool result, recursively.

    The model reasons over metadata only — never raw storage locations
    (CLAUDE.md rule 8: files are served through the broker-scoped document
    routes, not by key).
    """
    if isinstance(node, dict):
        return {k: _strip_keys(v) for k, v in node.items() if k not in _STRIP_KEYS}
    if isinstance(node, list):
        return [_strip_keys(item) for item in node]
    return node


def _dump(model: BaseModel) -> dict:
    return _strip_keys(json.loads(model.model_dump_json()))


# =============================================================================
# READ executors — all reuse the routers' own broker-scoped queries/helpers.
# Router imports are lazy: this module is imported by services and routers and
# must not create an import cycle.
# =============================================================================


def _exec_search_entities(db: Session, *, broker_id: int, user: User, args: SearchEntitiesArgs) -> dict:
    from app.api.routers.search import global_search

    results = global_search(q=args.query, db=db, broker_id=broker_id, current_user=user)
    payload = _dump(results)
    if args.kinds:
        wanted = set(args.kinds)
        payload = {kind: hits for kind, hits in payload.items() if kind in wanted}
    return payload


def _exec_get_group_tree(db: Session, *, broker_id: int, user: User, args: GetGroupTreeArgs) -> dict:
    from app.services.navigator import GroupNotFound, build_group_tree

    try:
        tree = build_group_tree(db, user, args.group_id, broker_id=broker_id)
    except GroupNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc) or "Group not found"
        ) from exc
    return _dump(tree)


def _exec_get_case_file(db: Session, *, broker_id: int, user: User, args: GetCaseFileArgs) -> dict:
    from app.api.routers.case_files import _get_case
    from app.models.case_file import CaseFile
    from app.models.client import Client
    from app.models.document import Document
    from app.models.insured import Insured
    from app.services import case_files as machine

    case = _get_case(db, args.case_file_id, broker_id, user)

    client_name = None
    client_rut = None
    client = db.get(Client, case.client_id) if case.client_id else None
    if client is not None:
        insured = db.get(Insured, client.insured_id)
        if insured is not None:
            client_name, client_rut = insured.legal_name, insured.rut

    doc_counts = {
        (section.value if section is not None else "unfiled"): int(count)
        for section, count in db.execute(
            select(Document.section, func.count(Document.id))
            .where(Document.broker_id == broker_id, Document.case_file_id == case.id)
            .group_by(Document.section)
        ).all()
    }
    children = db.scalars(
        select(CaseFile)
        .where(CaseFile.broker_id == broker_id, CaseFile.parent_case_file_id == case.id)
        .order_by(CaseFile.id)
    ).all()
    options = machine.transition_options(db, case)

    return {
        "id": case.id,
        "reference": case.reference,
        "title": case.title,
        "kind": case.kind.value,
        "stage": case.stage.value,
        "status": case.status.value,
        "period_label": case.period_label,
        "period_start": case.period_start.isoformat() if case.period_start else None,
        "period_end": case.period_end.isoformat() if case.period_end else None,
        "client": {"id": case.client_id, "legal_name": client_name, "rut": client_rut},
        "account_group_id": case.account_group_id,
        "placement_id": case.placement_id,
        "policy_id": case.policy_id,
        "documents_by_section": doc_counts,
        "children": [
            {"id": child.id, "reference": child.reference, "kind": child.kind.value,
             "stage": child.stage.value}
            for child in children
        ],
        "transitions": [
            {"to_stage": option.to_stage.value, "allowed": option.allowed,
             "reason": option.reason}
            for option in options
        ],
        "summary": case.summary,
    }


def _exec_get_case_history(db: Session, *, broker_id: int, user: User, args: GetCaseHistoryArgs) -> dict:
    from app.api.routers.case_files import get_case_file_history

    payload = get_case_file_history(
        args.case_file_id, db=db, broker_id=broker_id, current_user=user
    )
    return _dump(payload)


def _exec_get_quote_comparison(
    db: Session, *, broker_id: int, user: User, args: GetQuoteComparisonArgs
) -> dict:
    from app.api.routers.quotes import compare_proposals

    payload = compare_proposals(
        quote_id=args.quote_request_id,
        db=db,
        broker_id=broker_id,
        _user=user,
        include_rejected=args.include_rejected,
    )
    return _dump(payload)


def _exec_list_documents(db: Session, *, broker_id: int, user: User, args: ListDocumentsArgs) -> dict:
    from app.models.document import Document, DocumentCategory
    from app.models.enums import CaseSection

    def _member(enum_cls, raw: str, label: str):
        try:
            return enum_cls(raw)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Unknown {label} '{raw}'",
            ) from exc

    stmt = select(Document).where(Document.broker_id == broker_id)
    if args.case_file_id is not None:
        stmt = stmt.where(Document.case_file_id == args.case_file_id)
    if args.entity_type and args.entity_id:
        stmt = stmt.where(
            Document.entity_type == _member(EntityType, args.entity_type, "entity_type"),
            Document.entity_id == args.entity_id,
        )
    if args.category:
        stmt = stmt.where(
            Document.category == _member(DocumentCategory, args.category, "category")
        )
    if args.section:
        stmt = stmt.where(
            Document.section == _member(CaseSection, args.section, "section")
        )
    rows = db.scalars(stmt.order_by(Document.id).limit(50)).all()
    return {
        "documents": [
            {
                "id": row.id,
                "category": row.category.value if row.category else None,
                "section": row.section.value if row.section else None,
                "document_code": row.document_code,
                "original_name": row.original_name,
                "mime_type": row.mime_type,
                "case_file_id": row.case_file_id,
                "uploaded_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ],
        "total": len(rows),
    }


# =============================================================================
# WRITE validators / executors / summaries
# =============================================================================


def _validate_create_group(db: Session, *, broker_id: int, user: User, args: CreateGroupArgs) -> None:
    from app.api.routers.account_groups import _resolve_client

    for client_id in args.client_ids or []:
        _resolve_client(db, client_id, broker_id)  # 404 outside the tenant


def _exec_create_group(db: Session, *, broker_id: int, user: User, args: CreateGroupArgs) -> dict:
    from app.api.routers.account_groups import create_account_group
    from app.schemas.account_group import AccountGroupCreate

    detail = create_account_group(
        payload=AccountGroupCreate(
            name=args.name, notes=args.notes, client_ids=args.client_ids
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.ACCOUNT_GROUP.value,
        "entity_id": detail.id,
        "url": f"/groups/{detail.id}",
        "detail": f"Grupo «{detail.name}» creado",
    }


def _summarize_create_group(db: Session, *, broker_id: int, args: CreateGroupArgs) -> str:
    extra = ""
    if args.client_ids:
        count = len(args.client_ids)
        extra = f" y adjuntar {count} cliente{'s' if count != 1 else ''}"
    return f"Crear el grupo «{args.name}»{extra}"


def _gates_create_group(args: CreateGroupArgs) -> list[tuple[str, str]]:
    gates = [("Groups", "Create")]
    if args.client_ids:
        gates.append(("Groups", "Edit"))  # attaching is an Edit, matching the route
    return gates


def _validate_attach_client(
    db: Session, *, broker_id: int, user: User, args: AttachClientToGroupArgs
) -> None:
    from app.api.routers.account_groups import _get_group, _resolve_client

    _get_group(db, args.group_id, broker_id)
    _resolve_client(db, args.client_id, broker_id)


def _exec_attach_client(
    db: Session, *, broker_id: int, user: User, args: AttachClientToGroupArgs
) -> dict:
    from app.api.routers.account_groups import attach_client
    from app.schemas.account_group import AccountGroupAttachClient

    detail = attach_client(
        group_id=args.group_id,
        payload=AccountGroupAttachClient(client_id=args.client_id),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.ACCOUNT_GROUP.value,
        "entity_id": detail.id,
        "url": f"/groups/{detail.id}",
        "detail": f"Cliente adjuntado al grupo «{detail.name}»",
    }


def _summarize_attach_client(
    db: Session, *, broker_id: int, args: AttachClientToGroupArgs
) -> str:
    from app.models.account_group import AccountGroup
    from app.models.client import Client
    from app.models.insured import Insured

    group = db.get(AccountGroup, args.group_id)
    group_name = group.name if group is not None and group.broker_id == broker_id else f"#{args.group_id}"
    client_name = f"#{args.client_id}"
    client = db.get(Client, args.client_id)
    if client is not None and client.broker_id == broker_id:
        insured = db.get(Insured, client.insured_id)
        if insured is not None:
            client_name = insured.legal_name
    return f"Adjuntar el cliente {client_name} al grupo «{group_name}»"


def _validate_create_case_file(
    db: Session, *, broker_id: int, user: User, args: CreateCaseFileArgs
) -> None:
    from app.api.routers.account_groups import _get_group
    from app.api.routers.case_files import _resolve_client

    _resolve_client(db, args.client_id, broker_id)
    if args.group_id is not None:
        _get_group(db, args.group_id, broker_id)
    if args.period_start is None or args.period_end is None:
        # The route would refuse this at confirm; refuse the doomed proposal now.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                "period_start and period_end are required for an account case "
                "file: the vigencia IS the folder"
            ),
        )


def _exec_create_case_file(
    db: Session, *, broker_id: int, user: User, args: CreateCaseFileArgs
) -> dict:
    from app.api.routers.case_files import create_case_file
    from app.models.case_file import CaseFileKind
    from app.schemas.case_file import CaseFileCreate

    detail = create_case_file(
        payload=CaseFileCreate(
            kind=CaseFileKind.ACCOUNT,
            client_id=args.client_id,
            title=args.title,
            insurance_line_id=args.insurance_line_id,
            account_group_id=args.group_id,
            period_start=args.period_start,
            period_end=args.period_end,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Expediente {detail.reference or detail.id} creado",
    }


def _summarize_create_case_file(
    db: Session, *, broker_id: int, args: CreateCaseFileArgs
) -> str:
    return f"Abrir el expediente «{args.title}» para el cliente #{args.client_id}"


def _validate_renew_case(db: Session, *, broker_id: int, user: User, args: RenewCaseArgs) -> None:
    from app.api.routers.case_files import _get_case

    _get_case(db, args.case_file_id, broker_id, user)


def _exec_renew_case(db: Session, *, broker_id: int, user: User, args: RenewCaseArgs) -> dict:
    from app.api.routers.case_files import renew_case_file
    from app.schemas.case_file import CaseRenewBody

    detail = renew_case_file(
        case_id=args.case_file_id,
        payload=CaseRenewBody(
            period_start=args.period_start,
            period_end=args.period_end,
            period_label=args.period_label,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    if args.note:
        from app.api.routers.notes import create_note
        from app.schemas.note import NoteCreate

        create_note(
            payload=NoteCreate(
                entity_type=EntityType.CASE_FILE, entity_id=detail.id, body=args.note
            ),
            db=db,
            broker_id=broker_id,
            current_user=user,
        )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Renovación abierta como {detail.reference or detail.id}",
    }


def _summarize_renew_case(db: Session, *, broker_id: int, args: RenewCaseArgs) -> str:
    from app.models.case_file import CaseFile

    case = db.get(CaseFile, args.case_file_id)
    label = (
        case.reference or f"#{args.case_file_id}"
        if case is not None and case.broker_id == broker_id
        else f"#{args.case_file_id}"
    )
    return (
        f"Abrir la renovación de {label} para la vigencia "
        f"{args.period_start.isoformat()} → {args.period_end.isoformat()}"
    )


def _note_entity_type(raw: str) -> EntityType:
    try:
        return EntityType(raw)
    except ValueError as exc:
        raise ValueError(f"Notes cannot be attached to '{raw}'") from exc


def _gates_add_note(args: AddNoteArgs) -> list[tuple[str, str]]:
    from app.api.routers.notes import MODULE_BY_ENTITY

    entity_type = _note_entity_type(args.entity_type)
    module = MODULE_BY_ENTITY.get(entity_type)
    if module is None:
        raise ValueError(f"Notes cannot be attached to '{args.entity_type}'")
    return [(module, "Comment")]


def _validate_add_note(db: Session, *, broker_id: int, user: User, args: AddNoteArgs) -> None:
    from app.api.routers.notes import _resolve_target

    _resolve_target(db, _note_entity_type(args.entity_type), args.entity_id, broker_id)


def _exec_add_note(db: Session, *, broker_id: int, user: User, args: AddNoteArgs) -> dict:
    from app.api.routers.notes import create_note
    from app.schemas.note import NoteCreate

    note = create_note(
        payload=NoteCreate(
            entity_type=_note_entity_type(args.entity_type),
            entity_id=args.entity_id,
            body=args.body,
            is_internal=args.is_internal,
            follow_up_on=args.follow_up_on,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": args.entity_type,
        "entity_id": args.entity_id,
        "url": (f"/cases/{args.entity_id}" if args.entity_type == "case_file" else None),
        "detail": f"Nota agregada (nota #{note.id})",
        "note_id": note.id,
    }


def _summarize_add_note(db: Session, *, broker_id: int, args: AddNoteArgs) -> str:
    body = args.body if len(args.body) <= 80 else args.body[:77] + "…"
    return f"Agregar una nota a {args.entity_type} #{args.entity_id}: «{body}»"


# =============================================================================
# v12 — policies, cobranza, journeys, share links, portals, skills
# (docs/v12/05-agent.md). Every executor calls the lane's own router function
# or service, so a confirmed action is the exact transaction the UI performs.
# Router functions are called directly: EVERY parameter is passed explicitly,
# because an omitted ``Query(...)`` default would arrive as a FieldInfo.
# =============================================================================

_POLICY_LIMIT = 20
_INSTALLMENT_LIMIT = 30
_LIST_LIMIT = 20

_POLICY_STATUS = Literal["upcoming", "active", "expired", "cancelled", "draft", "renewed"]
_COLLECTION_FILTER = Literal["overdue", "due_soon", "no_plan", "ok"]
_MISSING_KEY = Literal[
    "source_document", "policy_number", "plan", "gross_premium",
    "payer_contact", "premiums", "commission",
]
_BUCKET = Literal["upcoming", "overdue", "current", "settled"]
_LEVEL = Literal["record", "assisted", "native"]

#: Spanish labels of the policy fields the agent may complete (card copy).
_POLICY_FIELD_ES = {
    "policy_number": "n° de póliza",
    "start_date": "inicio de vigencia",
    "end_date": "término de vigencia",
    "issued_at": "fecha de emisión",
    "insured_amount_uf": "monto asegurado",
    "taxable_premium_uf": "prima afecta",
    "exempt_premium_uf": "prima exenta",
    "net_premium_uf": "prima neta",
    "vat_uf": "IVA",
    "total_premium_uf": "prima total",
    "commission_pct": "% de comisión",
    "payment_method": "método de pago",
    "insurance_subtype_id": "subtipo",
    "notes": "notas",
}

#: Money in the tool schemas: validated as Decimal (never float), advertised to
#: the model as a plain number (Pydantic's Decimal regex is pure token noise).
_UF = Annotated[Decimal, Field(ge=0), WithJsonSchema({"type": "number", "minimum": 0})]
_POSITIVE = Annotated[Decimal, Field(gt=0), WithJsonSchema({"type": "number", "exclusiveMinimum": 0})]
_PCT = Annotated[
    Decimal, Field(ge=0, le=100), WithJsonSchema({"type": "number", "minimum": 0, "maximum": 100})
]

_AUDIENCE_ES = {"insured": "el asegurado", "insurer": "la compañía"}
_SHARE_ENTITY_ES = {
    "policy": "la póliza",
    "collection_plan": "el plan de pago",
    "installment": "la cuota",
    "case_file": "la cuenta",
}


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so Decimal/date/Enum fold like the API renders."""
    return _strip_keys(json.loads(json.dumps(value, ensure_ascii=False, default=str)))


def _pick(data: dict, keys: tuple[str, ...]) -> dict:
    return {key: data.get(key) for key in keys if key in data}


def _fmt_day(value: date | None) -> str:
    return value.strftime("%d-%m-%Y") if value else "—"


def _unprocessable(detail: Any) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=detail)


def _own_policy(db: Session, broker_id: int, policy_id: int):
    from app.models.policy import Policy

    policy = db.get(Policy, policy_id)
    return policy if policy is not None and policy.broker_id == broker_id else None


def _policy_label(db: Session, broker_id: int, policy_id: int | None) -> str:
    policy = _own_policy(db, broker_id, policy_id) if policy_id else None
    if policy is None:
        return f"#{policy_id}"
    from app.services import policy_registry as registry

    if registry.is_placeholder_number(policy.policy_number):
        return f"#{policy.id} (sin n°)"
    return f"N° {policy.policy_number}"


def _own_installment(db: Session, broker_id: int, installment_id: int):
    from app.models.collection import CollectionInstallment

    inst = db.get(CollectionInstallment, installment_id)
    return inst if inst is not None and inst.broker_id == broker_id else None


def _installment_label(db: Session, broker_id: int, installment_id: int) -> str:
    inst = _own_installment(db, broker_id, installment_id)
    if inst is None:
        return f"la cuota #{installment_id}"
    policy_id = inst.collection_plan.policy_id if inst.collection_plan else None
    return f"la cuota {inst.number} de la póliza {_policy_label(db, broker_id, policy_id)}"


def _insurer_label(db: Session, insurer_id: int | None) -> str:
    from app.models.insurer import Insurer

    insurer = db.get(Insurer, insurer_id) if insurer_id else None
    if insurer is None:
        return f"la compañía #{insurer_id}"
    return insurer.trade_name or insurer.legal_name


def _client_label(db: Session, broker_id: int, client_id: int | None) -> str | None:
    from app.models.client import Client
    from app.models.insured import Insured

    client = db.get(Client, client_id) if client_id else None
    if client is None or client.broker_id != broker_id:
        return None
    insured = db.get(Insured, client.insured_id)
    return insured.legal_name if insured is not None else None


# --- args -----------------------------------------------------------------------


class ListPoliciesArgs(_Args):
    q: str | None = Field(default=None, max_length=120)
    status: list[_POLICY_STATUS] | None = None
    insurer_id: int | None = Field(default=None, gt=0)
    client_id: int | None = Field(default=None, gt=0)
    line_code: str | None = Field(default=None, max_length=8)
    subtype_id: int | None = Field(default=None, gt=0)
    collection: list[_COLLECTION_FILTER] | None = None
    missing: list[_MISSING_KEY] | None = None
    account_group_id: int | None = Field(default=None, gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    owner_me: bool = False
    limit: int = Field(default=_POLICY_LIMIT, ge=1)


class PolicyIdArgs(_Args):
    policy_id: int = Field(..., gt=0)


class ListInstallmentsArgs(_Args):
    bucket: _BUCKET | None = None
    level: _LEVEL | None = None
    insurer_id: int | None = Field(default=None, gt=0)
    line_code: str | None = Field(default=None, max_length=8)
    policy_id: int | None = Field(default=None, gt=0)
    q: str | None = Field(default=None, max_length=120)
    owner_me: bool = False
    limit: int = Field(default=_INSTALLMENT_LIMIT, ge=1)


class EmptyArgs(_Args):
    pass


class InstallmentIdArgs(_Args):
    installment_id: int = Field(..., gt=0)


class ListStatusInquiriesArgs(_Args):
    status: Literal["draft", "sent", "replied", "resolved", "failed", "cancelled"] | None = None
    policy_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=_LIST_LIMIT, ge=1)


class ListPendingEvidenceArgs(_Args):
    installment_id: int | None = Field(default=None, gt=0)
    limit: int = Field(default=_LIST_LIMIT, ge=1)


class CheckCaseFilesArgs(_Args):
    case_file_id: int = Field(..., gt=0)


class GetCatalogArgs(_Args):
    line_code: str | None = Field(default=None, max_length=8)


class ListPortalSearchesArgs(_Args):
    case_file_id: int | None = Field(default=None, gt=0)
    status: Literal["queued", "running", "succeeded", "failed", "cancelled"] | None = None
    limit: int = Field(default=_LIST_LIMIT, ge=1)


class PortalSearchIdArgs(_Args):
    run_id: int = Field(..., gt=0)


class ListNotificationsArgs(_Args):
    tray: Literal["todo", "fyi"] | None = None
    limit: int = Field(default=_LIST_LIMIT, ge=1)


class LoadSkillArgs(_Args):
    key: str = Field(..., min_length=1, max_length=64)


class _PartyArgs(_Args):
    name: str = Field(..., min_length=1, max_length=255)
    rut: str | None = Field(default=None, max_length=20)
    email: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=64)

    @field_validator("rut")
    @classmethod
    def _rut(cls, value: str | None) -> str | None:
        from app.schemas.policy import _normalize_optional_rut

        return _normalize_optional_rut(value)


class CreatePolicyManualArgs(_Args):
    client_id: int | None = Field(default=None, gt=0)
    insured_rut: str | None = Field(default=None, max_length=20)
    insured_name: str | None = Field(default=None, min_length=1, max_length=255)
    insurer_id: int = Field(..., gt=0)
    insurance_subtype_id: int = Field(..., gt=0)
    start_date: date
    end_date: date
    policy_number: str | None = Field(default=None, min_length=1, max_length=64)
    insured_amount_uf: _UF | None = None
    taxable_premium_uf: _UF | None = None
    exempt_premium_uf: _UF | None = None
    net_premium_uf: _UF | None = None
    vat_uf: _UF | None = None
    total_premium_uf: _UF | None = None
    commission_pct: _PCT | None = None
    payment_method: PaymentMode | None = None
    currency: PolicyCurrency = PolicyCurrency.UF
    is_provisional: bool = False
    case_file_id: int | None = Field(default=None, gt=0)
    policyholder: _PartyArgs | None = None
    payer: _PartyArgs | None = None
    notes: str | None = Field(default=None, max_length=4000)

    @field_validator("insured_rut")
    @classmethod
    def _insured_rut(cls, value: str | None) -> str | None:
        from app.schemas.policy import _normalize_optional_rut

        return _normalize_optional_rut(value)

    @model_validator(mode="after")
    def _insured_and_period(self) -> "CreatePolicyManualArgs":
        if self.client_id is None and not (self.insured_rut and self.insured_name):
            raise ValueError("Indica el asegurado: client_id, o insured_rut + insured_name")
        if self.end_date <= self.start_date:
            raise ValueError("La vigencia debe terminar después de su inicio")
        return self


class UpdatePolicyFieldsArgs(_Args):
    # Completar: fill EMPTY fields only. A set field is corrected in the ficha.
    policy_id: int = Field(..., gt=0)
    policy_number: str | None = Field(default=None, min_length=1, max_length=64)
    start_date: date | None = None
    end_date: date | None = None
    issued_at: date | None = None
    insured_amount_uf: _UF | None = None
    taxable_premium_uf: _UF | None = None
    exempt_premium_uf: _UF | None = None
    net_premium_uf: _UF | None = None
    vat_uf: _UF | None = None
    total_premium_uf: _UF | None = None
    commission_pct: _PCT | None = None
    payment_method: PaymentMode | None = None
    insurance_subtype_id: int | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=4000)

    def fields(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True, exclude={"policy_id"})

    @model_validator(mode="after")
    def _at_least_one(self) -> "UpdatePolicyFieldsArgs":
        if not self.fields():
            raise ValueError("Indica al menos un dato a completar")
        return self


class RegisterPaymentArgs(_Args):
    installment_id: int = Field(..., gt=0)
    amount: _POSITIVE
    currency: PaymentCurrency = PaymentCurrency.UF
    paid_on: date
    method: PaymentMode | None = None
    confirmed_by_insurer: bool = False
    receipt_document_id: int | None = Field(default=None, gt=0)
    note: str | None = Field(default=None, max_length=4000)


class RecordPaymentPromiseArgs(_Args):
    installment_id: int = Field(..., gt=0)
    promise_until: date
    note: str | None = Field(default=None, max_length=4000)


class StartCollectionArgs(_Args):
    plan_id: int | None = Field(default=None, gt=0)
    policy_id: int | None = Field(default=None, gt=0)
    level_preference: Literal["auto", "assisted"] | None = None

    @model_validator(mode="after")
    def _anchor(self) -> "StartCollectionArgs":
        if self.plan_id is None and self.policy_id is None:
            raise ValueError("Indica el plan de pago (plan_id) o la póliza (policy_id)")
        return self


class SendStatusInquiryArgs(_Args):
    policy_id: int = Field(..., gt=0)
    # Empty -> every open cuota of the policy, resolved at proposal time so
    # the card names exactly what will be asked.
    installment_ids: list[int] | None = Field(default=None, max_length=60)
    to_address: str | None = Field(default=None, max_length=255)
    message: str | None = Field(default=None, max_length=4000)


class SendPaymentReminderArgs(_Args):
    installment_id: int = Field(..., gt=0)
    kind: Literal["payer_reminder", "payer_overdue"] = "payer_reminder"


class CreateShareLinkArgs(_Args):
    entity_type: Literal["policy", "collection_plan", "installment", "case_file"] = "policy"
    entity_id: int = Field(..., gt=0)
    audience: Literal["insured", "insurer"]
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    label: str | None = Field(default=None, max_length=160)


class CreatePaymentLinkArgs(_Args):
    installment_ids: list[int] = Field(..., min_length=1, max_length=50)


class SkipCaseToStageArgs(_Args):
    case_file_id: int = Field(..., gt=0)
    target_stage: Literal["quotes_received", "insured_decision", "policy_issued", "active"]
    note: str | None = Field(default=None, max_length=2000)


class ActivateCaseStageArgs(_Args):
    case_file_id: int = Field(..., gt=0)
    stage: str = Field(..., min_length=1, max_length=40)
    note: str | None = Field(default=None, max_length=2000)


class RunPortalSearchArgs(_Args):
    portal: str = Field(default="hdi", min_length=1, max_length=32)
    insurance_subtype_id: int = Field(..., gt=0)
    case_file_id: int | None = Field(default=None, gt=0)
    input: dict[str, Any]


# --- READ executors -------------------------------------------------------------

_POLICY_ROW_KEYS = (
    "id", "policy_number", "has_policy_number", "insured_name", "insured_rut",
    "insurer_id", "insurer_name", "start_date", "end_date", "computed_status",
    "total_premium_uf", "net_premium_uf", "commission_pct", "currency", "origin",
    "case_file_id", "group_id", "missing",
)


def _policy_row(row: Any) -> dict:
    data = json.loads(row.model_dump_json())
    out = _pick(data, _POLICY_ROW_KEYS)
    out["line"] = (data.get("line") or {}).get("name")
    subtype = data.get("subtype") or {}
    out["subtype"] = f"{subtype.get('code')} {subtype.get('name')}" if subtype else None
    summary = data.get("collection_summary") or {}
    out["collection"] = _pick(
        summary,
        ("state", "next_due_date", "next_due_amount_uf", "overdue_count", "overdue_uf", "level"),
    )
    return out


def _exec_list_policies(db: Session, *, broker_id: int, user: User, args: ListPoliciesArgs) -> dict:
    from app.api.routers.policies import list_policies
    from app.services.analytics import ScopeFilters

    page = list_policies(
        db=db,
        broker_id=broker_id,
        current_user=user,
        client_id=args.client_id,
        insurer_id=args.insurer_id,
        placement_id=None,
        scope=ScopeFilters(account_group_id=args.account_group_id, case_file_id=args.case_file_id),
        status_filter=list(args.status) if args.status else None,
        q=args.q,
        line_code=args.line_code,
        subtype_id=args.subtype_id,
        collection=list(args.collection) if args.collection else None,
        origin=None,
        missing=list(args.missing) if args.missing else None,
        owner="me" if args.owner_me else None,
        limit=min(args.limit, _POLICY_LIMIT),
        offset=0,
    )
    return {
        "total": page.total,
        "shown": len(page.items),
        "policies": [_policy_row(item) for item in page.items],
    }


_POLICY_DETAIL_DROP = {
    "broker_id", "payload", "core_validation", "period_start_at", "period_end_at",
    "created_at", "updated_at", "extraction_id",
}


def _exec_get_policy(db: Session, *, broker_id: int, user: User, args: PolicyIdArgs) -> dict:
    from app.api.routers.policies import get_policy
    from app.services import policy_registry as registry

    detail = get_policy(args.policy_id, db=db, broker_id=broker_id, _user=user)
    data = {k: v for k, v in _dump(detail).items() if k not in _POLICY_DETAIL_DROP}
    data["missing_reasons"] = {
        key: registry._REASONS.get(key) for key in (detail.missing or [])
    }
    return data


_INSTALLMENT_ROW_KEYS = (
    "id", "plan_id", "policy_id", "policy_number", "number", "installment_count",
    "due_date", "currency", "gross_amount_uf", "paid_uf", "balance_uf", "balance_clp",
    "state", "flags", "days_late", "evidence", "paid_on", "level", "level_reason",
    "level_missing", "promise_until", "direct_debit_rejected_at", "insured_name",
    "insurer_id", "insurer_name", "line_name", "payment_mode", "notices_enabled",
)


def _installment_rows(page: Any) -> list[dict]:
    rows = []
    for item in page.items:
        data = json.loads(item.model_dump_json())
        row = _pick(data, _INSTALLMENT_ROW_KEYS)
        note = data.get("last_note") or {}
        if note:
            body = str(note.get("body") or "")
            row["last_note"] = body if len(body) <= 140 else body[:137] + "…"
        rows.append(row)
    return rows


def _board_page(db: Session, *, broker_id: int, user: User, **filters: Any):
    from app.api.routers.collections import list_board_installments

    params = {
        "bucket": None, "insurer_id": None, "line_code": None, "method": None,
        "level": None, "owner": None, "q": None, "policy_id": None, "plan_id": None,
        "limit": _INSTALLMENT_LIMIT, "offset": 0,
    }
    params.update(filters)
    return list_board_installments(db=db, broker_id=broker_id, current_user=user, **params)


def _exec_list_installments(
    db: Session, *, broker_id: int, user: User, args: ListInstallmentsArgs
) -> dict:
    page = _board_page(
        db, broker_id=broker_id, user=user,
        bucket=args.bucket, level=args.level, insurer_id=args.insurer_id,
        line_code=args.line_code, policy_id=args.policy_id, q=args.q,
        owner="me" if args.owner_me else None,
        limit=min(args.limit, _INSTALLMENT_LIMIT),
    )
    return {
        "as_of": page.as_of.isoformat(),
        "uf_rate": str(page.uf_rate) if page.uf_rate is not None else None,
        "total": page.total,
        "shown": len(page.items),
        "counts": page.counts,
        "installments": _installment_rows(page),
    }


def _exec_get_collection_overview(
    db: Session, *, broker_id: int, user: User, args: EmptyArgs
) -> dict:
    from app.api.routers.collections import get_collections_summary
    from app.services.analytics import ScopeFilters

    summary = get_collections_summary(
        db=db, broker_id=broker_id, _user=user, scope=ScopeFilters(), group_by=None
    )
    data = _dump(summary)
    cards = _pick(
        data,
        (
            "as_of", "due_7d", "due_30d", "overdue", "termination_risk",
            "collected_month_uf", "unconfirmed_payments", "pending_evidence",
            "plans_missing", "total", "by_status",
        ),
    )
    top = _board_page(db, broker_id=broker_id, user=user, bucket="overdue", limit=5)
    return {
        "summary": cards,
        "pending_evidence": summary.pending_evidence,
        "top_overdue": _installment_rows(top),
        "overdue_total": top.total,
    }


def _exec_get_collection_mode(
    db: Session, *, broker_id: int, user: User, args: InstallmentIdArgs
) -> dict:
    from app.api.routers.collections import get_installment_mode

    mode = get_installment_mode(args.installment_id, db=db, broker_id=broker_id, _user=user)
    return {
        "installment_id": args.installment_id,
        "installment": _installment_label(db, broker_id, args.installment_id),
        **_dump(mode),
    }


_INQUIRY_KEYS = (
    "id", "token", "status", "policy_id", "policy_number", "insured_name",
    "insurer_name", "to_address", "installments", "mail_status", "sent_at",
    "replied_at", "resolved_at", "created_by_job", "created_at", "evidence_open",
)


def _exec_list_status_inquiries(
    db: Session, *, broker_id: int, user: User, args: ListStatusInquiriesArgs
) -> dict:
    from app.api.routers.inquiries import list_status_inquiries
    from app.models.enums import InquiryStatus

    page = list_status_inquiries(
        status_filter=InquiryStatus(args.status) if args.status else None,
        policy_id=args.policy_id,
        limit=min(args.limit, _LIST_LIMIT),
        offset=0,
        db=db,
        broker_id=broker_id,
        _user=user,
    )
    return {
        "total": page.total,
        "inquiries": [_pick(_dump(item), _INQUIRY_KEYS) for item in page.items],
    }


_EVIDENCE_KEYS = (
    "id", "status", "source", "installment_id", "installment_number",
    "installment_due_date", "installment_amount_uf", "policy_id", "policy_number",
    "insurer_name", "status_inquiry_id", "mail_subject", "mail_from",
    "reported_status", "reported_amount", "reported_currency", "reported_paid_on",
    "score", "sender_verified", "note", "created_at",
)


def _exec_list_pending_evidence(
    db: Session, *, broker_id: int, user: User, args: ListPendingEvidenceArgs
) -> dict:
    from app.api.routers.inquiries import list_evidence

    page = list_evidence(
        status_filter=None,
        inquiry_id=None,
        installment_id=args.installment_id,
        limit=min(args.limit, _LIST_LIMIT),
        offset=0,
        db=db,
        broker_id=broker_id,
        _user=user,
    )
    return {
        "total": page.total,
        "evidence": [_pick(_dump(item), _EVIDENCE_KEYS) for item in page.items],
    }


def _exec_check_case_files(
    db: Session, *, broker_id: int, user: User, args: CheckCaseFilesArgs
) -> dict:
    from app.api.routers.case_files import _detail as case_detail
    from app.api.routers.case_files import _get_case
    from app.models.catalog import InsuranceSubtype
    from app.services.expediente import _antecedentes

    case = _get_case(db, args.case_file_id, broker_id, user)
    detail = case_detail(db, case)
    antecedentes, _documents = _antecedentes(db, case, broker_id)
    subtype = (
        db.get(InsuranceSubtype, case.insurance_subtype_id)
        if getattr(case, "insurance_subtype_id", None)
        else None
    )
    slots = [
        {
            "label": slot.label,
            "category": slot.category,
            "required": slot.required,
            "filled": slot.filled,
            "documents_count": slot.documents_count,
        }
        for slot in antecedentes.slots
    ]
    stages = [
        {"stage": row.stage.value, "state": row.state,
         "entered_at": row.entered_at.isoformat() if row.entered_at else None}
        for row in detail.stages
    ]
    return {
        "case_file_id": case.id,
        "reference": case.reference,
        "kind": case.kind.value,
        "status": case.status.value,
        "stage": case.stage.value,
        "entry_origin": getattr(getattr(case, "entry_origin", None), "value", None),
        "route": getattr(getattr(case, "route", None), "value", getattr(case, "route", None)),
        "subtype": f"{subtype.code} {subtype.name}" if subtype is not None else None,
        "ramo": antecedentes.ramo_name,
        "has_policy": detail.has_policy,
        "stages": stages,
        "skipped_stages": [row["stage"] for row in stages if row["state"] == "skipped"],
        "skip_targets": [stage.value for stage in detail.skip_targets],
        "recommended_files": slots,
        "recommended_total": antecedentes.recommended_total,
        "recommended_filled": antecedentes.recommended_filled,
        "missing_required": antecedentes.missing_required,
        "missing_optional": [s["label"] for s in slots if not s["filled"] and not s["required"]],
        "free_uploads_count": antecedentes.free_uploads_count,
        "documents_count": antecedentes.documents_count,
        "extractions_count": antecedentes.extractions_count,
        "antecedentes_status": antecedentes.record_status,
        "documents_by_section": _jsonable([s.model_dump() for s in detail.documents_by_section]),
    }


def _exec_check_policy_files(
    db: Session, *, broker_id: int, user: User, args: PolicyIdArgs
) -> dict:
    from app.api.routers.policies import get_policy_or_404
    from app.models.document import Document
    from app.services import policy_registry as registry
    from app.services.collection.resolver import payer_contact_email

    policy = get_policy_or_404(db, args.policy_id, broker_id)
    missing = registry.compute_policy_missing(db, policy)
    operability = registry.policy_operability(db, policy, missing)
    installments = sum(len(plan.installments) for plan in (policy.collection_plans or []))
    documents = int(
        db.scalar(
            select(func.count(Document.id)).where(
                Document.broker_id == broker_id,
                Document.entity_type == EntityType.POLICY,
                Document.entity_id == policy.id,
            )
        )
        or 0
    )
    return {
        "policy_id": policy.id,
        "policy": _policy_label(db, broker_id, policy.id),
        "has_source_document": bool(policy.has_source_document),
        "source_document_id": policy.source_document_id,
        "documents_count": documents,
        "has_plan": installments > 0,
        "installments_count": installments,
        "payer_email": payer_contact_email(policy),
        "commission_pct": str(policy.commission_pct) if policy.commission_pct is not None else None,
        "missing": [{"key": key, "reason": registry._REASONS.get(key)} for key in missing],
        "features": [_pick(row, ("feature", "enabled", "reason")) for row in operability],
    }


def _exec_get_catalog(db: Session, *, broker_id: int, user: User, args: GetCatalogArgs) -> dict:
    from app.services import catalog as catalog_service

    wanted = (args.line_code or "").strip().upper() or None
    lines = []
    for line in catalog_service.catalog_lines(db):
        if wanted and (line.code or "").upper() != wanted:
            continue
        subtypes = sorted((s for s in line.subtypes if s.is_active), key=lambda s: (s.sort_order, s.id))
        lines.append(
            {
                "code": line.code,
                "name": line.name,
                "subtypes": [
                    {
                        "id": s.id,
                        "code": s.code,
                        "name": s.name,
                        "suggested_route": getattr(s.suggested_route, "value", s.suggested_route),
                    }
                    for s in subtypes
                ],
            }
        )
    return {"lines": lines, "total": len(lines)}


def _exec_list_portal_searches(
    db: Session, *, broker_id: int, user: User, args: ListPortalSearchesArgs
) -> dict:
    from app.api.routers.portal_searches import list_runs
    from app.models.enums import PortalRunStatus

    page = list_runs(
        case_file_id=args.case_file_id,
        portal=None,
        status_filter=PortalRunStatus(args.status) if args.status else None,
        limit=min(args.limit, _LIST_LIMIT),
        offset=0,
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {"total": page.total, "runs": [_dump(item) for item in page.items]}


def _exec_get_portal_search(
    db: Session, *, broker_id: int, user: User, args: PortalSearchIdArgs
) -> dict:
    from app.api.routers.portal_searches import get_run

    data = _dump(get_run(args.run_id, db=db, broker_id=broker_id, current_user=user))
    steps = data.get("steps") or []
    if len(steps) > 10:
        data["steps"] = steps[-10:]
    data.pop("screenshot_url", None)
    return data


def _exec_list_notifications(
    db: Session, *, broker_id: int, user: User, args: ListNotificationsArgs
) -> dict:
    from app.api.routers.notifications import list_notifications
    from app.models.enums import NotificationTray

    page = list_notifications(
        tray=NotificationTray(args.tray) if args.tray else None,
        status_filter=None,
        limit=min(args.limit, _LIST_LIMIT),
        offset=0,
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    keys = ("id", "tray", "kind", "level", "status", "origin_type", "origin_id",
            "params", "link_path", "updated_at")
    return {"total": page.total, "notifications": [_pick(_dump(n), keys) for n in page.items]}


def _exec_load_skill(
    db: Session, *, broker_id: int, user: User, args: LoadSkillArgs, thread: Any = None
) -> dict:
    from app.services import agent_skills

    spec = agent_skills.get_skill(args.key)
    if spec is None:
        raise _unprocessable(
            f"Habilidad desconocida: {args.key}. Disponibles: " + ", ".join(agent_skills.SKILLS)
        )
    active = agent_skills.active_keys(getattr(thread, "active_skills", None))
    if thread is not None and spec.key not in active:
        active = [*active, spec.key]
        thread.active_skills = active  # a NEW list: JSON columns track by identity
    return {
        "key": spec.key,
        "title": spec.title,
        "instructions": spec.instructions,
        "tools": list(spec.tools),
        "active_skills": active,
    }


# --- WRITE validators / executors / summaries --------------------------------------


def _manual_policy_payload(args: CreatePolicyManualArgs):
    from app.schemas.policy import PartyContact, PolicyCreate

    def _party(party: _PartyArgs | None):
        return PartyContact(**party.model_dump()) if party is not None else None

    return PolicyCreate(
        client_id=args.client_id,
        insured_rut=args.insured_rut if args.client_id is None else None,
        insured_name=args.insured_name if args.client_id is None else None,
        insurer_id=args.insurer_id,
        insurance_subtype_id=args.insurance_subtype_id,
        start_date=args.start_date,
        end_date=args.end_date,
        policy_number=args.policy_number,
        insured_amount_uf=args.insured_amount_uf,
        taxable_premium_uf=args.taxable_premium_uf,
        exempt_premium_uf=args.exempt_premium_uf,
        net_premium_uf=args.net_premium_uf,
        vat_uf=args.vat_uf,
        total_premium_uf=args.total_premium_uf,
        commission_pct=args.commission_pct,
        payment_method=args.payment_method,
        currency=args.currency,
        is_provisional=args.is_provisional,
        case_file_id=args.case_file_id,
        policyholder=_party(args.policyholder),
        payer=_party(args.payer),
        notes=args.notes,
    )


def _validate_create_policy_manual(
    db: Session, *, broker_id: int, user: User, args: CreatePolicyManualArgs
) -> None:
    from app.api.routers.policies import (
        _check_manual_folder,
        _number_taken,
        _resolve_insurer,
        _resolve_own_case,
        _resolve_subtype,
    )
    from app.models.client import Client
    from app.schemas.proposal import reconcile_money

    subtype = _resolve_subtype(db, args.insurance_subtype_id)
    _resolve_insurer(db, args.insurer_id, broker_id)
    client = None
    if args.client_id is not None:
        client = db.get(Client, args.client_id)
        if client is None or client.broker_id != broker_id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Client not found")
    if args.case_file_id is not None:
        case = _resolve_own_case(db, args.case_file_id, broker_id)
        if client is not None:
            _check_manual_folder(db, case, client=client, subtype=subtype, start_date=args.start_date)
    number = (args.policy_number or "").strip()
    if number and _number_taken(db, broker_id, number):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una póliza con el n° '{number}' en esta corredora.",
        )
    _derived, errors = reconcile_money(
        {
            "taxable_premium_uf": args.taxable_premium_uf,
            "exempt_premium_uf": args.exempt_premium_uf,
            "net_premium_uf": args.net_premium_uf,
            "vat_uf": args.vat_uf,
            "total_premium_uf": args.total_premium_uf,
        }
    )
    if errors:
        raise _unprocessable(errors)


def _exec_create_policy_manual(
    db: Session, *, broker_id: int, user: User, args: CreatePolicyManualArgs
) -> dict:
    from app.api.routers.policies import create_policy

    detail = create_policy(
        payload=_manual_policy_payload(args), db=db, broker_id=broker_id, current_user=user
    )
    return {
        "entity_type": EntityType.POLICY.value,
        "entity_id": detail.id,
        "url": f"/policies/{detail.id}",
        "detail": f"Póliza {detail.policy_number} registrada a mano",
        "warnings": list(detail.warnings or []),
        "missing": list(detail.missing or []),
    }


def _summarize_create_policy_manual(
    db: Session, *, broker_id: int, args: CreatePolicyManualArgs
) -> str:
    from app.models.catalog import InsuranceSubtype

    insured = _client_label(db, broker_id, args.client_id) or (
        f"{args.insured_name} ({args.insured_rut})" if args.insured_name else f"#{args.client_id}"
    )
    subtype = db.get(InsuranceSubtype, args.insurance_subtype_id)
    subtype_label = f"{subtype.code} {subtype.name}" if subtype is not None else "subtipo"
    number = f"n° {args.policy_number}" if args.policy_number else "sin n°"
    total = f", prima total UF {args.total_premium_uf}" if args.total_premium_uf is not None else ""
    return (
        f"Registrar a mano la póliza {number} de {insured} con "
        f"{_insurer_label(db, args.insurer_id)} ({subtype_label}), vigencia "
        f"{_fmt_day(args.start_date)} → {_fmt_day(args.end_date)}{total}"
    )


def _validate_update_policy_fields(
    db: Session, *, broker_id: int, user: User, args: UpdatePolicyFieldsArgs
) -> None:
    from app.api.routers.policies import (
        _MONEY_FIELDS,
        _is_empty,
        _number_taken,
        _post_write_money,
        _resolve_subtype,
        _same,
        get_policy_or_404,
    )

    policy = get_policy_or_404(db, args.policy_id, broker_id)
    fields = args.fields()
    taken = [
        name
        for name, value in fields.items()
        if not _is_empty(name, getattr(policy, name)) and not _same(getattr(policy, name), value)
    ]
    if taken:
        labels = ", ".join(_POLICY_FIELD_ES.get(name, name) for name in sorted(taken))
        raise _unprocessable(
            f"La póliza ya tiene {labels}: el agente solo completa datos vacíos. "
            "Para cambiar un dato registrado usa Corregir dato en la ficha, con su motivo."
        )
    if args.insurance_subtype_id is not None:
        _resolve_subtype(db, args.insurance_subtype_id)
    if args.policy_number and _number_taken(
        db, broker_id, args.policy_number.strip(), exclude_id=policy.id
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Ya existe una póliza con el n° '{args.policy_number.strip()}' en esta corredora.",
        )
    money = {name: value for name, value in fields.items() if name in _MONEY_FIELDS}
    if money:
        _post_write_money(policy, money)  # pure: 422 when the arithmetic breaks


def _exec_update_policy_fields(
    db: Session, *, broker_id: int, user: User, args: UpdatePolicyFieldsArgs
) -> dict:
    from app.api.routers.policies import update_policy
    from app.schemas.policy import PolicyUpdate

    fields = args.fields()
    detail = update_policy(
        args.policy_id,
        payload=PolicyUpdate(**fields),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.POLICY.value,
        "entity_id": detail.id,
        "url": f"/policies/{detail.id}",
        "detail": "Póliza completada: " + ", ".join(
            _POLICY_FIELD_ES.get(name, name) for name in sorted(fields)
        ),
        "missing": list(detail.missing or []),
    }


def _summarize_update_policy_fields(
    db: Session, *, broker_id: int, args: UpdatePolicyFieldsArgs
) -> str:
    parts = []
    for name, value in sorted(args.fields().items()):
        label = _POLICY_FIELD_ES.get(name, name)
        if isinstance(value, date):
            value = _fmt_day(value)
        elif hasattr(value, "value"):
            value = value.value
        text = str(value)
        parts.append(f"{label} = {text if len(text) <= 40 else text[:37] + '…'}")
    return f"Completar la póliza {_policy_label(db, broker_id, args.policy_id)}: " + "; ".join(parts)


def _validate_installment(db: Session, *, broker_id: int, installment_id: int):
    from app.api.routers.collections import get_installment_or_404

    return get_installment_or_404(db, installment_id, broker_id)


def _installment_result(db: Session, broker_id: int, installment_id: int, detail: str, **extra) -> dict:
    inst = _own_installment(db, broker_id, installment_id)
    plan = inst.collection_plan if inst is not None else None
    return {
        "entity_type": EntityType.COLLECTION_PLAN.value,
        "entity_id": plan.id if plan is not None else None,
        "url": f"/policies/{plan.policy_id}" if plan is not None else "/collections",
        "detail": detail,
        "installment_id": installment_id,
        **extra,
    }


def _validate_register_payment(
    db: Session, *, broker_id: int, user: User, args: RegisterPaymentArgs
) -> None:
    from app.api.routers.policies import _resolve_document

    _validate_installment(db, broker_id=broker_id, installment_id=args.installment_id)
    _resolve_document(db, args.receipt_document_id, broker_id)


def _exec_register_payment(
    db: Session, *, broker_id: int, user: User, args: RegisterPaymentArgs
) -> dict:
    from app.api.routers.collections import register_installment_payment
    from app.schemas.collection_board import PaymentCreate

    result = register_installment_payment(
        args.installment_id,
        payload=PaymentCreate(
            amount=args.amount,
            currency=args.currency,
            paid_on=args.paid_on,
            method=args.method,
            receipt_document_id=args.receipt_document_id,
            confirmed_by_insurer=args.confirmed_by_insurer,
            note=args.note,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return _installment_result(
        db, broker_id, args.installment_id,
        f"Pago registrado ({result.payment.evidence_level.value}); cuota {result.state.state}",
        payment_id=result.payment.id,
        state=result.state.state,
        evidence=result.payment.evidence_level.value,
    )


def _summarize_register_payment(db: Session, *, broker_id: int, args: RegisterPaymentArgs) -> str:
    evidence = (
        " — confirmado por la compañía"
        if args.confirmed_by_insurer
        else " — pago declarado (sin confirmación de la compañía)"
    )
    return (
        f"Registrar un pago de {args.amount} {args.currency.value} el {_fmt_day(args.paid_on)} "
        f"en {_installment_label(db, broker_id, args.installment_id)}{evidence}"
    )


def _validate_record_promise(
    db: Session, *, broker_id: int, user: User, args: RecordPaymentPromiseArgs
) -> None:
    _validate_installment(db, broker_id=broker_id, installment_id=args.installment_id)


def _exec_record_promise(
    db: Session, *, broker_id: int, user: User, args: RecordPaymentPromiseArgs
) -> dict:
    from app.api.routers.collections import register_payment_promise
    from app.schemas.collection_board import PromiseCreate

    result = register_payment_promise(
        args.installment_id,
        payload=PromiseCreate(promise_until=args.promise_until, note=args.note),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return _installment_result(
        db, broker_id, args.installment_id,
        f"Promesa de pago anotada para el {_fmt_day(args.promise_until)}",
        state=result.state.state,
        note_id=result.note_id,
    )


def _summarize_record_promise(
    db: Session, *, broker_id: int, args: RecordPaymentPromiseArgs
) -> str:
    return (
        f"Anotar que {_installment_label(db, broker_id, args.installment_id)} "
        f"se pagará el {_fmt_day(args.promise_until)}"
    )


def _plan_for(db: Session, broker_id: int, args: StartCollectionArgs):
    from app.api.routers.collections import get_plan_or_404
    from app.api.routers.policies import get_policy_or_404
    from app.models.collection import CollectionPlan

    if args.plan_id is not None:
        return get_plan_or_404(db, args.plan_id, broker_id)
    policy = get_policy_or_404(db, args.policy_id, broker_id)
    plan = db.scalars(
        select(CollectionPlan)
        .where(CollectionPlan.broker_id == broker_id, CollectionPlan.policy_id == policy.id)
        .order_by(CollectionPlan.id.desc())
    ).first()
    if plan is None:
        raise _unprocessable(
            "La póliza no tiene plan de pago: cárgalo en la ficha antes de iniciar la cobranza."
        )
    return plan


def _validate_start_collection(
    db: Session, *, broker_id: int, user: User, args: StartCollectionArgs
) -> None:
    _plan_for(db, broker_id, args)


def _exec_start_collection(
    db: Session, *, broker_id: int, user: User, args: StartCollectionArgs
) -> dict:
    from app.api.routers.collections import start_collection
    from app.models.enums import CollectionLevelPreference
    from app.schemas.collection_board import PlanStart

    plan = _plan_for(db, broker_id, args)
    result = start_collection(
        plan.id,
        payload=PlanStart(
            level_preference=(
                CollectionLevelPreference(args.level_preference) if args.level_preference else None
            )
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.COLLECTION_PLAN.value,
        "entity_id": result.id,
        "url": f"/policies/{result.policy_id}",
        "detail": "Cobranza iniciada (recordatorios al pagador activados)",
    }


def _summarize_start_collection(db: Session, *, broker_id: int, args: StartCollectionArgs) -> str:
    from app.models.collection import CollectionPlan

    policy_id = args.policy_id
    if policy_id is None and args.plan_id is not None:
        plan = db.get(CollectionPlan, args.plan_id)
        policy_id = plan.policy_id if plan is not None and plan.broker_id == broker_id else None
    level = {"assisted": " (cobro asistido)", "auto": " (nivel automático)"}.get(
        args.level_preference or "", ""
    )
    return (
        f"Iniciar la cobranza de la póliza {_policy_label(db, broker_id, policy_id)}"
        f"{level}: activa los recordatorios al pagador"
    )


def _validate_send_status_inquiry(
    db: Session, *, broker_id: int, user: User, args: SendStatusInquiryArgs
) -> None:
    from app.api.routers.policies import get_policy_or_404
    from app.services.collection.resolver import insurer_contact_email, lead_insurer_id
    from app.services.mail import inquiries as inquiry_service
    from app.services.notices import installment_state

    policy = get_policy_or_404(db, args.policy_id, broker_id)
    own = inquiry_service.policy_installments(db, policy)
    if args.installment_ids:
        if any(i not in {inst.id for inst in own} for i in args.installment_ids):
            raise _unprocessable(inquiry_service.REASON_FOREIGN_INSTALLMENT)
    else:
        from app.api.routers.collections import _today

        today = _today()
        open_ids = [
            inst.id
            for inst in own
            if installment_state(db, inst, today=today).state not in ("paid", "voided")
        ]
        if not open_ids:
            raise _unprocessable(
                "La póliza no tiene cuotas pendientes que consultar a la compañía."
            )
        # The card shows exactly what will be asked: resolved now, stored as-is.
        args.installment_ids = open_ids
    insurer_id = lead_insurer_id(policy) or policy.insurer_id
    if not (args.to_address or "").strip() and not insurer_contact_email(
        db, broker_id=broker_id, insurer_id=insurer_id
    ):
        raise _unprocessable(
            inquiry_service.REASON_NO_INSURER_EMAIL.format(insurer=_insurer_label(db, insurer_id))
        )


def _exec_send_status_inquiry(
    db: Session, *, broker_id: int, user: User, args: SendStatusInquiryArgs
) -> dict:
    from app.api.routers.inquiries import create_status_inquiry
    from app.schemas.inquiries import InquiryCreate

    inquiry = create_status_inquiry(
        payload=InquiryCreate(
            policy_id=args.policy_id,
            installment_ids=list(args.installment_ids or []),
            to_address=args.to_address,
            message=args.message,
            send_via="auto",
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    mail_status = getattr(inquiry.mail_status, "value", inquiry.mail_status)
    return {
        "entity_type": EntityType.STATUS_INQUIRY.value,
        "entity_id": inquiry.id,
        "url": "/collections/inquiries",
        "detail": f"Consulta {inquiry.token} a {inquiry.to_address} ({mail_status})",
        "mail_status": mail_status,
        "reference": inquiry.token,
    }


def _summarize_send_status_inquiry(
    db: Session, *, broker_id: int, args: SendStatusInquiryArgs
) -> str:
    from app.models.collection import CollectionInstallment

    numbers = []
    for inst_id in args.installment_ids or []:
        inst = db.get(CollectionInstallment, inst_id)
        if inst is not None and inst.broker_id == broker_id:
            numbers.append(str(inst.number))
    policy = _own_policy(db, broker_id, args.policy_id)
    insurer = _insurer_label(db, policy.insurer_id) if policy is not None else "la compañía"
    to = f" ({args.to_address})" if args.to_address else ""
    cuotas = f"las cuotas {', '.join(numbers)}" if numbers else "sus cuotas pendientes"
    return (
        f"Consultar a {insurer}{to} por correo el estado de pago de {cuotas} "
        f"de la póliza {_policy_label(db, broker_id, args.policy_id)}"
    )


def _validate_send_payment_reminder(
    db: Session, *, broker_id: int, user: User, args: SendPaymentReminderArgs
) -> None:
    from app.services import notices
    from app.services.collection.resolver import payer_contact_email

    inst = _validate_installment(db, broker_id=broker_id, installment_id=args.installment_id)
    today = notices.business_today()
    if notices.installment_state(db, inst, today=today).state in ("paid", "voided"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=notices.REASON_SETTLED)
    if notices.find_existing_notice(
        db, installment_id=inst.id, kind=args.kind, cadence_level=None,
        channel="email", today=today,
    ) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=notices.REASON_DUPLICATE)
    if not payer_contact_email(inst.collection_plan.policy):
        raise _unprocessable(notices.REASON_NO_PAYER)
    if notices.effective_template(db, broker_id=broker_id, kind=args.kind, channel="email") is None:
        raise _unprocessable(notices.REASON_NO_TEMPLATE)


def _exec_send_payment_reminder(
    db: Session, *, broker_id: int, user: User, args: SendPaymentReminderArgs
) -> dict:
    from app.api.routers.inquiries import send_payer_notice
    from app.models.enums import NoticeChannel, NoticeKind
    from app.schemas.inquiries import NoticeRequest

    notice = send_payer_notice(
        payload=NoticeRequest(
            installment_id=args.installment_id,
            kind=NoticeKind(args.kind),
            channel=NoticeChannel.EMAIL,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    notice_status = getattr(notice.status, "value", notice.status)
    return _installment_result(
        db, broker_id, args.installment_id,
        f"Aviso al pagador enviado a {notice.to_address} ({notice_status})",
        notice_id=notice.id,
        notice_status=notice_status,
    )


def _summarize_send_payment_reminder(
    db: Session, *, broker_id: int, args: SendPaymentReminderArgs
) -> str:
    from app.services.collection.resolver import payer_contact_email

    inst = _own_installment(db, broker_id, args.installment_id)
    to = payer_contact_email(inst.collection_plan.policy) if inst is not None else None
    kind = "aviso de cuota vencida" if args.kind == "payer_overdue" else "recordatorio de pago"
    return (
        f"Enviar un {kind} al pagador{f' ({to})' if to else ''} por "
        f"{_installment_label(db, broker_id, args.installment_id)}"
    )


def _gates_create_share_link(args: CreateShareLinkArgs) -> list[tuple[str, str]]:
    from app.services.share_links import module_for_entity

    return [(module_for_entity(args.entity_type), "View")]


def _validate_create_share_link(
    db: Session, *, broker_id: int, user: User, args: CreateShareLinkArgs
) -> None:
    from app.api.routers.share import _target_or_404
    from app.models.enums import ShareEntity
    from app.services.share_links import audience_refusal

    _target_or_404(
        db, broker_id=broker_id, user=user,
        entity_type=ShareEntity(args.entity_type), entity_id=args.entity_id,
    )
    refusal = audience_refusal(args.entity_type, args.audience)
    if refusal is not None:
        raise _unprocessable(refusal)


def _exec_create_share_link(
    db: Session, *, broker_id: int, user: User, args: CreateShareLinkArgs
) -> dict:
    from app.api.routers.share import create_link
    from app.models.enums import ShareAudience, ShareEntity
    from app.schemas.share import ShareLinkCreate

    link = create_link(
        payload=ShareLinkCreate(
            entity_type=ShareEntity(args.entity_type),
            entity_id=args.entity_id,
            audience=ShareAudience(args.audience),
            expires_in_days=args.expires_in_days,
            label=args.label,
        ),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.SHARE_LINK.value,
        "entity_id": link.id,
        # The PUBLIC link (absolute) — what the broker sends.
        "url": link.url,
        "share_url": link.url,
        "whatsapp_url": link.whatsapp_url,
        "whatsapp_text": link.whatsapp_text,
        "expires_at": link.expires_at.isoformat() if link.expires_at else None,
        "detail": f"Enlace público creado para {_AUDIENCE_ES.get(args.audience, args.audience)}",
    }


def _summarize_create_share_link(db: Session, *, broker_id: int, args: CreateShareLinkArgs) -> str:
    target = _SHARE_ENTITY_ES.get(args.entity_type, args.entity_type)
    if args.entity_type == "policy":
        target = f"la póliza {_policy_label(db, broker_id, args.entity_id)}"
    elif args.entity_type == "installment":
        target = _installment_label(db, broker_id, args.entity_id)
    else:
        target = f"{target} #{args.entity_id}"
    days = args.expires_in_days or 60
    return (
        f"Crear un enlace público (WhatsApp) de {target} para "
        f"{_AUDIENCE_ES.get(args.audience, args.audience)}, válido por {days} días"
    )


def _validate_create_payment_link(
    db: Session, *, broker_id: int, user: User, args: CreatePaymentLinkArgs
) -> None:
    from app.services.collection.resolver import resolve_collection_mode

    for installment_id in dict.fromkeys(args.installment_ids):
        inst = _validate_installment(db, broker_id=broker_id, installment_id=installment_id)
        mode = resolve_collection_mode(db, inst, broker_id=broker_id)
        if mode.level != "native":
            raise _unprocessable(
                f"La cuota {inst.number} no se cobra con link de pago (nivel {mode.level}): "
                f"{mode.reason}"
            )


def _exec_create_payment_link(
    db: Session, *, broker_id: int, user: User, args: CreatePaymentLinkArgs
) -> dict:
    from app.api.routers.payments import create_intent
    from app.schemas.payments import IntentCreate

    created = create_intent(
        payload=IntentCreate(installment_ids=list(dict.fromkeys(args.installment_ids))),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.PAYMENT_INTENT.value,
        "entity_id": created.intent.id,
        "url": created.checkout_url,
        "checkout_url": created.checkout_url,
        "reused": created.reused,
        "detail": "Link de pago " + ("reutilizado" if created.reused else "generado"),
    }


def _summarize_create_payment_link(
    db: Session, *, broker_id: int, args: CreatePaymentLinkArgs
) -> str:
    labels = [_installment_label(db, broker_id, i) for i in dict.fromkeys(args.installment_ids)]
    return "Generar un link de pago (Forpay) por " + "; ".join(labels)


def _validate_skip_case(
    db: Session, *, broker_id: int, user: User, args: SkipCaseToStageArgs
) -> None:
    from app.api.routers.case_files import _detail as case_detail
    from app.api.routers.case_files import _get_case

    case = _get_case(db, args.case_file_id, broker_id, user)
    targets = [stage.value for stage in case_detail(db, case).skip_targets]
    if args.target_stage not in targets:
        raise _unprocessable(
            f"La carpeta {case.reference or case.id} (etapa {case.stage.value}) no puede saltar a "
            f"{args.target_stage}. "
            + (
                "Destinos posibles: " + ", ".join(targets) + "."
                if targets
                else "Solo una cuenta abierta salta etapas, y solo hacia adelante."
            )
        )


def _exec_skip_case(db: Session, *, broker_id: int, user: User, args: SkipCaseToStageArgs) -> dict:
    from app.api.routers.case_files import skip_case_file_to
    from app.models.enums import CaseStage
    from app.schemas.case_file import CaseSkipTo

    detail = skip_case_file_to(
        args.case_file_id,
        payload=CaseSkipTo(target_stage=CaseStage(args.target_stage), note=args.note),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Cuenta {detail.reference or detail.id} saltó a {args.target_stage}",
    }


def _case_label(db: Session, broker_id: int, case_file_id: int) -> str:
    from app.models.case_file import CaseFile

    case = db.get(CaseFile, case_file_id)
    if case is None or case.broker_id != broker_id:
        return f"#{case_file_id}"
    return case.reference or f"#{case.id}"


def _summarize_skip_case(db: Session, *, broker_id: int, args: SkipCaseToStageArgs) -> str:
    return (
        f"Saltar la cuenta {_case_label(db, broker_id, args.case_file_id)} a la etapa "
        f"{args.target_stage}, marcando como omitidas las etapas intermedias"
    )


def _validate_activate_stage(
    db: Session, *, broker_id: int, user: User, args: ActivateCaseStageArgs
) -> None:
    from app.api.routers.case_files import _detail as case_detail
    from app.api.routers.case_files import _get_case

    case = _get_case(db, args.case_file_id, broker_id, user)
    detail = case_detail(db, case)
    if detail.has_policy:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ya tiene póliza: los cambios se hacen por endoso o corrección",
        )
    skipped = [row.stage.value for row in detail.stages if row.state == "skipped"]
    if args.stage not in skipped:
        raise _unprocessable(
            f"La etapa '{args.stage}' no está omitida en {case.reference or case.id}. "
            + ("Omitidas: " + ", ".join(skipped) + "." if skipped else "No hay etapas omitidas.")
        )


def _exec_activate_stage(
    db: Session, *, broker_id: int, user: User, args: ActivateCaseStageArgs
) -> dict:
    from app.api.routers.case_files import activate_case_file_stage
    from app.models.enums import CaseStage
    from app.schemas.case_file import CaseActivateStage

    try:
        stage = CaseStage(args.stage)
    except ValueError as exc:
        raise _unprocessable(f"Etapa desconocida: {args.stage}") from exc
    detail = activate_case_file_stage(
        args.case_file_id,
        payload=CaseActivateStage(stage=stage, note=args.note),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    return {
        "entity_type": EntityType.CASE_FILE.value,
        "entity_id": detail.id,
        "url": f"/cases/{detail.id}",
        "detail": f"Etapa {args.stage} activada en {detail.reference or detail.id}",
    }


def _summarize_activate_stage(db: Session, *, broker_id: int, args: ActivateCaseStageArgs) -> str:
    return (
        f"Activar la etapa omitida {args.stage} en la cuenta "
        f"{_case_label(db, broker_id, args.case_file_id)}"
    )


def _validate_run_portal_search(
    db: Session, *, broker_id: int, user: User, args: RunPortalSearchArgs
) -> None:
    from app.api.routers.portal_searches import (
        MSG_INVALID_INPUT,
        MSG_UNKNOWN_PORTAL,
        MSG_UNKNOWN_SUBTYPE,
        MSG_UNSUPPORTED_SUBTYPE,
        _check_case,
        _require_available,
    )
    from app.models.catalog import InsuranceSubtype
    from app.services.rpa.base import InputInvalid
    from app.services.rpa.registry import UPCOMING_REASON, get_connector, is_upcoming

    key = args.portal.strip().lower()
    connector = get_connector(key)
    if connector is None:
        raise _unprocessable(UPCOMING_REASON if is_upcoming(key) else MSG_UNKNOWN_PORTAL)
    if args.case_file_id is not None:
        _check_case(db, args.case_file_id, broker_id=broker_id, user=user)
    subtype = db.get(InsuranceSubtype, args.insurance_subtype_id)
    if subtype is None or not subtype.is_active:
        raise _unprocessable(MSG_UNKNOWN_SUBTYPE)
    if subtype.code not in connector.supported_subtypes:
        raise _unprocessable(MSG_UNSUPPORTED_SUBTYPE.format(portal=connector.name))
    try:
        connector.validate_input(args.input)
    except InputInvalid as exc:
        raise _unprocessable({"message": MSG_INVALID_INPUT, "errors": exc.errors}) from exc
    _require_available(db, connector, broker_id=broker_id)


def _exec_run_portal_search(
    db: Session, *, broker_id: int, user: User, args: RunPortalSearchArgs
) -> dict:
    from fastapi import BackgroundTasks

    from app.api.routers.portal_searches import create_run
    from app.schemas.portal_searches import RunCreate

    # Queued for the ``rpa.run`` job: a confirm request must not hold a
    # browser session open (and the background task of this throwaway
    # ``BackgroundTasks`` never runs outside a response).
    run = create_run(
        payload=RunCreate(
            portal=args.portal,
            insurance_subtype_id=args.insurance_subtype_id,
            case_file_id=args.case_file_id,
            input=args.input,
        ),
        background=BackgroundTasks(),
        db=db,
        broker_id=broker_id,
        current_user=user,
    )
    run_status = getattr(run.status, "value", run.status)
    return {
        "entity_type": EntityType.PORTAL_SEARCH.value,
        "entity_id": run.id,
        "url": "/portal-search",
        "detail": f"Búsqueda en {run.portal} en cola ({run_status})",
        "status": run_status,
    }


def _summarize_run_portal_search(db: Session, *, broker_id: int, args: RunPortalSearchArgs) -> str:
    from app.models.catalog import InsuranceSubtype

    subtype = db.get(InsuranceSubtype, args.insurance_subtype_id)
    label = subtype.code if subtype is not None else f"#{args.insurance_subtype_id}"
    plate = args.input.get("plate") if isinstance(args.input, dict) else None
    extra = f" para la patente {plate}" if plate else ""
    return f"Buscar cotizaciones en el portal {args.portal.upper()} ({label}){extra}"


# =============================================================================
# The registry
# =============================================================================

TOOL_REGISTRY: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        # --- READ ------------------------------------------------------------
        ToolSpec(
            name="search_entities",
            kind="read",
            module="Dashboard",
            action="View",
            description=(
                "Busca en el espacio de trabajo de la corredora: grupos, clientes, "
                "bienes, cotizaciones y propuestas. Entrega hasta 5 resultados por "
                "tipo con su id y etiqueta."
            ),
            args_schema=SearchEntitiesArgs,
            execute=_exec_search_entities,
        ),
        ToolSpec(
            name="get_group_tree",
            kind="read",
            module="Groups",
            action="View",
            description=(
                "El árbol completo de un grupo: clientes con sus RUT, vigencias, "
                "ramos y los expedientes de cada uno con su etapa."
            ),
            args_schema=GetGroupTreeArgs,
            execute=_exec_get_group_tree,
            resolve_gates=lambda args: [("Groups", "View"), ("CaseFiles", "View")],
        ),
        ToolSpec(
            name="get_case_file",
            kind="read",
            module="CaseFiles",
            action="View",
            description=(
                "El detalle de un expediente: referencia, etapa, vigencia, cliente, "
                "documentos por sección, hijos de post-venta y las transiciones "
                "permitidas con el motivo cuando una no lo está."
            ),
            args_schema=GetCaseFileArgs,
            execute=_exec_get_case_file,
        ),
        ToolSpec(
            name="get_case_history",
            kind="read",
            module="CaseFiles",
            action="View",
            description=(
                "La cadena de origen de un expediente y el contexto de la vigencia "
                "anterior: qué se aseguró, qué pólizas salieron y qué costó en "
                "siniestros. Úsalo para comparar contra la vigencia anterior."
            ),
            args_schema=GetCaseHistoryArgs,
            execute=_exec_get_case_history,
        ),
        ToolSpec(
            name="get_quote_comparison",
            kind="read",
            module="Proposals",
            action="View",
            description=(
                "El comparador de una solicitud de cotización: prima por propuesta "
                "(afecta, exenta, neta, IVA, total en UF), tasas, deducibles "
                "alineados por peligro y coberturas/exclusiones alineadas."
            ),
            args_schema=GetQuoteComparisonArgs,
            execute=_exec_get_quote_comparison,
        ),
        ToolSpec(
            name="list_documents",
            kind="read",
            module="Documents",
            action="View",
            description=(
                "Lista los documentos de un expediente u otra entidad: categoría, "
                "sección, código y nombre. Solo metadatos; nunca el archivo."
            ),
            args_schema=ListDocumentsArgs,
            execute=_exec_list_documents,
        ),
        # --- WRITE (propose-only in the loop; execute at confirm) -------------
        ToolSpec(
            name="create_group",
            kind="write",
            module="Groups",
            action="Create",
            description=(
                "PROPONE crear un grupo de cuentas (y opcionalmente adjuntar "
                "clientes existentes por id). No escribe nada: la persona debe "
                "confirmar la acción en el chat."
            ),
            args_schema=CreateGroupArgs,
            execute=_exec_create_group,
            summarize=_summarize_create_group,
            validate_proposal=_validate_create_group,
            resolve_gates=_gates_create_group,
            anchor=lambda arguments: (EntityType.ACCOUNT_GROUP, None),
        ),
        ToolSpec(
            name="attach_client_to_group",
            kind="write",
            module="Groups",
            action="Edit",
            description=(
                "PROPONE adjuntar un cliente existente a un grupo. No escribe "
                "nada: la persona debe confirmar la acción en el chat."
            ),
            args_schema=AttachClientToGroupArgs,
            execute=_exec_attach_client,
            summarize=_summarize_attach_client,
            validate_proposal=_validate_attach_client,
            anchor=lambda arguments: (
                EntityType.ACCOUNT_GROUP, arguments.get("group_id")
            ),
        ),
        ToolSpec(
            name="create_case_file",
            kind="write",
            module="CaseFiles",
            action="Create",
            description=(
                "PROPONE abrir un expediente de cuenta (kind=account) para un "
                "cliente, con su vigencia (period_start y period_end son "
                "obligatorios). No escribe nada: la persona debe confirmar."
            ),
            args_schema=CreateCaseFileArgs,
            execute=_exec_create_case_file,
            summarize=_summarize_create_case_file,
            validate_proposal=_validate_create_case_file,
            anchor=lambda arguments: (EntityType.CASE_FILE, None),
        ),
        ToolSpec(
            name="renew_case",
            kind="write",
            module="CaseFiles",
            action="Create",
            description=(
                "PROPONE abrir la renovación de un expediente para la próxima "
                "vigencia (period_start y period_end). No escribe nada: la "
                "persona debe confirmar."
            ),
            args_schema=RenewCaseArgs,
            execute=_exec_renew_case,
            summarize=_summarize_renew_case,
            validate_proposal=_validate_renew_case,
            anchor=lambda arguments: (
                EntityType.CASE_FILE, arguments.get("case_file_id")
            ),
        ),
        ToolSpec(
            name="add_note",
            kind="write",
            module="CaseFiles",  # display default; the gate resolves per entity_type
            action="Comment",
            description=(
                "PROPONE agregar una nota a una entidad (case_file, client, "
                "policy, proposal, sales_lead…), opcionalmente con fecha de "
                "seguimiento. No escribe nada: la persona debe confirmar."
            ),
            args_schema=AddNoteArgs,
            execute=_exec_add_note,
            summarize=_summarize_add_note,
            validate_proposal=_validate_add_note,
            resolve_gates=_gates_add_note,
            anchor=lambda arguments: (
                _note_entity_type(str(arguments.get("entity_type"))),
                arguments.get("entity_id"),
            ),
        ),
        # --- v12 READ ----------------------------------------------------------
        ToolSpec(
            name="list_policies",
            kind="read",
            module="Policies",
            action="View",
            description=(
                "Lista pólizas de la corredora (vista principal) con filtros: q (n°, "
                "asegurado o RUT), status (upcoming|active|expired|cancelled), "
                "insurer_id, line_code, subtype_id, collection (overdue|due_soon|"
                "no_plan|ok), missing (source_document|policy_number|plan|"
                "gross_premium|payer_contact|premiums|commission), grupo o cuenta. "
                "Máx. 20 filas; 'total' trae el conteo real."
            ),
            args_schema=ListPoliciesArgs,
            execute=_exec_list_policies,
            max_result_chars=9_000,
        ),
        ToolSpec(
            name="get_policy",
            kind="read",
            module="Policies",
            action="View",
            description=(
                "Ficha de una póliza: montos (afecta, exenta, neta, IVA, total UF), "
                "partes (asegurado, contratante, pagador), faltantes con su motivo, "
                "operatividad, resumen de cobranza y recorrido de su cuenta."
            ),
            args_schema=PolicyIdArgs,
            execute=_exec_get_policy,
            max_result_chars=8_000,
        ),
        ToolSpec(
            name="list_installments",
            kind="read",
            module="Collections",
            action="View",
            description=(
                "Cuotas de Cobranza con su estado vivo (bucket upcoming|overdue|"
                "current|settled), nivel de cobro (record|assisted|native) y su "
                "motivo, días de atraso, saldo UF/CLP, evidencia y última gestión. "
                "Máx. 30 filas."
            ),
            args_schema=ListInstallmentsArgs,
            execute=_exec_list_installments,
            max_result_chars=10_000,
        ),
        ToolSpec(
            name="get_collection_overview",
            kind="read",
            module="Collections",
            action="View",
            description=(
                "Tablero de cobranza: vencidas (cantidad y UF), riesgo de término, "
                "vencimientos a 7 y 30 días, recaudado del mes, pagos sin confirmar, "
                "respuestas de compañías por revisar, pólizas sin plan, y las 5 "
                "cuotas vencidas más urgentes."
            ),
            args_schema=EmptyArgs,
            execute=_exec_get_collection_overview,
            max_result_chars=8_000,
        ),
        ToolSpec(
            name="get_collection_mode",
            kind="read",
            module="Collections",
            action="View",
            description=(
                "Nivel de cobro de una cuota ahora (record, assisted o native), el "
                "motivo en español, los canales y lo que falta para subir de nivel."
            ),
            args_schema=InstallmentIdArgs,
            execute=_exec_get_collection_mode,
        ),
        ToolSpec(
            name="list_status_inquiries",
            kind="read",
            module="Collections",
            action="View",
            description=(
                "Consultas de estado de pago enviadas a las compañías: estado "
                "(draft|sent|replied|resolved|failed|cancelled), cuotas consultadas "
                "y respuestas abiertas."
            ),
            args_schema=ListStatusInquiriesArgs,
            execute=_exec_list_status_inquiries,
        ),
        ToolSpec(
            name="list_pending_evidence",
            kind="read",
            module="Collections",
            action="View",
            description=(
                "Bandeja de respuestas por confirmar: lo que una compañía o un "
                "pagador informó (pagado, impago, parcial) y aún no confirma una "
                "persona. Es evidencia sugerida, no un pago registrado."
            ),
            args_schema=ListPendingEvidenceArgs,
            execute=_exec_list_pending_evidence,
        ),
        ToolSpec(
            name="check_case_files",
            kind="read",
            module="CaseFiles",
            action="View",
            description=(
                "Revisa una cuenta: etapas hechas, actual, omitidas y pendientes, a "
                "qué etapas puede saltar, si tiene póliza, y los archivos "
                "recomendados del ramo (cuáles están y cuáles faltan)."
            ),
            args_schema=CheckCaseFilesArgs,
            execute=_exec_check_case_files,
            max_result_chars=8_000,
        ),
        ToolSpec(
            name="check_policy_files",
            kind="read",
            module="Policies",
            action="View",
            description=(
                "El 'Falta: X' de una póliza: PDF de respaldo, n° de póliza, plan de "
                "pago, prima bruta, correo del pagador, primas y comisión, con el "
                "motivo y qué funciones quedan deshabilitadas."
            ),
            args_schema=PolicyIdArgs,
            execute=_exec_check_policy_files,
        ),
        ToolSpec(
            name="get_catalog",
            kind="read",
            module="Dashboard",
            action="View",
            description=(
                "Catálogo cerrado de ramos y subtipos de Radal (código, nombre, id "
                "del subtipo y ruta sugerida). Filtra con line_code."
            ),
            args_schema=GetCatalogArgs,
            execute=_exec_get_catalog,
            max_result_chars=12_000,
        ),
        ToolSpec(
            name="list_portal_searches",
            kind="read",
            module="Integrations",
            action="View",
            description=(
                "Búsquedas de cotizaciones en portales de compañías (RPA): estado, "
                "cantidad de cotizaciones y error cuando falló."
            ),
            args_schema=ListPortalSearchesArgs,
            execute=_exec_list_portal_searches,
        ),
        ToolSpec(
            name="get_portal_search",
            kind="read",
            module="Integrations",
            action="View",
            description=(
                "Detalle de una búsqueda en portal: pasos, cotizaciones (prima UF/"
                "CLP, deducible, coberturas), el motivo de una falla y las "
                "alternativas (reintentar, subir PDF, otro portal)."
            ),
            args_schema=PortalSearchIdArgs,
            execute=_exec_get_portal_search,
            max_result_chars=8_000,
        ),
        ToolSpec(
            name="list_notifications",
            kind="read",
            module="Dashboard",
            action="View",
            description=(
                "Las notificaciones abiertas de la persona (bandeja todo = por hacer, "
                "fyi = informativas)."
            ),
            args_schema=ListNotificationsArgs,
            execute=_exec_list_notifications,
        ),
        ToolSpec(
            name="load_skill",
            kind="read",
            module="Dashboard",
            action="View",
            description=(
                "Carga el playbook completo de una habilidad (key) para una tarea de "
                "varios pasos; queda activa en esta conversación."
            ),
            args_schema=LoadSkillArgs,
            execute=_exec_load_skill,
            thread_aware=True,
            max_result_chars=8_000,
        ),
        # --- v12 WRITE (propose-only; execute at confirm) ----------------------
        ToolSpec(
            name="create_policy_manual",
            kind="write",
            module="Policies",
            action="Create",
            description=(
                "PROPONE registrar una póliza a mano: asegurado (client_id, o "
                "insured_rut + insured_name), insurer_id, insurance_subtype_id y "
                "vigencia son obligatorios; n°, primas y comisión solo si la persona "
                "los dio. No escribe nada: la persona debe confirmar."
            ),
            args_schema=CreatePolicyManualArgs,
            execute=_exec_create_policy_manual,
            summarize=_summarize_create_policy_manual,
            validate_proposal=_validate_create_policy_manual,
            anchor=lambda arguments: (EntityType.POLICY, None),
        ),
        ToolSpec(
            name="update_policy_fields",
            kind="write",
            module="Policies",
            action="Edit",
            description=(
                "PROPONE completar datos VACÍOS de una póliza (Completar). Nunca "
                "cambia un dato ya registrado: eso se corrige en la ficha con su "
                "motivo. La persona debe confirmar."
            ),
            args_schema=UpdatePolicyFieldsArgs,
            execute=_exec_update_policy_fields,
            summarize=_summarize_update_policy_fields,
            validate_proposal=_validate_update_policy_fields,
            anchor=lambda arguments: (EntityType.POLICY, arguments.get("policy_id")),
        ),
        ToolSpec(
            name="register_payment",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE registrar un pago (total o parcial) de una cuota que la "
                "persona informó, con monto, moneda (UF|CLP|USD) y fecha. Queda como "
                "pago declarado salvo confirmed_by_insurer. La persona debe confirmar."
            ),
            args_schema=RegisterPaymentArgs,
            execute=_exec_register_payment,
            summarize=_summarize_register_payment,
            validate_proposal=_validate_register_payment,
            anchor=lambda arguments: (EntityType.INSTALLMENT, arguments.get("installment_id")),
        ),
        ToolSpec(
            name="record_payment_promise",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE anotar una promesa de pago de una cuota (promete pagar el "
                "promise_until) con una nota de gestión. La persona debe confirmar."
            ),
            args_schema=RecordPaymentPromiseArgs,
            execute=_exec_record_promise,
            summarize=_summarize_record_promise,
            validate_proposal=_validate_record_promise,
            anchor=lambda arguments: (EntityType.INSTALLMENT, arguments.get("installment_id")),
        ),
        ToolSpec(
            name="start_collection",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE iniciar la cobranza de un plan de pago (plan_id o policy_id): "
                "activa los recordatorios al pagador. La cobranza asistida funciona "
                "con cualquier compañía. La persona debe confirmar."
            ),
            args_schema=StartCollectionArgs,
            execute=_exec_start_collection,
            summarize=_summarize_start_collection,
            validate_proposal=_validate_start_collection,
            anchor=lambda arguments: (EntityType.COLLECTION_PLAN, arguments.get("plan_id")),
        ),
        ToolSpec(
            name="send_status_inquiry",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE preguntar por correo a la compañía el estado de pago de las "
                "cuotas de una póliza (sin installment_ids: todas las pendientes). Úsalo "
                "en vez de suponer un pago. La persona debe confirmar."
            ),
            args_schema=SendStatusInquiryArgs,
            execute=_exec_send_status_inquiry,
            summarize=_summarize_send_status_inquiry,
            validate_proposal=_validate_send_status_inquiry,
            anchor=lambda arguments: (EntityType.POLICY, arguments.get("policy_id")),
        ),
        ToolSpec(
            name="send_payment_reminder",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE enviar ahora un aviso por correo al pagador de una cuota "
                "(payer_reminder o payer_overdue). La persona debe confirmar."
            ),
            args_schema=SendPaymentReminderArgs,
            execute=_exec_send_payment_reminder,
            summarize=_summarize_send_payment_reminder,
            validate_proposal=_validate_send_payment_reminder,
            anchor=lambda arguments: (EntityType.INSTALLMENT, arguments.get("installment_id")),
        ),
        ToolSpec(
            name="create_share_link",
            kind="write",
            module="Policies",  # display default; the gate resolves per entity_type
            action="View",
            description=(
                "PROPONE crear un enlace público (listo para WhatsApp) de una póliza, "
                "plan, cuota o cuenta para el asegurado (insured) o la compañía "
                "(insurer). Una cuenta solo se comparte con el asegurado. La persona "
                "debe confirmar."
            ),
            args_schema=CreateShareLinkArgs,
            execute=_exec_create_share_link,
            summarize=_summarize_create_share_link,
            validate_proposal=_validate_create_share_link,
            resolve_gates=_gates_create_share_link,
            anchor=lambda arguments: (EntityType.SHARE_LINK, None),
        ),
        ToolSpec(
            name="create_payment_link",
            kind="write",
            module="Collections",
            action="Edit",
            description=(
                "PROPONE generar un link de pago (Forpay) por cuotas de nivel nativo. "
                "Si una cuota no es nativa se rechaza con el motivo. La persona debe "
                "confirmar."
            ),
            args_schema=CreatePaymentLinkArgs,
            execute=_exec_create_payment_link,
            summarize=_summarize_create_payment_link,
            validate_proposal=_validate_create_payment_link,
            anchor=lambda arguments: (EntityType.PAYMENT_INTENT, None),
        ),
        ToolSpec(
            name="skip_case_to_stage",
            kind="write",
            module="CaseFiles",
            action="Edit",
            description=(
                "PROPONE saltar una cuenta hacia adelante (quotes_received, "
                "insured_decision, policy_issued o active), marcando las etapas "
                "intermedias como omitidas. La persona debe confirmar."
            ),
            args_schema=SkipCaseToStageArgs,
            execute=_exec_skip_case,
            summarize=_summarize_skip_case,
            validate_proposal=_validate_skip_case,
            anchor=lambda arguments: (EntityType.CASE_FILE, arguments.get("case_file_id")),
        ),
        ToolSpec(
            name="activate_case_stage",
            kind="write",
            module="CaseFiles",
            action="Edit",
            description=(
                "PROPONE reactivar una etapa OMITIDA de una cuenta que aún no tiene "
                "póliza. La persona debe confirmar."
            ),
            args_schema=ActivateCaseStageArgs,
            execute=_exec_activate_stage,
            summarize=_summarize_activate_stage,
            validate_proposal=_validate_activate_stage,
            anchor=lambda arguments: (EntityType.CASE_FILE, arguments.get("case_file_id")),
        ),
        ToolSpec(
            name="run_portal_search",
            kind="write",
            module="Integrations",
            action="Submit",
            description=(
                "PROPONE buscar cotizaciones en el portal de una compañía (hoy 'hdi', "
                "vehículos VEH-01): insurance_subtype_id e input {plate, brand, model, "
                "year, use, commune, insured_rut, start_date}. Queda en cola para el "
                "robot. La persona debe confirmar."
            ),
            args_schema=RunPortalSearchArgs,
            execute=_exec_run_portal_search,
            summarize=_summarize_run_portal_search,
            validate_proposal=_validate_run_portal_search,
            anchor=lambda arguments: (EntityType.PORTAL_SEARCH, None),
        ),
    )
}

READ_TOOLS = tuple(s.name for s in TOOL_REGISTRY.values() if s.kind == "read")
WRITE_TOOLS = tuple(s.name for s in TOOL_REGISTRY.values() if s.kind == "write")


def _validate_registry() -> None:
    """Import-time typo guard, like ``require_permission``'s ValueError."""
    for spec in TOOL_REGISTRY.values():
        if spec.module not in MODULES:
            raise ValueError(f"Tool {spec.name!r} names unknown module {spec.module!r}")
        if spec.action not in ACTIONS:
            raise ValueError(f"Tool {spec.name!r} names unknown action {spec.action!r}")
        if spec.kind == "write" and spec.summarize is None:
            raise ValueError(f"Write tool {spec.name!r} must define summarize()")
        if spec.kind == "write" and spec.validate_proposal is None:
            raise ValueError(f"Write tool {spec.name!r} must define validate_proposal()")
        if spec.kind == "write" and spec.thread_aware:
            raise ValueError(f"Write tool {spec.name!r} cannot be thread_aware")


_validate_registry()


def openai_tools() -> list[dict]:
    """The registry as OpenAI-compatible ``tools`` dicts.

    Parameters are the args schema's JSON Schema run through the existing
    ``_compact_schema`` pruning in :mod:`app.services.ai` (title/default noise
    stripped), so the payload stays a model-friendly size.
    """
    from app.services.ai import _compact_schema

    return [
        {
            "type": "function",
            "function": {
                "name": spec.name,
                "description": spec.description,
                "parameters": _compact_schema(spec.args_schema),
            },
        }
        for spec in TOOL_REGISTRY.values()
    ]


def action_anchor(tool: str, arguments: dict) -> tuple[EntityType, int | None]:
    """Which entity the action is about — anchors activity rows (discard incl.)."""
    spec = TOOL_REGISTRY.get(tool)
    if spec is not None and spec.anchor is not None:
        try:
            return spec.anchor(dict(arguments or {}))
        except Exception:  # noqa: BLE001 - a broken anchor must not block resolution
            pass
    return (EntityType.CASE_FILE, None)


def action_allowed(action: Any, user: User) -> bool:
    """Whether ``user`` currently holds every gate this action needs.

    Computed per response for the caller so the card can render Confirmar
    disabled-with-reason; the confirm endpoint re-checks regardless.
    """
    spec = TOOL_REGISTRY.get(action.tool)
    if spec is None:
        return False
    try:
        args = spec.args_schema.model_validate(action.arguments or {})
        gates = gates_for(spec, args)
    except Exception:  # noqa: BLE001 - unresolvable arguments -> not confirmable
        return False
    return all(user_has_permission(user, module, act) for module, act in gates)
